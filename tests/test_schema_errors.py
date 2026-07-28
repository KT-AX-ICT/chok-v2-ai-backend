"""검증 오류 요약 포맷 단위 테스트.

세 경계(요청 수신·RCA 산출물·Spring 전송)가 같은 형식을 쓰므로, 이 포맷이 깨지면
로그를 읽는 절차가 전부 어긋난다.
"""

import pytest
from pydantic import BaseModel, ValidationError

from app.core.schema_errors import MAX_ERRORS, MSG_MAX, summarize_validation_errors


class _Inner(BaseModel):
    name: str


class _Outer(BaseModel):
    inner: _Inner
    count: int


def _errors_of(payload: dict) -> list[dict]:
    with pytest.raises(ValidationError) as exc:
        _Outer.model_validate(payload)
    return exc.value.errors()


def test_missing_field_shows_path_and_type():
    text = summarize_validation_errors(_errors_of({"count": 1}))
    assert "inner[missing]" in text
    assert "Field required" in text


def test_nested_path_joined_with_dots():
    text = summarize_validation_errors(_errors_of({"inner": {}, "count": 1}))
    assert "inner.name[missing]" in text


def test_multiple_errors_joined():
    text = summarize_validation_errors(_errors_of({"inner": {}, "count": "abc"}))
    assert "inner.name[missing]" in text
    assert "count[int_parsing]" in text
    assert " | " in text


def test_input_field_is_not_logged():
    """err["input"]은 싣지 않는다 — 번들 본문이 로그로 복사되면 유출 표면이 된다.

    커스텀 검증기가 스스로 값을 메시지에 넣는 경우(contracts._valid_iso8601)만 예외이며,
    그때도 msg 길이 상한이 걸린다 — test_value_error_message_is_kept_but_bounded.
    """
    text = summarize_validation_errors(
        _errors_of({"inner": {"name": "비밀값-abc123"}, "count": "not-a-number"})
    )
    assert "not-a-number" not in text
    assert "비밀값-abc123" not in text


def test_value_error_message_is_kept_but_bounded():
    """커스텀 검증기 메시지는 그대로 남긴다 — 값 자체가 진단 정보인 경우(형식 오류)."""
    long_value = "x" * (MSG_MAX * 2)
    errors = [
        {
            "loc": ("body", "window", "start"),
            "type": "value_error",
            "msg": f"Value error, 타임스탬프 형식 오류(ISO-8601 아님): {long_value!r}",
        }
    ]
    text = summarize_validation_errors(errors)
    assert "타임스탬프 형식 오류" in text
    assert len(text) <= len("body.window.start[value_error] ") + MSG_MAX + 1


def test_long_message_is_truncated():
    errors = [{"loc": ("body", "x"), "type": "value_error", "msg": "가" * (MSG_MAX + 50)}]
    text = summarize_validation_errors(errors)
    assert len(text) < MSG_MAX + 60
    assert text.endswith("…")


def test_error_count_is_capped_with_total_noted():
    """번들은 3종 배열이 수천 건까지 온다 — 전량을 찍으면 로그가 본문 부피가 된다."""
    errors = [
        {"loc": ("body", "logs", i, "timestamp"), "type": "missing", "msg": "Field required"}
        for i in range(MAX_ERRORS + 7)
    ]
    text = summarize_validation_errors(errors)
    assert text.count(" | ") == MAX_ERRORS  # 상한 건수 + 생략 안내 1건
    assert "7건 생략" in text
    assert f"총 {MAX_ERRORS + 7}건" in text


def test_limit_override():
    errors = [
        {"loc": ("body", "logs", i), "type": "missing", "msg": "Field required"}
        for i in range(5)
    ]
    text = summarize_validation_errors(errors, limit=2)
    assert "3건 생략" in text


def test_empty_error_list_is_handled():
    assert summarize_validation_errors([]) == "(오류 상세 없음)"


def test_missing_loc_does_not_crash():
    text = summarize_validation_errors([{"type": "unknown", "msg": "무언가"}])
    assert "위치 불명" in text
