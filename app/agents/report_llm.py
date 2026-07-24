"""report 에이전트(LLM) + assemble(코드).

report: Evidence 3종 + 최소 컨텍스트 → ReportDraft (gpt-5.5, structured output).
        raw 데이터는 다시 넣지 않는다 — 정제된 Evidence만 입력.
assemble: detail.evidence는 LLM이 재복사하지 않고 코드가 모달리티 산출물을
        그대로 주입한다. trace의 origin_service는 대표 service로 승격(Q-007).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.llm import llm_limit, make_llm
from app.agents.prompts import load_prompt
from app.agents.schemas import ReportDraft
from app.core.config import settings
from app.schemas.contracts import (
    Evidence,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    RcaResult,
    ReportDetail,
    TraceEvidence,
)

ReportAgent = Callable[
    [IngestBundle, LogEvidence, MetricEvidence, TraceEvidence], Awaitable[ReportDraft]
]


def build_report_message(
    bundle: IngestBundle,
    log_ev: LogEvidence,
    metric_ev: MetricEvidence,
    trace_ev: TraceEvidence,
) -> str:
    """Evidence 3종 + 최소 컨텍스트(window·trigger)만 직렬화. raw 재투입 금지."""

    def dump(ev) -> str:
        return ev.model_dump_json(exclude_none=True)

    return (
        f"- 윈도: {bundle.window.start} ~ {bundle.window.end}\n"
        f"- 트리거 시각: {bundle.trigger_info.trigger_time}\n"
        f"- 트리거 모달리티: {', '.join(bundle.trigger_info.triggered_by) or '(없음)'}\n"
        f"\n## log Evidence\n{dump(log_ev)}\n"
        f"\n## metric Evidence\n{dump(metric_ev)}\n"
        f"\n## trace Evidence\n{dump(trace_ev)}"
    )


async def llm_report(
    bundle: IngestBundle,
    log_ev: LogEvidence,
    metric_ev: MetricEvidence,
    trace_ev: TraceEvidence,
) -> ReportDraft:
    """기본 report 에이전트 — 상관분석·rootCause 추론은 품질이 곧 제품(최상위 모델)."""
    messages = [
        SystemMessage(content=load_prompt("report")),
        HumanMessage(content=build_report_message(bundle, log_ev, metric_ev, trace_ev)),
    ]
    llm = make_llm(settings.openai_model_report, "high").with_structured_output(ReportDraft)
    async with llm_limit():
        return await llm.ainvoke(messages)


def assemble(
    draft: ReportDraft,
    log_ev: LogEvidence,
    metric_ev: MetricEvidence,
    trace_ev: TraceEvidence,
) -> RcaResult:
    """ReportDraft + Evidence 3종 → 최종 RcaResult (evidence 코드 주입)."""
    service = trace_ev.origin_service or draft.service
    return RcaResult(
        type=draft.type,
        severity=draft.severity,
        service=service,
        detail=ReportDetail(
            rca=draft.rca,
            summary=draft.summary,
            evidence=Evidence(log=log_ev, trace=trace_ev, metric=metric_ev),
            impact=draft.impact,
            actions=draft.actions,
        ),
    )
