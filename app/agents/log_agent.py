"""[얕은 분석 — LLM 미연결] log 모달리티 분석기.

파이프라인이 돌도록 로그 원문에서 에러/경고 신호를 세어 결론을 요약한다.
추후 LLM 기반 심층 분석으로 교체(#4). 입력 정제(parse_for_log_agent) 경로는 유지해
LLM 승격 시 그대로 프롬프트로 넘긴다.
"""

from __future__ import annotations

from app.schemas.contracts import IngestBundle, LogEvidence, LogLine
from app.services.bundle_parser import parse_for_log_agent

_LEVELS = ("ERROR", "WARN", "INFO", "DEBUG")


def _infer_level(raw: str) -> str | None:
    upper = raw.upper()
    for lvl in _LEVELS:
        if lvl in upper:
            return lvl
    return None


async def analyze_log(bundle: IngestBundle) -> LogEvidence:
    parse_for_log_agent(bundle)  # 입력 정제 경로 유지(추후 LLM 프롬프트 입력)

    logs = bundle.logs
    if not logs:
        return LogEvidence(conclusion="로그 없음", source="log-agent(shallow)")

    errors = [item for item in logs if _infer_level(item.raw) in ("ERROR", "WARN")]
    signal = errors or logs
    conclusion = f"로그 {len(logs)}건 중 에러/경고 {len(errors)}건 감지 (얕은 분석)"
    lines = [
        LogLine(timestamp=item.timestamp, level=_infer_level(item.raw), msg=item.raw)
        for item in signal[:5]
    ]
    return LogEvidence(conclusion=conclusion, source="log-agent(shallow)", lines=lines)
