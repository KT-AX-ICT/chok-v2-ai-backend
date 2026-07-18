from app.schemas.contracts import (
    IngestBundle,
    ModalityDetail,
    ModalityInfo,
    ModalityInterval,
    ModalityItem,
    TriggerInfo,
    Window,
)
from app.services.bundle_parser import (
    parse_for_log_agent,
    parse_for_metric_agent,
    parse_for_trace_agent,
)

_BUNDLE = IngestBundle(
    bundle_version="1.0",
    window=Window(start="2026-01-15T10:00:00Z", end="2026-01-15T10:03:00Z"),
    trigger_info=TriggerInfo(
        trigger_time="2026-01-15T10:01:30Z",
        triggered_by=["metric", "log"],
    ),
    modality_info=ModalityInfo(
        log=ModalityDetail(intervals=[
            ModalityInterval(fileName="UserService_.log", status="missing"),
        ]),
        metric=ModalityDetail(intervals=[
            ModalityInterval(start="2026-01-15T10:00:00Z", end="2026-01-15T10:03:00Z", status="ok"),
        ]),
        trace=ModalityDetail(intervals=[]),
    ),
    logs=[ModalityItem(timestamp="2026-01-15T10:01:00Z", service="svc-a", raw="ERROR timeout")],
    metrics=[ModalityItem(timestamp="2026-01-15T10:01:00Z", service="svc-a", raw="error_rate=0.9")],
    traces=[ModalityItem(timestamp="2026-01-15T10:01:10Z", service="svc-a", raw="span 15000ms")],
)


def test_log_agent_input_has_logs():
    result = parse_for_log_agent(_BUNDLE)
    assert "logs" in result
    assert "ERROR timeout" in result["logs"]
    assert result["trigger_time"] == "2026-01-15T10:01:30Z"
    assert result["window_start"] == "2026-01-15T10:00:00Z"


def test_log_agent_includes_interval_summary():
    result = parse_for_log_agent(_BUNDLE)
    assert "log_intervals" in result
    assert "missing" in result["log_intervals"]


def test_log_agent_excludes_filename():
    """LLM 입력에 파일명이 노출되지 않음 (정답 유출 방지)."""
    result = parse_for_log_agent(_BUNDLE)
    assert "UserService_.log" not in result["log_intervals"]


def test_metric_agent_input_has_metrics():
    result = parse_for_metric_agent(_BUNDLE)
    assert "metrics" in result
    # 압축(시리즈 통계) 표현 — 라벨과 값이 통계로 유지된다
    assert "error_rate" in result["metrics"]
    assert "0.9" in result["metrics"]


def test_trace_agent_input_has_traces():
    result = parse_for_trace_agent(_BUNDLE)
    assert "traces" in result
    assert "span 15000ms" in result["traces"]


def test_empty_modality_returns_placeholder():
    empty_bundle = _BUNDLE.model_copy(update={"logs": []})
    result = parse_for_log_agent(empty_bundle)
    assert result["logs"] == "(없음)"


def test_empty_intervals_returns_placeholder():
    result = parse_for_trace_agent(_BUNDLE)
    assert result["trace_intervals"] == "(없음)"


def test_triggered_by_constrained_to_modality_types():
    """triggered_by 값이 log/metric/trace 외에는 Pydantic 오류."""
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TriggerInfo(trigger_time="2026-01-15T10:01:30Z", triggered_by=["error_rate"])
