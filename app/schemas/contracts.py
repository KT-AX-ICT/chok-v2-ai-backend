"""[초안 - 예지 확인 전, 확정 아님]

I5(모달리티 에이전트) 공동 작업 계약 제안. 가희가 초안 작성.
확인해줬으면 하는 것:
  1. TraceEvidence.origin_service - trace 에이전트가 evidence에서 진원 서비스를 뽑아
     여기 채워주면, 종합 에이전트가 이 값을 대표 service(Q-007)로 승격시킬 계획.
     이 필드명/위치 괜찮은지?
  2. IngestBundle 구조가 SDK 번들 계약(api-spec 3.1)과 맞는지 다시 확인 필요.
  3. 전체적으로 바꾸고 싶은 부분 있으면 편하게 갈아엎어도 됨 - 아직 아무것도
     안 정해진 상태.

동의되면 그때 커밋/push 예정. 그 전까진 가희 로컬에만 있음.

----------------------------------------------------------

CHOK Phase 2 - 에이전트 I/O 계약 (초안 v0.1, 2026-07-13).

입력  = /ingest 번들 (SDK가 보냄, api-spec 3.1)
출력  = detail 5키 (프론트 상세 탭과 1:1, api-spec 2.4)

계약 원칙 (문서에서 이미 잠긴 것):
- detail 5키(rca, summary, evidence, impact, actions) 이름/존재는 고정. 프론트가 키로 화면을 그림.
- 필수 최소선만 required. optional 필드는 못 채우면 None -> 직렬화 시 키 생략(null 금지).
- type, severity, service = LLM 판정 (트리거 아님). service는 evidence origin에서 채움(Q-007).
- LLM 입력 정제: title, bundle_id, 파일명은 프롬프트에서 제외 (정답 유출 방지).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ModalityItem(BaseModel):
    """log/metric/trace 공통 아이템. raw는 string (D-021)."""
    timestamp: str
    service: str = ""
    raw: str


class Window(BaseModel):
    start: str
    end: str


class TriggerInfo(BaseModel):
    """트리거 최소 정보만 (D-021).
    triggered_by 값은 모달리티 종류만 허용 — 서비스명/signal 제외 (정답 유출 방지, D-020).
    """
    trigger_time: str
    triggered_by: list[Literal["log", "metric", "trace"]] = Field(default_factory=list)


class ModalityInterval(BaseModel):
    """모달리티별 파일 구간 메타데이터."""
    fileName: str = ""
    start: str | None = None
    end: str | None = None
    status: str | None = None
    present: str | None = None


class ModalityDetail(BaseModel):
    intervals: list[ModalityInterval] = Field(default_factory=list)


class ModalityInfo(BaseModel):
    """수집된 각 모달리티의 파일·구간 정보 (에이전트 컨텍스트용)."""
    log: ModalityDetail = Field(default_factory=ModalityDetail)
    metric: ModalityDetail = Field(default_factory=ModalityDetail)
    trace: ModalityDetail = Field(default_factory=ModalityDetail)


class IngestBundle(BaseModel):
    """수집기 → FastAPI 번들. 에이전트 3종의 공통 입력."""
    bundle_version: str = "1.0"
    window: Window
    trigger_info: TriggerInfo
    modality_info: ModalityInfo = Field(default_factory=ModalityInfo)
    logs: list[ModalityItem] = Field(default_factory=list)
    metrics: list[ModalityItem] = Field(default_factory=list)
    traces: list[ModalityItem] = Field(default_factory=list)


class LogLine(BaseModel):
    timestamp: str | None = None
    level: str | None = None
    msg: str | None = None


class TraceSpan(BaseModel):
    traceId: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    duration: int | None = None
    status: str | None = None
    model_config = {"populate_by_name": True}


class MetricItem(BaseModel):
    label: str | None = None
    value: str | None = None
    threshold: str | None = None
    exceeded: bool | None = None


class LogEvidence(BaseModel):
    conclusion: str
    source: str | None = None
    lines: list[LogLine] | None = None


class TraceEvidence(BaseModel):
    """[예지 확인 필요] - 이예지 담당 영역."""
    conclusion: str
    source: str | None = None
    spans: list[TraceSpan] | None = None
    origin_service: str | None = None


class MetricEvidence(BaseModel):
    conclusion: str
    source: str | None = None
    items: list[MetricItem] | None = None


class Rca(BaseModel):
    rootCause: str
    propagation: str
    confidence: int | None = None


class Summary(BaseModel):
    highlight: str
    chips: list[str] | None = None
    errorTags: list[str] | None = None
    neutralTags: list[str] | None = None


class Evidence(BaseModel):
    log: LogEvidence
    trace: TraceEvidence
    metric: MetricEvidence


class Affected(BaseModel):
    service: str
    errors: int | None = None
    type: str | None = None


class ImpactMetric(BaseModel):
    label: str | None = None
    value: str | None = None


class Impact(BaseModel):
    affected: list[Affected]
    metrics: list[ImpactMetric] | None = None


class Actions(BaseModel):
    steps: list[str]
    recovery: str | None = None


class ReportDetail(BaseModel):
    """result JSON 그대로. 5키 존재 고정, 내부 optional 생략."""
    rca: Rca
    summary: Summary
    evidence: Evidence
    impact: Impact
    actions: Actions


class RcaResult(BaseModel):
    """종합 에이전트 최종 산출 = PATCH DONE 바디 재료."""
    type: str
    severity: str
    service: str
    detail: ReportDetail


if __name__ == "__main__":
    result = RcaResult(
        type="Code_Stop", severity="HIGH", service="media-service",
        detail=ReportDetail(
            rca=Rca(rootCause="media-service 무응답 -> compose 대기 폭발",
                    propagation="media-service -> compose-post -> nginx"),
            summary=Summary(highlight="media-service가 종료되어 로그 침묵"),
            evidence=Evidence(
                log=LogEvidence(conclusion="Failed to connect media-service-client 다수"),
                trace=TraceEvidence(conclusion="compose_post_client 16초 블록", origin_service="media-service"),
                metric=MetricEvidence(conclusion="CPU 무신호"),
            ),
            impact=Impact(affected=[Affected(service="compose-post-service")]),
            actions=Actions(steps=["media-service 재시작 정책 점검"]),
        ),
    )
    import json
    print(json.dumps(result.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False, indent=2))