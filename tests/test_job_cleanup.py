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


async def test_files_in_use_lists_only_unfinished_jobs(factory):
    """파일 정리에서 제외할 목록 — 아직 안 끝난 job의 원본만 담긴다.

    끝난 job의 파일은 종료 시점에 지워졌어야 하므로, 남아 있다면 정리 대상이 맞다.
    """
    now = _utc_naive_now()
    async with factory() as db:
        for status, name in (
            ("PENDING", "a.json"),
            ("RUNNING", "b.json"),
            ("DELIVERING", "c.json"),
            ("DONE", "d.json"),
            ("FAILED", "e.json"),
            ("RUNNING", None),  # 파일 없는 job은 목록에 안 들어감
        ):
            db.add(
                IngestJob(
                    status=status, bundle={}, updated_at=now, signals_path=name
                )
            )
        await db.commit()

    cleaner = JobCleaner(session_factory=factory)

    assert await cleaner.files_in_use() == {"a.json", "b.json", "c.json"}


async def test_purge_returns_zero_when_nothing_expired(factory):
    now = _utc_naive_now()
    await _seed(factory, "DONE", now - timedelta(hours=1))
    cleaner = JobCleaner(retention_hours=24, session_factory=factory)
    assert await cleaner.purge_once() == 0


async def test_running_loop_purges_then_stops_cleanly(factory):
    now = _utc_naive_now()
    old_done = await _seed(factory, "DONE", now - timedelta(hours=48))

    # interval을 길게 잡아 stop() 시점에 루프가 sleep 중이도록 보장한다.
    # (짧은 interval이면 cancel이 DB 쿼리 도중에 떨어져 aiosqlite 커넥션이 폐기되고,
    #  StaticPool이 새 커넥션 = 새 :memory: DB를 만들어 "no such table"로 깨진다)
    cleaner = JobCleaner(
        retention_hours=24, interval_seconds=60, session_factory=factory
    )
    cleaner.start()
    # 기동 직후 첫 purge가 끝났는지 폴링으로 확인
    for _ in range(200):
        if old_done not in await _remaining_ids(factory):
            break
        await asyncio.sleep(0.01)
    await cleaner.stop()  # CancelledError 없이 깔끔히 종료되면 성공

    assert old_done not in await _remaining_ids(factory)
