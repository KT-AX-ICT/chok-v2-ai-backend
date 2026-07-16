from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.ingest import router as ingest_router
from app.db.session import init_db
from app.services.job_cleanup import job_cleaner
from app.services.job_queue import job_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception:
        pass  # MySQL 미연결 환경(테스트/로컬)에서도 서버 기동 허용
    # 워커·정리 루프 모두 이벤트 루프 안 asyncio 태스크(논블로킹)
    job_queue.start()
    job_cleaner.start()
    yield
    await job_cleaner.stop()
    await job_queue.stop()


app = FastAPI(title="CHOK v2 AI Backend", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router)
