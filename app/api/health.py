"""헬스 체크 — DB 연결 상태 노출.

기동은 무DB여도 허용(소프트)하므로, 배포의 readiness probe·로드밸런서가 이 엔드포인트로
DB 준비 여부를 확인한다. SELECT 1이 성공하면 200, 실패하면 503.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(tags=["health"])

logger = logging.getLogger(__name__)


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    """DB 연결 확인. 되면 ok, 안 되면 503 — readiness 판단 근거."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        logger.warning("health: DB 연결 실패", exc_info=True)
        raise HTTPException(status_code=503, detail="db unavailable")
    return {"status": "ok"}
