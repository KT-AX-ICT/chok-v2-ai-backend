"""RCA job 처리용 asyncio Queue 워커.

수집기(SDK)는 스케줄대로 계속 번들을 쏘므로, 요청 폭주 시 병렬 처리 상한을
제어해야 한다. `BackgroundTasks`는 요청당 태스크를 무제한 생성하므로 상한 제어가
안 된다. 이 모듈은 고정 개수(N)의 워커가 큐에서 job을 꺼내 처리하는 구조로,
동시 실행 job 수를 최대 N개로 묶는다.

큐에는 job_id만 싣는다 — 번들 원본은 DB(ingest_job.bundle)에 이미 저장돼 있으므로,
워커가 job을 조회하면서 함께 복원한다(단일 출처, 큐 경량화).

역할 분리:
  - 워커(_process): job 상태 머신 담당. PENDING → RUNNING → DELIVERING → DONE / FAILED.
    RCA 실패 시 오케스트레이터부터 1회 재시작하고, 검증을 통과한 산출물만
    Spring으로 보낸다. Spring 전송이 성공해야 DONE으로 확정하고, 실패하면
    DELIVERING에 머물러 재전송 루프(delivery_reconciler)가 다시 민다. 최종
    분석 실패 시에는 폴백으로 실패 사유+번들을 Spring에 전송.
  - runner: 산출물 생성만 담당(기본은 orchestrator.run). LLM 심층 구현으로
    교체할 때 이 시그니처만 맞추면 된다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import select

from app.core.config import settings
from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal
from app.schemas.contracts import IngestBundle, RcaResult
from app.services import bundle_store
from app.services.rca_validation import validate_rca_result

logger = logging.getLogger(__name__)

# runner(job_id, bundle): RCA 산출물 생성.
#   반환: RcaResult 또는 dict(검증 대상), 또는 None(산출물 없음).
#   실패 시 예외를 던지면 워커가 재시도 후 job을 FAILED로 전환.
RcaRunner = Callable[[int, IngestBundle], Awaitable[object | None]]


async def _default_runner(job_id: int, bundle: IngestBundle) -> object:
    """기본 runner — 오케스트레이터로 RCA 산출물을 만들어 반환한다.

    Spring 전송·검증·재시도는 워커(_process)가 담당한다. runner는 '산출물 생성'만
    책임지므로, LLM 심층 분석 구현으로 교체할 때 이 시그니처만 맞추면 된다.
    """
    from app.agents.orchestrator import orchestrator

    return await orchestrator.run(job_id, bundle)


class RcaJobQueue:
    """고정 워커 풀 + asyncio.Queue 기반 job 처리기."""

    def __init__(
        self,
        concurrency: int | None = None,
        session_factory: Callable = AsyncSessionLocal,
        runner: RcaRunner = _default_runner,
    ) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._concurrency = concurrency or settings.rca_worker_concurrency
        self._session_factory = session_factory
        self._runner = runner
        self._workers: list[asyncio.Task] = []
        self._started = False

    async def enqueue(self, job_id: int) -> None:
        """job_id만 큐에 넣는다. 번들은 워커가 DB(job.bundle)에서 복원한다.

        워커 미기동 상태여도 안전(큐에 적재만)."""
        await self._queue.put(job_id)

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
            job_id = await self._queue.get()
            try:
                await self._process(job_id)
            except Exception:  # 워커 자체는 절대 죽지 않는다
                logger.exception("워커 %d: job %s 처리 중 예외", idx, job_id)
            finally:
                self._queue.task_done()

    async def _run_rca(self, job_id: int, bundle: IngestBundle) -> RcaResult | None:
        """RCA 실행 + 5키 검증. 실패 시 오케스트레이터부터 1회 재시작.

        runner가 None을 반환하면(산출물 없음) 검증 없이 None을 통과시킨다.
        최초+재시도 2회 모두 실패하면 마지막 예외를 던져 상위에서 FAILED 처리한다.
        """
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                raw = await self._runner(job_id, bundle)
                if raw is None:
                    return None
                return validate_rca_result(raw)
            except Exception as exc:
                last_exc = exc
                logger.warning("job %s RCA 시도 %d/2 실패: %s", job_id, attempt, exc)
        assert last_exc is not None
        raise last_exc

    async def _process(self, job_id: int) -> None:
        """job 상태 머신: RUNNING → RCA(1회 재시도) → DELIVERING → DONE / FAILED.

        번들은 큐가 아니라 DB(job.bundle)에서 복원한다(단일 출처). 분석 결과는 먼저
        DB에 저장(DELIVERING)하고, Spring 전송이 성공해야 DONE으로 확정한다. 전송
        실패 시 DELIVERING에 머무르며, 재전송 루프(delivery_reconciler)가 다시 민다.
        """
        outcome: tuple[str, object] | None = None
        async with self._session_factory() as db:
            result = await db.execute(
                select(IngestJob).where(IngestJob.job_id == job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                logger.warning("job %s 없음 — 스킵", job_id)
                return

            signals_path = job.signals_path
            stored = job.bundle
            try:
                bundle = await bundle_store.restore_bundle(stored, signals_path)
            except bundle_store.SignalsMissing as exc:
                # 원본 파일이 사라지면 분석이 불가능하다. 조용히 두지 않고 FAILED로 확정한 뒤
                # 사유를 Spring에 알린다(3종 배열 없이 경량 번들로).
                logger.error("job %s 원본 파일 없음 — FAILED 확정: %s", job_id, exc)
                job.status = "FAILED"
                job.error = str(exc)
                await db.commit()
                await self._deliver_failure(
                    job_id, IngestBundle.model_validate(stored), str(exc)
                )
                await bundle_store.discard(signals_path)
                return

            job.status = "RUNNING"
            await db.commit()

            try:
                validated = await self._run_rca(job_id, bundle)
                if validated is not None:
                    # result를 먼저 저장하고 DELIVERING로 — 전송은 세션 밖에서.
                    job.result = validated.model_dump(by_alias=True, exclude_none=True)
                    job.status = "DELIVERING"
                    job.error = None
                    await db.commit()
                    outcome = ("deliver", validated)
                else:
                    # 산출물 없음 — 전송할 것이 없으므로 바로 DONE.
                    job.status = "DONE"
                    job.error = None
                    await db.commit()
            except Exception as exc:
                logger.exception("job %s RCA 최종 실패(재시도 후)", job_id)
                job.status = "FAILED"
                job.error = str(exc)  # 사유 전체 저장(truncation 없음)
                await db.commit()
                outcome = ("failure", str(exc))

        # DB 확정 후, 세션 밖에서 Spring 전송.
        # 원본 파일은 전송까지 끝나야 버릴 수 있다 — Spring 페이로드에 3종 배열이 실리고,
        # 전송 실패로 DELIVERING에 남으면 재전송 루프가 같은 파일을 다시 쓴다.
        if outcome is None:  # 산출물 없이 DONE — 더 이상 원본이 필요 없음
            await bundle_store.discard(signals_path)
            return
        kind, payload = outcome
        if kind == "deliver":
            if await self._deliver_and_finalize(job_id, bundle, payload):
                await bundle_store.discard(signals_path)
        else:
            await self._deliver_failure(job_id, bundle, payload)
            await bundle_store.discard(signals_path)

    async def _deliver_and_finalize(
        self, job_id: int, bundle: IngestBundle, result: object
    ) -> bool:
        """Spring 전송 성공 시에만 DELIVERING → DONE. 실패하면 DELIVERING 유지(재전송 대기).

        전송 성공 여부를 반환한다 — 호출부가 원본 파일을 버려도 되는지 판단하는 근거.
        """
        from app.services.spring_client import spring_client

        try:
            await spring_client.save_result(job_id, bundle, result)
        except Exception:
            logger.warning(
                "Spring 결과 전송 실패 — DELIVERING 유지, 재전송 대기: job %s",
                job_id,
                exc_info=True,
            )
            return False
        await self._mark_done(job_id)
        return True

    async def _mark_done(self, job_id: int) -> None:
        """전송 성공한 job을 DONE으로 확정. DELIVERING인 것만 전이(중복 방지)."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(IngestJob).where(IngestJob.job_id == job_id)
            )
            job = result.scalar_one_or_none()
            if job is None or job.status != "DELIVERING":
                return
            job.status = "DONE"
            await db.commit()
        logger.info("job %s 전송 완료 → DONE", job_id)

    async def _deliver_failure(
        self, job_id: int, bundle: IngestBundle, error: str
    ) -> None:
        """실패 폴백 — 실패 사유+번들을 Spring에 전송(best-effort)."""
        from app.services.spring_client import spring_client

        try:
            await spring_client.save_failure(job_id, bundle, error)
        except Exception:
            logger.warning("Spring 실패 폴백 전송 실패(무시): job %s", job_id, exc_info=True)


# 앱 전역 큐 인스턴스. lifespan에서 start()/stop() 호출.
job_queue = RcaJobQueue()
