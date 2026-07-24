from httpx import AsyncClient

BUNDLE_PAYLOAD = {
    "bundle_version": "1.0",
    "window": {
        "start": "2026-01-15T10:00:00Z",
        "end": "2026-01-15T10:03:00Z",
    },
    "trigger_info": {
        "trigger_time": "2026-01-15T10:01:30Z",
        "triggered_by": ["metric", "log"],
    },
    "modality_info": {
        "log": {
            "intervals": [
                {"fileName": "UserService_.log", "status": "missing"},
                {"fileName": "NginxThrift_.log", "start": "2026-01-15T10:01:00Z", "end": "2026-01-15T10:03:00Z", "status": "data", "recordCount": 1, "totalCount": 20},
            ]
        },
        "metric": {"intervals": [{"fileName": "", "start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z", "status": ""}]},
        "trace":  {"intervals": [{"fileName": "", "start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z", "status": ""}]},
    },
    "logs": [
        {"timestamp": "2026-01-15T10:01:00Z", "service": "api-gateway", "raw": "ERROR connect timeout"},
    ],
    "metrics": [
        {"timestamp": "2026-01-15T10:01:00Z", "service": "api-gateway", "raw": "error_rate=0.85"},
    ],
    "traces": [
        {"timestamp": "2026-01-15T10:01:10Z", "service": "api-gateway", "raw": "span: 16000ms TIMEOUT"},
    ],
}


async def test_ingest_returns_201_with_job_id(client: AsyncClient):
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    assert resp.status_code == 201
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], int)


async def test_ingest_job_id_increments(client: AsyncClient):
    resp1 = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    resp2 = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    assert resp1.json()["job_id"] < resp2.json()["job_id"]


async def test_get_job_status_after_ingest(client: AsyncClient):
    ingest_resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    job_id = ingest_resp.json()["job_id"]

    status_resp = await client.get(f"/ingest/{job_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["job_id"] == job_id
    assert body["status"] in {"PENDING", "RUNNING", "DELIVERING", "DONE", "FAILED"}


async def test_get_job_status_not_found(client: AsyncClient):
    resp = await client.get("/ingest/99999")
    assert resp.status_code == 404


async def test_ingest_invalid_bundle_returns_422(client: AsyncClient):
    resp = await client.post("/ingest", json={"bundle_version": "1.0"})
    assert resp.status_code == 422


async def test_ingest_invalid_triggered_by_returns_422(client: AsyncClient):
    """triggered_by 값이 log/metric/trace 외의 값이면 422."""
    bad = {**BUNDLE_PAYLOAD, "trigger_info": {"trigger_time": "2026-01-15T10:01:30Z", "triggered_by": ["error_rate"]}}
    resp = await client.post("/ingest", json=bad)
    assert resp.status_code == 422


async def test_ingest_invalid_timestamp_returns_422(client: AsyncClient):
    """window.start가 ISO-8601 형식이 아니면 422 (Spring 전송 전 FastAPI가 거름)."""
    bad = {
        **BUNDLE_PAYLOAD,
        "window": {"start": "not-a-timestamp", "end": "2026-01-15T10:03:00Z"},
    }
    resp = await client.post("/ingest", json=bad)
    assert resp.status_code == 422


async def test_ingest_db_failure_returns_503(client: AsyncClient, monkeypatch):
    """job 기록 중 DB 오류가 나면 raw 500이 아니라 503으로 정리해 응답한다."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(self, *args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    assert resp.status_code == 503


async def test_ingest_ignores_removed_present_field(client: AsyncClient):
    """SDK가 구형 present를 계속 보내도 422로 막지 않는다(Pydantic extra=ignore)."""
    legacy = {
        **BUNDLE_PAYLOAD,
        "modality_info": {
            "log": {"intervals": [{"fileName": "a.log", "present": "empty"}]},
        },
    }
    resp = await client.post("/ingest", json=legacy)
    assert resp.status_code == 201
    
    
async def test_ingest_stores_light_bundle_and_signals_file(client: AsyncClient, db_engine):
    """무거운 3종은 파일로 빠지고, DB에는 경량 번들 + 파일 이름만 남는다."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.models import IngestJob
    from app.services import bundle_store

    resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)
    job_id = resp.json()["job_id"]

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.job_id == job_id))
        ).scalar_one()

    assert job.signals_path  # 파일 이름이 기록됨
    for key in bundle_store.SIGNAL_KEYS:
        assert key not in job.bundle  # 무거운 배열은 DB에 없음
    assert job.bundle["trigger_info"]["trigger_time"] == "2026-01-15T10:01:30Z"  # 메타는 유지

    restored = await bundle_store.restore_bundle(job.bundle, job.signals_path)
    assert restored.logs[0].raw == "ERROR connect timeout"  # 파일에서 원본 복원


async def test_ingest_empty_modalities_accepted(client: AsyncClient):
    minimal = {
        "bundle_version": "1.0",
        "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        "trigger_info": {"trigger_time": "2026-01-15T10:01:30Z"},
    }
    resp = await client.post("/ingest", json=minimal)
    assert resp.status_code == 201
