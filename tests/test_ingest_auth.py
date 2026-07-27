"""/ingest API 키 인증 — 라우터 레벨 검증 동작.

각 테스트가 monkeypatch로 키를 직접 설정한다(conftest의 autouse가 기본을 비워두므로,
픽스처 적용 순서에 의존하지 않게 명시적으로 덮어쓴다).
"""

from httpx import AsyncClient

from app.core.config import settings
from tests.test_ingest import BUNDLE_PAYLOAD

KEY = "test-key-abcdef"


async def test_missing_header_returns_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_keys", KEY)
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    assert resp.status_code == 401


async def test_wrong_key_returns_401(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_keys", KEY)
    resp = await client.post(
        "/ingest", json=BUNDLE_PAYLOAD, headers={"X-API-Key": "wrong-key"}
    )
    assert resp.status_code == 401


async def test_valid_key_accepted(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_keys", KEY)
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD, headers={"X-API-Key": KEY})
    assert resp.status_code == 201


async def test_second_key_in_list_accepted(client: AsyncClient, monkeypatch):
    """무중단 교체 — old,new를 동시에 허용하는 구간이 있어야 SDK를 순차 갱신할 수 있다."""
    monkeypatch.setattr(settings, "ingest_api_keys", f"old-key, {KEY}")
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD, headers={"X-API-Key": KEY})
    assert resp.status_code == 201


async def test_no_key_configured_allows_request(client: AsyncClient, monkeypatch):
    """단계적 전환 — 키 미설정이면 헤더 없이도 통과한다."""
    monkeypatch.setattr(settings, "ingest_api_keys", "")
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    assert resp.status_code == 201


async def test_job_status_also_protected(client: AsyncClient, monkeypatch):
    """GET도 보호 대상 — result에 RCA 전문이 실리고 job_id가 순차라 열거가 쉽다.

    인증이 핸들러보다 먼저 도므로 없는 id여도 404가 아니라 401이다.
    """
    monkeypatch.setattr(settings, "ingest_api_keys", KEY)
    resp = await client.get("/ingest/99999")
    assert resp.status_code == 401


async def test_health_not_protected(client: AsyncClient, monkeypatch):
    """LB·readiness probe가 무인증으로 접근해야 한다 — health는 별도 라우터라 제외."""
    monkeypatch.setattr(settings, "ingest_api_keys", KEY)
    resp = await client.get("/health")
    assert resp.status_code == 200
