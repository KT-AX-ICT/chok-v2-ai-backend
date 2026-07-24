"""중단된 job 회수 루프.

워커가 job을 RUNNING으로 표시한 뒤 프로세스가 죽거나 DB 커밋이 실패하면, 그 job은
아무도 손대지 않은 채 RUNNING으로 영구 잔류한다. job_cleanup은 종료 상태(DONE/FAILED)만
지우고 진행 중 상태는 보호하며, delivery_reconciler는 DELIVERING만 본다. 즉 RUNNING
잔류분에는 회수 주체가 없어 리포트가 조용히 유실된다. 이 루프가 그 구멍을 막는다.

회수 정책:
  - RUNNING이 임계 시간을 넘기면 중단으로 간주.
  - 재투입 허용 횟수(max_job_requeue)가 남으면 PENDING으로 되돌리고 큐에 다시 넣는다.
  - 허용 횟수를 소진했으면 FAILED로 확정하고 실패 사유를 Spring에 폴백 전송한다
    (조용한 유실 방지). 무한 재투입·크래시 루프도 이 상한으로 막힌다.

임계 시간(stuck_job_after_seconds)은 짧을수록 위험하다 — RUNNING 전이 이후에는 job 행을
갱신하지 않아 updated_at만으로는 "처리 중"과 "멈춤"을 구분할 수 없어서, 짧게 잡으면 정상
처리 중인 job까지 회수해버린다. 값을 어떻게 정했는지는 config의 해당 설정 주석 참조.

기동 시 복구(recover_on_startup)는 임계를 기다리지 않는다 — 재시작 시점에는 이전 프로세스가
이미 사라져 RUNNING을 처리하던 주체가 없는 게 확실하기 때문이다. 큐가 메모리(asyncio.Queue)라
적재분도 함께 사라지므로 PENDING은 그대로 큐에 다시 넣는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal
from app.schemas.contracts import IngestBundle
from app.services import bundle_store

logger = logging.getLogger(__name__)

# FAILED 확정 시 job.error와 Spring reason에 남길 사유.
STUCK_REASON = "워커 중단으로 회수 — RUNNING 잔류, 재투입 허용 횟수 소진"


def _utc_naive_now() -> datetime:
    # DB의 func.now()가 naive UTC이므로 비교 기준도 naive UTC로 맞춘다.
    return datetime.now(UTC).replace(tzinfo=None)


class StuckJobReaper:
    """RUNNING에 잔류한 job을 주기적으로 회수하는 백그라운드 루프."""

    def __init__(
        self,
        interval_seconds: float | None = None,
        stuck_after_seconds: float | None = None,
        max_requeue: int | None = None,
        session_factory: Callable = AsyncSessionLocal,
        queue: object | None = None,
    ) -> None:
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else settings.stuck_job_interval_seconds
        )
        self._stuck_after = timedelta(
            seconds=(
                stuck_after_seconds
                if stuck_after_seconds is not None
                else settings.stuck_job_after_seconds
            )
        )
        self._max_requeue = (
            max_requeue if max_requeue is not None else settings.max_job_requeue
        )
        self._session_factory = session_factory
        self._queue = queue
        self._task: asyncio.Task | None = None

    def _get_queue(self):
        """전역 큐를 지연 해석한다(테스트에서 주입 가능, 임포트 순환 방지)."""
        if self._queue is not None:
            return self._queue
        from app.services.job_queue import job_queue

        return job_queue

    # ---------------------------------------------------------------- 회수

    async def reap_once(self) -> tuple[int, int]:
        """임계를 넘긴 RUNNING job을 회수. (재투입 건수, 실패 확정 건수) 반환."""
        return await self._recover_running(cutoff=_utc_naive_now() - self._stuck_after)

    async def recover_on_startup(self) -> tuple[int, int, int]:
        """기동 복구. (PENDING 재큐잉, RUNNING 재투입, RUNNING 실패 확정) 반환.

        재시작으로 큐가 비었으므로 PENDING을 다시 싣고, RUNNING은 처리 주체가 사라진 게
        확실하므로 임계를 기다리지 않고 즉시 회수한다.
        """
        pending = await self._requeue_pending()
        requeued, failed = await self._recover_running(cutoff=None)
        if pending or requeued or failed:
            logger.info(
                "기동 복구: PENDING 재큐잉 %d건, RUNNING 재투입 %d건, 실패 확정 %d건",
                pending,
                requeued,
                failed,
            )
        return pending, requeued, failed

    async def _requeue_pending(self) -> int:
        """DB에 남은 PENDING job을 큐에 다시 싣는다(큐는 메모리라 재시작 시 소실)."""
        async with self._session_factory() as db:
            job_ids = list(
                (
                    await db.execute(
                        select(IngestJob.job_id).where(IngestJob.status == "PENDING")
                    )
                )
                .scalars()
                .all()
            )
        queue = self._get_queue()
        for job_id in job_ids:
            await queue.enqueue(job_id)
        return len(job_ids)

    async def _recover_running(self, cutoff: datetime | None) -> tuple[int, int]:
        """RUNNING job 회수. cutoff=None이면 임계 없이 전부(기동 복구용).

        DB를 먼저 확정한 뒤, 세션 밖에서 큐 적재·Spring 전송을 한다(세션 점유 최소화).
        """
        to_enqueue: list[int] = []
        # (job_id, 경량 번들, 원본 파일 이름) — 번들 복원은 세션 밖에서(파일 I/O)
        to_fail: list[tuple[int, dict, str | None]] = []

        async with self._session_factory() as db:
            stmt = select(IngestJob).where(IngestJob.status == "RUNNING")
            if cutoff is not None:
                stmt = stmt.where(IngestJob.updated_at < cutoff)
            jobs = list((await db.execute(stmt)).scalars().all())

            for job in jobs:
                if job.requeue_count < self._max_requeue:
                    job.requeue_count += 1
                    job.status = "PENDING"
                    to_enqueue.append(job.job_id)
                    logger.warning(
                        "job %s 중단 감지 — 큐 재투입 (%d/%d)",
                        job.job_id,
                        job.requeue_count,
                        self._max_requeue,
                    )
                else:
                    job.status = "FAILED"
                    job.error = STUCK_REASON
                    to_fail.append((job.job_id, job.bundle, job.signals_path))
                    logger.error(
                        "job %s 중단 회수 — 재투입 %d회 소진, FAILED 확정",
                        job.job_id,
                        job.requeue_count,
                    )
            if jobs:
                await db.commit()

        queue = self._get_queue()
        for job_id in to_enqueue:
            # 재투입한 job은 다시 처리되므로 원본 파일을 남겨둔다.
            await queue.enqueue(job_id)
        for job_id, stored, signals_path in to_fail:
            await self._notify_failure(job_id, stored, signals_path)
            await bundle_store.discard(signals_path)  # 실패 확정 — 원본 회수
        return len(to_enqueue), len(to_fail)

    async def _notify_failure(
        self, job_id: int, stored: dict, signals_path: str | None
    ) -> None:
        """실패 확정을 Spring에 알린다(best-effort). 못 보내도 job은 FAILED로 남는다."""
        from app.services.spring_client import spring_client

        try:
            bundle = await self._restore(stored, signals_path)
            await spring_client.save_failure(job_id, bundle, STUCK_REASON)
        except Exception:
            logger.warning(
                "중단 job %s 실패 폴백 전송 실패(무시)", job_id, exc_info=True
            )

    @staticmethod
    async def _restore(stored: dict, signals_path: str | None) -> IngestBundle:
        """번들 복원. 원본 파일이 없어도 사유는 전달해야 하므로 경량 번들로 폴백."""
        try:
            return await bundle_store.restore_bundle(stored, signals_path)
        except bundle_store.SignalsMissing:
            return IngestBundle.model_validate(stored)

    # ---------------------------------------------------------------- 루프

    def start(self) -> None:
        """회수 루프를 기동한다. 실행 중인 이벤트 루프 안에서 호출해야 한다."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="stuck-job-reaper")
        logger.info(
            "중단 job 회수 루프 기동 (임계 %s, 주기 %ss, 재투입 상한 %d)",
            self._stuck_after,
            self._interval,
            self._max_requeue,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("중단 job 회수 루프 정지")

    async def _loop(self) -> None:
        while True:
            try:
                requeued, failed = await self.reap_once()
                if requeued or failed:
                    logger.info(
                        "중단 job 회수: 재투입 %d건, 실패 확정 %d건", requeued, failed
                    )
            except Exception:  # 루프는 절대 죽지 않는다
                logger.exception("중단 job 회수 루프 오류")
            await asyncio.sleep(self._interval)


# 앱 전역 인스턴스. lifespan에서 start()/stop() 호출.
stuck_job_reaper = StuckJobReaper()
