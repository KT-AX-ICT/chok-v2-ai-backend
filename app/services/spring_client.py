"""Spring 게이트웨이 내부 API 클라이언트.

신규 구조(7/10, api-spec §5.1): 분석 완료 시 **번들 + 분석결과를 한 번의 POST**로
저장 위임. (기존 번들/리포트 분리 저장·PATCH 방식 폐기.)

엔드포인트: POST /api/internal/reports
Spring 계약은 **camelCase**로 통일 — 페이로드 전체를 by_alias=True로 직렬화.
페이로드(api-spec §5.1, docs/spring-contract.md):
  - 번들 필드: /ingest 계약(camelCase) — bundleVersion·window·triggerInfo·3종
  - status:  "DONE"(성공) / "FAILED"(에이전트 분석 실패)
  - severity: RcaResult.severity (HIGH/MID/LOW, NULL 허용)
  - result:  type·service + detail 5키(rca·summary·evidence·impact·actions), camelCase

계약 반영:
  - type·service는 result 내부에 포함 (최상위 아님 — Q-007 경로 확정)
  - logs/metrics/traces의 raw는 전송 직전 모달리티별 JSON으로 정규화
    (raw_normalizer). Spring이 DB 저장 후 조회 시 역직렬화해 evidence 배열을 채움.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.contracts import IngestBundle, RcaResult
from app.services.raw_normalizer import normalize_payload_signals


class SpringClient:
    def __init__(self) -> None:
        self._base = settings.spring_base_url.rstrip("/")

    # ---------------------------------------------------------- 페이로드 조립

    @staticmethod
    def _result_payload(bundle: IngestBundle, result: RcaResult) -> dict:
        """분석 완료 페이로드. type·service는 result 내부에 두고, raw는 정규화."""
        detail = result.detail.model_dump(by_alias=True, exclude_none=True)
        payload = {
            **bundle.model_dump(by_alias=True),
            "status": "DONE",
            "severity": result.severity,
            "result": {"type": result.type, "service": result.service, **detail},
        }
        normalize_payload_signals(payload)
        return payload

    @staticmethod
    def _failure_payload(bundle: IngestBundle, reason: str) -> dict:
        """실패 페이로드. result 없이 status=FAILED + reason(계약: error 아님)."""
        payload = {
            **bundle.model_dump(by_alias=True),
            "status": "FAILED",
            "reason": reason,
        }
        normalize_payload_signals(payload)
        return payload

    # ---------------------------------------------------------- 전송

    async def _post(self, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self._base}/api/internal/reports", json=payload)
            resp.raise_for_status()

    async def save_result(
        self, job_id: int, bundle: IngestBundle, result: RcaResult
    ) -> None:
        """분석 완료된 번들 + 분석결과를 한 번에 저장 위임 (api-spec §5.1)."""
        await self._post(self._result_payload(bundle, result))

    async def save_failure(
        self, job_id: int, bundle: IngestBundle, error: str
    ) -> None:
        """RCA 최종 실패(1회 재시도 후) 폴백 — 번들 + 실패 사유를 Spring에 전송.

        검증 통과 산출물이 없으므로 result는 보내지 않고, status=FAILED와 reason만 싣는다.
        """
        await self._post(self._failure_payload(bundle, error))


spring_client = SpringClient()
