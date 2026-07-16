"""RCA 산출물 정합성 검증 레이어.

오케스트레이터/report 에이전트가 만든 결과가 프론트 계약(detail 5키)에 맞는지
job 완료 전에 강제한다. 어긋나면 RcaResultInvalid 예외를 던지고, 워커가 이를 받아
job을 FAILED로 전환하며 사유를 기록한다.

프론트가 5키(rca·summary·evidence·impact·actions) 이름으로 화면을 그리므로,
검증을 통과하지 못한 산출물이 DONE으로 저장되면 조회 화면이 깨진다. 이 레이어가
그 계약 위반을 job 경계에서 차단한다.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.schemas.contracts import RcaResult


class RcaResultInvalid(ValueError):
    """RCA 산출물이 RcaResult 계약(5키)에 맞지 않을 때."""


def validate_rca_result(raw: object) -> RcaResult:
    """runner 산출물을 RcaResult로 검증해 반환. 실패 시 RcaResultInvalid.

    허용 입력:
      - RcaResult 인스턴스 → 그대로 통과
      - dict → 스키마 검증 후 RcaResult로 파싱
    그 외 타입이거나 스키마 불일치면 예외.
    """
    if isinstance(raw, RcaResult):
        return raw
    if isinstance(raw, dict):
        try:
            return RcaResult.model_validate(raw)
        except ValidationError as exc:
            locs = "; ".join(
                ".".join(str(p) for p in err["loc"]) for err in exc.errors()
            )
            raise RcaResultInvalid(f"RcaResult 스키마 불일치 필드: {locs}") from exc
    raise RcaResultInvalid(
        f"RCA 산출물 타입 부적합: {type(raw).__name__} (dict 또는 RcaResult 필요)"
    )
