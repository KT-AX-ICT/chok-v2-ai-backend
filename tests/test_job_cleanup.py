"""JobCleaner 정리 루프 테스트.

updated_at을 명시 주입해 보존기간 경계와 상태별 보호를 결정적으로 검증한다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import IngestJob
from app.db.session import Base
from app.services.job_cleanup import JobCleaner


def _utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
async def factory():
    # 정리 루프가 동시 접근하므로 단일 커넥션 공유(StaticPool)로 같은 :memory: DB 보장
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, status: str, updated_at: datetime) -> int:
    async with factory() as db:
        job = IngestJob(status=status, bundle={}, updated_at=updated_at)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.job_id


async def _remaining_ids(factory) -> set[int]:
    async with factory() as db:
        rows = await db.execute(select(IngestJob.job_id))
        return set(rows.scalars().all())


async def test_purges_only_old_terminal_jobs(factory):
    now = _utc_naive_now()
    old = now - timedelta(hours=48)
    recent = now - timedelta(hours=1)

    old_done = await _seed(factory, "DONE", old)
    old_failed = await _seed(factory, "FAILED", old)
    recent_done = await _seed(factory, "DONE", recent)  # 최근 → 보존
    old_pending = await _seed(factory, "PENDING", old)  # 진행중 → 절대 삭제 X
    old_running = await _seed(factory, "RUNNING", old)  # 진행중 → 절대 삭제 X

    cleaner = JobCleaner(retention_hours=24, session_factory=factory)
    deleted = await cleaner.purge_once()

    assert deleted == 2  # old_done, old_failed 만
    assert await _remaining_ids(factory) == {recent_done, old_pending, old_running}
    # 참조 회피용(린트) — 삭제 대상 id 들
    assert old_done not in await _remaining_ids(factory)
    assert old_failed not in await _remaining_ids(factory)


async def test_purge_returns_zero_when_nothing_expired(factory):
    now = _utc_naive_now()
    await _seed(factory, "DONE", now - timedelta(hours=1))
    cleaner = JobCleaner(retention_hours=24, session_factory=factory)
    assert await cleaner.purge_once() == 0


async def test_running_loop_purges_then_stops_cleanly(factory):
    now = _utc_naive_now()
    old_done = await _seed(factory, "DONE", now - timedelta(hours=48))

    cleaner = JobCleaner(
        retention_hours=24, interval_seconds=0.01, session_factory=factory
    )
    cleaner.start()
    await asyncio.sleep(0.05)  # 루프가 최소 1회 purge 하도록
    await cleaner.stop()  # CancelledError 없이 깔끔히 종료되면 성공

    assert old_done not in await _remaining_ids(factory)
