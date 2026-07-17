"""[얕은 종합 — LLM 미연결] report 작성 에이전트.

Evidence 3종을 RcaResult 5키 계약으로 조립한다. 실제 상관분석·rootCause 추론은
LLM report 에이전트로 교체(#12). 지금은 검증을 통과하는 유효한 RcaResult를 만드는 게 목적.
"""

from __future__ import annotations

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


def _guess_service(bundle: IngestBundle) -> str | None:
    for items in (bundle.traces, bundle.logs, bundle.metrics):
        for item in items:
            if item.service:
                return item.service
    return None


async def build_report(
    bundle: IngestBundle,
    log_ev: LogEvidence,
    metric_ev: MetricEvidence,
    trace_ev: TraceEvidence,
) -> RcaResult:
    # 대표 서비스: trace origin 우선, 없으면 번들에서 추정
    service = trace_ev.origin_service or _guess_service(bundle) or "unknown"
    triggered = bundle.trigger_info.triggered_by or []

    detail = ReportDetail(
        rca=Rca(
            rootCause=f"{service} 관련 이상 징후 (얕은 분석 — 확정 아님)",
            propagation="전파 경로 미분석 (LLM 승격 전)",
        ),
        summary=Summary(
            highlight=f"{service}에서 트리거 발생: {', '.join(triggered) or '미상'}",
        ),
        evidence=Evidence(log=log_ev, trace=trace_ev, metric=metric_ev),
        impact=Impact(affected=[Affected(service=service)]),
        actions=Actions(steps=[f"{service} 상태 점검", "LLM 심층 분석 필요"]),
    )
    # severity 값 체계는 HIGH/MID/LOW (api-spec §2.3). 얕은 버전은 MID 고정.
    return RcaResult(type="Unknown", severity="MID", service=service, detail=detail)
