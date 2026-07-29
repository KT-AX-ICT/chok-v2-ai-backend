"""report 에이전트(LLM) + assemble(코드).

report: Evidence 3종 + 최소 컨텍스트 → ReportDraft (gpt-5.5, structured output).
        raw 데이터는 다시 넣지 않는다 — 정제된 Evidence만 입력.
assemble: detail.evidence는 LLM이 재복사하지 않고 코드가 모달리티 산출물을
        그대로 주입한다. 대표 service는 검증·종합을 마친 report(draft.service)를 우선한다.
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


_LIVENESS_RANK = {"data": 3, "empty": 2, "missing": 1}


def service_liveness(bundle: IngestBundle) -> dict[str, dict[str, str]]:
    """서비스별 모달리티 관측 status(data/empty/missing)를 modalityInfo에서 집계.

    어느 단일 모달리티 에이전트도 못 보는 **교차 신호**를 종합 에이전트에 넘기기 위함 —
    예: metric=data(프로세스 살아있음)인데 log·trace=missing/empty(작업 신호 없음)면 코드가
    멈춘 것(CODE_STOP), 셋 다 없으면 소실(SERVICE_DOWN). 한 서비스가 여러 구간이면 가장
    강한 관측(data>empty>missing)을 대표로 삼는다.
    """
    out: dict[str, dict[str, str]] = {}
    mods = {
        "log": bundle.modality_info.log,
        "metric": bundle.modality_info.metric,
        "trace": bundle.modality_info.trace,
    }
    for mod, detail in mods.items():
        for iv in detail.intervals:
            name = (iv.fileName or "").split("/")[-1]
            for suf in (".log", ".json", ".txt"):
                if name.lower().endswith(suf):
                    name = name[: -len(suf)]
            svc = name.rstrip("_").lower()
            if not svc:
                continue
            st = (iv.status or "?").lower()
            cur = out.setdefault(svc, {})
            if _LIVENESS_RANK.get(st, 0) > _LIVENESS_RANK.get(cur.get(mod, ""), 0):
                cur[mod] = st
    return out


def _render_liveness(liveness: dict[str, dict[str, str]]) -> str:
    if not liveness:
        return ""
    lines = [
        "## 서비스별 관측 신호 (모달리티 status — data=관측됨 / empty=구간에 기록 없음 / missing=파일 없음)"
    ]
    for svc in sorted(liveness):
        s = liveness[svc]
        lines.append(
            f"{svc}\tlog={s.get('log', '-')}\tmetric={s.get('metric', '-')}\ttrace={s.get('trace', '-')}"
        )
    return "\n".join(lines)


def build_report_message(
    bundle: IngestBundle,
    log_ev: LogEvidence,
    metric_ev: MetricEvidence,
    trace_ev: TraceEvidence,
) -> str:
    """Evidence 3종 + 최소 컨텍스트(window·trigger·서비스 생존 신호)만 직렬화. raw 재투입 금지."""

    def dump(ev) -> str:
        return ev.model_dump_json(exclude_none=True)

    liveness = _render_liveness(service_liveness(bundle))
    liveness_block = f"\n{liveness}\n" if liveness else ""

    return (
        f"- 윈도: {bundle.window.start} ~ {bundle.window.end}\n"
        f"- 트리거 시각: {bundle.trigger_info.trigger_time}\n"
        f"- 트리거 모달리티: {', '.join(bundle.trigger_info.triggered_by) or '(없음)'}\n"
        f"{liveness_block}"
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
    """ReportDraft + Evidence 3종 → 최종 RcaResult (evidence 코드 주입).

    대표 service는 **검증·종합을 마친 report(draft.service)를 우선**한다 — report가 세 Evidence를
    상관분석해 진원을 정하고 trace origin_service의 오귀속(호출자·프록시)을 보정하기 때문.
    trace origin은 report가 미확정일 때의 폴백으로만 둔다.
    """
    service = draft.service or trace_ev.origin_service or "UNKNOWN"
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
