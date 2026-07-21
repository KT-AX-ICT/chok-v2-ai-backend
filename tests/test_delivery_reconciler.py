"""DeliveryReconciler 테스트 — DELIVERING job 재전송."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import IngestJob
from app.db.session import Base
from app.services.delivery_reconciler import DeliveryReconciler

_BUNDLE = {
    "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
    "triggerInfo": {"triggerTime": "2026-01-15T10:01:30Z", "triggeredBy": ["log"]},
    "logs": [],
    "metrics": [],
    "traces": [],
}
_RESULT = {
    "type": "Code_Stop",
    "severity": "HIGH",
    "service": "media",
    "detail": {
        "rca": {"rootCause": "rc", "propagation": "p"},
        "summary": {"highlight": "h"},
        "evidence": {
            "log": {"conclusion": "l"},
            "trace": {"conclusion": "t"},
            "metric": {"conclusion": "m"},
        },
        "impact": {"affected": [{"service": "x"}]},
        "actions": {"steps": ["s"]},
    },
}


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


async def _seed_delivering(factory, result=_RESULT) -> int:
    async with factory() as db:
        job = IngestJob(status="DELIVERING", bundle=_BUNDLE, result=result)
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.job_id


# grace_seconds 음수 → cutoff가 미래라 방금 넣은 DELIVERING도 즉시 대상(타이밍 무관).


async def test_reconciler_redelivers_and_marks_done(factory, monkeypatch):
    import app.services.spring_client as spring_mod

    sent = []

    async def _ok(job_id, bundle, result):
        sent.append(job_id)

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _ok)

    job_id = await _seed_delivering(factory)
    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)
    n = await rec.redeliver_once()

    assert n == 1
    assert sent == [job_id]
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "DONE"


async def test_reconciler_keeps_delivering_on_failure(factory, monkeypatch):
    import app.services.spring_client as spring_mod

    async def _boom(*args, **kwargs):
        raise RuntimeError("spring down")

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _boom)

    job_id = await _seed_delivering(factory)
    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)
    n = await rec.redeliver_once()

    assert n == 0
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "DELIVERING"  # 재전송 실패 → 유지


async def test_reconciler_respects_grace_period(factory, monkeypatch):
    """grace가 크면(방금 만든 job) 워커 전송과 경합 방지 위해 아직 집지 않는다."""
    import app.services.spring_client as spring_mod

    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _ok)

    job_id = await _seed_delivering(factory)
    rec = DeliveryReconciler(grace_seconds=3600, session_factory=factory)
    n = await rec.redeliver_once()

    assert n == 0  # grace(1시간) 안이라 대상 아님
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "DELIVERING"
