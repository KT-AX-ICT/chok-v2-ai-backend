"""오래된 job 정리 백그라운드 루프.

FastAPI DB의 ingest_job은 job 진행상태만 관리한다(번들 원본·최종 리포트는 Spring에
저장). 따라서 종료된(DONE/FAILED) job은 일정 시간이 지나면 지워도 된다. 이 루프가
주기적으로 보존기간이 지난 종료 job을 삭제해 테이블이 무한정 커지는 것을 막는다.

진행 중(PENDING/RUNNING) job은 오래돼도 절대 삭제하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, select

from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal
from app.services import bundle_store

logger = logging.getLogger(__name__)

# 삭제 대상이 되는 종료 상태. 진행 중 상태는 여기 없으므로 보호된다.
TERMINAL_STATUSES = ("DONE", "FAILED")

# 아직 끝나지 않은 상태. 이 job들이 쓰는 원본 파일은 정리 대상에서 제외한다.
ACTIVE_STATUSES = ("PENDING", "RUNNING", "DELIVERING")


def _utc_naive_now() -> datetime:
    # DB의 func.now()가 naive UTC(SQLite) 문자열이므로 비교 기준도 naive UTC로 맞춘다.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobCleaner:
    """보존기간 지난 종료 job을 주기적으로 삭제하는 백그라운드 루프."""

    def __init__(
        self,
        retention_hours: float = 24.0,
        interval_seconds: float = 3600.0,
        session_factory: Callable = AsyncSessionLocal,
    ) -> None:
        self._retention = timedelta(hours=retention_hours)
        self._interval = interval_seconds
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None

    async def purge_once(self) -> int:
        """보존기간 지난 DONE/FAILED job 삭제. 삭제된 행 수 반환."""
        cutoff = _utc_naive_now() - self._retention
        async with self._session_factory() as db:
            result = await db.execute(
                delete(IngestJob)
                .where(IngestJob.status.in_(TERMINAL_STATUSES))
                .where(IngestJob.updated_at < cutoff)
            )
            await db.commit()
            return result.rowcount or 0

    async def files_in_use(self) -> set[str]:
        """아직 끝나지 않은 job이 쓰는 원본 파일 이름. 파일 정리에서 제외할 목록.

        파일 저장소(bundle_store)는 DB를 모르는 파일시스템 전용으로 두고, job 상태를 아는
        이 루프가 조회해 넘긴다.
        """
        async with self._session_factory() as db:
            rows = await db.execute(
                select(IngestJob.signals_path)
                .where(IngestJob.status.in_(ACTIVE_STATUSES))
                .where(IngestJob.signals_path.is_not(None))
            )
            return {name for name in rows.scalars().all() if name}

    def start(self) -> None:
        """정리 루프를 기동한다. 실행 중인 이벤트 루프 안에서 호출해야 한다."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="job-cleaner")
        logger.info(
            "job 정리 루프 기동 (보존 %s, 주기 %ss)", self._retention, self._interval
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
        logger.info("job 정리 루프 정지")

    async def _loop(self) -> None:
        while True:
            try:
                deleted = await self.purge_once()
                if deleted:
                    logger.info("job 정리: %d건 삭제", deleted)
                # 번들 원본 파일은 job 종료 시점에 지우는 것이 정상 경로다. 그때 놓친 파일
                # (파일만 쓰이고 job 기록이 실패한 경우 등)을 여기서 회수한다.
                # 아직 끝나지 않은 job이 쓰는 파일은 제외해, 전송이 오래 지연돼도 원본을
                # 잃지 않게 한다(경과 시간만으로 지우면 사용 중인 파일이 사라질 수 있음).
                swept = await bundle_store.sweep_orphans(keep=await self.files_in_use())
                if swept:
                    logger.info("번들 원본 고아 파일 정리: %d건 삭제", swept)
            except Exception:  # 루프는 절대 죽지 않는다
                logger.exception("job 정리 루프 오류")
            await asyncio.sleep(self._interval)


# 앱 전역 인스턴스. lifespan에서 start()/stop() 호출.
job_cleaner = JobCleaner()
