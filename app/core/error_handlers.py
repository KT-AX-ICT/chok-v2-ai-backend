"""요청 스키마 불일치(422)를 서버 로그에 남기는 예외 핸들러.

FastAPI 기본 동작은 필드 정보를 **422 응답 본문에만** 담는다. 서버 쪽에는 access log의
`422 Unprocessable Entity` 한 줄만 남으므로, 호출자가 응답 본문을 로깅하지 않으면 어느
필드가 틀렸는지 아무도 모른다. /ingest는 SDK가 유일한 호출자이고 본문이 수 MB 번들이라
사후 재현도 어렵다 — 수신 측에도 단서를 남긴다.

응답은 기본 핸들러에 그대로 위임한다. 응답 형식은 SDK의 오류 처리와 맞물린 계약이므로
로깅을 붙이는 변경이 형식을 건드려서는 안 된다.

검증 통과 요청은 [app/api/ingest.py](../api/ingest.py)가 수신 로그를 남긴다 — 그 로그는
검증 이후에 실행되므로 실패 요청은 여기서만 흔적이 생긴다.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response

from app.core.schema_errors import summarize_validation_errors

logger = logging.getLogger(__name__)


async def log_request_validation_error(
    request: Request, exc: RequestValidationError
) -> Response:
    """422 사유를 필드 단위로 남기고, 응답 생성은 FastAPI 기본 핸들러에 위임."""
    # 본문 크기는 헤더 값을 그대로 쓴다(재직렬화 비용 0) — 대용량 요청이 원인인지 판별용.
    logger.warning(
        "요청 스키마 불일치 422: %s %s (본문 %s bytes) — %s",
        request.method,
        request.url.path,
        request.headers.get("content-length", "?"),
        summarize_validation_errors(exc.errors()),
    )
    return await request_validation_exception_handler(request, exc)


def register_error_handlers(app: FastAPI) -> None:
    """앱에 예외 핸들러를 부착. 기동 시 한 번 호출(app/main.py)."""
    app.add_exception_handler(RequestValidationError, log_request_validation_error)
