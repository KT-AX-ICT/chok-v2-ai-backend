"""StuckJobReaper 테스트 — RUNNING 잔류 job 회수(재투입 → FAILED)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import IngestJob
from app.db.session import Base
from app.services.stuck_job_reaper import STUCK_REASON, StuckJobReaper

_BUNDLE = {
    "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
    "triggerInfo": {"triggerTime": "2026-01-15T10:01:30Z", "triggeredBy": ["log"]},
    "logs": [],
    "metrics": [],
    "traces": [],
}


class _FakeQueue:
    """enqueue만 기록하는 큐 대역 — 워커를 띄우지 않고 재투입 여부만 본다."""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    async def enqueue(self, job_id: int) -> None:
        self.enqueued.append(job_id)


@pytest.fixture()
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory, status: str, requeue_count: int = 0) -> int:
    async with factory() as db:
        job = IngestJob(status=status, bundle=_BUNDLE, requeue_count=requeue_count)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.job_id


async def _get(factory, job_id: int) -> IngestJob:
    async with factory() as db:
        return (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()


# stuck_after_seconds 음수 → cutoff가 미래라 방금 만든 RUNNING도 즉시 대상(타이밍 무관).


async def test_stuck_running_is_requeued_once(factory):
    """재투입 여력이 남은 RUNNING은 PENDING으로 되돌리고 큐에 다시 넣는다."""
    queue = _FakeQueue()
    job_id = await _seed(factory, "RUNNING", requeue_count=0)
    reaper = StuckJobReaper(
        stuck_after_seconds=-100, max_requeue=1, session_factory=factory, queue=queue
    )

    requeued, failed = await reaper.reap_once()

    assert (requeued, failed) == (1, 0)
    assert queue.enqueued == [job_id]
    job = await _get(factory, job_id)
    assert job.status == "PENDING"
    assert job.requeue_count == 1


async def test_requeue_exhausted_marks_failed_and_notifies_spring(factory, monkeypatch):
    """재투입 상한을 소진한 RUNNING은 FAILED로 확정하고 Spring에 사유를 보낸다."""
    import app.services.spring_client as spring_mod

    sent: list[tuple[int, str]] = []

    async def _capture(job_id, bundle, error):
        sent.append((job_id, error))

    monkeypatch.setattr(spring_mod.spring_client, "save_failure", _capture)

    queue = _FakeQueue()
    job_id = await _seed(factory, "RUNNING", requeue_count=1)
    reaper = StuckJobReaper(
        stuck_after_seconds=-100, max_requeue=1, session_factory=factory, queue=queue
    )

    requeued, failed = await reaper.reap_once()

    assert (requeued, failed) == (0, 1)
    assert queue.enqueued == []  # 재투입 없음
    assert sent == [(job_id, STUCK_REASON)]  # 조용한 유실 방지
    job = await _get(factory, job_id)
    assert job.status == "FAILED"
    assert job.error == STUCK_REASON


async def test_spring_failure_does_not_block_failed_transition(factory, monkeypatch):
    """Spring 전송이 실패해도 job은 FAILED로 남는다(전송은 best-effort)."""
    import app.services.spring_client as spring_mod

    async def _boom(*args, **kwargs):
        raise RuntimeError("spring down")

    monkeypatch.setattr(spring_mod.spring_client, "save_failure", _boom)

    job_id = await _seed(factory, "RUNNING", requeue_count=1)
    reaper = StuckJobReaper(
        stuck_after_seconds=-100,
        max_requeue=1,
        session_factory=factory,
        queue=_FakeQueue(),
    )

    _, failed = await reaper.reap_once()

    assert failed == 1
    assert (await _get(factory, job_id)).status == "FAILED"


async def test_fresh_running_is_not_reaped(factory):
    """임계 안쪽의 RUNNING은 정상 처리 중일 수 있으므로 건드리지 않는다."""
    queue = _FakeQueue()
    job_id = await _seed(factory, "RUNNING")
    reaper = StuckJobReaper(
        stuck_after_seconds=3600, max_requeue=1, session_factory=factory, queue=queue
    )

    assert await reaper.reap_once() == (0, 0)
    assert queue.enqueued == []
    assert (await _get(factory, job_id)).status == "RUNNING"


async def test_other_statuses_are_untouched(factory):
    """DELIVERING/DONE/FAILED는 이 루프의 대상이 아니다(각자 담당 루프가 있음)."""
    queue = _FakeQueue()
    for status in ("DELIVERING", "DONE", "FAILED"):
        await _seed(factory, status)
    reaper = StuckJobReaper(
        stuck_after_seconds=-100, max_requeue=1, session_factory=factory, queue=queue
    )

    assert await reaper.reap_once() == (0, 0)
    assert queue.enqueued == []


async def test_recover_on_startup_requeues_pending_and_running(factory):
    """재기동 복구 — 메모리 큐가 비었으므로 PENDING을 다시 싣고, RUNNING은 임계 없이 회수."""
    queue = _FakeQueue()
    pending_id = await _seed(factory, "PENDING")
    running_id = await _seed(factory, "RUNNING", requeue_count=0)
    # 임계를 크게 잡아도 기동 복구는 기다리지 않는다는 점까지 확인.
    reaper = StuckJobReaper(
        stuck_after_seconds=3600, max_requeue=1, session_factory=factory, queue=queue
    )

    pending, requeued, failed = await reaper.recover_on_startup()

    assert (pending, requeued, failed) == (1, 1, 0)
    assert sorted(queue.enqueued) == sorted([pending_id, running_id])
    assert (await _get(factory, pending_id)).status == "PENDING"  # 상태는 그대로
    assert (await _get(factory, running_id)).status == "PENDING"  # RUNNING → 되돌림
