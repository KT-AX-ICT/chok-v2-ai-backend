"""Pydantic 검증 오류를 로그 한 줄로 요약 — 스키마 불일치 진단의 공용 포맷.

스키마 불일치는 "어느 필드가 왜 틀렸는지"만 알면 대개 즉시 끝나는 문제다. 그런데 이
서비스의 검증 지점은 세 곳으로 흩어져 있다 — 요청 수신(/ingest), RCA 산출물(job 경계),
Spring 전송(응답 본문). 각자 다른 형식으로 남기면 로그를 읽는 쪽이 매번 다시 해석해야
하므로, 형식을 이 모듈 하나로 고정한다.

형식: `필드경로[오류코드] 사유` — 여러 건은 ` | `로 이음.
  body.logs.0.timestamp[value_error] Value error, 타임스탬프 형식 오류(...) | body.window[missing] Field required

남기는 것: loc(필드 경로) · type(오류 코드) · msg(사유).
남기지 않는 것: input(입력값 원본) · ctx.
  /ingest 본문은 3종 배열이 수천 건까지 오는 대용량 번들이고 logs[].raw는 원본 로그
  한 줄이다. 입력값을 그대로 실으면 로그가 본문 사본이 되어 부피와 유출 표면이 함께
  늘어난다. 단, 커스텀 검증기가 스스로 값을 msg에 넣는 경우(contracts._valid_iso8601의
  타임스탬프)는 값 자체가 진단 정보이므로 살리고, 길이 상한(MSG_MAX)으로 막는다.

건수 상한(MAX_ERRORS)도 같은 이유다 — 배열 원소 수천 건이 전부 틀리면 오류도 수천 건이
되므로, 앞 N건만 남기고 생략 건수를 함께 적어 "잘렸다"는 사실이 로그에 드러나게 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 사유 문자열 상한. 표준 pydantic 메시지는 100자 이내이고, 값이 섞이는 커스텀 검증기
# 메시지도 이 길이면 어느 필드가 어떤 형식으로 왔는지 판단에는 충분하다.
MSG_MAX = 120

# 한 번에 남기는 오류 건수 상한. 상위 몇 건만 봐도 원인 패턴(같은 필드 반복 등)은 드러난다.
MAX_ERRORS = 20


def _format_one(err: dict[str, Any]) -> str:
    loc = ".".join(str(part) for part in err.get("loc") or ()) or "(위치 불명)"
    err_type = err.get("type") or "unknown"
    msg = str(err.get("msg") or "")
    if len(msg) > MSG_MAX:
        msg = msg[:MSG_MAX] + "…"
    return f"{loc}[{err_type}] {msg}".rstrip()


def summarize_validation_errors(
    errors: Sequence[dict[str, Any]], *, limit: int = MAX_ERRORS
) -> str:
    """ValidationError.errors() 결과를 로그용 한 줄 요약으로 변환.

    입력은 pydantic v2 `errors()` 형태의 dict 시퀀스(loc·type·msg 키). 빈 시퀀스도
    허용한다 — 검증 실패인데 상세가 비는 경우가 있어도 호출부가 분기하지 않게 한다.
    """
    total = len(errors)
    if total == 0:
        return "(오류 상세 없음)"

    parts = [_format_one(err) for err in errors[:limit]]
    if total > limit:
        parts.append(f"(…외 {total - limit}건 생략, 총 {total}건)")
    return " | ".join(parts)
