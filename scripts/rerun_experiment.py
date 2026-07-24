"""저장된 job의 번들로 오케스트레이터를 실제로 재실행 — 실험 브랜치 검증용.

DB의 경량 번들(export된 JSON) + data/bundles의 원본 신호 파일을 합쳐 IngestBundle을
복원하고, 실제 LLM 호출로 RcaResult를 재생성한다. 결과는 버전 메타(실험명, git
브랜치/커밋, 프롬프트 버전, 실행 시각)를 붙여 JSON으로 저장한다 — 같은 job을 코드
변경 전/후로 재실행해 비교할 수 있게.

실행: .venv/Scripts/python.exe scripts/rerun_experiment.py <job_id> <light_bundle_json_path> <signals_json_path> <experiment_tag>
예:   .venv/Scripts/python.exe scripts/rerun_experiment.py 3 tmp/job3_light_bundle.json data/bundles/a1814a3e84c94dd180765d342da34fef.json baseline-injection-v1
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.agents.orchestrator import orchestrator  # noqa: E402
from app.agents.prompts import PROMPT_NAMES, prompt_version  # noqa: E402
from app.schemas.contracts import IngestBundle  # noqa: E402


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


async def _run(job_id: int, light_path: Path, signals_path: Path, tag: str) -> None:
    light = json.loads(light_path.read_text(encoding="utf-8"))
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    bundle = IngestBundle.model_validate({**light, **signals})

    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    result = await orchestrator.run(job_id, bundle)
    elapsed = round(time.monotonic() - started, 1)

    out = {
        "job_id": job_id,
        "experiment_tag": tag,
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "prompt_versions": {name: prompt_version(name) for name in PROMPT_NAMES},
        "started_at": started_at,
        "elapsed_seconds": elapsed,
        "result": result.model_dump(by_alias=True, exclude_none=True),
    }

    out_dir = _ROOT / "tmp" / "rca_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"job{job_id}_rca_result_{tag}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"저장: {out_path}")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "사용법: rerun_experiment.py <job_id> <light_bundle_json> <signals_json> <experiment_tag>"
        )
    job_id = int(sys.argv[1])
    light_path = Path(sys.argv[2])
    signals_path = Path(sys.argv[3])
    tag = sys.argv[4]
    asyncio.run(_run(job_id, light_path, signals_path, tag))


if __name__ == "__main__":
    main()
