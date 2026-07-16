"""[얕은 분석 — 이예지 담당 영역의 임시 stub] trace 모달리티 분석기.

실제 심층 분석·trace 그래프 tool·origin_service 판정은 이예지 구현으로 교체(#6).
여기서는 파이프라인이 끝까지 돌도록 최소 결론과 origin_service 추정만 만든다.
"""

from __future__ import annotations

from app.schemas.contracts import IngestBundle, TraceEvidence, TraceSpan
from app.services.bundle_parser import parse_for_trace_agent


async def analyze_trace(bundle: IngestBundle) -> TraceEvidence:
    parse_for_trace_agent(bundle)  # 입력 정제 경로 유지

    traces = bundle.traces
    if not traces:
        return TraceEvidence(conclusion="트레이스 없음", source="trace-agent(shallow)")

    origin = next((t.service for t in traces if t.service), None)
    conclusion = f"트레이스 {len(traces)}건 관측 (얕은 분석)"
    spans = [TraceSpan(from_=t.service or None) for t in traces[:5]]
    return TraceEvidence(
        conclusion=conclusion,
        source="trace-agent(shallow)",
        spans=spans,
        origin_service=origin,
    )
