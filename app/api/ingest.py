import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestJob
from app.db.session import get_db
from app.schemas.contracts import IngestBundle
from app.services.job_queue import job_queue

router = APIRouter(prefix="/ingest", tags=["ingest"])

logger = logging.getLogger(__name__)


class IngestResponse(BaseModel):
    job_id: int


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    result: dict | None = None
    error: str | None = None


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_bundle(
    bundle: IngestBundle,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    # 본문 바이트 크기. 대용량 요청이 저장 실패의 원인인지(MySQL max_allowed_packet 초과 등)
    # 사후 판별하려면 항목 개수만으로는 부족하다. 재직렬화 비용 없이 헤더 값을 그대로 쓴다.
    body_bytes = request.headers.get("content-length", "?")

    # 수집기에 즉시 응답하기 위해 job만 기록하고 실제 RCA는 큐 워커에 위임한다.
    try:
        job = IngestJob(status="PENDING", bundle=bundle.model_dump())
        db.add(job)
        await db.commit()
        await db.refresh(job)
    except Exception:
        await db.rollback()
        logger.exception(
            "ingest: job 기록 실패 (트리거 %s, 본문 %s bytes, log=%d metric=%d trace=%d)",
            bundle.trigger_info.trigger_time,
            body_bytes,
            len(bundle.logs),
            len(bundle.metrics),
            len(bundle.traces),
        )
        raise HTTPException(status_code=503, detail="failed to persist job")

    logger.info(
        "ingest 수신: job %s (트리거 %s, 본문 %s bytes, log=%d metric=%d trace=%d)",
        job.job_id,
        bundle.trigger_info.trigger_time,
        body_bytes,
        len(bundle.logs),
        len(bundle.metrics),
        len(bundle.traces),
    )

    # 번들은 DB에 저장했으므로 큐에는 job_id만 넘긴다(워커가 DB에서 복원).
    await job_queue.enqueue(job.job_id)

    return IngestResponse(job_id=job.job_id)


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: int,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(
        job_id=job.job_id, status=job.status, result=job.result, error=job.error
    )
