"""중앙 로깅 설정 — 포맷·레벨·시각을 한 곳에서 지정.

앱 기동(lifespan) 시 setup_logging()을 호출한다. 미호출이면 루트 로거 기본값
(WARNING·무포맷)이라 INFO 로그가 보이지 않으므로, 이 설정이 서비스 로그의 기반이다.
"""

from __future__ import annotations

import logging

from app.core.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 서드파티 소음 억제 — 요청마다 INFO를 쏟는 로거는 WARNING으로 낮춘다.
_NOISY = ("httpx", "httpcore")


def setup_logging(level: str | None = None) -> None:
    """루트 로거에 포맷·레벨을 적용(idempotent). level 미지정 시 settings.log_level."""
    resolved = (level or settings.log_level).upper()
    logging.basicConfig(level=resolved, format=_FORMAT, datefmt=_DATEFMT, force=True)
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger(__name__).debug("로깅 설정 완료 (level=%s)", resolved)
