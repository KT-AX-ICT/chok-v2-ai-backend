import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.db.session import init_db
from app.services.delivery_reconciler import delivery_reconciler
from app.services.job_cleanup import job_cleaner
from app.services.job_queue import job_queue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 실서버 fail-fast — 무키 기동은 테스트 전용(conftest 더미 키). 실배포는 키 필수.
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY 미설정 — 실서버 기동 거부")
        raise RuntimeError(
            "OPENAI_API_KEY 미설정 — 실서버 기동 거부 (무키 기동은 테스트 전용)"
        )
    try:
        await init_db()
    except Exception:
        # DB는 소프트 — 미연결이어도 기동 허용(원인은 남김). readiness는 /health로 판단.
        logger.warning("init_db 실패 — DB 미연결 상태로 기동 계속", exc_info=True)
    # 워커·정리 루프 모두 이벤트 루프 안 asyncio 태스크(논블로킹)
    job_queue.start()
    job_cleaner.start()
    delivery_reconciler.start()
    yield
    await delivery_reconciler.stop()
    await job_cleaner.stop()
    await job_queue.stop()


app = FastAPI(title="CHOK v2 AI Backend", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(health_router)
