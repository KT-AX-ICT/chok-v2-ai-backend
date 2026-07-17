"""RCA 오케스트레이터 — 모달리티 분석(병렬) → 종합 리포트.

[갈아끼움 설계] 각 단계 에이전트를 생성자 주입으로 교체할 수 있다. 지금 기본값은
얕은 에이전트(app/agents/*_agent.py)지만, LLM 심층 분석 구현이 나오면 아래 시그니처만
맞춰 `Orchestrator(log_agent=..., report_agent=...)`로 갈아끼우면 된다.
오케스트레이터 본문은 바뀌지 않는다.

async optimization: 3개 모달리티 분석을 asyncio.gather로 병렬 실행.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.agents.log_agent import analyze_log
from app.agents.metric_agent import analyze_metric
from app.agents.report_agent import build_report
from app.agents.trace_agent import analyze_trace
from app.schemas.contracts import (
    IngestBundle,
    LogEvidence,
    MetricEvidence,
    RcaResult,
    TraceEvidence,
)

logger = logging.getLogger(__name__)

# 갈아끼움 계약(시그니처). 실제 LLM 구현은 이 시그니처만 맞추면 주입 가능하다.
LogAgent = Callable[[IngestBundle], Awaitable[LogEvidence]]
MetricAgent = Callable[[IngestBundle], Awaitable[MetricEvidence]]
TraceAgent = Callable[[IngestBundle], Awaitable[TraceEvidence]]
ReportAgent = Callable[
    [IngestBundle, LogEvidence, MetricEvidence, TraceEvidence], Awaitable[RcaResult]
]


class Orchestrator:
    """모달리티 분석기 3종 + report 에이전트를 조립. 각 단계는 주입으로 교체 가능."""

    def __init__(
        self,
        log_agent: LogAgent = analyze_log,
        metric_agent: MetricAgent = analyze_metric,
        trace_agent: TraceAgent = analyze_trace,
        report_agent: ReportAgent = build_report,
    ) -> None:
        self._log_agent = log_agent
        self._metric_agent = metric_agent
        self._trace_agent = trace_agent
        self._report_agent = report_agent

    async def run(self, job_id: int, bundle: IngestBundle) -> RcaResult:
        # 모달리티 분석 병렬 실행(async optimization)
        log_ev, metric_ev, trace_ev = await asyncio.gather(
            self._log_agent(bundle),
            self._metric_agent(bundle),
            self._trace_agent(bundle),
        )
        result = await self._report_agent(bundle, log_ev, metric_ev, trace_ev)
        logger.info("job %s RCA 종합 완료 (service=%s)", job_id, result.service)
        return result


# 기본 오케스트레이터(얕은 에이전트). LLM 구현 교체 시 이 인스턴스나 주입 인자만 바꾼다.
orchestrator = Orchestrator()
