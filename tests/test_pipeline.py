"""RCA 파이프라인(오케스트레이터 + 얕은 에이전트) 및 풀 사이클 테스트."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents.orchestrator import Orchestrator, orchestrator
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
from app.services.rca_validation import validate_rca_result


def _bundle(with_data: bool = True) -> IngestBundle:
    base = {
        "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        "trigger_info": {
            "trigger_time": "2026-01-15T10:01:30Z",
            "triggered_by": ["log", "metric"],
        },
    }
    if with_data:
        base["logs"] = [
            {"timestamp": "2026-01-15T10:01:00Z", "service": "api-gateway", "raw": "ERROR connect timeout"}
        ]
        base["metrics"] = [
            {"timestamp": "2026-01-15T10:01:00Z", "service": "api-gateway", "raw": "error_rate=0.85"}
        ]
        base["traces"] = [
            {"timestamp": "2026-01-15T10:01:10Z", "service": "media-service", "raw": "span 16000ms TIMEOUT"}
        ]
    return IngestBundle(**base)


@pytest.fixture()
async def factory():
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


async def _seed_job(factory, bundle: IngestBundle) -> int:
    async with factory() as db:
        job = IngestJob(status="PENDING", bundle=bundle.model_dump())
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.job_id


async def test_orchestrator_returns_valid_rca_result():
    result = await orchestrator.run(1, _bundle())
    # 5키 계약 통과 + 대표 서비스는 trace origin 우선
    validate_rca_result(result.model_dump(by_alias=True, exclude_none=True))
    assert result.service == "media-service"
    assert set(result.detail.model_dump()) == {
        "rca",
        "summary",
        "evidence",
        "impact",
        "actions",
    }


async def test_empty_modalities_still_valid():
    result = await orchestrator.run(1, _bundle(with_data=False))
    validate_rca_result(result.model_dump(by_alias=True, exclude_none=True))
    assert result.service == "unknown"


async def test_agents_are_swappable():
    """오케스트레이터 본문 변경 없이 report 에이전트만 갈아끼워진다."""

    async def fake_report(bundle, log_ev, metric_ev, trace_ev) -> RcaResult:
        return RcaResult(
            type="Swapped",
            severity="LOW",
            service="SWAPPED",
            detail=ReportDetail(
                rca=Rca(rootCause="rc", propagation="p"),
                summary=Summary(highlight="h"),
                evidence=Evidence(
                    log=log_ev, trace=trace_ev, metric=metric_ev
                ),
                impact=Impact(affected=[Affected(service="SWAPPED")]),
                actions=Actions(steps=["s"]),
            ),
        )

    swapped = Orchestrator(report_agent=fake_report)
    result = await swapped.run(1, _bundle())
    assert result.service == "SWAPPED"
    assert result.type == "Swapped"


async def test_full_pipeline_through_queue_reaches_done(factory):
    """POST 이후 흐름: 큐 → 오케스트레이터 → 검증 → DONE + result 저장."""
    q = RcaJobQueue(concurrency=1, session_factory=factory)  # 기본 runner = 오케스트레이터
    bundle = _bundle()
    job_id = await _seed_job(factory, bundle)
    q.start()
    await q.enqueue(job_id)
    await q.stop()

    async with factory() as db:
        result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        job = result.scalar_one()
    assert job.status == "DONE"
    assert job.error is None
    assert set(job.result["detail"]) == {
        "rca",
        "summary",
        "evidence",
        "impact",
        "actions",
    }
