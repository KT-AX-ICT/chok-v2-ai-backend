"""RCA 파이프라인(LLM 오케스트레이터 + 큐) 통합 테스트 — LLM 실호출 없음(fake 주입)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.agents.orchestrator as orchestrator_mod
from app.agents.graph import LlmOrchestrator
from app.agents.schemas import MODALITIES, PlanDecision, ReportDraft
from app.db.models import IngestJob
from app.db.session import Base
from app.schemas.contracts import (
    Actions,
    Affected,
    Impact,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    Rca,
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


def _fake_orchestrator(
    trace_origin: str | None = "media-service",
    draft_service: str = "unknown",
    draft_type: str = "Unknown",
) -> LlmOrchestrator:
    """LLM 노드 전부를 fake로 채운 오케스트레이터 — 그래프 배선은 실제 그대로."""

    async def planner(bundle):
        return PlanDecision(log="deep", metric="deep", trace="deep", reason="테스트")

    evidences = {
        "log": LogEvidence(conclusion="로그 결론"),
        "metric": MetricEvidence(conclusion="메트릭 결론"),
        "trace": TraceEvidence(conclusion="트레이스 결론", origin_service=trace_origin),
    }

    def make(modality):
        async def agent(bundle):
            return evidences[modality]

        return agent

    agents = {(m, d): make(m) for m in MODALITIES for d in ("deep", "scan")}

    async def report(bundle, log_ev, metric_ev, trace_ev):
        return ReportDraft(
            type=draft_type,
            severity="MID",
            service=draft_service,
            rca=Rca(rootCause="rc", propagation="p"),
            summary=Summary(highlight="h"),
            impact=Impact(affected=[Affected(service=draft_service)]),
            actions=Actions(steps=["s"]),
        )

    return LlmOrchestrator(planner=planner, agents=agents, report_agent=report)


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
    result = await _fake_orchestrator().run(1, _bundle())
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
    """전 모달리티 0건 → LLM 생략 + '데이터 없음' Evidence로도 계약 유지."""
    result = await _fake_orchestrator(trace_origin=None).run(1, _bundle(with_data=False))
    validate_rca_result(result.model_dump(by_alias=True, exclude_none=True))
    assert result.service == "unknown"  # trace origin 없으면 draft.service
    assert "데이터 없음" in result.detail.evidence.log.conclusion


async def test_agents_are_swappable():
    """그래프 본문 변경 없이 노드 에이전트만 갈아끼워진다."""
    swapped = _fake_orchestrator(
        trace_origin=None, draft_service="SWAPPED", draft_type="Swapped"
    )
    result = await swapped.run(1, _bundle())
    assert result.service == "SWAPPED"
    assert result.type == "Swapped"


async def test_full_pipeline_through_queue_reaches_done(factory, monkeypatch):
    """POST 이후 흐름: 큐 → LLM 오케스트레이터(fake) → 검증 → DONE + result 저장."""
    monkeypatch.setattr(orchestrator_mod, "orchestrator", _fake_orchestrator())
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
