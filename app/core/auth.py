"""서버 간 인증 — /ingest 인바운드 API 키 검증.

SDK가 X-API-Key 헤더로 키를 보내고, settings.ingest_api_keys(콤마 구분)에 있을 때만
통과한다. 키 미설정이면 검증하지 않는다 — SDK 적용과 서버 배포를 분리하는 단계적
전환용이며, 그 상태는 기동 시 경고로 드러낸다(app/main.py).

키 저장소를 env에서 DB(company_code ↔ 키 해시)로 옮길 때 변경 지점을 이 파일로
한정하려고 분리했다.
"""

import logging
import secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"

# APIKeyHeader를 쓰는 이유는 OpenAPI 스키마에 보안 요건으로 등재되기 때문 —
# /docs에 자물쇠 UI가 생기고 스펙만 봐도 인증 필요가 드러난다.
# auto_error=False: 헤더가 없을 때 FastAPI가 403을 자동 발생시키지 않고 None을 넘긴다.
# 미설정 시 통과(단계적 전환) 판단을 이 모듈이 해야 하므로 필수.
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def mask(key: str | None) -> str:
    """로그용 키 축약 — 앞 4자 + 길이.

    전체를 남기면 로그 유출이 곧 키 유출이 된다. 이 정도면 "SDK가 구 키를 쓰고 있다"
    판단에는 충분하다.
    """
    if not key:
        return "(없음)"
    return f"{key[:4]}…({len(key)}자)"


async def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    keys = settings.ingest_api_key_set
    if not keys:
        return  # 단계적 전환 — 키 미설정이면 검증하지 않는다
    # compare_digest = 상수 시간 비교. ==는 불일치 위치에 따라 소요 시간이 달라져
    # 타이밍 공격에 키가 새어 나간다. bytes로 넘기는 이유는 compare_digest가 비-ASCII
    # str에 TypeError를 던져, 이상한 키를 보낸 요청이 401이 아니라 500이 되기 때문.
    provided = (api_key or "").encode("utf-8")
    if not any(secrets.compare_digest(provided, k.encode("utf-8")) for k in keys):
        logger.warning("ingest 인증 실패 — 키 %s", mask(api_key))
        # 키 없음과 틀린 키를 응답에서 구분하지 않는다 — 구분하면 "헤더 이름은 맞았다"는
        # 힌트가 된다. 구분은 위 로그로 충분하다.
        raise HTTPException(status_code=401, detail="invalid or missing API key")
