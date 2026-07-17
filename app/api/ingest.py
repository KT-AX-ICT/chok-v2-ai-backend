from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestJob
from app.db.session import get_db
from app.schemas.contracts import IngestBundle
from app.services.job_queue import job_queue

router = APIRouter(prefix="/ingest", tags=["ingest"])


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
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    # 수집기에 즉시 응답하기 위해 job만 기록하고 실제 RCA는 큐 워커에 위임한다.
    job = IngestJob(status="PENDING", bundle=bundle.model_dump())
    db.add(job)
    await db.commit()
    await db.refresh(job)

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
