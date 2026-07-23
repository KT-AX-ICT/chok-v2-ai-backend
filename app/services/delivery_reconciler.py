"""미전달 리포트 재전송 루프.

워커(job_queue._process)는 분석 결과를 DB에 저장하고 status=DELIVERING로 둔 뒤 Spring
전송을 시도한다. 성공하면 DONE으로 확정하지만, 실패하면 DELIVERING에 머문다. 이 루프가
주기적으로 DELIVERING job을 다시 밀어 최종적으로 DONE으로 만든다.

재전송이 안전한 이유: Spring이 triggerTime UNIQUE 멱등키를 가져, 같은 리포트를 다시 받아도
중복 저장 없이 409로 응답한다(docs/spring-contract.md). 결과는 DB(job.result)에 이미 있으니
LLM 재실행 없이 재구성해 보낸다.

grace: 방금 DELIVERING이 된 job은 워커가 스스로 전송 중이므로, updated_at이 grace보다
오래된 것만 집어 워커의 즉시 전송과 경합을 피한다(경합해도 멱등이라 무해하지만 낭비 방지).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select

from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal
from app.schemas.contracts import IngestBundle, RcaResult
from app.services import bundle_store

logger = logging.getLogger(__name__)


def _utc_naive_now() -> datetime:
    # DB의 func.now()가 naive UTC이므로 비교 기준도 naive UTC로 맞춘다.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeliveryReconciler:
    """DELIVERING(전송 미완) job을 주기적으로 재전송해 DONE으로 확정하는 루프."""

    def __init__(
        self,
        interval_seconds: float = 60.0,
        grace_seconds: float = 30.0,
        session_factory: Callable = AsyncSessionLocal,
    ) -> None:
        self._interval = interval_seconds
        self._grace = timedelta(seconds=grace_seconds)
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None

    async def redeliver_once(self) -> int:
        """grace보다 오래 DELIVERING인 job을 재전송. DONE으로 확정한 건수 반환."""
        from app.services.spring_client import spring_client

        cutoff = _utc_naive_now() - self._grace
        async with self._session_factory() as db:
            rows = await db.execute(
                select(
                    IngestJob.job_id,
                    IngestJob.bundle,
                    IngestJob.result,
                    IngestJob.signals_path,
                )
                .where(IngestJob.status == "DELIVERING")
                .where(IngestJob.updated_at < cutoff)
            )
            pending = rows.all()

        delivered = 0
        for job_id, raw_bundle, raw_result, signals_path in pending:
            if raw_result is None:  # 이론상 없음(DELIVERING은 result 저장 후 진입)
                logger.warning("job %s DELIVERING인데 result 없음 — 스킵", job_id)
                continue
            try:
                bundle = await self._restore(job_id, raw_bundle, signals_path)
                result = RcaResult.model_validate(raw_result)
                await spring_client.save_result(job_id, bundle, result)
            except Exception:
                logger.warning("job %s 재전송 실패 — 다음 주기 재시도", job_id, exc_info=True)
                continue
            if await self._mark_done(job_id):
                delivered += 1
                await bundle_store.discard(signals_path)  # 전송 확정 후 원본 회수
        return delivered

    async def _restore(
        self, job_id: int, stored: dict, signals_path: str | None
    ) -> IngestBundle:
        """번들 복원. 원본 파일이 사라졌으면 3종 배열 없이라도 보낸다.

        리포트의 핵심은 분석 결과(result)이므로, 원본 행을 못 싣는다고 전송을 포기해
        리포트를 통째로 잃는 것보다 결과만이라도 전달하는 편이 낫다.
        """
        try:
            return await bundle_store.restore_bundle(stored, signals_path)
        except bundle_store.SignalsMissing:
            logger.warning("job %s 원본 파일 없음 — 3종 배열 없이 결과만 재전송", job_id)
            return IngestBundle.model_validate(stored)

    async def _mark_done(self, job_id: int) -> bool:
        """전송 성공한 job을 DONE으로. DELIVERING인 것만 전이(워커와 중복 방지)."""
        async with self._session_factory() as db:
            row = await db.execute(
                select(IngestJob).where(IngestJob.job_id == job_id)
            )
            job = row.scalar_one_or_none()
            if job is None or job.status != "DELIVERING":
                return False
            job.status = "DONE"
            await db.commit()
        logger.info("job %s 재전송 성공 → DONE", job_id)
        return True

    def start(self) -> None:
        """재전송 루프를 기동한다. 실행 중인 이벤트 루프 안에서 호출해야 한다."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="delivery-reconciler")
        logger.info("리포트 재전송 루프 기동 (주기 %ss)", self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("리포트 재전송 루프 정지")

    async def _loop(self) -> None:
        while True:
            try:
                n = await self.redeliver_once()
                if n:
                    logger.info("리포트 재전송: %d건 DONE 확정", n)
            except Exception:  # 루프는 절대 죽지 않는다
                logger.exception("리포트 재전송 루프 오류")
            await asyncio.sleep(self._interval)


# 앱 전역 인스턴스. lifespan에서 start()/stop() 호출.
delivery_reconciler = DeliveryReconciler()
