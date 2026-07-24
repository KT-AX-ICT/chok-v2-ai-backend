"""번들 raw 압축기 — 모달리티별 무손실에 가까운 재표현.

규칙 문서: docs/bundle-compression.md (실데이터 검증 근거 포함)
  - log    : Drain 템플릿 마이닝으로 dedup — 가변부를 데이터에서 학습, 원문 샘플 유지
  - metric : 시리즈별 baseline/incident 통계 + onset·peak 이상점 (JSON·key=value 지원)
  - trace  : (서비스, 오퍼레이션) 집계 + 서비스별 볼륨 타임라인 + exemplar 원문

공통 표현 규칙:
  - 절대 시각 축약(HH:MM:SS.mmm) — 상대 시각 금지, 정밀 절대값 유지
  - 서비스별 그룹핑, JSON 대신 TSV 직렬화(키 반복 제거)
  - 파싱 불가 시 원문 통과 폴백(손실보다 안전 우선)
  - D-020/D-021(정답 유출 방지)은 상위 파서(bundle_parser)가 보장

이 모듈의 산출물은 LLM 입력용(전량 집계 텍스트)이다. Spring 전송용 항목 선별은
signal_selector가 담당하며, 판정 기준을 두 벌로 두지 않도록 아래를 공유한다:
parse_ts · LEVEL_RE · LEVEL_ORDER · make_miner · metric_pairs · span_fields · TRACE_ERR_RE
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from statistics import mean, pstdev

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from app.schemas.contracts import ModalityItem

_EMPTY = "(없음)"

# 실험 전용 baseline(정상 운영) 프로필. scripts/analyze_baseline.py가 생성하며
# datasets/baseline/는 gitignore 대상 — 파일이 없는 게 정상 상태(운영 서버·CI 등)이므로
# 없으면 조용히 빈 프로필로 폴백한다(런타임 필수 의존성 아님).
_BASELINE_PROFILE_PATH = Path(__file__).resolve().parent.parent.parent / "datasets" / "baseline" / "log_profile.json"


@lru_cache(maxsize=1)
def _load_baseline_profile() -> dict[tuple[str, str, str], int]:
    """(서비스, 레벨, Drain 템플릿) → 24h 정상 운영 발생 횟수. 파일 없으면 빈 dict."""
    if not _BASELINE_PROFILE_PATH.exists():
        return {}
    try:
        rows = json.loads(_BASELINE_PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {(r["service"], r["level"], r["template"]): r["count"] for r in rows}


# ---------------------------------------------------------------- 공통 유틸


def parse_ts(ts: str) -> datetime | None:
    """ISO-8601 문자열 파싱(Z 허용). 실패 시 None — 비교가 필요한 곳만 사용."""
    try:
        return datetime.fromisoformat(ts)  # 3.11+ fromisoformat은 'Z'를 직접 처리
    except (ValueError, AttributeError):
        return None


def short_ts(ts: str) -> str:
    """절대 시각 축약 — 날짜부 생략, 시각부(HH:MM:SS[.fff])만.

    번들이 단일 윈도 내라 날짜 중복이 불필요하다. 날짜·기준시각은 프롬프트 상단
    윈도/트리거에 전체 형식으로 한 번만 싣고, 본문·구간은 시각만 남긴다.
    파싱 불가 형식은 원문 유지. bundle_parser의 구간 렌더링도 이 함수를 공유한다.
    """
    m = re.search(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?", ts)
    return m.group(0) if m else ts


def _fmt(v: float) -> str:
    return f"{v:.4g}"


# ---------------------------------------------------------------- log dedup

LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\b", re.I)
# 레벨 정렬 우선순위 — 에러·경고 패턴을 먼저 보여준다
LEVEL_ORDER = {"FATAL": 0, "CRITICAL": 0, "ERROR": 0, "WARN": 1, "WARNING": 1}

# 고카디널리티 토큰은 Drain 클러스터링 전에 마스킹해 템플릿을 안정화한다.
# (나머지 가변부 — 유저ID·경로·호스트명 등 — 는 Drain이 데이터에서 학습)
_MASKING = [
    # 대괄호형 [YYYY-Mon-DD HH:MM:SS(.ffffff)] 전체를 통째로 마스킹 — 월 약어(Jul/Nov 등)는
    # 숫자가 아니라 뒤의 <NUM>/시각 패턴만으로는 안 가려져서, 다른 달에 수집된 같은 로그가
    # 서로 다른 템플릿으로 갈린다(baseline 프로필 매칭·자정 넘는 윈도 dedup 둘 다 깨짐).
    # 시간 패턴보다 먼저 와야 이 통짜 마스킹이 우선 적용된다.
    MaskingInstruction(r"\[\d{4}-[A-Za-z]{3}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\]", "TS"),
    MaskingInstruction(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "TS"),
    MaskingInstruction(r"\d{2}:\d{2}:\d{2}(?:\.\d+)?", "TS"),
    MaskingInstruction(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "IP"),
    MaskingInstruction(r"\b[0-9a-fA-F]{8,}\b", "HEX"),
    MaskingInstruction(r"\b\d{4,}\b", "NUM"),
]


def make_miner() -> TemplateMiner:
    """번들 1건 처리용 Drain 템플릿 마이너. 상태를 공유하지 않도록 호출마다 새로 만든다."""
    config = TemplateMinerConfig()
    config.masking_instructions = _MASKING
    config.profiling_enabled = False
    return TemplateMiner(config=config)


def compress_logs(items: list[ModalityItem], trigger_time: str | None = None) -> str:
    """Drain으로 로그 템플릿을 학습해 `서비스·레벨·×횟수(base/incid)·최초~최후·샘플` 1줄로 축약.

    정규식 하드코딩 패턴이 아니라 데이터에서 템플릿을 추출하므로, 사전에 모르는
    로그·시스템 로그 형식도 가변부를 학습해 dedup한다. 희귀 라인은 자기 클러스터로 남는다.

    compress_metrics와 동형으로 trigger_time 기준 base(이전)/incid(이후) 건수를 같이
    집계한다 — "baseline에도 있던 만성 패턴인지 트리거 이후 신규 패턴인지"를 first~last
    두 시각만 보고 추측하지 않아도 되게 한다.

    baseline 프로필(datasets/baseline/log_profile.json, 실험 전용)이 있으면 같은
    (서비스,레벨,템플릿)의 평소(24시간 정상 운영) 발생 횟수도 같이 붙인다 — 번들 자체의
    base 구간이 짧아 우연히 0으로 나온 만성 패턴(예: 7분에 1번꼴 노이즈)을 구분하기
    위함. 프로필 파일이 없으면(운영 서버·CI 등 기본 상태) 이 표기를 생략한다.

    정렬은 레벨(ERROR 우선)·count가 아니라 "평소와 얼마나 다른가"를 우선한다 — ERROR라도
    평소에도 흔하면(예: 24h 200회) 뒤로 밀리고, INFO라도 평소엔 거의 없던 게 이번에
    튀었으면(예: 서버 재시작 로그가 평소 1회인데 이번엔 2회) 앞으로 온다. 로그 레벨만으로는
    "정상 부팅 메시지가 두 번 찍힌 이상 징후"를 절대 구분할 수 없기 때문 — INFO는 항상
    ERROR/WARN보다 후순위였던 기존 정렬로는 이런 신호가 구조적으로 노출될 수 없었다.
    """
    if not items:
        return _EMPTY

    # 서비스별로 별도 마이너 — 하나를 전체 번들에 공유하면 서로 다른 서비스의 비슷하게
    # 생긴 로그(예: "Starting the X-service server...")가 한 클러스터로 합쳐지면서
    # 서비스명 토큰까지 <*>로 일반화된다. 그러면 baseline 프로필(서비스별 파일 기준으로
    # 만들어짐)의 템플릿과 텍스트가 어긋나 매칭이 깨진다.
    miners: dict[str | None, TemplateMiner] = {}
    trigger_dt = parse_ts(trigger_time) if trigger_time else None
    baseline_profile = _load_baseline_profile()
    groups: dict[tuple, dict] = {}
    for item in items:
        level_m = LEVEL_RE.search(item.raw)
        level = level_m.group(1).upper() if level_m else "-"
        miner = miners.setdefault(item.service, make_miner())
        cluster = miner.add_log_message(item.raw)
        key = (item.service, level, cluster["cluster_id"])
        ts_dt = parse_ts(item.timestamp)
        is_base = bool(trigger_dt and ts_dt and ts_dt < trigger_dt)
        g = groups.get(key)
        if g is None:
            groups[key] = {
                "count": 1,
                "base": 1 if is_base else 0,
                "incid": 0 if is_base else 1,
                "first": item.timestamp,
                "last": item.timestamp,
                "sample": item.raw,
                "template": cluster["template_mined"],
            }
        else:
            g["count"] += 1
            g["base" if is_base else "incid"] += 1
            g["last"] = item.timestamp  # 입력은 시간순 가정(수집기 계약)

    # 정렬용 "평소와 다른 정도" 계산 — incid를 (번들 내 base + 평소24h)로 나눈다.
    # 분모가 작을수록(평소엔 드물수록) 같은 incid라도 값이 커진다. +1은 0으로 못 나누게
    # 하는 보정일 뿐, 별도 임계값이 아니다. 평소24h를 모르면(프로필 없음) base만으로 계산 —
    # 정보가 없는 만큼 원래 count 우선 정렬과 비슷하게 동작한다(하위 호환).
    for (service, level, _cid), g in groups.items():
        usual24h = baseline_profile.get((service, level, g["template"])) if baseline_profile else None
        g["usual24h"] = usual24h
        g["surprise"] = g["incid"] / (g["base"] + (usual24h or 0) + 1)

    def sort_key(entry):
        (_, level, _), g = entry
        if trigger_dt is None:
            # 트리거 시각 자체가 없으면 base/incid 분리가 무의미(전부 incid로 잡힘) —
            # "평소와 다른 정도"를 계산할 근거가 없으니 기존 레벨 우선 정렬로 폴백한다.
            return (0.0, LEVEL_ORDER.get(level, 2), -g["count"])
        return (-g["surprise"], LEVEL_ORDER.get(level, 2), -g["count"])

    header_cols = "서비스<TAB>레벨<TAB>횟수(base=트리거이전/incid=이후"
    header_cols += ("/평소24h=정상운영 24시간 발생횟수" if baseline_profile else "") + ")<TAB>최초~최후<TAB>샘플 원문"
    lines = [
        f"# 로그 패턴 dedup ({len(items)}건 → {len(groups)}패턴, 평소와 다른 정도순 정렬) — {header_cols}"
    ]
    for (service, level, _), g in sorted(groups.items(), key=sort_key):
        span = (
            short_ts(g["first"])
            if g["count"] == 1
            else f"{short_ts(g['first'])}~{short_ts(g['last'])}"
        )
        count_tag = f"base={g['base']} incid={g['incid']}"
        if baseline_profile:
            count_tag += f" 평소24h={g['usual24h'] or 0}"
        lines.append(f"{service or '?'}\t{level}\t×{g['count']}({count_tag})\t{span}\t{g['sample']}")
    return "\n".join(lines)


# ------------------------------------------------------------ metric 통계

# key=value 텍스트 폴백용 (예: "cpu_usage=53.5 mem=1200")
_PAIR_RE = re.compile(r"([A-Za-z_][\w.%-]*)=([-+]?\d+(?:\.\d+)?)")
# Prometheus 노출형: name{labels}? value [ts]?  (예: 'node_cpu{instance="n:9100"} 2.22')
_PROM_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{[^}]*\})?"
    r"\s+(?P<value>[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"(?:\s+\d+)?\s*$"
)
# name·value 쌍 JSON에서 라벨/값 키 후보 (예: {"metric": "cpu", "value": 0.85})
_METRIC_NAME_KEYS = ("metric", "name", "__name__", "metric_name")
_METRIC_VALUE_KEYS = ("value", "val", "v")


def _num(v) -> float | None:
    """숫자만 float로. bool은 int subclass라 명시적으로 제외."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def metric_pairs(raw: str) -> list[tuple[str, float]]:
    """raw에서 (라벨, 값) 추출. 지원 형식(넓은 순):

      1) 평면 JSON       {"cpu_usage": 53.5, "mem": 1200}     → 숫자 필드 전부
      2) name·value JSON {"metric": "cpu", "value": 53.5}     → 라벨=name, 값=value
      3) Prometheus 노출형 'node_cpu{instance="n:9100"} 2.22' → 라벨=지표명 (라벨셋 드롭)
      4) key=value 텍스트 "cpu_usage=53.5 mem=1200"           → 정규식 폴백

    모두 실패하면 [] — 호출부가 원문 통과 처리.
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
    # 형태 3: Prometheus 노출형 (라벨셋은 드롭 — 지표명 + service 그룹핑으로 충분)
    if m := _PROM_RE.match(raw.strip()):
        return [(m.group("name"), float(m.group("value")))]
    # 형태 4: key=value 텍스트
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
        pairs = metric_pairs(item.raw)
        if not pairs:
            unparsed.append(item)
            continue
        for label, value in pairs:
            series[(item.service, label)].append(
                (parse_ts(item.timestamp), item.timestamp, value)
            )

    trigger_dt = parse_ts(trigger_time)
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
                    f"onset={_fmt(onset[2])}@{short_ts(onset[1])} "
                    f"peak={_fmt(peak[2])}@{short_ts(peak[1])}"
                )
        lines.append(f"{service or '?'}\t{label}\tbase {base_txt}\tincid {incid_txt}\t{anomaly}")

    if unparsed:
        lines.append("# 미파싱 원문 통과 — [시각] 원문")
        lines.extend(f"[{short_ts(i.timestamp)}] {i.raw}" for i in unparsed)
    return "\n".join(lines)


# ------------------------------------------------------------- trace 집계

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(us|µs|ms|s)\b", re.I)
TRACE_ERR_RE = re.compile(r"\b(ERROR|TIMEOUT|FAIL\w*|5\d{2})\b", re.I)
_EXEMPLAR_LIMIT = 3


def span_fields(raw: str) -> tuple[str, float | None, bool]:
    """raw에서 (오퍼레이션, 지연 ms, 에러 여부) 추출. JSON 우선, 실패 시 정규식.

    signal_selector(Spring 전송 선별)도 같은 판정을 써야 하므로 raw 문자열만 받는다.
    """
    operation, duration_ms, is_err = "?", None, False
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        d = None
    if isinstance(d, dict):
        # name: OTel 스팬 표준 키. operation/operationName 뒤, to 앞 순위
        operation = (
            d.get("operation") or d.get("operationName") or d.get("name") or d.get("to") or "?"
        )
        if (v := d.get("duration_us")) is not None:
            duration_ms = float(v) / 1000
        elif (v := d.get("duration_ms")) is not None or (v := d.get("duration")) is not None:
            duration_ms = float(v)
        status = str(d.get("status") or d.get("http_status_code") or "")
        is_err = bool(TRACE_ERR_RE.search(status))
    else:
        if m := _DURATION_RE.search(raw):
            value, unit = float(m.group(1)), m.group(2).lower()
            duration_ms = value * {"us": 1e-3, "µs": 1e-3, "ms": 1.0, "s": 1e3}[unit]
        is_err = bool(TRACE_ERR_RE.search(raw))
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
        operation, duration_ms, is_err = span_fields(item.raw)
        g = agg[(item.service, operation)]
        g["count"] += 1
        g["err"] += int(is_err)
        if duration_ms is not None:
            g["durations"].append(duration_ms)
        minute = short_ts(item.timestamp)[:5]  # HH:MM
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
            f"[{short_ts(i.timestamp)}] {i.service or '?'} {i.raw}" for i in exemplars
        )
    return "\n".join(lines)
