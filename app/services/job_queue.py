"""RCA job 처리용 asyncio Queue 워커.

수집기(SDK)는 스케줄대로 계속 번들을 쏘므로, 요청 폭주 시 병렬 처리 상한을
제어해야 한다. `BackgroundTasks`는 요청당 태스크를 무제한 생성하므로 상한 제어가
안 된다. 이 모듈은 고정 개수(N)의 워커가 큐에서 job을 꺼내 처리하는 구조로,
동시 실행 job 수를 최대 N개로 묶는다.

역할 분리:
  - 워커(_process): job 상태 머신 담당. PENDING → RUNNING → DONE/FAILED.
  - runner: 실제 RCA 작업(현재는 Spring 위임 stub, 추후 orchestrator.run).
    runner가 예외를 던지면 워커가 job을 FAILED로 전환한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import select

from app.core.config import settings
from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal
from app.schemas.contracts import IngestBundle
from app.services.rca_validation import validate_rca_result

logger = logging.getLogger(__name__)

# runner(job_id, bundle): 실제 RCA 작업.
#   반환: RcaResult 또는 dict(검증 대상), 또는 None(산출물 없음 — Spring 위임만).
#   실패 시 예외를 던지면 워커가 job을 FAILED로 전환.
RcaRunner = Callable[[int, IngestBundle], Awaitable[object | None]]


async def _default_runner(job_id: int, bundle: IngestBundle) -> object:
    """기본 runner — 오케스트레이터로 RCA 산출 후 Spring 저장.

    RcaResult를 반환하면 워커가 검증·저장하고 DONE 전환한다.
    Spring 저장은 best-effort: 데모/개발 중 Spring 미가동이어도 job은 실패시키지 않는다.
    실 연동 강화(재시도·엄격 실패 처리)는 #9.
    """
    from app.agents.orchestrator import orchestrator
    from app.services.spring_client import spring_client

    result = await orchestrator.run(job_id, bundle)
    try:
        # 신규 구조: 번들 + 리포트를 한 번에 POST
        await spring_client.save_result(job_id, bundle, result)
    except Exception:
        logger.warning("Spring 저장 실패(무시, 데모): job %s", job_id)
    return result


class RcaJobQueue:
    """고정 워커 풀 + asyncio.Queue 기반 job 처리기."""

    def __init__(
        self,
        concurrency: int | None = None,
        session_factory: Callable = AsyncSessionLocal,
        runner: RcaRunner = _default_runner,
    ) -> None:
        self._queue: asyncio.Queue[tuple[int, IngestBundle]] = asyncio.Queue()
        self._concurrency = concurrency or settings.rca_worker_concurrency
        self._session_factory = session_factory
        self._runner = runner
        self._workers: list[asyncio.Task] = []
        self._started = False

    async def enqueue(self, job_id: int, bundle: IngestBundle) -> None:
        """job을 큐에 넣는다. 워커 미기동 상태여도 안전(큐에 적재만)."""
        await self._queue.put((job_id, bundle))

    def start(self) -> None:
        """워커 풀을 기동한다. 실행 중인 이벤트 루프 안에서 호출해야 한다."""
        if self._started:
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"rca-worker-{i}")
            for i in range(self._concurrency)
        ]
        logger.info("RCA 워커 %d개 기동", self._concurrency)

    async def stop(self) -> None:
        """큐를 비운 뒤 워커를 정리한다(graceful)."""
        if not self._started:
            return
        await self._queue.join()  # 적재분 처리 완료까지 대기
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers = []
        self._started = False
        logger.info("RCA 워커 정리 완료")

    @property
    def size(self) -> int:
        """대기 중인 job 수(관측용)."""
        return self._queue.qsize()

    async def _worker(self, idx: int) -> None:
        while True:
            job_id, bundle = await self._queue.get()
            try:
                await self._process(job_id, bundle)
            except Exception:  # 워커 자체는 절대 죽지 않는다
                logger.exception("워커 %d: job %s 처리 중 예외", idx, job_id)
            finally:
                self._queue.task_done()

    async def _process(self, job_id: int, bundle: IngestBundle) -> None:
        """job 상태 머신: RUNNING 전환 → runner 실행 → DONE/FAILED."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(IngestJob).where(IngestJob.job_id == job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                logger.warning("job %s 없음 — 스킵", job_id)
                return

            job.status = "RUNNING"
            await db.commit()

            try:
                raw = await self._runner(job_id, bundle)
                if raw is not None:
                    # 산출물이 있으면 RcaResult 5키 계약에 맞는지 검증 후 저장.
                    # 어긋나면 RcaResultInvalid → FAILED 전환.
                    validated = validate_rca_result(raw)
                    job.result = validated.model_dump(by_alias=True, exclude_none=True)
                job.status = "DONE"
                job.error = None
            except Exception as exc:
                logger.exception("job %s RCA 실패", job_id)
                job.status = "FAILED"
                job.error = str(exc)  # 사유 전체 저장(truncation 없음)
            finally:
                await db.commit()


# 앱 전역 큐 인스턴스. lifespan에서 start()/stop() 호출.
job_queue = RcaJobQueue()
