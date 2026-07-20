"""테스트 공용 픽스처.

MySQL 없이 실행 가능하도록 SQLite in-memory DB 사용.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.session import Base, get_db
from app.main import app
from app.services.job_queue import job_queue

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def _dummy_openai_key(monkeypatch):
    """make_llm()의 ChatOpenAI는 생성 시점에 키 존재를 요구한다.

    테스트는 LLM을 실호출하지 않지만(전부 모킹/fake) 팩토리가 객체를 만들긴 하므로,
    키가 비어 있을 때만 더미 키를 주입해 환경변수 없이도 그린이 되게 한다.
    실 키가 있으면(스모크) 덮어쓰지 않는다.
    """
    if not settings.openai_api_key:
        monkeypatch.setattr(settings, "openai_api_key", "sk-test")


@pytest.fixture()
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest.fixture()
async def client(db_engine):
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db() -> AsyncSession:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # 큐 워커(_process)도 테스트 DB 사용
    original_factory = job_queue._session_factory
    job_queue._session_factory = session_factory

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    job_queue._session_factory = original_factory
    app.dependency_overrides.clear()
