"""DB 엔진 커넥션 복원력 설정 회귀 테스트.

이 옵션들이 빠지면 유휴 중 서버가 끊은 커넥션을 그대로 재사용하거나(죽은 소켓),
타임아웃 없이 무한 대기해 ingest가 503조차 못 내리는 상태로 되돌아간다.

값 자체는 ENGINE_RESILIENCE_KWARGS로 확인하고, 그 값이 실제 엔진에 반영됐는지는
풀 속성으로 확인한다(둘 중 하나만 보면 '선언만 하고 안 넘김'을 놓친다).
"""

from app.core.config import settings
from app.db.session import ENGINE_RESILIENCE_KWARGS, engine


def _pool():
    # AsyncEngine은 내부 동기 엔진의 풀을 그대로 쓴다.
    return engine.sync_engine.pool


def test_pre_ping_declared_and_applied():
    """체크아웃 시 생존 확인 — 죽은 커넥션을 투명 교체하는 핵심 옵션."""
    assert ENGINE_RESILIENCE_KWARGS["pool_pre_ping"] is True
    assert _pool()._pre_ping is True


def test_recycle_declared_and_applied():
    """MySQL wait_timeout보다 짧게 잡아 선제 재생성."""
    assert ENGINE_RESILIENCE_KWARGS["pool_recycle"] == settings.db_pool_recycle_seconds
    assert _pool()._recycle == settings.db_pool_recycle_seconds


def test_connect_timeout_declared():
    """무한 대기를 예외로 바꿔 ingest 503 경로가 작동하게 하는 장치.

    connect_args는 URL이 아니라 드라이버로 직접 넘어가 풀에서 조회할 수 없으므로,
    선언값을 검증한다. 실제 전달 여부는 위 두 테스트가 같은 kwargs 묶음으로 보장한다.
    """
    assert (
        ENGINE_RESILIENCE_KWARGS["connect_args"]["connect_timeout"]
        == settings.db_connect_timeout_seconds
    )
    assert settings.db_connect_timeout_seconds > 0
