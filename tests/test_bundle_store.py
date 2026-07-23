"""bundle_store 테스트 — 3종 원본 파일 분리 저장·복원·삭제·고아 회수."""

import json
import os
import time

import pytest

from app.schemas.contracts import IngestBundle
from app.services import bundle_store

_BUNDLE = {
    "bundleVersion": "1.0",
    "companyCode": "SN001",
    "window": {"start": "2026-01-15T10:00:00Z", "end": "2026-01-15T10:03:00Z"},
    "triggerInfo": {"triggerTime": "2026-01-15T10:01:30Z", "triggeredBy": ["log"]},
    "logs": [{"timestamp": "2026-01-15T10:01:00Z", "service": "api", "raw": "ERROR boom"}],
    "metrics": [{"timestamp": "2026-01-15T10:01:00Z", "service": "api", "raw": "cpu=0.9"}],
    "traces": [{"timestamp": "2026-01-15T10:01:00Z", "service": "media", "raw": "span 500ms"}],
}


async def test_split_removes_heavy_arrays_from_db_side():
    """DB에 남길 번들에는 3종 배열이 빠지고, 나머지 메타는 그대로 유지."""
    light, name = await bundle_store.split_and_save(dict(_BUNDLE))

    for key in bundle_store.SIGNAL_KEYS:
        assert key not in light
    assert light["companyCode"] == "SN001"  # 경량 메타는 보존
    assert light["window"]["start"] == "2026-01-15T10:00:00Z"
    assert name.endswith(".json")


async def test_split_does_not_mutate_input():
    """호출부가 원본 dict를 계속 쓸 수 있어야 하므로 입력을 변형하지 않는다."""
    source = dict(_BUNDLE)
    await bundle_store.split_and_save(source)
    assert set(bundle_store.SIGNAL_KEYS) <= set(source)


async def test_file_holds_only_signals():
    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    saved = json.loads((bundle_store.storage_dir() / name).read_text(encoding="utf-8"))

    assert set(saved) == set(bundle_store.SIGNAL_KEYS)
    assert saved["logs"][0]["raw"] == "ERROR boom"
    assert "window" not in saved  # 메타는 DB 몫


async def test_restore_round_trips_to_original_bundle():
    """경량 번들 + 파일을 합치면 원본과 같은 IngestBundle이 된다."""
    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    restored = await bundle_store.restore_bundle(light, name)

    expected = IngestBundle.model_validate(_BUNDLE)
    assert restored.model_dump(by_alias=True) == expected.model_dump(by_alias=True)


async def test_restore_without_path_reads_inline_arrays():
    """signals_path가 없으면 파일 분리 도입 이전 job — bundle 안의 배열을 그대로 쓴다."""
    restored = await bundle_store.restore_bundle(_BUNDLE, None)
    assert restored.logs[0].raw == "ERROR boom"


async def test_restore_raises_when_file_gone():
    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    (bundle_store.storage_dir() / name).unlink()

    with pytest.raises(bundle_store.SignalsMissing):
        await bundle_store.restore_bundle(light, name)


async def test_restore_raises_when_file_corrupted():
    """반쯤 쓰인 파일을 정상 데이터로 오인하지 않는다."""
    light, name = await bundle_store.split_and_save(dict(_BUNDLE))
    (bundle_store.storage_dir() / name).write_text("{ broken", encoding="utf-8")

    with pytest.raises(bundle_store.SignalsMissing):
        await bundle_store.restore_bundle(light, name)


async def test_discard_removes_file_and_tolerates_repeat():
    """워커와 재전송 루프가 같은 job을 확정할 수 있어 삭제가 두 번 불려도 안전해야 한다."""
    _, name = await bundle_store.split_and_save(dict(_BUNDLE))
    path = bundle_store.storage_dir() / name

    await bundle_store.discard(name)
    assert not path.exists()
    await bundle_store.discard(name)  # 두 번째 호출도 예외 없이 통과
    await bundle_store.discard(None)  # 경로 없는 job(레거시)도 무해


async def test_sweep_keeps_files_still_in_use():
    """아직 끝나지 않은 job이 쓰는 파일은 오래됐어도 지우지 않는다(1차 방어선).

    전송이 하루 넘게 지연돼도 원본을 잃지 않아야 한다.
    """
    _, in_use = await bundle_store.split_and_save(dict(_BUNDLE))
    _, leftover = await bundle_store.split_and_save(dict(_BUNDLE))

    aged = time.time() - 7200
    for name in (in_use, leftover):
        os.utime(bundle_store.storage_dir() / name, (aged, aged))

    removed = await bundle_store.sweep_orphans(max_age_seconds=3600, keep={in_use})

    assert removed == 1
    assert (bundle_store.storage_dir() / in_use).exists()  # 사용 중 → 보존
    assert not (bundle_store.storage_dir() / leftover).exists()


async def test_sweep_removes_only_aged_files():
    """정상 경로는 job 종료 시 삭제이므로, 오래 남은 파일만 고아로 보고 회수."""
    _, old = await bundle_store.split_and_save(dict(_BUNDLE))
    _, fresh = await bundle_store.split_and_save(dict(_BUNDLE))

    old_path = bundle_store.storage_dir() / old
    aged = time.time() - 7200  # 2시간 전
    os.utime(old_path, (aged, aged))

    removed = await bundle_store.sweep_orphans(max_age_seconds=3600)

    assert removed == 1
    assert not old_path.exists()
    assert (bundle_store.storage_dir() / fresh).exists()
