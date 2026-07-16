from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestJob
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.contracts import IngestBundle

router = APIRouter(prefix="/ingest", tags=["ingest"])

# 테스트에서 교체 가능하도록 모듈 변수로 분리
_session_factory: Callable = AsyncSessionLocal


class IngestResponse(BaseModel):
    job_id: int


class JobStatusResponse(BaseModel):
    job_id: int
    status: str
    result: dict | None = None


@router.post("", response_model=IngestResponse, status_code=201)
async def ingest_bundle(
    bundle: IngestBundle,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> IngestResponse:
    job = IngestJob(status="PENDING", bundle=bundle.model_dump())
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(_run_rca, job.job_id, bundle)

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
    return JobStatusResponse(job_id=job.job_id, status=job.status, result=job.result)


async def _run_rca(job_id: int, bundle: IngestBundle) -> None:
    """Spring 위임 + RCA 오케스트레이터 킥오프 (추후 orchestrator.py 연결)."""
    from app.services.spring_client import spring_client

    async with _session_factory() as db:
        result = await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        job = result.scalar_one_or_none()
        if job is None:
            return

        job.status = "RUNNING"
        await db.commit()

        try:
            await spring_client.save_bundle(job_id, bundle)
            # TODO: orchestrator.run(job_id, bundle) 로 교체
            job.status = "DONE"
        except Exception:
            job.status = "FAILED"
        finally:
            await db.commit()
