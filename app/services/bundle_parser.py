"""IngestBundle → 에이전트별 LLM 입력 정제.

계약(contracts.py) 원칙:
  - title, bundle_id는 LLM 프롬프트에서 제외 (정답 유출 방지, D-020)
  - triggered_by 값은 모달리티 종류만 ("log"/"metric"/"trace") — 서비스명 제외
  - raw 필드만 LLM에 노출; timestamp는 시간 순서 파악용
  - modality_info(파일 구간 메타) 는 에이전트 컨텍스트로 포함 (fileName 포함)
"""

from __future__ import annotations

import re

from app.schemas.contracts import IngestBundle, ModalityInterval
from app.services.bundle_compression import (
    compress_logs,
    compress_metrics,
    compress_traces,
    short_ts,
)

_DATE_RE = re.compile(r"\d{4}-(\d{2}-\d{2})")  # 날짜부에서 MM-DD 추출


def _date_of(ts: str) -> str | None:
    """타임스탬프의 날짜부(MM-DD). 파싱 불가면 None."""
    m = _DATE_RE.search(ts)
    return m.group(1) if m else None


def _fmt_range(start: str, end: str) -> str:
    """구간 시각 표기 — 기본은 시각만(short_ts), 시작·끝 날짜가 다르면 날짜를 병기.

    윈도가 자정을 넘으면(예: 23:58 ~ 00:03) 시각만으로는 00:03이 다음날인지
    드러나지 않는다. 날짜가 갈리는 드문 경우에만 MM-DD를 붙여 순서를 명확히 한다.
    """
    start_date, end_date = _date_of(start), _date_of(end)
    if start_date and end_date and start_date != end_date:
        return f"{start_date} {short_ts(start)} ~ {end_date} {short_ts(end)}"
    return f"{short_ts(start)} ~ {short_ts(end)}"


def render_interval(iv: ModalityInterval) -> str:
    """구간 항목 1건을 프롬프트 한 줄로. router와 모달리티 에이전트가 함께 쓴다.

    fileName을 싣는다 — 파일이 특정되지 않으면 status=missing이 "어딘가의 무언가가
    없었다"가 되어 진원 국소화에 못 쓴다(모듈 독스트링의 D-020 갱신 참조).
    값이 없는 필드는 줄에서 뺀다 — 항목마다 반복되므로 토큰이 항목 수만큼 곱해진다.
    시각은 short_ts로 축약 — 날짜·기준시각은 상단 윈도/트리거에 전체 형식으로 한 번만
    싣고, 구간·압축 데이터는 시각(HH:MM:SS)만 남겨 압축 로그 표기와 형식을 맞춘다.
    단 구간이 자정을 넘어 시작·끝 날짜가 갈리면 _fmt_range가 MM-DD를 병기한다.
    """
    time_range = _fmt_range(iv.start, iv.end) if iv.start and iv.end else "시간 미상"
    parts = [f"[{time_range}]"]
    if iv.fileName:
        parts.append(iv.fileName)
    if iv.status:
        parts.append(f"status={iv.status}")
    if iv.record_count is not None and iv.total_count is not None:
        parts.append(f"{iv.record_count}/{iv.total_count}건")
    elif iv.record_count is not None:
        parts.append(f"{iv.record_count}건")
    elif iv.total_count is not None:
        parts.append(f"원본 {iv.total_count}건")
    return " ".join(parts)


def _interval_summary(intervals: list[ModalityInterval]) -> str:
    if not intervals:
        return "(없음)"
    return "\n".join(render_interval(iv) for iv in intervals)


def parse_for_log_agent(bundle: IngestBundle) -> dict:
    return {
        "window_start": bundle.window.start,
        "window_end": bundle.window.end,
        "trigger_time": bundle.trigger_info.trigger_time,
        "triggered_by": bundle.trigger_info.triggered_by,
        "log_intervals": _interval_summary(bundle.modality_info.log.intervals),
        "logs": compress_logs(bundle.logs, bundle.trigger_info.trigger_time),
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
