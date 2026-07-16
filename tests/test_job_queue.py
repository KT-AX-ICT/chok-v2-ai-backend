"""RcaJobQueue 워커 테스트.

runner를 주입해 Spring/에이전트 없이 상태 머신·동시성 상한만 검증한다.
"""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import IngestJob
from app.db.session import Base
from app.schemas.contracts import (
    Actions,
    Affected,
    Evidence,
    Impact,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    Rca,
    RcaResult,
    ReportDetail,
    Summary,
    TraceEvidence,
)
from app.services.job_queue import RcaJobQueue


def _valid_result() -> RcaResult:
    return RcaResult(
        type="Code_Stop",
        severity="HIGH",
        service="media-service",
        detail=ReportDetail(
            rca=Rca(rootCause="rc", propagation="p"),
            summary=Summary(highlight="h"),
            evidence=Evidence(
                log=LogEvidence(conclusion="lc"),
                trace=TraceEvidence(conclusion="tc"),
                metric=MetricEvidence(conclusion="mc"),
            ),
            impact=Impact(affected=[Affected(service="svc")]),
            actions=Actions(steps=["s1"]),
        ),
    )


@pytest.fixture()
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _make_bundle() -> IngestBundle:
    return IngestBundle(
        window={"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        trigger_info={"trigger_time": "2026-01-15T10:01:30Z"},
    )


async def _seed_job(factory) -> int:
    async with factory() as db:
        job = IngestJob(status="PENDING", bundle=_make_bundle().model_dump())
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.job_id


async def _status(factory, job_id: int) -> str:
    async with factory() as db:
        result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        return result.scalar_one().status


async def test_worker_processes_job_to_done(factory):
    ran: list[int] = []

    async def runner(job_id: int, bundle: IngestBundle) -> None:
        ran.append(job_id)

    q = RcaJobQueue(concurrency=2, session_factory=factory, runner=runner)
    q.start()
    job_id = await _seed_job(factory)
    await q.enqueue(job_id, _make_bundle())
    await q.stop()

    assert ran == [job_id]
    assert await _status(factory, job_id) == "DONE"


async def test_worker_marks_failed_on_runner_error(factory):
    async def runner(job_id: int, bundle: IngestBundle) -> None:
        raise RuntimeError("boom")

    q = RcaJobQueue(concurrency=1, session_factory=factory, runner=runner)
    q.start()
    job_id = await _seed_job(factory)
    await q.enqueue(job_id, _make_bundle())
    await q.stop()

    assert await _status(factory, job_id) == "FAILED"


async def test_valid_result_is_persisted_on_done(factory):
    async def runner(job_id: int, bundle: IngestBundle) -> RcaResult:
        return _valid_result()

    q = RcaJobQueue(concurrency=1, session_factory=factory, runner=runner)
    q.start()
    job_id = await _seed_job(factory)
    await q.enqueue(job_id, _make_bundle())
    await q.stop()

    async with factory() as db:
        result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        job = result.scalar_one()
    assert job.status == "DONE"
    assert job.error is None
    assert job.result["service"] == "media-service"
    assert set(job.result["detail"]) == {"rca", "summary", "evidence", "impact", "actions"}


async def test_invalid_result_marks_failed_with_reason(factory):
    async def runner(job_id: int, bundle: IngestBundle) -> dict:
        bad = _valid_result().model_dump(by_alias=True, exclude_none=True)
        del bad["detail"]["actions"]  # 5키 중 하나 누락 → 계약 위반
        return bad

    q = RcaJobQueue(concurrency=1, session_factory=factory, runner=runner)
    q.start()
    job_id = await _seed_job(factory)
    await q.enqueue(job_id, _make_bundle())
    await q.stop()

    async with factory() as db:
        result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        job = result.scalar_one()
    assert job.status == "FAILED"
    assert job.result is None
    assert "actions" in job.error  # 사유에 누락 필드 기록


async def test_missing_job_is_skipped_without_crash(factory):
    async def runner(job_id: int, bundle: IngestBundle) -> None:  # 호출되면 안 됨
        raise AssertionError("존재하지 않는 job에 runner 호출됨")

    q = RcaJobQueue(concurrency=1, session_factory=factory, runner=runner)
    q.start()
    await q.enqueue(99999, _make_bundle())
    await q.stop()  # 예외 없이 정상 종료되면 성공


async def test_concurrency_cap_limits_parallelism(factory):
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def runner(job_id: int, bundle: IngestBundle) -> None:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1

    q = RcaJobQueue(concurrency=2, session_factory=factory, runner=runner)
    q.start()
    for _ in range(6):
        job_id = await _seed_job(factory)
        await q.enqueue(job_id, _make_bundle())
    await q.stop()

    assert peak <= 2
