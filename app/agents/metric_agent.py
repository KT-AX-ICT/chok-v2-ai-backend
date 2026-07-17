"""[얕은 분석 — LLM 미연결] metric 모달리티 분석기.

메트릭 원문을 세어 결론을 요약한다. 추후 LLM 심층 분석으로 교체(#5).
"""

from __future__ import annotations

from app.schemas.contracts import IngestBundle, MetricEvidence, MetricItem
from app.services.bundle_parser import parse_for_metric_agent


async def analyze_metric(bundle: IngestBundle) -> MetricEvidence:
    parse_for_metric_agent(bundle)  # 입력 정제 경로 유지

    metrics = bundle.metrics
    if not metrics:
        return MetricEvidence(conclusion="메트릭 없음", source="metric-agent(shallow)")

    conclusion = f"메트릭 {len(metrics)}건 관측 (얕은 분석)"
    items = [
        MetricItem(label=item.service or "metric", value=item.raw)
        for item in metrics[:5]
    ]
    return MetricEvidence(
        conclusion=conclusion, source="metric-agent(shallow)", items=items
    )
