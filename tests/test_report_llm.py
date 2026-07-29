"""report 종합 계층(report_llm) 단위 테스트 — 모달리티 생존 신호·서비스별 에러 수 주입.

LLM 실호출 없음. `service_liveness`·`service_error_counts`(룰베이스 신호)와 `assemble`(코드 조립)만 검증.
"""

from app.agents.report_llm import assemble, build_report_message, service_liveness
from app.agents.schemas import ReportDraft
from app.schemas.contracts import (
    Actions,
    Affected,
    Impact,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    ModalityDetail,
    ModalityInfo,
    ModalityInterval,
    ModalityItem,
    Rca,
    Summary,
    TraceEvidence,
    TriggerInfo,
    Window,
)
from app.services.bundle_compression import service_error_counts

# ---------------------------------------------- 모달리티 생존 신호 (교차 status)


def _liveness_bundle():
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
    lv = service_liveness(_liveness_bundle())
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
        _liveness_bundle(), LogEvidence(conclusion="l"), MetricEvidence(conclusion="m"),
        TraceEvidence(conclusion="t"),
    )
    assert "서비스별 관측 신호" in msg
    assert "media\tlog=missing\tmetric=data\ttrace=empty" in msg


# ------------------------------------------ 서비스별 에러 수 (impact.affected.errors)


def _errors_bundle():
    logs = [
        ModalityItem(timestamp="2026-01-15T10:00:00Z", service="media", raw="<error>: boom1"),
        ModalityItem(timestamp="2026-01-15T10:00:01Z", service="media", raw="ERROR boom2"),
        ModalityItem(timestamp="2026-01-15T10:00:02Z", service="media", raw="FATAL boom3"),
        ModalityItem(timestamp="2026-01-15T10:00:03Z", service="media", raw="INFO ok"),   # 에러 아님
        ModalityItem(timestamp="2026-01-15T10:00:04Z", service="user", raw="ERROR dup"),
        ModalityItem(timestamp="2026-01-15T10:00:05Z", service="user", raw="WARN slow"),  # WARN 제외
    ]
    return IngestBundle(
        window=Window(start="2026-01-15T10:00:00Z", end="2026-01-15T10:06:00Z"),
        trigger_info=TriggerInfo(trigger_time="2026-01-15T10:03:00Z"),
        logs=logs,
    )


def test_service_error_counts_error_level_only():
    c = service_error_counts(_errors_bundle())
    assert c["media"] == 3   # error + fatal
    assert c["user"] == 1    # error (warn·info 제외)


def _draft(affected):
    return ReportDraft(
        type="SERVICE_DOWN", severity="HIGH", service="media",
        rca=Rca(rootCause="rc", propagation="p"), summary=Summary(highlight="h"),
        impact=Impact(affected=affected), actions=Actions(steps=["s"]),
    )


def test_assemble_fills_affected_errors_from_logs():
    draft = _draft([Affected(service="media"), Affected(service="user-service")])
    result = assemble(
        draft, LogEvidence(conclusion="l"), MetricEvidence(conclusion="m"),
        TraceEvidence(conclusion="t"), _errors_bundle(),
    )
    by = {a.service: a.errors for a in result.detail.impact.affected}
    assert by["media"] == 3
    assert by["user-service"] == 1  # 정규화 매칭(user-service ↔ user)


def test_assemble_leaves_errors_none_when_service_has_no_logs():
    draft = _draft([Affected(service="ghost")])
    result = assemble(
        draft, LogEvidence(conclusion="l"), MetricEvidence(conclusion="m"),
        TraceEvidence(conclusion="t"), _errors_bundle(),
    )
    assert result.detail.impact.affected[0].errors is None
