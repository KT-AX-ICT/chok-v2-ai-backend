"""router — 메타데이터만으로 모달리티별 deep/scan 결정 + 코드 가드레일.

가드레일(코드 강제, LLM 판단보다 우선):
  (1) triggered_by 포함 모달리티는 무조건 deep — 승격 전용, 강등 근거로 쓰지 않음
  (2) router 호출 실패 시 전 모달리티 deep (안전 기본값)
  * 데이터 0건 모달리티의 LLM 생략은 그래프 노드가 담당(호출 자체가 없음)
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.llm import llm_limit, make_llm
from app.agents.prompts import load_prompt
from app.agents.schemas import MODALITIES, Depth, Modality, RouteDecision
from app.core.config import settings
from app.schemas.contracts import IngestBundle

logger = logging.getLogger(__name__)

Router = Callable[[IngestBundle], Awaitable[RouteDecision]]


def build_router_message(bundle: IngestBundle) -> str:
    """raw 없이 메타데이터만 — 건수·구간 상태·트리거. 수백 토큰 수준."""

    def interval_lines(intervals) -> str:
        if not intervals:
            return "(없음)"
        return "; ".join(
            f"{iv.start or '?'}~{iv.end or '?'} status={iv.status or iv.present or 'ok'}"
            for iv in intervals
        )

    info = bundle.modality_info
    counts = {"log": len(bundle.logs), "metric": len(bundle.metrics), "trace": len(bundle.traces)}
    return (
        f"- 윈도: {bundle.window.start} ~ {bundle.window.end}\n"
        f"- 트리거 시각: {bundle.trigger_info.trigger_time}\n"
        f"- 트리거 모달리티(triggered_by): {', '.join(bundle.trigger_info.triggered_by) or '(없음)'}\n"
        f"- 건수: log={counts['log']}, metric={counts['metric']}, trace={counts['trace']}\n"
        f"- log 구간: {interval_lines(info.log.intervals)}\n"
        f"- metric 구간: {interval_lines(info.metric.intervals)}\n"
        f"- trace 구간: {interval_lines(info.trace.intervals)}"
    )


async def llm_router(bundle: IngestBundle) -> RouteDecision:
    """기본 router — nano 모델 + structured output."""
    messages = [
        SystemMessage(content=load_prompt("router")),
        HumanMessage(content=build_router_message(bundle)),
    ]
    llm = make_llm(settings.openai_model_light, "low").with_structured_output(RouteDecision)
    async with llm_limit():
        return await llm.ainvoke(messages)


async def route_with_guardrails(
    bundle: IngestBundle, router: Router = llm_router
) -> dict[Modality, Depth]:
    """router 호출 + 가드레일 적용 → 모달리티별 최종 deep/scan."""
    try:
        decision = await router(bundle)
        routes: dict[Modality, Depth] = {m: getattr(decision, m) for m in MODALITIES}
        logger.info("router 결정: %s (사유: %s)", routes, decision.reason)
    except Exception:
        # 가드레일 (2) — router 실패는 전 모달리티 deep으로 안전 폴백
        logger.exception("router 호출 실패 — 전 모달리티 deep 폴백")
        routes = {m: "deep" for m in MODALITIES}

    # 가드레일 (1) — triggered_by는 승격 전용
    for m in bundle.trigger_info.triggered_by:
        routes[m] = "deep"
    return routes
