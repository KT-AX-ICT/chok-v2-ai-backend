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
    render_interval,
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
            ModalityInterval(
                start="2026-01-15T10:00:00Z",
                end="2026-01-15T10:03:00Z",
                status="data",
                record_count=1523,
                total_count=20000,
            ),
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


def test_log_agent_includes_filename():
    """구간 정보에 파일명을 싣는다 — 없으면 missing이 어느 파일인지 특정 불가."""
    result = parse_for_log_agent(_BUNDLE)
    assert "UserService_.log" in result["log_intervals"]


def test_interval_omits_absent_fields():
    """값이 없는 필드는 줄에서 뺀다 — 항목 수만큼 토큰이 곱해지므로."""
    line = render_interval(ModalityInterval(fileName="UserService_.log", status="missing"))
    assert line == "[시간 미상] UserService_.log status=missing"


def test_interval_renders_both_counts():
    """받은 건수/원본 건수를 그대로 노출 — 절단 여부는 LLM이 두 수로 판단."""
    line = render_interval(
        ModalityInterval(
            start="2026-01-15T10:00:00Z",
            end="2026-01-15T10:03:00Z",
            status="data",
            record_count=1523,
            total_count=20000,
        )
    )
    assert "1523/20000건" in line
    assert "UserService" not in line  # 파일명 없으면 빈 자리도 안 남김


def test_interval_renders_single_count():
    """한쪽만 온 경우에도 어느 쪽 수인지 구분되게 표기."""
    assert "1523건" in render_interval(ModalityInterval(record_count=1523))
    assert "원본 20000건" in render_interval(ModalityInterval(total_count=20000))


def test_interval_has_no_invented_status():
    """정의에 없는 'ok' 폴백 금지 — status가 없으면 status 자체를 출력하지 않는다."""
    line = render_interval(ModalityInterval(fileName="a.log"))
    assert "status" not in line
    assert "ok" not in line


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
