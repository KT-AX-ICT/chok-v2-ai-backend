"""LangGraph 오케스트레이션 — router → 모달리티 병렬(deep/scan) → report → assemble.

그래프 구조 (docs/agent-design.md):

    START → router → (log · metric · trace 병렬) → report(+assemble) → END

실패 처리:
  - 모달리티 노드 실패 → "분석 실패" Evidence로 대체하고 완주 (부분 실패)
  - report 실패 → 예외 전파 → job_queue 워커의 재시도/FAILED 경로 (전체 실패)
  - 데이터 0건 모달리티 → LLM 생략, 코드가 "데이터 없음" Evidence 생성

`LlmOrchestrator.run(job_id, bundle) → RcaResult`는 기존 RcaRunner 시그니처와
호환 — job_queue는 무변경. 노드 에이전트는 생성자 주입으로 교체 가능(테스트 대체).
"""

from __future__ import annotations

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from app.agents.modality_agents import ModalityAgent, make_modality_agent
from app.agents.report_llm import ReportAgent, assemble, llm_report
from app.agents.router import Router, llm_router, route_with_guardrails
from app.agents.schemas import MODALITIES, Depth, Modality
from app.schemas.contracts import (
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    RcaResult,
    TraceEvidence,
)

logger = logging.getLogger(__name__)

_EVIDENCE_CLS: dict[Modality, type[BaseModel]] = {
    "log": LogEvidence,
    "metric": MetricEvidence,
    "trace": TraceEvidence,
}

# 모달리티별 (상태 키, 번들 항목 접근자)
_STATE_KEY: dict[Modality, str] = {"log": "log_ev", "metric": "metric_ev", "trace": "trace_ev"}


def _code_evidence(modality: Modality, conclusion: str) -> BaseModel:
    """LLM을 거치지 않고 코드가 만드는 Evidence (데이터 없음 / 분석 실패)."""
    return _EVIDENCE_CLS[modality](conclusion=conclusion, source=f"{modality}-agent(code)")


class RcaState(TypedDict, total=False):
    """그래프 상태 — 모달리티 노드는 서로 다른 키를 갱신하므로 병렬 충돌 없음."""

    job_id: int
    bundle: IngestBundle
    routes: dict[Modality, Depth]
    log_ev: LogEvidence
    metric_ev: MetricEvidence
    trace_ev: TraceEvidence
    result: RcaResult


class LlmOrchestrator:
    """LangGraph 기반 오케스트레이터. 기존 RcaRunner 시그니처(run)를 유지한다."""

    def __init__(
        self,
        router: Router = llm_router,
        agents: dict[tuple[Modality, Depth], ModalityAgent] | None = None,
        report_agent: ReportAgent = llm_report,
    ) -> None:
        self._router = router
        self._agents = agents or {
            (m, d): make_modality_agent(m, d) for m in MODALITIES for d in ("deep", "scan")
        }
        self._report_agent = report_agent
        self._graph = self._build_graph()

    # ------------------------------------------------------------- 노드

    async def _router_node(self, state: RcaState) -> dict:
        routes = await route_with_guardrails(state["bundle"], self._router)
        return {"routes": routes}

    def _make_modality_node(self, modality: Modality):
        items_of = {
            "log": lambda b: b.logs,
            "metric": lambda b: b.metrics,
            "trace": lambda b: b.traces,
        }[modality]

        async def node(state: RcaState) -> dict:
            bundle = state["bundle"]
            key = _STATE_KEY[modality]
            if not items_of(bundle):
                # 데이터 0건 — LLM 생략 (가드레일)
                return {key: _code_evidence(modality, f"{modality} 데이터 없음 — 분석 생략")}
            mode = state["routes"][modality]
            try:
                evidence = await self._agents[(modality, mode)](bundle)
                return {key: evidence}
            except Exception as exc:
                # 부분 실패 — 가짜 분석으로 대체하지 않고 실패를 정직하게 전달
                logger.exception("job %s: %s %s 분석 실패", state.get("job_id"), modality, mode)
                return {key: _code_evidence(modality, f"분석 실패 — {exc}")}

        return node

    async def _report_node(self, state: RcaState) -> dict:
        # report 실패는 전파 — 워커의 재시도/FAILED 경로(전체 실패)
        draft = await self._report_agent(
            state["bundle"], state["log_ev"], state["metric_ev"], state["trace_ev"]
        )
        result = assemble(draft, state["log_ev"], state["metric_ev"], state["trace_ev"])
        return {"result": result}

    # ------------------------------------------------------------- 조립

    def _build_graph(self):
        g = StateGraph(RcaState)
        g.add_node("router", self._router_node)
        for m in MODALITIES:
            g.add_node(m, self._make_modality_node(m))
        g.add_node("report", self._report_node)

        g.add_edge(START, "router")
        for m in MODALITIES:
            g.add_edge("router", m)
        g.add_edge(list(MODALITIES), "report")  # join: 3종 Evidence 수집 후 진행
        g.add_edge("report", END)
        return g.compile()

    async def run(self, job_id: int, bundle: IngestBundle) -> RcaResult:
        state = await self._graph.ainvoke({"job_id": job_id, "bundle": bundle})
        result: RcaResult = state["result"]
        logger.info("job %s RCA 종합 완료 (service=%s)", job_id, result.service)
        return result
