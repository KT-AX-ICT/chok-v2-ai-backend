from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# 커넥션 복원력 옵션. 하나라도 빠지면 죽은 커넥션 재사용이나 무한 대기로 되돌아가므로
# 상수로 드러내고 tests/test_db_session.py가 값과 반영 여부를 함께 검증한다.
ENGINE_RESILIENCE_KWARGS = {
    # 풀에서 커넥션을 꺼낼 때마다 가벼운 생존 확인. 유휴 중 서버가 끊어버린 커넥션을
    # 조용히 새것으로 교체하므로 호출부는 죽은 커넥션 오류를 보지 않는다.
    # (쿼리마다가 아니라 체크아웃 시점에만 확인하므로 비용이 작다.)
    "pool_pre_ping": True,
    # MySQL이 wait_timeout으로 끊기 전에 선제 재생성.
    "pool_recycle": settings.db_pool_recycle_seconds,
    # 타임아웃이 없으면 서버가 조용히 끊었을 때 예외 없이 무한 대기한다. 그러면 ingest의
    # 503 경로가 아예 작동하지 못해 SDK가 응답을 못 받는다 — 대기를 예외로 바꾸는 장치.
    "connect_args": {"connect_timeout": settings.db_connect_timeout_seconds},
}

engine = create_async_engine(
    settings.async_db_url, echo=False, **ENGINE_RESILIENCE_KWARGS
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
