"""번들 원본 신호(logs/metrics/traces) 파일 저장소.

SDK 번들의 원본 3종은 한 건이 수십 MB까지 커진다(로그 30만 줄 관측). 이걸
`ingest_job.bundle`(JSON 컬럼)에 그대로 넣으면 MySQL `max_allowed_packet`을 넘겨 INSERT가
실패하고, 그때 서버가 커넥션을 끊어버린다. 그래서 무거운 3종 배열만 파일로 빼고 DB에는
파일 이름만 남긴다.

  - DB에 남는 bundle : bundleVersion·companyCode·window·triggerInfo·modalityInfo (가벼운 메타)
  - 파일에 담기는 것 : logs·metrics·traces (무거운 원본)

파일 수명은 job 종료까지다. 분석뿐 아니라 **Spring 전송 페이로드에도 3종 배열이 실리므로**
(spring_client), DONE(전송 성공)이나 FAILED(사유 전송)에 도달해야 지울 수 있다. 재전송 루프가
DELIVERING job을 다시 밀 때도 이 파일이 필요하다. 종료 시점에 지우지 못한 파일은
`sweep_orphans()`가 나이 기준으로 회수한다.

I/O는 전부 `asyncio.to_thread`로 감싼다 — 수십 MB짜리 직렬화·파일 쓰기를 이벤트 루프에서
동기로 돌리면 그동안 서버 전체가 멈춘다.

저장 위치가 설정(`bundle_storage_dir`) 한 곳에서만 정해지므로, 나중에 공유 스토리지로 옮길
때 이 모듈만 바꾸면 된다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Iterable

from app.core.config import settings
from app.schemas.contracts import IngestBundle

logger = logging.getLogger(__name__)

# 파일로 분리하는 무거운 키. 나머지 번들 필드는 DB에 그대로 남는다.
SIGNAL_KEYS = ("logs", "metrics", "traces")

_SUFFIX = ".json"
_TMP_SUFFIX = ".tmp"


class SignalsMissing(RuntimeError):
    """번들 원본 파일을 찾을 수 없음. 호출부는 job을 FAILED로 확정해야 한다."""


def storage_dir() -> Path:
    """저장 디렉터리(없으면 생성). 컨테이너에서는 볼륨이 마운트된 경로."""
    path = Path(settings.bundle_storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(name: str) -> Path:
    """파일 이름 → 실제 경로. DB에는 이름만 저장하므로 디렉터리는 설정에서 해석한다.

    경로 조작 방지를 위해 이름만 취한다(디렉터리 성분 제거).
    """
    return storage_dir() / Path(name).name


# ---------------------------------------------------------------- 쓰기


def _write_sync(payload: dict) -> str:
    """3종 배열을 파일로 쓰고 파일 이름 반환. 임시 파일에 쓴 뒤 원자적으로 교체한다.

    (쓰다가 프로세스가 죽어도 반쯤 쓰인 파일이 읽히지 않게 한다.)
    """
    name = f"{uuid.uuid4().hex}{_SUFFIX}"
    final = _resolve(name)
    tmp = final.with_suffix(_TMP_SUFFIX)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, final)
    return name


async def split_and_save(bundle_dump: dict) -> tuple[dict, str]:
    """번들 dict를 (DB에 남길 경량 번들, 파일 이름)으로 분리 저장.

    입력 dict는 변형하지 않는다(호출부가 원본을 계속 쓸 수 있게).
    """
    signals = {key: bundle_dump.get(key) or [] for key in SIGNAL_KEYS}
    light = {k: v for k, v in bundle_dump.items() if k not in SIGNAL_KEYS}
    name = await asyncio.to_thread(_write_sync, signals)
    return light, name


# ---------------------------------------------------------------- 읽기


def _read_sync(name: str) -> dict:
    path = _resolve(name)
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise SignalsMissing(f"번들 원본 파일 없음: {name}") from exc
    except json.JSONDecodeError as exc:
        raise SignalsMissing(f"번들 원본 파일 손상: {name}") from exc


async def restore_bundle(stored: dict, signals_path: str | None) -> IngestBundle:
    """DB의 경량 번들 + 파일의 3종 배열을 합쳐 IngestBundle로 복원.

    `signals_path`가 없으면 이 기능 도입 이전에 만들어진 job이므로, 배열이 `stored` 안에
    그대로 들어 있다고 보고 그대로 검증한다(배포 시점에 진행 중이던 job 보호).

    파일이 없거나 깨졌으면 SignalsMissing을 던진다.
    """
    if signals_path is None:
        return IngestBundle.model_validate(stored)
    signals = await asyncio.to_thread(_read_sync, signals_path)
    return IngestBundle.model_validate({**stored, **signals})


# ---------------------------------------------------------------- 삭제


def _discard_sync(name: str) -> bool:
    path = _resolve(name)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


async def discard(signals_path: str | None) -> None:
    """job 종료 시 원본 파일 삭제(best-effort). 실패해도 흐름을 막지 않는다.

    이미 없으면 조용히 지나간다 — 재전송 루프와 워커가 같은 job을 확정할 수 있어
    삭제가 두 번 불릴 수 있다.
    """
    if not signals_path:
        return
    try:
        if await asyncio.to_thread(_discard_sync, signals_path):
            logger.debug("번들 원본 파일 삭제: %s", signals_path)
    except OSError:
        logger.warning("번들 원본 파일 삭제 실패(무시): %s", signals_path, exc_info=True)


def _sweep_sync(max_age_seconds: float, keep: frozenset[str]) -> int:
    """사용 중이 아니면서 오래 남은 파일을 지우고 건수 반환.

    거르는 순서:
      1. 아직 끝나지 않은 job이 쓰는 파일(keep)은 무조건 제외 — 사용 중인 파일 삭제 방지.
      2. 남은 것 중 마지막 수정 후 경과 시간이 기준을 넘은 것만 삭제.

    job 종료 시점 삭제가 정상 경로이므로, 여기 걸리는 건 놓친 파일이다. 대표적으로 파일은
    썼는데 job 기록이 실패한 경우가 해당한다. 2번의 경과 시간 조건은 keep 목록을 조회한
    직후에 새로 만들어진 파일까지 지켜주는 2차 방어선이다.
    """
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in storage_dir().iterdir():
        if not path.is_file() or path.name in keep:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # 다른 주체가 먼저 지웠거나 권한 문제 — 다음 주기에 재시도
            continue
    return removed


async def sweep_orphans(
    max_age_seconds: float | None = None,
    keep: Iterable[str] | None = None,
) -> int:
    """고아 파일 회수. 삭제 건수 반환.

    keep: 아직 끝나지 않은 job이 쓰는 파일 이름. 호출부(job_cleanup)가 DB에서 조회해 넘긴다
          — 이 모듈은 DB를 모르는 파일시스템 전용으로 둔다.
    """
    age = (
        max_age_seconds
        if max_age_seconds is not None
        else settings.bundle_orphan_max_age_hours * 3600
    )
    return await asyncio.to_thread(_sweep_sync, age, frozenset(keep or ()))
