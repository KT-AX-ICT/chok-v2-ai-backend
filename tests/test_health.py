"""헬스 체크 + 무키 fail-fast 테스트."""

import logging

import pytest
from httpx import AsyncClient


async def test_health_ok_when_db_reachable(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_503_when_db_down(client: AsyncClient, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(self, *args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(AsyncSession, "execute", boom)
    resp = await client.get("/health")
    assert resp.status_code == 503


async def test_lifespan_fails_fast_without_api_key(monkeypatch):
    """실서버 기동(lifespan)은 키가 없으면 거부한다. (테스트는 lifespan 미실행이라 무영향.)"""
    from app.core.config import settings
    from app.main import app, lifespan

    monkeypatch.setattr(settings, "openai_api_key", "")  # conftest 더미 키를 무키로 되돌림
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            async with lifespan(app):
                pass
    finally:
        root.handlers[:] = saved  # setup_logging(force=True)로 갈린 핸들러 원복
        root.setLevel(level)
