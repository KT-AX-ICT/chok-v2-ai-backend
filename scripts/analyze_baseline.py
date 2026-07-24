"""baseline(정상 운영) 로그 24h profile 생성 — 실험용 오프라인 스크립트.

datasets/baseline/log/**/*.log (24시간 무장애 운영 로그)를 compress_logs와 동일한
Drain 클러스터링으로 묶어서, (서비스, 레벨, 패턴)별 정상 발생 횟수 표를 만든다.

이 표가 "log profile" — 나중에 compress_logs가 인시던트 로그의 패턴을 여기서
찾아보고 "이거 원래도 있던 만성 패턴"인지 판별하는 근거 자료가 된다. 지금은 표만
만든다 — 런타임 조회 연동은 별도 작업.

실행: .venv/Scripts/python.exe scripts/analyze_baseline.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.bundle_compression import LEVEL_RE, make_miner  # noqa: E402

BASELINE_LOG_DIR = _ROOT / "datasets" / "baseline" / "log"
OUTPUT_PATH = _ROOT / "datasets" / "baseline" / "log_profile.json"

# 파일명(서비스별 로그소스) → 번들 IngestBundle.logs[].service 표기 매핑.
# 실제 job 데이터(job #3)에서 관측된 service 태그 기준.
SERVICE_NAME_MAP = {
    "UserService": "user",
    "MediaService": "media",
    "ComposePostService": "composepost",
    "HomeTimelineService": "hometimeline",
    "NginxThrift": "nginx",
    "PostStorageService": "poststorage",
    "SocialGraphService": "socialgraph",
    "TextService": "text",
    "UniqueIdService": "uniqueid",
    "UrlShortenService": "urlshorten",
    "UserMentionService": "usermention",
    "UserTimelineService": "usertimeline",
}

_TS_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]")


def _parse_log_ts(raw: str) -> datetime | None:
    """`[2025-Nov-03 22:03:13.065368] ...` 형식에서 시각만 파싱. 실패 시 None."""
    m = _TS_RE.match(raw)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("ts"), "%Y-%b-%d %H:%M:%S.%f")
    except ValueError:
        return None


def _service_from_filename(path: Path) -> str:
    stem = path.stem.rstrip("_")  # "UserService_" -> "UserService"
    return SERVICE_NAME_MAP.get(stem, stem.lower())


def build_profile() -> list[dict]:
    """baseline 로그 전체를 클러스터링해 (서비스,레벨,패턴)별 24h 발생 횟수 표를 만든다."""
    log_files = sorted(BASELINE_LOG_DIR.glob("*/*.log"))
    if not log_files:
        raise SystemExit(f"baseline 로그 파일을 찾을 수 없음: {BASELINE_LOG_DIR}")

    groups: dict[tuple[str, str, int], dict] = {}
    for path in log_files:
        service = _service_from_filename(path)
        miner = make_miner()  # 서비스(파일)당 별도 마이너 — 클러스터 학습 오염 방지
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                level_m = LEVEL_RE.search(line)
                level = level_m.group(1).upper() if level_m else "-"
                cluster = miner.add_log_message(line)
                # cluster_id는 마이너 인스턴스 로컬이라 파일 간 비교 불가 — 매칭 키는
                # template_mined(와일드카드 처리된 템플릿 텍스트) 사용. 같은 마스킹
                # 설정(make_miner)을 쓰는 한 번들 쪽 compress_logs와 텍스트가 일치한다.
                key = (service, level, cluster["template_mined"])
                ts = _parse_log_ts(line)
                g = groups.get(key)
                if g is None:
                    groups[key] = {
                        "count": 1,
                        "first": ts.isoformat() if ts else None,
                        "last": ts.isoformat() if ts else None,
                        "sample": line,
                    }
                else:
                    g["count"] += 1
                    if ts:
                        g["last"] = ts.isoformat()

    rows = [
        {
            "service": service,
            "level": level,
            "template": template,
            "count": g["count"],
            "first": g["first"],
            "last": g["last"],
            "sample": g["sample"],
        }
        for (service, level, template), g in groups.items()
    ]
    rows.sort(key=lambda r: (r["service"], -r["count"]))
    return rows


def main() -> None:
    rows = build_profile()
    OUTPUT_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)}개 (서비스,레벨,패턴) 프로필 -> {OUTPUT_PATH}")

    preview = sorted(rows, key=lambda r: (r["level"] != "ERROR", -r["count"]))[:10]
    print("\n상위 미리보기 (ERROR 우선, count 내림차순):")
    for r in preview:
        print(f"  {r['service']:<14} {r['level']:<6} x{r['count']:<6} {r['sample'][:90]}")


if __name__ == "__main__":
    main()
