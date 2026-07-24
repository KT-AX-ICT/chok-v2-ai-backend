"""signal_selector 테스트 — Spring 전송용 상한 선별.

핵심 관심사는 두 가지다.
  1) 상한을 넘겨 Spring 저장이 깨지지 않을 것
  2) 남은 항목이 진단에 쓸모 있을 것 — 대량 중복 패턴이 자리를 독식하지 않아야 한다
"""

import json

from app.services.signal_selector import select_signals

_TRIGGER = "2026-01-15T10:01:00Z"


def _log(idx: int, raw: str, service: str = "api") -> dict:
    """timestamp를 idx 순으로 증가시켜 시간순 입력(수집기 계약)을 흉내낸다."""
    return {
        "timestamp": f"2026-01-15T10:00:{idx % 60:02d}Z",
        "service": service,
        "raw": raw,
    }


def _trace(idx: int, operation: str, duration_us: int, status: int | str) -> dict:
    return {
        "timestamp": f"2026-01-15T10:00:{idx % 60:02d}Z",
        "service": "media",
        "raw": json.dumps(
            {"operation": operation, "duration_us": duration_us, "http_status_code": status}
        ),
    }


def _metric(idx: int, value: float, service: str = "api") -> dict:
    return {
        "timestamp": f"2026-01-15T10:0{idx // 60}:{idx % 60:02d}Z",
        "service": service,
        "raw": json.dumps({"metric": "cpu", "value": value}),
    }


# ---------------------------------------------------------------- 상한


def test_under_limit_passes_through_untouched():
    """상한 이하면 그룹핑조차 하지 않고 입력을 그대로 돌려준다."""
    items = [_log(i, f"INFO line {i}") for i in range(10)]
    selection = select_signals("log", items, _TRIGGER, limit=200)

    assert selection.items == items
    assert selection.total == 10
    assert selection.truncated is False


def test_over_limit_truncates_to_cap():
    items = [_log(i, f"INFO line {i}") for i in range(500)]
    selection = select_signals("log", items, _TRIGGER, limit=200)

    assert len(selection.items) == 200
    assert selection.total == 500
    assert selection.truncated is True


def test_empty_input_is_safe():
    selection = select_signals("log", [], _TRIGGER, limit=200)
    assert selection.items == []
    assert selection.total == 0
    assert selection.truncated is False


# ---------------------------------------------------------------- 다양성


def test_rare_patterns_survive_a_dominant_one():
    """대량 중복 패턴이 상한을 독식하지 않는다 — 이 PR의 핵심 목적.

    같은 에러가 5000건이어도 라운드로빈이라 한 바퀴에 1건씩만 가져가므로,
    3건뿐인 희귀 패턴도 반드시 실린다.
    """
    items = [_log(i, "ERROR connection refused") for i in range(5000)]
    items += [
        _log(5000, "ERROR disk quota exceeded"),
        _log(5001, "ERROR certificate expired"),
        _log(5002, "FATAL out of memory"),
    ]

    selection = select_signals("log", items, _TRIGGER, limit=200)
    kept = [item["raw"] for item in selection.items]

    assert "ERROR disk quota exceeded" in kept
    assert "ERROR certificate expired" in kept
    assert "FATAL out of memory" in kept
    # 지배 패턴도 남되 전부를 먹지는 않는다
    assert 0 < kept.count("ERROR connection refused") < 200


def test_error_lines_beat_info_lines():
    """자리가 모자라면 에러가 정보 로그보다 먼저 실린다."""
    items = [_log(i, f"INFO heartbeat {i}") for i in range(300)]
    items += [_log(300 + i, f"ERROR upstream timeout {i}") for i in range(5)]

    selection = select_signals("log", items, _TRIGGER, limit=50)
    kept = [item["raw"] for item in selection.items]

    assert sum("ERROR upstream timeout" in raw for raw in kept) == 5


def test_error_spans_beat_healthy_spans():
    items = [_trace(i, "/api/list", 1000, 200) for i in range(400)]
    items += [_trace(400 + i, "/api/compose", 30_000_000, 500) for i in range(3)]

    selection = select_signals("trace", items, _TRIGGER, limit=50)
    operations = [json.loads(item["raw"])["operation"] for item in selection.items]

    assert operations.count("/api/compose") == 3


def test_metric_anomalies_beat_steady_values():
    """트리거 이전 baseline에서 3σ 이상 벗어난 지점이 우선 선별된다."""
    items = [_metric(i, 10.0) for i in range(300)]  # 10:00:00~ baseline
    items += [_metric(120 + i, 9999.0) for i in range(3)]  # 트리거 이후 급등

    selection = select_signals("metric", items, _TRIGGER, limit=50)
    values = [json.loads(item["raw"])["value"] for item in selection.items]

    assert values.count(9999.0) == 3


def test_unparsable_metrics_are_not_dropped():
    """파싱 불가 항목도 후보로 남긴다 — 압축기의 '원문 통과' 폴백과 같은 취지."""
    items = [_metric(i, 10.0) for i in range(300)]
    items += [
        {"timestamp": "2026-01-15T10:02:00Z", "service": "api", "raw": "무슨 형식인지 모를 값"}
    ]

    selection = select_signals("metric", items, _TRIGGER, limit=50)
    kept = [item["raw"] for item in selection.items]

    assert "무슨 형식인지 모를 값" in kept


# ---------------------------------------------------------------- 결정성·정렬


def test_selection_is_deterministic():
    """재전송 시 같은 200건이 나가야 멱등키(triggerTime) 정책과 어긋나지 않는다."""
    items = [_log(i, f"ERROR fail {i % 7}") for i in range(1000)]

    first = select_signals("log", items, _TRIGGER, limit=200)
    second = select_signals("log", items, _TRIGGER, limit=200)

    assert first.items == second.items


def test_result_is_sorted_by_timestamp():
    """Spring이 행으로 저장하고 화면이 시간순으로 읽으므로 오름차순으로 돌려준다."""
    items = [_log(i, f"ERROR fail {i % 9}") for i in range(600)]

    selection = select_signals("log", items, _TRIGGER, limit=200)
    stamps = [item["timestamp"] for item in selection.items]

    assert stamps == sorted(stamps)


def test_group_count_over_limit_is_logged(caplog):
    """그룹 수가 상한을 넘으면 다양성 보장이 깨지는 유일한 경우 — 근거를 남긴다.

    Drain은 토큰 수가 다르면 별도 클러스터로 잡으므로, 길이를 달리해 그룹을 벌린다.
    """
    items = [
        _log(i, "ERROR " + " ".join(["fault"] * (2 + i))) for i in range(60)
    ]

    with caplog.at_level("INFO", logger="app.services.signal_selector"):
        select_signals("log", items, _TRIGGER, limit=10)

    messages = [record.getMessage() for record in caplog.records]
    assert any("누락" in message for message in messages)
