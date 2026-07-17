"""Spring 게이트웨이 내부 API 클라이언트.

신규 구조(7/10, api-spec §5.1): 분석 완료 시 **번들 + 분석결과를 한 번의 POST**로
저장 위임. (기존 번들/리포트 분리 저장·PATCH 방식 폐기.)

엔드포인트: POST /api/internal/reports
Spring 계약은 **camelCase**로 통일 — 페이로드 전체를 by_alias=True로 직렬화.
페이로드(api-spec §5.1):
  - 번들 필드: /ingest 계약(camelCase) — bundleVersion·window·triggerInfo·3종
  - status:  D-022 단순화로 항상 "DONE"
  - severity: RcaResult.severity (HIGH/MID/LOW, NULL 허용)
  - result:  detail 5키(rca·summary·evidence·impact·actions), camelCase

[미결 — api-spec §6 쟁점 3] type·service의 전달 경로(최상위 vs result 내부)는 미확정 →
현재 페이로드에서 제외. 확정되면 추가.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.contracts import IngestBundle, RcaResult


class SpringClient:
    def __init__(self) -> None:
        self._base = settings.spring_base_url.rstrip("/")

    async def save_result(
        self, job_id: int, bundle: IngestBundle, result: RcaResult
    ) -> None:
        """분석 완료된 번들 + 분석결과를 한 번에 저장 위임 (api-spec §5.1)."""
        payload = {
            # Spring 계약은 camelCase — by_alias=True로 bundleVersion·triggerInfo 등 출력
            **bundle.model_dump(by_alias=True),
            "status": "DONE",
            "severity": result.severity,
            "result": result.detail.model_dump(by_alias=True, exclude_none=True),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/internal/reports", json=payload
            )
            resp.raise_for_status()

    async def save_failure(
        self, job_id: int, bundle: IngestBundle, error: str
    ) -> None:
        """RCA 최종 실패(1회 재시도 후) 폴백 — 번들 + 실패 사유를 Spring에 전송.

        검증 통과 산출물이 없으므로 result는 보내지 않고, status=FAILED와 error만 싣는다.
        [미결 — api-spec §6] status=FAILED 리포트 수신 계약은 미확정(§5.1은 D-022로
        status를 DONE으로 단순화). 아래 페이로드는 잠정안 — 계약 확정 시 조정.
        """
        payload = {
            **bundle.model_dump(by_alias=True),
            "status": "FAILED",
            "error": error,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/internal/reports", json=payload
            )
            resp.raise_for_status()


spring_client = SpringClient()
