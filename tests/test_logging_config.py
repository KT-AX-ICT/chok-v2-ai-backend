"""중앙 로깅 설정 단위 테스트."""

import logging

from app.core.logging_config import setup_logging


def test_setup_logging_applies_level_and_formatter():
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    try:
        setup_logging("DEBUG")
        assert root.level == logging.DEBUG
        assert root.handlers, "루트 핸들러가 있어야 함"
        assert root.handlers[0].formatter is not None
        assert logging.getLogger("httpx").level == logging.WARNING  # 소음 로거 억제
    finally:
        root.handlers[:] = saved
        root.setLevel(saved_level)


def test_setup_logging_defaults_to_settings_level():
    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    try:
        setup_logging()  # settings.log_level 기본 INFO
        assert root.level == logging.INFO
    finally:
        root.handlers[:] = saved
        root.setLevel(saved_level)
