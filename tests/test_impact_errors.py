"""impact.affected[].errors를 로그의 서비스별 에러 수로 채우는 처리 테스트."""

from app.agents.report_llm import assemble
from app.agents.schemas import ReportDraft
from app.schemas.contracts import (
    Actions,
    Affected,
    Impact,
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    ModalityItem,
    Rca,
    Summary,
    TraceEvidence,
    TriggerInfo,
    Window,
)
from app.services.bundle_compression import service_error_counts


def _bundle():
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
    c = service_error_counts(_bundle())
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
        TraceEvidence(conclusion="t"), _bundle(),
    )
    by = {a.service: a.errors for a in result.detail.impact.affected}
    assert by["media"] == 3
    assert by["user-service"] == 1  # 정규화 매칭(user-service ↔ user)


def test_assemble_leaves_errors_none_when_service_has_no_logs():
    draft = _draft([Affected(service="ghost")])
    result = assemble(
        draft, LogEvidence(conclusion="l"), MetricEvidence(conclusion="m"),
        TraceEvidence(conclusion="t"), _bundle(),
    )
    assert result.detail.impact.affected[0].errors is None
