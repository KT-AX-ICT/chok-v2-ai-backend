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
