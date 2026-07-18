"""에이전트 내부 스키마 — LLM structured output 전용.

외부 계약(contracts.py)과 분리한다: 여기 스키마는 그래프 내부에서만 흐르고,
최종 산출물은 assemble 단계에서 RcaResult(계약)로 변환된다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.contracts import Actions, Impact, Rca, Summary

Modality = Literal["log", "metric", "trace"]
Depth = Literal["deep", "scan"]

MODALITIES: tuple[Modality, ...] = ("log", "metric", "trace")


class PlanDecision(BaseModel):
    """planner 출력 — 모달리티별 deep/scan 결정 + 한 줄 사유."""

    log: Depth
    metric: Depth
    trace: Depth
    reason: str = Field(description="결정 사유 한 줄")


class ReportDraft(BaseModel):
    """report 에이전트 출력 — RcaResult에서 detail.evidence를 제외한 초안.

    evidence는 LLM이 재복사하지 않고 assemble 단계(코드)가 모달리티 산출물을
    그대로 주입한다 (토큰 절약 + 원본 보존).
    """

    type: str = Field(description="장애 유형 (예: Code_Stop, Svc_Kill, Unknown)")
    severity: str = Field(description="HIGH / MID / LOW")
    service: str = Field(description="진원 서비스명")
    rca: Rca
    summary: Summary
    impact: Impact
    actions: Actions
