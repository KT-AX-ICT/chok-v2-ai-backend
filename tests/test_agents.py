"""router 가드레일·모달리티 에이전트 배선 테스트 — LLM 실호출 없음(fake 주입)."""

import pytest

from app.agents.modality_agents import build_user_message, make_modality_agent
from app.agents.router import build_router_message, route_with_guardrails
from app.agents.schemas import RouteDecision
from app.core.config import settings
from app.schemas.contracts import IngestBundle, ModalityInterval


def _bundle(triggered_by=("log",), logs=True) -> IngestBundle:
    base = {
        "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        "trigger_info": {
            "trigger_time": "2026-01-15T10:01:30Z",
            "triggered_by": list(triggered_by),
        },
    }
    if logs:
        base["logs"] = [
            {"timestamp": "2026-01-15T10:01:00Z", "service": "svc-a", "raw": "ERROR timeout"}
        ]
    return IngestBundle(**base)


# ------------------------------------------------------- router 가드레일


async def test_triggered_modality_promoted_to_deep():
    """가드레일(1): LLM이 scan으로 내려도 triggered_by 모달리티는 deep 승격."""

    async def fake_router(bundle):
        return RouteDecision(log="scan", metric="scan", trace="scan", reason="전부 정상으로 오판")

    routes = await route_with_guardrails(_bundle(triggered_by=("log", "metric")), fake_router)
    assert routes == {"log": "deep", "metric": "deep", "trace": "scan"}


async def test_non_triggered_scan_decision_respected():
    """역방향 강등 없음: 비트리거 모달리티는 LLM 결정(deep)을 그대로 존중."""

    async def fake_router(bundle):
        return RouteDecision(log="deep", metric="scan", trace="deep", reason="trace 의심")

    routes = await route_with_guardrails(_bundle(triggered_by=("log",)), fake_router)
    assert routes == {"log": "deep", "metric": "scan", "trace": "deep"}


async def test_router_failure_falls_back_to_all_deep():
    """가드레일(2): router 예외 시 전 모달리티 deep."""

    async def broken_router(bundle):
        raise RuntimeError("429 소진")

    routes = await route_with_guardrails(_bundle(triggered_by=()), broken_router)
    assert routes == {"log": "deep", "metric": "deep", "trace": "deep"}


def test_router_message_is_metadata_only():
    """router 입력에 raw 데이터가 섞이지 않음 — 건수·구간·트리거만."""
    msg = build_router_message(_bundle())
    assert "log=1" in msg  # 건수
    assert "ERROR timeout" not in msg  # raw 미포함
    assert "2026-01-15T10:01:30Z" in msg  # 트리거 시각


def test_router_message_shows_received_vs_original():
    """받은 건수는 우리가 세고, 원본 건수는 SDK totalCount 합으로 병기한다."""
    bundle = _bundle()
    bundle.modality_info.log.intervals = [
        ModalityInterval(fileName="a.log", status="data", record_count=1, total_count=12),
        ModalityInterval(fileName="b.log", status="data", record_count=0, total_count=8),
    ]
    msg = build_router_message(bundle)

    # 12+8=20이 원본, 받은 건수 1은 실제 배열 길이. totalCount 없는 모달리티는 병기 없음.
    assert "건수: log=1 (원본 20 중 수집), metric=0, trace=0" in msg


def test_router_message_includes_filename_in_intervals():
    bundle = _bundle()
    bundle.modality_info.log.intervals = [
        ModalityInterval(fileName="UserService_.log", status="missing")
    ]
    assert "UserService_.log" in build_router_message(bundle)


# --------------------------------------------------- 모달리티 에이전트 배선


@pytest.mark.parametrize(
    "modality,mode,expected_model_attr,expected_prompt",
    [
        ("log", "deep", "openai_model_analysis", "log"),
        ("metric", "deep", "openai_model_analysis", "metric"),
        ("trace", "deep", "openai_model_analysis", "trace"),
        ("log", "scan", "openai_model_light", "scan"),
        ("trace", "scan", "openai_model_light", "scan"),
    ],
)
def test_agent_factory_wiring(modality, mode, expected_model_attr, expected_prompt):
    """deep→mini 모델·모달리티 프롬프트, scan→nano 모델·scan 프롬프트."""
    agent = make_modality_agent(modality, mode)
    assert agent.modality == modality
    assert agent.mode == mode
    assert agent.model == getattr(settings, expected_model_attr)
    assert agent.prompt_name == expected_prompt


def test_user_message_contains_compressed_data_and_context():
    from app.services.bundle_parser import parse_for_log_agent

    bundle = _bundle()
    msg = build_user_message("log", parse_for_log_agent(bundle))
    assert "분석 대상 모달리티: log" in msg
    assert "트리거 시각: 2026-01-15T10:01:30Z" in msg
    assert "ERROR timeout" in msg  # 압축 표현 속 원문 샘플
