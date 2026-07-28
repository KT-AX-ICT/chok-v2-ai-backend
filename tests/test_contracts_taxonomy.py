"""RCA 출력 정형화(type enum · service 앵커링 · confidence 제거) 계약 테스트."""

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


def _result() -> RcaResult:
    return RcaResult(
        type="SERVICE_DOWN",
        severity="HIGH",
        service="media",
        detail=ReportDetail(
            rca=Rca(rootCause="rc", propagation="p"),
            summary=Summary(highlight="h"),
            evidence=Evidence(
                log=LogEvidence(conclusion="l"),
                trace=TraceEvidence(conclusion="t"),
                metric=MetricEvidence(conclusion="m"),
            ),
            impact=Impact(affected=[Affected(service="media")]),
            actions=Actions(steps=["s"]),
        ),
    )


def test_rca_has_no_confidence_field():
    assert "confidence" not in Rca.model_fields


def test_result_payload_omits_confidence():
    dumped = _result().model_dump(by_alias=True, exclude_none=True)
    assert "confidence" not in dumped["detail"]["rca"]
