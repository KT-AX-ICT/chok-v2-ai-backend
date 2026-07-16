import pytest
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
                {"fileName": "NginxThrift_.log", "start": "2026-01-15T10:01:00Z", "end": "2026-01-15T10:03:00Z", "present": "empty"},
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
    assert body["status"] in {"PENDING", "RUNNING", "DONE", "FAILED"}


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


async def test_ingest_empty_modalities_accepted(client: AsyncClient):
    minimal = {
        "bundle_version": "1.0",
        "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
        "trigger_info": {"trigger_time": "2026-01-15T10:01:30Z"},
    }
    resp = await client.post("/ingest", json=minimal)
    assert resp.status_code == 201
