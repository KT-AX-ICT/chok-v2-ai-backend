"""IngestBundle → 에이전트별 LLM 입력 정제.

계약(contracts.py) 원칙:
  - title, bundle_id, 파일명은 LLM 프롬프트에서 제외 (정답 유출 방지, D-020)
  - triggered_by 값은 모달리티 종류만 ("log"/"metric"/"trace") — 서비스명 제외
  - raw 필드만 LLM에 노출; timestamp는 시간 순서 파악용
  - modality_info(파일 메타) 는 에이전트 컨텍스트로 포함 (fileName은 제외)
"""

from __future__ import annotations

from app.schemas.contracts import IngestBundle
from app.services.bundle_compression import (
    compress_logs,
    compress_metrics,
    compress_traces,
)


def _interval_summary(intervals) -> str:
    """ModalityInterval 목록을 상태 요약 텍스트로 변환. fileName은 제외."""
    if not intervals:
        return "(없음)"
    parts = []
    for iv in intervals:
        status = iv.status or iv.present or "ok"
        time_range = f"{iv.start} ~ {iv.end}" if iv.start and iv.end else "시간 미상"
        parts.append(f"[{time_range}] status={status}")
    return "\n".join(parts)


def parse_for_log_agent(bundle: IngestBundle) -> dict:
    return {
        "window_start": bundle.window.start,
        "window_end": bundle.window.end,
        "trigger_time": bundle.trigger_info.trigger_time,
        "triggered_by": bundle.trigger_info.triggered_by,
        "log_intervals": _interval_summary(bundle.modality_info.log.intervals),
        "logs": compress_logs(bundle.logs),
    }


def parse_for_metric_agent(bundle: IngestBundle) -> dict:
    return {
        "window_start": bundle.window.start,
        "window_end": bundle.window.end,
        "trigger_time": bundle.trigger_info.trigger_time,
        "triggered_by": bundle.trigger_info.triggered_by,
        "metric_intervals": _interval_summary(bundle.modality_info.metric.intervals),
        "metrics": compress_metrics(bundle.metrics, bundle.trigger_info.trigger_time),
    }


def parse_for_trace_agent(bundle: IngestBundle) -> dict:
    return {
        "window_start": bundle.window.start,
        "window_end": bundle.window.end,
        "trigger_time": bundle.trigger_info.trigger_time,
        "triggered_by": bundle.trigger_info.triggered_by,
        "trace_intervals": _interval_summary(bundle.modality_info.trace.intervals),
        "traces": compress_traces(bundle.traces),
    }
