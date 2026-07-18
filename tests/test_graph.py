"""LangGraph 오케스트레이터(graph.py) 테스트 — fake 에이전트 주입, LLM 실호출 없음."""

import pytest

from app.agents.graph import LlmOrchestrator
from app.agents.schemas import MODALITIES, PlanDecision, ReportDraft
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
from app.services.rca_validation import validate_rca_result


def _bundle(triggered_by=("log",), with_traces=True) -> IngestBundle:
    base = {
        "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        "trigger_info": {
            "trigger_time": "2026-01-15T10:01:30Z",
            "triggered_by": list(triggered_by),
        },
        "logs": [{"timestamp": "2026-01-15T10:01:00Z", "service": "svc-a", "raw": "ERROR x"}],
        "metrics": [{"timestamp": "2026-01-15T10:01:00Z", "service": "svc-a", "raw": "cpu=90"}],
    }
    if with_traces:
        base["traces"] = [
            {"timestamp": "2026-01-15T10:01:10Z", "service": "media", "raw": "span 16s"}
        ]
    return IngestBundle(**base)


async def _fake_planner(bundle):
    return PlanDecision(log="deep", metric="scan", trace="deep", reason="테스트")


def _draft(service="media-service") -> ReportDraft:
    return ReportDraft(
        type="Svc_Kill",
        severity="HIGH",
        service=service,
        rca=Rca(rootCause="media 종료", propagation="media → compose"),
        summary=Summary(highlight="media-service 소실"),
        impact=Impact(affected=[Affected(service="compose-post")]),
        actions=Actions(steps=["media 재시작"]),
    )


def _make_fake_agents(calls: list):
    """호출 기록을 남기는 fake 에이전트 사전 — (modality, mode) 전 조합."""
    evidences = {
        "log": LogEvidence(conclusion="로그 결론"),
        "metric": MetricEvidence(conclusion="메트릭 결론"),
        "trace": TraceEvidence(conclusion="트레이스 결론", origin_service="media-service"),
    }

    def make(modality, mode):
        async def agent(bundle):
            calls.append((modality, mode))
            return evidences[modality]

        return agent

    return {(m, d): make(m, d) for m in MODALITIES for d in ("deep", "scan")}


async def _fake_report(bundle, log_ev, metric_ev, trace_ev):
    return _draft()


async def test_happy_path_routes_by_plan_and_assembles():
    calls: list = []
    orch = LlmOrchestrator(
        planner=_fake_planner, agents=_make_fake_agents(calls), report_agent=_fake_report
    )
    result = await orch.run(1, _bundle())

    # plan대로 라우팅: log deep / metric scan / trace deep
    assert sorted(calls) == [("log", "deep"), ("metric", "scan"), ("trace", "deep")]
    # origin_service 승격 + evidence 코드 주입
    assert result.service == "media-service"
    assert result.detail.evidence.log.conclusion == "로그 결론"
    assert result.detail.evidence.trace.origin_service == "media-service"
    # 워커 검증 게이트(5키 계약) 통과
    assert validate_rca_result(result) is result


async def test_empty_modality_skips_llm():
    calls: list = []
    orch = LlmOrchestrator(
        planner=_fake_planner, agents=_make_fake_agents(calls), report_agent=_fake_report
    )
    result = await orch.run(2, _bundle(with_traces=False))

    assert ("trace", "deep") not in calls  # 0건 → LLM 생략
    assert "데이터 없음" in result.detail.evidence.trace.conclusion
    assert result.service == "media-service"  # trace origin 없으면 draft.service


async def test_partial_failure_becomes_failed_evidence():
    calls: list = []
    agents = _make_fake_agents(calls)

    async def broken_log_agent(bundle):
        raise RuntimeError("LLM 재시도 소진")

    agents[("log", "deep")] = broken_log_agent
    orch = LlmOrchestrator(planner=_fake_planner, agents=agents, report_agent=_fake_report)
    result = await orch.run(3, _bundle())

    assert "분석 실패" in result.detail.evidence.log.conclusion  # 부분 실패 완주
    assert result.detail.evidence.metric.conclusion == "메트릭 결론"


async def test_report_failure_propagates():
    """전체 실패: report 예외는 전파 → 워커 재시도/FAILED 경로."""

    async def broken_report(bundle, log_ev, metric_ev, trace_ev):
        raise RuntimeError("report 실패")

    orch = LlmOrchestrator(
        planner=_fake_planner, agents=_make_fake_agents([]), report_agent=broken_report
    )
    with pytest.raises(RuntimeError, match="report 실패"):
        await orch.run(4, _bundle())


async def test_planner_failure_still_completes_all_deep():
    """planner 실패 → 전 모달리티 deep 폴백으로 완주."""
    calls: list = []

    async def broken_planner(bundle):
        raise RuntimeError("planner 실패")

    orch = LlmOrchestrator(
        planner=broken_planner, agents=_make_fake_agents(calls), report_agent=_fake_report
    )
    result = await orch.run(5, _bundle())
    assert sorted(calls) == [("log", "deep"), ("metric", "deep"), ("trace", "deep")]
    assert result.type == "Svc_Kill"
