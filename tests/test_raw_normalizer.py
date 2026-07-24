"""raw_normalizer 단위 테스트 — 모달리티별 JSON 정규화 + 폴백."""

import json

from app.services.raw_normalizer import (
    normalize_log,
    normalize_metric,
    normalize_payload_signals,
    normalize_trace,
)

# ---------------------------------------------------------------- log


def test_log_json_extracts_level_and_msg():
    out = json.loads(normalize_log('{"level": "error", "message": "connect timeout"}'))
    assert out == {"level": "error", "msg": "connect timeout"}


def test_log_text_fallback_puts_whole_line_in_msg():
    raw = "2026-01-15 ERROR Failed to connect media-service"
    out = json.loads(normalize_log(raw))
    assert out["level"] == "ERROR"
    assert out["msg"] == raw  # 원문 한 줄 통째로


def test_log_unparseable_keeps_keys_with_empty_level():
    out = json.loads(normalize_log("plain line no level"))
    assert out == {"level": "", "msg": "plain line no level"}


# ---------------------------------------------------------------- metric


def test_metric_name_value_json():
    raw = '{"metric": "cpu_usage", "value": 0.85, "threshold": 0.8, "exceeded": true}'
    out = json.loads(normalize_metric(raw))
    assert out == {"label": "cpu_usage", "value": "0.85", "threshold": "0.8", "exceeded": True}


def test_metric_flat_json_first_numeric():
    out = json.loads(normalize_metric('{"cpu_usage": 53.5}'))
    assert out["label"] == "cpu_usage"
    assert out["value"] == "53.5"
    assert out["exceeded"] is None


def test_metric_prometheus_text():
    out = json.loads(normalize_metric('node_cpu{instance="n:9100"} 2.22'))
    assert out["label"] == "node_cpu"
    assert out["value"] == "2.22"


def test_metric_unparseable_keeps_keys_empty():
    out = json.loads(normalize_metric("이상한 형식의 메트릭"))
    assert out == {"label": "", "value": "", "threshold": "", "exceeded": None}


# ---------------------------------------------------------------- trace


def test_trace_json_fields():
    raw = '{"traceId": "abc", "from": "compose", "to": "media", "duration_ms": 16000, "status": "TIMEOUT"}'
    out = json.loads(normalize_trace(raw))
    assert out == {
        "traceId": "abc",
        "from": "compose",
        "to": "media",
        "duration": 16000,
        "status": "TIMEOUT",
    }


def test_trace_otel_name_and_duration_us():
    out = json.loads(normalize_trace('{"name": "op", "duration_us": 16000000}'))
    assert out["to"] == "op"  # OTel name → to
    assert out["duration"] == 16000  # us / 1000


def test_trace_text_fallback():
    out = json.loads(normalize_trace("span 15000ms TIMEOUT"))
    assert out["duration"] == 15000
    assert out["status"] == "TIMEOUT"
    assert out["traceId"] == ""


# ---------------------------------------------------------------- payload 적용


def test_normalize_payload_signals_replaces_each_raw():
    payload = {
        "logs": [{"timestamp": "t", "service": "s", "raw": "ERROR boom"}],
        "metrics": [{"timestamp": "t", "service": "s", "raw": "cpu=0.9"}],
        "traces": [{"timestamp": "t", "service": "s", "raw": "span 500ms TIMEOUT"}],
    }
    normalize_payload_signals(payload)
    assert json.loads(payload["logs"][0]["raw"]) == {"level": "ERROR", "msg": "ERROR boom"}
    assert json.loads(payload["metrics"][0]["raw"])["label"] == "cpu"
    assert json.loads(payload["traces"][0]["raw"])["duration"] == 500


def test_normalize_payload_signals_tolerates_missing_keys():
    payload = {"status": "DONE"}
    normalize_payload_signals(payload)  # logs/metrics/traces 없어도 무해
    assert payload == {"status": "DONE"}
