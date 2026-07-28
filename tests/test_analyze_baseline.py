"""정상 baseline 프로파일 생성 스크립트 단위 테스트."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import analyze_baseline as ab  # noqa: E402


def test_build_profile_counts_per_service_pattern(tmp_path, monkeypatch):
    log_dir = tmp_path / "log" / "run1"
    log_dir.mkdir(parents=True)
    (log_dir / "UserService_.log").write_text(
        "[2025-Nov-03 22:03:12.634283] <error>: dup key user_id: 444\n" * 5
        + "[2025-Nov-03 22:03:13.000000] <info>: started\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "BASELINE_LOG_DIR", tmp_path / "log")
    rows = ab.build_profile()
    err = [r for r in rows if r["service"] == "user" and r["level"] == "ERROR"]
    assert err and err[0]["count"] == 5
