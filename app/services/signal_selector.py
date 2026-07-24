"""Spring 전송용 신호 선별 — 모달리티별 상한 이내로 진단 가치 우선 축소.

번들 원본은 한 건이 30만 줄까지 커진다. PR B로 우리 DB에는 파일 이름만 남겼지만
Spring 전송은 여전히 전량이었고, Spring은 같은 MySQL을 쓰므로 max_allowed_packet
한계가 동일하다. 그래서 전송 직전에 모달리티별 상한(settings.spring_signal_limit)
이내로 줄인다.

이 배열은 단순 보관용이 아니라 **Spring이 상세 화면 evidence(lines/spans/items)를
조립하는 원천**(docs/spring-contract.md)이다. 따라서 "아무거나 200개"가 아니라
장애 확인에 쓸모 있는 항목을 남겨야 한다.

선별 방식 — 그룹 라운드로빈:
  1) 그룹핑   log=Drain 클러스터 / metric=(서비스,라벨) / trace=(서비스,오퍼레이션)
  2) 그룹 정렬 에러·이상 포함 그룹 우선 → 큰 그룹 우선
  3) 그룹 내   log=ERROR>WARN>기타 / metric=3σ 이상점 우선 / trace=에러>느린 순
  4) 라운드로빈으로 각 그룹에서 1건씩 뽑아 상한을 채움
  5) 시간 오름차순으로 재정렬 — 화면이 시간 흐름을 따르도록 (문자열이 아니라
     datetime으로 비교. 이유는 _chron 참조)

라운드로빈을 쓰는 이유: 25만 건이 전부 같은 에러여도 그 패턴은 한 바퀴에 1건씩만
가져가므로, 희귀하지만 중요한 패턴이 밀려나지 않는다. 고정 쿼터(그룹당 N건)는
그룹 수에 따라 값을 다시 잡아야 하지만 라운드로빈은 자동으로 맞춰진다.

판정 기준은 bundle_compression과 공유한다(두 벌 관리 금지). 압축기는 LLM 입력용
전량 집계를 계속 담당하고, 이 모듈은 Spring 전송용 부분집합만 고른다.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Callable, NamedTuple

from app.core.config import settings
from app.services.bundle_compression import (
    LEVEL_ORDER,
    LEVEL_RE,
    make_miner,
    metric_pairs,
    parse_ts,
    span_fields,
)

logger = logging.getLogger(__name__)

# payload 배열 키 → 모달리티. spring_client가 이 순서로 순회한다.
PAYLOAD_KEYS: dict[str, str] = {"logs": "log", "metrics": "metric", "traces": "trace"}

# 정렬에서 "우선"과 "보통"을 나타내는 등급. 작을수록 먼저.
_PRIOR, _NORMAL = 0, 1

# 파싱 불가 시각의 정렬 위치. 계약상 timestamp는 /ingest에서 ISO-8601로 검증되므로
# 정상 흐름에선 쓰이지 않는 방어선이다.
_UNKNOWN_TIME = datetime.min.replace(tzinfo=timezone.utc)


def _chron(ts: str) -> datetime:
    """정렬용 시각. 문자열 비교로는 시간순이 보장되지 않아 datetime으로 변환한다.

    문자열 정렬이 깨지는 경우:
      - 'Z'와 소수부가 섞이면 '.'(46) < 'Z'(90)이라 10:00:00.5Z가 10:00:00Z보다 앞
      - 타임존 오프셋이 섞이면(+09:00 등) 자리값 비교가 무의미
    SDK가 모달리티마다 다른 정밀도로 보내므로(logs는 마이크로초, metrics는 초)
    실제로 섞여 들어올 수 있다.

    naive는 UTC로 간주한다 — naive와 aware를 그대로 비교하면 TypeError가 난다.
    """
    dt = parse_ts(ts)
    if dt is None:
        return _UNKNOWN_TIME
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Selection(NamedTuple):
    """선별 결과. total을 함께 들고 있어야 호출부가 절단 사실을 고지할 수 있다."""

    items: list[dict]
    total: int

    @property
    def truncated(self) -> bool:
        return len(self.items) < self.total


# ---------------------------------------------------------------- 공통 로직


def _round_robin(groups: list[list[int]], limit: int) -> list[int]:
    """그룹을 순회하며 1건씩 뽑아 상한을 채운다. 소진된 그룹은 건너뛴다.

    그룹 순서·그룹 내 순서가 이미 우선순위대로 정렬돼 있다고 가정한다.
    """
    picked: list[int] = []
    cursors = [0] * len(groups)
    advanced = True
    while advanced and len(picked) < limit:
        advanced = False
        for gi, group in enumerate(groups):
            if cursors[gi] >= len(group):
                continue
            picked.append(group[cursors[gi]])
            cursors[gi] += 1
            advanced = True
            if len(picked) >= limit:
                break
    return picked


def _ordered_groups(buckets: dict[object, list[tuple[int, int]]]) -> list[list[int]]:
    """버킷(키 → [(등급, 원본 인덱스)]) → 우선순위대로 정렬된 그룹 목록.

    그룹 내부는 (등급, 인덱스) 순 — 인덱스가 tie-breaker라 결과가 결정적이다.
    그룹 사이는 (최고 등급, 큰 그룹 우선, 최소 인덱스) 순.
    """
    ordered: list[tuple[int, int, int, list[int]]] = []
    for entries in buckets.values():
        entries.sort()
        best_rank = entries[0][0]
        ordered.append((best_rank, -len(entries), entries[0][1], [i for _, i in entries]))
    ordered.sort(key=lambda g: g[:3])
    return [g[3] for g in ordered]


# ---------------------------------------------------------------- 모달리티별 그룹핑


def _log_buckets(items: list[dict]) -> dict[object, list[tuple[int, int]]]:
    """Drain 클러스터별로 묶고, 항목 등급은 로그 레벨로 매긴다."""
    miner = make_miner()
    buckets: dict[object, list[tuple[int, int]]] = defaultdict(list)
    for idx, item in enumerate(items):
        raw = item.get("raw") or ""
        level_m = LEVEL_RE.search(raw)
        level = level_m.group(1).upper() if level_m else "-"
        cluster = miner.add_log_message(raw)
        buckets[cluster["cluster_id"]].append((LEVEL_ORDER.get(level, 2), idx))
    return buckets


def _metric_buckets(
    items: list[dict], trigger_time: str
) -> dict[object, list[tuple[int, int]]]:
    """(서비스, 라벨) 시리즈로 묶고, 트리거 이후 3σ 이탈 지점을 우선 등급으로."""
    trigger_dt = parse_ts(trigger_time) if trigger_time else None
    # 시리즈 키 → [(원본 인덱스, 값, 트리거 이전 여부)]
    series: dict[object, list[tuple[int, float, bool]]] = defaultdict(list)
    unparsed: list[tuple[int, int]] = []

    for idx, item in enumerate(items):
        pairs = metric_pairs(item.get("raw") or "")
        if not pairs:
            unparsed.append((_NORMAL, idx))
            continue
        label, value = pairs[0]  # 대표 라벨 1개로 그룹핑 — 압축기와 달리 항목이 단위
        dt = parse_ts(item.get("timestamp") or "")
        is_base = bool(trigger_dt and dt and dt < trigger_dt)
        series[(item.get("service") or "", label)].append((idx, value, is_base))

    buckets: dict[object, list[tuple[int, int]]] = defaultdict(list)
    for key, points in series.items():
        base = [v for _, v, is_base in points if is_base]
        mu = mean(base) if base else None
        sigma = pstdev(base) if len(base) > 1 else 0.0
        for idx, value, is_base in points:
            deviant = (
                mu is not None
                and not is_base
                and (abs(value - mu) > 3 * sigma or (sigma == 0.0 and value != mu))
            )
            buckets[key].append((_PRIOR if deviant else _NORMAL, idx))
    if unparsed:
        # 파싱 불가 항목도 버리지 않는다 — 압축기의 "원문 통과" 폴백과 같은 취지.
        buckets[("", "(미파싱)")] = unparsed
    return buckets


def _trace_buckets(items: list[dict]) -> dict[object, list[tuple[int, int]]]:
    """(서비스, 오퍼레이션)으로 묶고, 에러 스팬을 우선 등급으로.

    같은 등급 안에서는 느린 스팬이 먼저 오도록 지연을 tie-breaker로 쓴다.
    """
    graded: dict[object, list[tuple[int, float, int]]] = defaultdict(list)
    for idx, item in enumerate(items):
        operation, duration_ms, is_err = span_fields(item.get("raw") or "")
        key = (item.get("service") or "", operation)
        graded[key].append((_PRIOR if is_err else _NORMAL, -(duration_ms or 0.0), idx))

    buckets: dict[object, list[tuple[int, int]]] = {}
    for key, entries in graded.items():
        entries.sort()  # (에러 우선, 느린 순, 인덱스)
        # 정렬 결과의 순서 자체가 그룹 내 우선순위 — 등급을 순위로 치환해 결정성 유지.
        buckets[key] = [(rank, idx) for rank, (_, _, idx) in enumerate(entries)]
    return buckets


_BUCKETERS: dict[str, Callable[..., dict]] = {
    "log": lambda items, _tt: _log_buckets(items),
    "metric": _metric_buckets,
    "trace": lambda items, _tt: _trace_buckets(items),
}


# ---------------------------------------------------------------- 공개 API


def select_signals(
    modality: str, items: list[dict], trigger_time: str = "", limit: int | None = None
) -> Selection:
    """모달리티 항목을 상한 이내로 선별. 상한 이하면 그룹핑 없이 전량 통과.

    반환 항목은 timestamp 오름차순 — Spring이 행으로 저장하고 화면이 시간순으로 읽는다.
    같은 입력이면 항상 같은 결과가 나오므로(정렬 tie-breaker=원본 인덱스) 재전송해도
    같은 200건이 나가 멱등키(triggerTime) 정책과 어긋나지 않는다.
    """
    cap = settings.spring_signal_limit if limit is None else limit
    total = len(items)
    if total <= cap:
        return Selection(items, total)

    buckets = _BUCKETERS[modality](items, trigger_time)
    groups = _ordered_groups(buckets)
    picked = _round_robin(groups, cap)

    if len(groups) > cap:
        # 그룹 수가 상한을 넘으면 뒤쪽 그룹은 한 건도 못 싣는다. 다양성 보장이 깨지는
        # 유일한 경우라 근거를 남긴다.
        logger.info(
            "%s 선별: 그룹 %d개 > 상한 %d — 하위 %d개 그룹 누락",
            modality,
            len(groups),
            cap,
            len(groups) - cap,
        )

    # 동점(같은 시각)은 원본 인덱스로 끊어 수집기 순서를 보존한다.
    picked.sort(key=lambda i: (_chron(items[i].get("timestamp") or ""), i))
    return Selection([items[i] for i in picked], total)
