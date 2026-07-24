"""모달리티 LLM 에이전트 — 심층(deep) 3종 + 경량 스캔(scan).

기존 주입 계약 유지: 에이전트는 `(IngestBundle) → Evidence` 코루틴.
deep은 mini 모델로 전체 심층 분석, scan은 nano 모델로 "이상 유무" 판정.
둘 다 출력 스키마는 동일(Evidence) — report가 구분 없이 소비한다.

프롬프트 캐싱 배치: 시스템 프롬프트(고정) → user 메시지(가변 raw) 순서.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm import llm_limit, make_llm, truncate_input
from app.agents.prompts import load_prompt
from app.agents.schemas import Depth, Modality
from app.core.config import settings
from app.schemas.contracts import (
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    TraceEvidence,
)
from app.services.bundle_parser import (
    parse_for_log_agent,
    parse_for_metric_agent,
    parse_for_trace_agent,
)

ModalityAgent = Callable[[IngestBundle], Awaitable[BaseModel]]

_EVIDENCE_SCHEMA: dict[Modality, type[BaseModel]] = {
    "log": LogEvidence,
    "metric": MetricEvidence,
    "trace": TraceEvidence,
}

_PARSER: dict[Modality, Callable[[IngestBundle], dict]] = {
    "log": parse_for_log_agent,
    "metric": parse_for_metric_agent,
    "trace": parse_for_trace_agent,
}

# 파서 산출물에서 (구간 요약 키, 데이터 본문 키)
_DATA_KEYS: dict[Modality, tuple[str, str]] = {
    "log": ("log_intervals", "logs"),
    "metric": ("metric_intervals", "metrics"),
    "trace": ("trace_intervals", "traces"),
}


def build_user_message(modality: Modality, parsed: dict) -> str:
    """파서 산출물을 user 메시지로 직렬화. 데이터 본문은 절단 상한 적용(최후 방어선)."""
    intervals_key, data_key = _DATA_KEYS[modality]
    data = truncate_input(parsed[data_key], trigger_time=parsed["trigger_time"])
    return (
        f"## 분석 대상 모달리티: {modality}\n"
        f"- 윈도: {parsed['window_start']} ~ {parsed['window_end']}\n"
        f"- 트리거 시각: {parsed['trigger_time']}\n"
        f"- 트리거 모달리티: {', '.join(parsed['triggered_by']) or '(없음)'}\n"
        f"\n## 수집 구간 상태 (참고)\n{parsed[intervals_key]}\n"
        f"\n## 데이터 (압축 표현)\n{data}"
    )


def make_modality_agent(modality: Modality, mode: Depth) -> ModalityAgent:
    """모달리티×모드별 에이전트 생성. 반환 코루틴은 `(bundle) → Evidence`."""
    prompt_name = modality if mode == "deep" else "scan"
    model = settings.openai_model_analysis if mode == "deep" else settings.openai_model_light
    effort = "medium" if mode == "deep" else "low"
    schema = _EVIDENCE_SCHEMA[modality]
    parser = _PARSER[modality]

    async def run(bundle: IngestBundle) -> BaseModel:
        parsed = parser(bundle)
        messages = [
            SystemMessage(content=load_prompt(prompt_name)),
            HumanMessage(content=build_user_message(modality, parsed)),
        ]
        llm = make_llm(model, effort).with_structured_output(schema)
        async with llm_limit():
            evidence = await llm.ainvoke(messages)
        # source 표기: 어느 모드가 만든 Evidence인지 리포트·디버깅에서 식별
        if getattr(evidence, "source", None) is None:
            evidence.source = f"{modality}-agent({mode})"
        return evidence

    # 관측·테스트용 메타데이터 (LLM 호출 없이 배선 검증 가능)
    run.modality, run.mode, run.model, run.prompt_name = modality, mode, model, prompt_name
    return run
