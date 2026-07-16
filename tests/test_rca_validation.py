"""RcaResult 검증 레이어 단위 테스트."""

import pytest

from app.schemas.contracts import (
    Actions,
    Affected,
    Evidence,
    Impact,
    LogEvidence,
    MetricEvidence,
    Rca,
    RcaResult,
    ReportDetail,
    Summary,
    TraceEvidence,
)
from app.services.rca_validation import RcaResultInvalid, validate_rca_result


def _valid_result() -> RcaResult:
    return RcaResult(
        type="Code_Stop",
        severity="HIGH",
        service="media-service",
        detail=ReportDetail(
            rca=Rca(rootCause="media-service 무응답", propagation="media → compose"),
            summary=Summary(highlight="media-service 종료"),
            evidence=Evidence(
                log=LogEvidence(conclusion="connect timeout 다수"),
                trace=TraceEvidence(conclusion="16초 블록", origin_service="media-service"),
                metric=MetricEvidence(conclusion="CPU 무신호"),
            ),
            impact=Impact(affected=[Affected(service="compose-post-service")]),
            actions=Actions(steps=["media-service 재시작"]),
        ),
    )


def test_passthrough_rca_result_instance():
    r = _valid_result()
    assert validate_rca_result(r) is r


def test_valid_dict_parses_to_rca_result():
    payload = _valid_result().model_dump(by_alias=True, exclude_none=True)
    out = validate_rca_result(payload)
    assert isinstance(out, RcaResult)
    assert out.service == "media-service"


def test_missing_key_raises_invalid():
    payload = _valid_result().model_dump(by_alias=True, exclude_none=True)
    del payload["detail"]["actions"]  # 5키 중 하나 누락
    with pytest.raises(RcaResultInvalid) as exc:
        validate_rca_result(payload)
    assert "actions" in str(exc.value)  # 사유에 누락 필드가 드러남


def test_wrong_type_raises_invalid():
    with pytest.raises(RcaResultInvalid):
        validate_rca_result("이건 문자열이지 RCA 결과가 아님")
