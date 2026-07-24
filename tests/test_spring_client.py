"""SpringClient 페이로드 조립 테스트 — 전송(post) 없이 순수 빌더만 검증."""

import json

from app.schemas.contracts import (
    Actions,
    Affected,
    Evidence,
    Impact,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    Rca,
    RcaResult,
    ReportDetail,
    Summary,
    TraceEvidence,
)
from app.services.spring_client import SpringClient


def _bundle() -> IngestBundle:
    return IngestBundle(
        window={"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        trigger_info={"trigger_time": "2026-01-15T10:01:30Z", "triggered_by": ["log"]},
        logs=[{"timestamp": "2026-01-15T10:01:00Z", "service": "api", "raw": "ERROR boom"}],
        metrics=[{"timestamp": "2026-01-15T10:01:00Z", "service": "api", "raw": "cpu=0.9"}],
        traces=[{"timestamp": "2026-01-15T10:01:00Z", "service": "media", "raw": "span 500ms TIMEOUT"}],
    )


def _result() -> RcaResult:
    return RcaResult(
        type="Code_Stop",
        severity="HIGH",
        service="media-service",
        detail=ReportDetail(
            rca=Rca(rootCause="rc", propagation="p"),
            summary=Summary(highlight="h"),
            evidence=Evidence(
                log=LogEvidence(conclusion="lc"),
                trace=TraceEvidence(conclusion="tc", origin_service="media-service"),
                metric=MetricEvidence(conclusion="mc"),
            ),
            impact=Impact(affected=[Affected(service="x")]),
            actions=Actions(steps=["s"]),
        ),
    )


def test_result_payload_puts_type_service_inside_result():
    payload = SpringClient._result_payload(_bundle(), _result())
    assert payload["status"] == "DONE"
    assert payload["severity"] == "HIGH"
    assert payload["result"]["type"] == "Code_Stop"
    assert payload["result"]["service"] == "media-service"
    assert {"rca", "summary", "evidence", "impact", "actions"} <= set(payload["result"])
    # type·service는 최상위에 두지 않음 (result 내부만)
    assert "type" not in payload
    assert "service" not in payload


def test_result_payload_normalizes_raw():
    payload = SpringClient._result_payload(_bundle(), _result())
    assert json.loads(payload["logs"][0]["raw"]) == {"level": "ERROR", "msg": "ERROR boom"}
    assert json.loads(payload["metrics"][0]["raw"])["label"] == "cpu"
    assert json.loads(payload["traces"][0]["raw"])["status"] == "TIMEOUT"


def test_result_evidence_has_no_signal_arrays():
    payload = SpringClient._result_payload(_bundle(), _result())
    ev = payload["result"]["evidence"]
    assert "lines" not in ev["log"]
    assert "spans" not in ev["trace"]
    assert "items" not in ev["metric"]


def test_failure_payload_uses_reason_not_error():
    payload = SpringClient._failure_payload(_bundle(), "boom failed")
    assert payload["status"] == "FAILED"
    assert payload["reason"] == "boom failed"
    assert "error" not in payload
    assert "result" not in payload
    # 실패 페이로드도 raw는 정규화됨
    assert json.loads(payload["logs"][0]["raw"])["level"] == "ERROR"


def test_payload_carries_company_code_default():
    """companyCode 미지정 시 기본값 SN001이 페이로드 최상위에 실린다."""
    payload = SpringClient._result_payload(_bundle(), _result())
    assert payload["companyCode"] == "SN001"


def test_payload_carries_provided_company_code():
    """지정한 companyCode는 성공·실패 페이로드 둘 다에 그대로 전달된다."""
    bundle = IngestBundle(
        company_code="SN042",
        window={"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        trigger_info={"trigger_time": "2026-01-15T10:01:30Z", "triggered_by": ["log"]},
    )
    ok = SpringClient._result_payload(bundle, _result())
    fail = SpringClient._failure_payload(bundle, "boom")
    assert ok["companyCode"] == "SN042"
    assert fail["companyCode"] == "SN042"


# ------------------------------------------------------- 전송 상한 선별


def _bulk_bundle(log_count: int) -> IngestBundle:
    return IngestBundle(
        window={"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        trigger_info={"trigger_time": "2026-01-15T10:01:30Z", "triggered_by": ["log"]},
        logs=[
            {
                "timestamp": "2026-01-15T10:01:00Z",
                "service": "api",
                "raw": f"ERROR boom {i}",
            }
            for i in range(log_count)
        ],
    )


def test_result_payload_caps_signals_at_limit(monkeypatch):
    """원본이 상한을 넘으면 잘라서 보낸다 — Spring도 같은 MySQL이라 한계가 같다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "spring_signal_limit", 20)
    payload = SpringClient._result_payload(_bulk_bundle(500), _result())

    assert len(payload["logs"]) == 20


def test_truncated_modality_notes_range_in_source(monkeypatch):
    """잘린 모달리티는 evidence.source에 수록 범위를 남겨 '200건뿐'으로 오해되지 않게 한다."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "spring_signal_limit", 20)
    payload = SpringClient._result_payload(_bulk_bundle(500), _result())

    assert "전체 500건 중 주요 20건 수록" in payload["result"]["evidence"]["log"]["source"]


def test_untruncated_modality_notes_full_coverage():
    """잘리지 않은 모달리티도 '전량 수록'을 명시 — 문구 부재를 정보 없음으로 읽지 않게."""
    payload = SpringClient._result_payload(_bundle(), _result())

    assert "전체 1건 전량 수록" in payload["result"]["evidence"]["metric"]["source"]


def test_note_appends_to_existing_llm_source():
    """LLM이 쓴 source는 보존하고 뒤에 덧붙인다."""
    result = _result()
    result.detail.evidence.log.source = "user-service 로그"
    payload = SpringClient._result_payload(_bundle(), result)

    assert payload["result"]["evidence"]["log"]["source"] == "user-service 로그 (전체 1건 전량 수록)"


def test_note_created_when_llm_left_source_empty():
    """source는 optional — LLM이 안 채웠으면 고지 문구만으로 새로 만든다."""
    payload = SpringClient._result_payload(_bundle(), _result())

    assert payload["result"]["evidence"]["log"]["source"] == "전체 1건 전량 수록"


def test_empty_modality_gets_no_note():
    """항목이 0건이면 표기할 건수가 없으므로 source를 만들지 않는다."""
    bundle = IngestBundle(
        window={"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        trigger_info={"trigger_time": "2026-01-15T10:01:30Z", "triggered_by": ["log"]},
    )
    payload = SpringClient._result_payload(bundle, _result())

    assert "source" not in payload["result"]["evidence"]["log"]


def test_conclusion_is_left_untouched():
    """LLM 결론과 코드가 쓴 사실을 한 문장에 섞지 않는다."""
    payload = SpringClient._result_payload(_bundle(), _result())

    assert payload["result"]["evidence"]["log"]["conclusion"] == "lc"


def test_failure_payload_also_caps_signals(monkeypatch):
    """실패 경로도 번들을 그대로 싣기 때문에 크기 위험이 같다 — 상한을 동일 적용."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "spring_signal_limit", 20)
    payload = SpringClient._failure_payload(_bulk_bundle(500), "boom")

    assert len(payload["logs"]) == 20
    assert "result" not in payload  # 고지할 자리가 없음


# ------------------------------------------------------- 시각 형식 정합


def test_naive_timestamp_gets_utc_marker():
    """SDK는 tz 없는 값을 보내는데 Spring 허용 형식이 아니라 422가 된다 — Z를 붙여 맞춘다."""
    bundle = IngestBundle(
        window={"start": "2026-07-24T02:33:23", "end": "2026-07-24T02:39:23.209880"},
        trigger_info={"trigger_time": "2026-07-24T02:36:23", "triggered_by": ["log"]},
        logs=[{"timestamp": "2026-07-24T02:34:10.20988", "service": "api", "raw": "ERROR x"}],
    )
    payload = SpringClient._result_payload(bundle, _result())

    assert payload["window"]["start"] == "2026-07-24T02:33:23Z"
    assert payload["window"]["end"] == "2026-07-24T02:39:23.209880Z"
    assert payload["triggerInfo"]["triggerTime"] == "2026-07-24T02:36:23Z"
    assert payload["logs"][0]["timestamp"] == "2026-07-24T02:34:10.209880Z"


def test_offset_timestamp_is_converted_to_utc():
    """오프셋이 이미 붙어 온 값은 UTC로 환산 — 가리키는 시각은 그대로."""
    bundle = IngestBundle(
        window={"start": "2026-07-24T11:33:23+09:00", "end": "2026-07-24T11:39:23+09:00"},
        trigger_info={"trigger_time": "2026-07-24T11:36:23+09:00", "triggered_by": ["log"]},
    )
    payload = SpringClient._result_payload(bundle, _result())

    assert payload["window"]["start"] == "2026-07-24T02:33:23Z"
    assert payload["triggerInfo"]["triggerTime"] == "2026-07-24T02:36:23Z"


def test_already_utc_timestamp_is_unchanged():
    """이미 Z가 붙은 값은 건드리지 않는다 — 멱등이라 재전송해도 같은 문자열."""
    payload = SpringClient._result_payload(_bundle(), _result())

    assert payload["window"]["start"] == "2026-01-15T10:00:00Z"
    assert payload["triggerInfo"]["triggerTime"] == "2026-01-15T10:01:30Z"


def test_unparsable_timestamp_raises():
    """도달 불가 지점 — /ingest와 restore가 Iso8601로 두 번 거르므로 여기 못 온다.

    그래도 왔다면 검증 가정이 깨진 것이라 조용히 흘리지 않고 터뜨린다.
    """
    import pytest

    from app.services.spring_client import _to_spring_ts

    with pytest.raises(ValueError):
        _to_spring_ts("형식을 알 수 없는 값")


def test_modality_interval_times_are_normalized():
    """한 페이로드 안에서 형식이 갈리면 받는 쪽이 파서를 두 벌 두어야 한다."""
    bundle = IngestBundle.model_validate(
        {
            "window": {"start": "2026-07-24T02:33:23", "end": "2026-07-24T02:39:23"},
            "triggerInfo": {"triggerTime": "2026-07-24T02:36:23"},
            "modalityInfo": {
                "log": {
                    "intervals": [
                        {
                            "fileName": "UserService_.log",
                            "start": "2026-07-24T02:33:23",
                            "end": "2026-07-24T02:36:23.500000",
                        }
                    ]
                }
            },
        }
    )
    payload = SpringClient._result_payload(bundle, _result())
    interval = payload["modalityInfo"]["log"]["intervals"][0]

    assert interval["start"] == "2026-07-24T02:33:23Z"
    assert interval["end"] == "2026-07-24T02:36:23.500000Z"


def test_failure_payload_also_normalizes_timestamps():
    """실패 리포트도 같은 계약으로 저장되므로 형식을 동일하게 맞춘다."""
    bundle = IngestBundle(
        window={"start": "2026-07-24T02:33:23", "end": "2026-07-24T02:39:23"},
        trigger_info={"trigger_time": "2026-07-24T02:36:23", "triggered_by": ["log"]},
        logs=[{"timestamp": "2026-07-24T02:34:10", "service": "api", "raw": "ERROR x"}],
    )
    payload = SpringClient._failure_payload(bundle, "boom")

    assert payload["triggerInfo"]["triggerTime"] == "2026-07-24T02:36:23Z"
    assert payload["logs"][0]["timestamp"] == "2026-07-24T02:34:10Z"


def test_normalization_is_idempotent():
    """두 번 정규화해도 값이 흔들리지 않아야 멱등키(triggerTime)가 안정적이다."""
    from app.services.spring_client import _to_spring_ts

    once = _to_spring_ts("2026-07-24T02:33:23.209880")
    assert _to_spring_ts(once) == once


def test_bundle_accepts_camelcase_company_code():
    """SDK가 보내는 camelCase(companyCode) 입력을 수용하고, by_alias로 다시 camelCase 출력."""
    bundle = IngestBundle.model_validate(
        {
            "companyCode": "SN099",
            "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
            "triggerInfo": {"triggerTime": "2026-01-15T10:01:30Z"},
        }
    )
    assert bundle.company_code == "SN099"
    assert bundle.model_dump(by_alias=True)["companyCode"] == "SN099"
