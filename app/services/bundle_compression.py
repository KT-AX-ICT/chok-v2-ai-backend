"""번들 raw 압축기 — 모달리티별 무손실에 가까운 재표현.

규칙 문서: docs/bundle-compression.md (실데이터 검증 근거 포함)
  - log    : 템플릿화(dedup) — 가변부 마스킹 후 동일 패턴을 1줄로 축약, 원문 샘플 유지
  - metric : 시리즈별 baseline/incident 통계 + onset·peak 이상점
  - trace  : (서비스, 오퍼레이션) 집계 + 서비스별 볼륨 타임라인 + exemplar 원문

공통 표현 규칙:
  - 절대 시각 축약(HH:MM:SS.mmm) — 상대 시각 금지, 정밀 절대값 유지
  - 서비스별 그룹핑, JSON 대신 TSV 직렬화(키 반복 제거)
  - 파싱 불가 시 원문 통과 폴백(손실보다 안전 우선)
  - D-020/D-021(정답 유출 방지)은 상위 파서(bundle_parser)가 보장
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from statistics import mean, pstdev

from app.schemas.contracts import ModalityItem

_EMPTY = "(없음)"

# ---------------------------------------------------------------- 공통 유틸


def _parse_ts(ts: str) -> datetime | None:
    """ISO-8601 문자열 파싱(Z 허용). 실패 시 None — 비교가 필요한 곳만 사용."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _short_ts(ts: str) -> str:
    """절대 시각 축약 — 날짜부 생략, 시각부(HH:MM:SS[.fff])만.

    번들이 단일 윈도 내라 날짜 중복이 불필요하다. 파싱 불가 형식은 원문 유지.
    """
    m = re.search(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?", ts)
    return m.group(0) if m else ts


def _fmt(v: float) -> str:
    return f"{v:.4g}"


# ---------------------------------------------------------------- log dedup

_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.I)
# 레벨 정렬 우선순위 — 에러·경고 패턴을 먼저 보여준다
_LEVEL_ORDER = {"FATAL": 0, "CRITICAL": 0, "ERROR": 0, "WARN": 1, "WARNING": 1}

# 가변부 마스킹: 시각 → <ts>, req_id → <id>, 긴 숫자열(4자리 이상) → <n>
_TS_IN_RAW_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?|\d{2}:\d{2}:\d{2}(?:\.\d+)?"
)
_REQ_ID_RE = re.compile(r"\b(req_id|trace_id|span_id)[=:]\s*\S+", re.I)
_LONG_NUM_RE = re.compile(r"\d{4,}")


def _log_template(raw: str) -> str:
    masked = _TS_IN_RAW_RE.sub("<ts>", raw)
    masked = _REQ_ID_RE.sub(lambda m: f"{m.group(1)}=<id>", masked)
    return _LONG_NUM_RE.sub("<n>", masked)


def compress_logs(items: list[ModalityItem]) -> str:
    """동일 패턴을 `서비스·레벨·×횟수·최초~최후·샘플` 1줄로 축약. 희귀 라인은 그대로 1줄."""
    if not items:
        return _EMPTY

    groups: dict[tuple, dict] = {}
    for item in items:
        level_m = _LEVEL_RE.search(item.raw)
        level = level_m.group(1).upper() if level_m else "-"
        key = (item.service, level, _log_template(item.raw))
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "count": 1,
                "first": item.timestamp,
                "last": item.timestamp,
                "sample": item.raw,
            }
        else:
            g["count"] += 1
            g["last"] = item.timestamp  # 입력은 시간순 가정(수집기 계약)

    def sort_key(entry):
        (_, level, _), g = entry
        return (_LEVEL_ORDER.get(level, 2), -g["count"])

    lines = [f"# 로그 패턴 dedup ({len(items)}건 → {len(groups)}패턴) — 서비스<TAB>레벨<TAB>횟수<TAB>최초~최후<TAB>샘플 원문"]
    for (service, level, _), g in sorted(groups.items(), key=sort_key):
        span = (
            _short_ts(g["first"])
            if g["count"] == 1
            else f"{_short_ts(g['first'])}~{_short_ts(g['last'])}"
        )
        lines.append(f"{service or '?'}\t{level}\t×{g['count']}\t{span}\t{g['sample']}")
    return "\n".join(lines)


# ------------------------------------------------------------ metric 통계

# key=value 텍스트 폴백용 (예: "cpu_usage=53.5 mem=1200")
_PAIR_RE = re.compile(r"([A-Za-z_][\w.%-]*)=([-+]?\d+(?:\.\d+)?)")
# name·value 쌍 JSON에서 라벨/값 키 후보 (예: {"metric": "cpu", "value": 0.85})
_METRIC_NAME_KEYS = ("metric", "name", "__name__", "metric_name")
_METRIC_VALUE_KEYS = ("value", "val", "v")


def _num(v) -> float | None:
    """숫자만 float로. bool은 int subclass라 명시적으로 제외."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _metric_pairs(raw: str) -> list[tuple[str, float]]:
    """raw에서 (라벨, 값) 추출. 지원 형식(넓은 순):

      1) 평면 JSON      {"cpu_usage": 53.5, "mem": 1200}  → 숫자 필드 전부
      2) name·value JSON {"metric": "cpu", "value": 53.5}  → 라벨=name, 값=value
      3) key=value 텍스트 "cpu_usage=53.5 mem=1200"        → 정규식 폴백

    셋 다 실패하면 [] — 호출부가 원문 통과 처리.
    """
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        d = None
    if isinstance(d, dict):
        # 형태 2 우선: name/value 쌍이 명시된 경우 그 의미를 살린다
        name = next((v for k in _METRIC_NAME_KEYS if (v := d.get(k)) and isinstance(v, str)), None)
        value = next((n for k in _METRIC_VALUE_KEYS if (n := _num(d.get(k))) is not None), None)
        if name is not None and value is not None:
            return [(name, value)]
        # 형태 1: 숫자 필드 전부를 라벨=키로
        pairs = [(k, n) for k, v in d.items() if (n := _num(v)) is not None]
        if pairs:
            return pairs
    # 형태 3: key=value 텍스트
    return [(label, float(value)) for label, value in _PAIR_RE.findall(raw)]


def _series_stats(points: list[tuple[datetime | None, float]]) -> str:
    values = [v for _, v in points]
    return f"n={len(values)} mean={_fmt(mean(values))} min={_fmt(min(values))} max={_fmt(max(values))}"


def compress_metrics(items: list[ModalityItem], trigger_time: str) -> str:
    """시리즈(서비스·라벨)별 baseline/incident 통계 + onset·peak. 파싱 불가는 원문 통과."""
    if not items:
        return _EMPTY

    series: dict[tuple[str, str], list] = defaultdict(list)
    unparsed: list[ModalityItem] = []
    for item in items:
        pairs = _metric_pairs(item.raw)
        if not pairs:
            unparsed.append(item)
            continue
        for label, value in pairs:
            series[(item.service, label)].append(
                (_parse_ts(item.timestamp), item.timestamp, value)
            )

    trigger_dt = _parse_ts(trigger_time)
    lines = [
        "# 메트릭 시리즈 통계 — 서비스<TAB>라벨<TAB>baseline(트리거 이전)<TAB>incident(이후)<TAB>이상점"
    ]
    for (service, label), pts in sorted(series.items()):
        base = [(dt, v) for dt, _, v in pts if trigger_dt and dt and dt < trigger_dt]
        incid = [(dt, ts, v) for dt, ts, v in pts if not (trigger_dt and dt and dt < trigger_dt)]

        base_txt = _series_stats(base) if base else "n=0"
        incid_txt = _series_stats([(dt, v) for dt, _, v in incid]) if incid else "n=0"

        anomaly = "-"
        if base and incid:
            mu = mean(v for _, v in base)
            sigma = pstdev([v for _, v in base]) if len(base) > 1 else 0.0
            deviants = [
                (dt, ts, v)
                for dt, ts, v in incid
                if abs(v - mu) > 3 * sigma or (sigma == 0.0 and v != mu)
            ]
            if deviants:
                onset = deviants[0]
                peak = max(deviants, key=lambda p: abs(p[2] - mu))
                anomaly = (
                    f"onset={_fmt(onset[2])}@{_short_ts(onset[1])} "
                    f"peak={_fmt(peak[2])}@{_short_ts(peak[1])}"
                )
        lines.append(f"{service or '?'}\t{label}\tbase {base_txt}\tincid {incid_txt}\t{anomaly}")

    if unparsed:
        lines.append("# 미파싱 원문 통과 — [시각] 원문")
        lines.extend(f"[{_short_ts(i.timestamp)}] {i.raw}" for i in unparsed)
    return "\n".join(lines)


# ------------------------------------------------------------- trace 집계

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", re.I)
_TRACE_ERR_RE = re.compile(r"\b(ERROR|TIMEOUT|FAIL\w*|5\d{2})\b", re.I)
_EXEMPLAR_LIMIT = 3


def _span_fields(item: ModalityItem) -> tuple[str, float | None, bool]:
    """raw에서 (오퍼레이션, 지연 ms, 에러 여부) 추출. JSON 우선, 실패 시 정규식."""
    operation, duration_ms, is_err = "?", None, False
    try:
        d = json.loads(item.raw)
    except (json.JSONDecodeError, TypeError):
        d = None
    if isinstance(d, dict):
        # name: OTel 스팬 표준 키. operation/operationName 뒤, to 앞 순위
        operation = (
            d.get("operation") or d.get("operationName") or d.get("name") or d.get("to") or "?"
        )
        if (v := d.get("duration_us")) is not None:
            duration_ms = float(v) / 1000
        elif (v := d.get("duration_ms")) is not None:
            duration_ms = float(v)
        elif (v := d.get("duration")) is not None:
            duration_ms = float(v)
        status = str(d.get("status") or d.get("http_status_code") or "")
        is_err = bool(_TRACE_ERR_RE.search(status))
    else:
        if m := _DURATION_RE.search(item.raw):
            value, unit = float(m.group(1)), m.group(2).lower()
            duration_ms = value * {"us": 1e-3, "µs": 1e-3, "ms": 1.0, "s": 1e3}[unit]
        is_err = bool(_TRACE_ERR_RE.search(item.raw))
    return str(operation), duration_ms, is_err


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(len(sorted_values) - 1, max(0, round(p * (len(sorted_values) - 1))))
    return sorted_values[idx]


def compress_traces(items: list[ModalityItem]) -> str:
    """(서비스, 오퍼레이션) 집계 + 서비스별 분단위 볼륨 + 느린/에러 exemplar 원문."""
    if not items:
        return _EMPTY

    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "err": 0, "durations": []}
    )
    volume: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    parsed: list[tuple[ModalityItem, float | None, bool]] = []

    for item in items:
        operation, duration_ms, is_err = _span_fields(item)
        g = agg[(item.service, operation)]
        g["count"] += 1
        g["err"] += int(is_err)
        if duration_ms is not None:
            g["durations"].append(duration_ms)
        minute = _short_ts(item.timestamp)[:5]  # HH:MM
        volume[item.service or "?"][minute] += 1
        parsed.append((item, duration_ms, is_err))

    lines = ["# 트레이스 구간 집계 — 서비스<TAB>오퍼레이션<TAB>호출<TAB>에러<TAB>p50/p95/max(ms)"]
    for (service, operation), g in sorted(
        agg.items(), key=lambda e: (-e[1]["err"], -e[1]["count"])
    ):
        if g["durations"]:
            ds = sorted(g["durations"])
            dur_txt = f"{_fmt(_percentile(ds, 0.5))}/{_fmt(_percentile(ds, 0.95))}/{_fmt(ds[-1])}"
        else:
            dur_txt = "-"
        lines.append(f"{service or '?'}\t{operation}\t×{g['count']}\terr={g['err']}\t{dur_txt}")

    lines.append("# 서비스별 볼륨 타임라인(분) — 급감·소실 구간이 구조적 장애 신호")
    for service, buckets in sorted(volume.items()):
        buckets_txt = " ".join(f"{m}={n}" for m, n in sorted(buckets.items()))
        lines.append(f"{service}\t{buckets_txt}")

    slowest = sorted(
        (p for p in parsed if p[1] is not None), key=lambda p: -p[1]
    )[:_EXEMPLAR_LIMIT]
    errors = [p for p in parsed if p[2]][:_EXEMPLAR_LIMIT]
    exemplars: list[ModalityItem] = []
    for item, _, _ in [*errors, *slowest]:
        if item not in exemplars:
            exemplars.append(item)
    if exemplars:
        lines.append("# exemplar 원문(가장 느린/에러 스팬) — [시각] 서비스 원문")
        lines.extend(
            f"[{_short_ts(i.timestamp)}] {i.service or '?'} {i.raw}" for i in exemplars
        )
    return "\n".join(lines)
