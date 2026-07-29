"""종합 에이전트 입력의 서비스별 모달리티 생존 신호(교차) 테스트."""

from app.agents.report_llm import build_report_message, service_liveness
from app.schemas.contracts import (
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    ModalityDetail,
    ModalityInfo,
    ModalityInterval,
    TraceEvidence,
    TriggerInfo,
    Window,
)


def _bundle():
    """media: metric=data(살아있음)인데 log=missing·trace=empty(작업 신호 없음) = CODE_STOP 정황."""
    return IngestBundle(
        window=Window(start="2026-01-15T10:00:00Z", end="2026-01-15T10:06:00Z"),
        trigger_info=TriggerInfo(trigger_time="2026-01-15T10:03:00Z", triggered_by=["log"]),
        modality_info=ModalityInfo(
            log=ModalityDetail(intervals=[ModalityInterval(fileName="media", status="missing")]),
            metric=ModalityDetail(
                intervals=[ModalityInterval(fileName="media", status="data",
                                            record_count=84, total_count=92)]
            ),
            trace=ModalityDetail(intervals=[ModalityInterval(fileName="media", status="empty")]),
        ),
    )


def test_service_liveness_cross_modality():
    lv = service_liveness(_bundle())
    assert lv["media"] == {"log": "missing", "metric": "data", "trace": "empty"}


def test_service_liveness_takes_strongest_observation():
    """같은 서비스가 여러 구간이면 data > empty > missing 중 가장 강한 관측을 대표로."""
    b = IngestBundle(
        window=Window(start="2026-01-15T10:00:00Z", end="2026-01-15T10:06:00Z"),
        trigger_info=TriggerInfo(trigger_time="2026-01-15T10:03:00Z"),
        modality_info=ModalityInfo(
            log=ModalityDetail(intervals=[
                ModalityInterval(fileName="user", status="missing"),
                ModalityInterval(fileName="user", status="data"),
            ]),
        ),
    )
    assert service_liveness(b)["user"]["log"] == "data"


def test_report_message_includes_liveness_signal():
    msg = build_report_message(
        _bundle(), LogEvidence(conclusion="l"), MetricEvidence(conclusion="m"),
        TraceEvidence(conclusion="t"),
    )
    assert "서비스별 관측 신호" in msg
    assert "media\tlog=missing\tmetric=data\ttrace=empty" in msg
