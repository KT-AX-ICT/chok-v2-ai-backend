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


async def test_signals_file_discarded_after_redelivery(factory, monkeypatch):
    """재전송으로 DONE이 확정되면 원본 파일을 회수한다."""
    import app.services.spring_client as spring_mod
    from app.services import bundle_store

    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _ok)

    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    async with factory() as db:
        job = IngestJob(
            status="DELIVERING", bundle=light, result=_RESULT, signals_path=name
        )
        db.add(job)
        await db.commit()

    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)

    assert await rec.redeliver_once() == 1
    assert not (bundle_store.storage_dir() / name).exists()


async def test_redelivers_result_even_when_signals_file_missing(factory, monkeypatch):
    """원본 파일이 사라져도 결과는 전달 — 원본 행을 못 싣는다고 리포트를 통째로 잃지 않는다."""
    import app.services.spring_client as spring_mod

    sent: list[int] = []

    async def _capture(job_id, bundle, result):
        sent.append(job_id)

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _capture)

    light = {k: v for k, v in _BUNDLE.items() if k not in ("logs", "metrics", "traces")}
    async with factory() as db:
        job = IngestJob(
            status="DELIVERING",
            bundle=light,
            result=_RESULT,
            signals_path="없는파일.json",
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.job_id

    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)

    assert await rec.redeliver_once() == 1
    assert sent == [job_id]
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "DONE"


async def test_reconciler_marks_failed_on_permanent_error(factory, monkeypatch):
    """영구 실패(4xx)는 재시도해도 소용없으므로 FAILED 확정하고 원본 파일을 회수한다."""
    import app.services.spring_client as spring_mod
    from app.services import bundle_store

    async def _boom(*args, **kwargs):
        raise spring_mod.DeliveryPermanentError(422, "invalid payload")

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _boom)

    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    async with factory() as db:
        job = IngestJob(
            status="DELIVERING", bundle=light, result=_RESULT, signals_path=name
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.job_id

    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)
    n = await rec.redeliver_once()

    assert n == 0  # delivered 카운트는 DONE 기준 — FAILED는 포함 안 됨
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "FAILED"
    assert "422" in job.error
    assert not (bundle_store.storage_dir() / name).exists()  # 재시도 안 하므로 회수


async def test_reconciler_treats_409_as_success(factory, monkeypatch):
    """409(멱등키 중복)는 spring_client가 이미 성공으로 흡수 — 재전송 대상에서 제외(DONE)."""
    import app.services.spring_client as spring_mod

    async def _ok(*args, **kwargs):  # spring_client._post가 409를 성공 취급하므로 예외 없음
        return None

    monkeypatch.setattr(spring_mod.spring_client, "save_result", _ok)

    job_id = await _seed_delivering(factory)
    rec = DeliveryReconciler(grace_seconds=-100, session_factory=factory)
    n = await rec.redeliver_once()

    assert n == 1
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()
    assert job.status == "DONE"


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
