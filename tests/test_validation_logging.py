"""요청 스키마 불일치(422) 서버 로그 — 필드 경로가 로그에 남는지.

SDK가 422 응답 본문을 로깅하지 않으면 서버 쪽에 단서가 없다는 공백을 막는 장치라,
"로그가 남는다"는 것 자체가 계약이다.
"""

import logging

from httpx import AsyncClient

from tests.test_ingest import BUNDLE_PAYLOAD


async def test_missing_field_logged_with_path(client: AsyncClient, caplog):
    with caplog.at_level(logging.WARNING):
        resp = await client.post("/ingest", json={"bundle_version": "1.0"})

    assert resp.status_code == 422
    text = caplog.text
    assert "요청 스키마 불일치" in text
    assert "body.window[missing]" in text
    # 필드 경로는 계약 별칭(camelCase) 표기 — SDK가 snake_case로 보내도 동일하다.
    # populate_by_name=True라 입력은 양쪽을 받지만 loc은 별칭으로 나오며, 이는 422 응답
    # 본문의 loc과 같은 표기라 응답·로그 대조가 된다.
    assert "body.triggerInfo[missing]" in text


async def test_log_includes_method_and_path(client: AsyncClient, caplog):
    with caplog.at_level(logging.WARNING):
        await client.post("/ingest", json={"bundle_version": "1.0"})

    assert "POST /ingest" in caplog.text


async def test_bad_timestamp_logged_with_field_path(client: AsyncClient, caplog):
    """형식 위반은 필드 경로 + 사유까지 — 배열 원소면 인덱스도 드러난다."""
    bad = {
        **BUNDLE_PAYLOAD,
        "logs": [{"timestamp": "2026/01/15 10:01", "service": "api-gateway", "raw": "x"}],
    }
    with caplog.at_level(logging.WARNING):
        resp = await client.post("/ingest", json=bad)

    assert resp.status_code == 422
    assert "body.logs.0.timestamp" in caplog.text


async def test_response_body_unchanged(client: AsyncClient):
    """응답 형식은 FastAPI 기본 그대로 — SDK의 422 처리와 계약이 깨지면 안 된다."""
    resp = await client.post("/ingest", json={"bundle_version": "1.0"})
    body = resp.json()
    assert isinstance(body["detail"], list)
    assert {"type", "loc", "msg"} <= set(body["detail"][0])


async def test_valid_request_logs_no_mismatch(client: AsyncClient, caplog):
    with caplog.at_level(logging.WARNING):
        resp = await client.post("/ingest", json=BUNDLE_PAYLOAD)

    assert resp.status_code == 201
    assert "요청 스키마 불일치" not in caplog.text
