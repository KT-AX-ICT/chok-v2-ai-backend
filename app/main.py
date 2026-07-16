from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.ingest import router as ingest_router
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception:
        pass  # MySQL 미연결 환경(테스트/로컬)에서도 서버 기동 허용
    yield


app = FastAPI(title="CHOK v2 AI Backend", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router)
