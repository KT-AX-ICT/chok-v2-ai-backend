"""raw 문자열 → 모달리티별 JSON 문자열 정규화 (Spring 저장 계약).

Spring의 log/metric/trace 테이블은 raw(TEXT) 한 컬럼에 이 JSON 문자열을 저장하고,
조회 시 역직렬화해 화면의 lines/spans/items를 조립한다. 그래서 raw의 모달리티별 키
구조가 계약이 된다 (docs/spring-contract.md):
  - log    : {"level", "msg"}
  - trace  : {"traceId", "from", "to", "duration", "status"}
  - metric : {"label", "value", "threshold", "exceeded"}

규칙:
  - JSON raw면 알려진 키에서 값을 뽑고, 아니면 정규식·통짜 폴백.
  - 파싱 불가 필드는 키를 남기고 값만 비움("" / None) — 키는 항상 유지.
  - log 전체 실패 시 원문 한 줄을 msg에 통째로 넣는다(내용 유실 방지).
  - timestamp는 상위 필드(ModalityItem.timestamp)로만 두고 raw에는 넣지 않는다(중복 금지).
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal["log", "metric", "trace"]


# ---------------------------------------------------------------- 출력 계약 스키마
# raw 정규화 결과의 모양을 Pydantic 모델로 못박는다. 문자열로 내보내기 전에 이 모델을
# 거치므로(구성=검증), 키·타입이 계약과 어긋나면 런타임에 걸린다(모양 drift 차단).
# Spring이 이 JSON을 역직렬화해 화면 lines/spans/items를 조립한다(docs/spring-contract.md).


class LogLine(BaseModel):
    level: str = ""
    msg: str = ""


class MetricItem(BaseModel):
    label: str = ""
    value: str = ""
    threshold: str = ""
    exceeded: bool | None = None


class TraceSpan(BaseModel):
    # 'from'은 파이썬 예약어라 필드는 from_, 직렬화 키는 alias 'from'.
    model_config = ConfigDict(populate_by_name=True)

    traceId: str = ""
    from_: str = Field("", alias="from")
    to: str = ""
    duration: int | None = None
    status: str = ""


# ---------------------------------------------------------------- 공통 유틸


def _loads_dict(raw: str) -> dict | None:
    """raw가 JSON 객체면 dict, 아니면 None."""
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return d if isinstance(d, dict) else None


def _first(d: dict, keys: tuple[str, ...]):
    """후보 키를 순서대로 보고 처음 나오는 non-None 값을 반환."""
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return None


def _s(v) -> str:
    """스칼라를 계약용 문자열로. None/dict/list는 빈 문자열."""
    if v is None or isinstance(v, (dict, list)):
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


# ---------------------------------------------------------------- log

_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.IGNORECASE)
_LOG_LEVEL_KEYS = ("level", "severity", "lvl", "levelname")
_LOG_MSG_KEYS = ("msg", "message", "log", "text")


def normalize_log(raw: str) -> str:
    """log raw → {"level", "msg"}. 파싱 실패 시 원문을 msg에 통째로."""
    d = _loads_dict(raw)
    if d is not None:
        level = _s(_first(d, _LOG_LEVEL_KEYS))
        msg_v = _first(d, _LOG_MSG_KEYS)
        msg = _s(msg_v) if msg_v is not None else raw  # 메시지 키 없으면 원문 통짜
    else:
        level = ""
        msg = raw  # 파싱 불가 → 원문 한 줄을 통째로
    if not level:  # dict든 텍스트든 레벨이 비면 메시지에서 마지막으로 시도
        m = _LEVEL_RE.search(msg)
        level = m.group(1).upper() if m else ""
    return LogLine(level=level, msg=msg).model_dump_json()


# ---------------------------------------------------------------- metric

_METRIC_LABEL_KEYS = ("label", "metric", "name", "__name__", "metric_name")
_METRIC_VALUE_KEYS = ("value", "val", "v")
# Prometheus 노출형: name{labels}? value [ts]?
_PROM_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{[^}]*\})?"
    r"\s+(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?:\s+\d+)?\s*$"
)
# key=value 텍스트 폴백
_PAIR_RE = re.compile(r"([A-Za-z_][\w.%-]*)=([-+]?\d+(?:\.\d+)?)")


def _metric_label_value(raw: str) -> tuple[str, str]:
    """raw에서 (label, value) 추출. JSON(명시 키 → 첫 숫자) → Prometheus → key=value 순."""
    d = _loads_dict(raw)
    if d is not None:
        label = _first(d, _METRIC_LABEL_KEYS)
        value = _first(d, _METRIC_VALUE_KEYS)
        if label is not None and value is not None:
            return _s(label), _s(value)
        for k, v in d.items():  # 평면 JSON {"cpu_usage": 53.5} → 첫 숫자 필드
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return k, _s(v)
        return _s(label), _s(value)
    if m := _PROM_RE.match(raw.strip()):
        return m.group("name"), m.group("value")
    if pairs := _PAIR_RE.findall(raw):
        return pairs[0][0], pairs[0][1]
    return "", ""


def normalize_metric(raw: str) -> str:
    """metric raw → {"label", "value", "threshold", "exceeded"}."""
    label, value = _metric_label_value(raw)
    d = _loads_dict(raw)
    threshold = _s(d.get("threshold")) if d else ""
    exceeded = d.get("exceeded") if d and isinstance(d.get("exceeded"), bool) else None
    return MetricItem(
        label=label, value=value, threshold=threshold, exceeded=exceeded
    ).model_dump_json()


# ---------------------------------------------------------------- trace

_TRACE_ID_KEYS = ("traceId", "trace_id", "traceID", "tid")
_TRACE_FROM_KEYS = ("from", "caller", "source", "client", "parent")
_TRACE_TO_KEYS = ("to", "callee", "operation", "operationName", "name", "target")
_TRACE_STATUS_KEYS = ("status", "http_status_code", "statusCode", "code")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", re.IGNORECASE)
_TRACE_ERR_RE = re.compile(r"\b(ERROR|TIMEOUT|FAIL\w*|5\d{2})\b", re.IGNORECASE)
_DUR_UNIT_MS = {"us": 1e-3, "µs": 1e-3, "ms": 1.0, "s": 1e3}


def _trace_duration_ms(d: dict) -> int | None:
    """duration_us/duration_ms/duration 순으로 ms 정수 환산."""
    if (v := d.get("duration_us")) is not None:
        try:
            return int(float(v) / 1000)
        except (ValueError, TypeError):
            return None
    for key in ("duration_ms", "duration"):
        if (v := d.get(key)) is not None:
            try:
                return int(float(v))
            except (ValueError, TypeError):
                return None
    return None


def normalize_trace(raw: str) -> str:
    """trace raw → {"traceId", "from", "to", "duration", "status"}."""
    d = _loads_dict(raw)
    if d is not None:
        trace_id = _s(_first(d, _TRACE_ID_KEYS))
        frm = _s(_first(d, _TRACE_FROM_KEYS))
        to = _s(_first(d, _TRACE_TO_KEYS))
        status = _s(_first(d, _TRACE_STATUS_KEYS))
        duration = _trace_duration_ms(d)
    else:
        trace_id, frm, to, status, duration = "", "", "", "", None
        if m := _DURATION_RE.search(raw):
            duration = int(float(m.group(1)) * _DUR_UNIT_MS[m.group(2).lower()])
        if m := _TRACE_ERR_RE.search(raw):  # 텍스트면 에러 신호를 status로
            status = m.group(1).upper()
    return TraceSpan(
        traceId=trace_id, from_=frm, to=to, duration=duration, status=status
    ).model_dump_json(by_alias=True)


# ---------------------------------------------------------------- 적용

_NORMALIZERS = {"log": normalize_log, "metric": normalize_metric, "trace": normalize_trace}
_PAYLOAD_KEYS: dict[str, Modality] = {"logs": "log", "metrics": "metric", "traces": "trace"}


def normalize_payload_signals(payload: dict) -> None:
    """payload의 logs/metrics/traces 각 항목 raw를 모달리티별 JSON 문자열로 치환(in-place).

    Spring 전송 직전에 호출한다. raw가 없으면 건너뛴다(스키마상 필수라 정상 흐름엔 항상 존재).
    """
    for key, modality in _PAYLOAD_KEYS.items():
        for item in payload.get(key) or []:
            raw = item.get("raw")
            if raw is not None:
                item["raw"] = _NORMALIZERS[modality](raw)
