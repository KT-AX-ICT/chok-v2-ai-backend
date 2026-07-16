"""Spring 게이트웨이 HTTP 클라이언트.

역할:
  - 스냅샷 번들 저장 위임 (POST /api/bundles)
  - RCA 리포트 저장 위임 (POST /api/reports)
  - Report Agent 완료 시 호출됨; 수집 API 자체는 호출 안 함
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.contracts import IngestBundle, RcaResult


class SpringClient:
    def __init__(self) -> None:
        self._base = settings.spring_base_url.rstrip("/")

    async def save_bundle(self, job_id: int, bundle: IngestBundle) -> None:
        payload = {"jobId": job_id, **bundle.model_dump()}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self._base}/api/internal/reports", json=payload)
            resp.raise_for_status()

    async def save_report(self, job_id: int, result: RcaResult) -> None:
        payload = {"jobId": job_id, **result.model_dump(by_alias=True, exclude_none=True)}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self._base}/api/reports", json=payload)
            resp.raise_for_status()


spring_client = SpringClient()
