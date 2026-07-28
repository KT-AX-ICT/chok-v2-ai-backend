"""프롬프트 평가 하네스 — 러너 (설계 B·D단계).

고정 픽스처(`fixtures/<scenario>.json`)를 `orchestrator.run`에 그대로 먹여 RcaResult를
얻고, 실행별 아티팩트(프롬프트 스냅샷·result·meta)와 인덱스(index.csv)를 남긴다.
입력이 고정이라 변수는 프롬프트·모델뿐 — 프롬프트를 바꿔 재실행하면 버전 간 비교가 된다.

실행(리포 루트에서):
    python -m eval.run                      # 픽스처가 있는 시나리오 전부, 1회씩
    python -m eval.run cpu --repeat 3       # cpu만 3회(LLM 비결정성 관찰)
    python -m eval.run --serve              # 평가 후 뷰어 서버까지 (서빙만: python -m eval.serve)

사전: OPENAI_API_KEY 등 .env 설정(실제 LLM 호출 = 실제 과금). 채점은 `eval/scoring.py`가
있으면 자동 적용(없으면 결과만 기록).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agents.orchestrator import orchestrator
from app.agents.prompts import PROMPT_NAMES, load_prompt
from app.core.config import settings
from app.schemas.contracts import IngestBundle

EVAL_DIR = Path(__file__).parent
FIXTURES_DIR = EVAL_DIR / "fixtures"
RUNS_DIR = EVAL_DIR / "runs"
INDEX_CSV = EVAL_DIR / "index.csv"
SCENARIOS = ("cpu", "kill_media", "code_media")

INDEX_COLS = [
    "ts", "scenario", "set_hash", "report_model",
    "pred_type", "pred_service", "pred_severity",
    "correct", "latency_s", "runs_path",
]

# 채점은 선택적 — scoring.py가 없으면 결과만 기록한다(하네스 초기엔 채점 없이 베이스라인).
try:
    from eval.scoring import score_result  # (scenario, result) -> (bool | None, str)
except ImportError:
    score_result = None


def _prompt_snapshot() -> tuple[dict[str, str], dict[str, str], str]:
    """6개 프롬프트 원문 + 각 content hash + 합친 set hash.

    커밋 안 한 실험 프롬프트도 실제 쓰인 내용의 해시로 결과와 묶인다.
    """
    texts = {name: load_prompt(name) for name in PROMPT_NAMES}
    hashes = {n: hashlib.sha256(t.encode("utf-8")).hexdigest()[:12] for n, t in texts.items()}
    set_hash = hashlib.sha256("".join(hashes[n] for n in PROMPT_NAMES).encode()).hexdigest()[:12]
    return texts, hashes, set_hash


def _load_fixture(scenario: str) -> IngestBundle:
    path = FIXTURES_DIR / f"{scenario}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"픽스처 없음: {path} — capture_sink로 먼저 캡처하세요 "
            f"(python eval/capture_sink.py {scenario} + SDK scripts/run_local_demo.sh {scenario})"
        )
    return IngestBundle.model_validate_json(path.read_text(encoding="utf-8"))


def _record(
    scenario: str,
    result,
    elapsed: float,
    texts: dict[str, str],
    hashes: dict[str, str],
    set_hash: str,
    ts: str,
) -> tuple[Path, dict]:
    """실행별 아티팩트를 runs/<ts>_<scenario>_<sethash>/에 저장하고 meta 반환."""
    run_dir = RUNS_DIR / f"{ts}_{scenario}_{set_hash}"
    run_dir.mkdir(parents=True, exist_ok=True)

    for name, text in texts.items():  # 프롬프트 스냅샷(그 실행이 쓴 원문 그대로)
        (run_dir / f"prompt_{name}.md").write_text(text, encoding="utf-8")

    (run_dir / "result.json").write_text(
        json.dumps(result.model_dump(by_alias=True, exclude_none=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    meta = {
        "scenario": scenario,
        "ts": ts,
        "set_hash": set_hash,
        "prompt_hashes": hashes,
        "models": {
            "report": settings.openai_model_report,
            "analysis": settings.openai_model_analysis,
            "light": settings.openai_model_light,
        },
        "latency_s": round(elapsed, 2),
        "pred_type": result.type,
        "pred_service": result.service,
        "pred_severity": result.severity,
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir, meta


def _append_index(meta: dict, run_dir: Path, correct: bool | None) -> None:
    """스칼라 요약을 index.csv에 append. 중첩 원문은 run_dir 파일로 분리."""
    is_new = not INDEX_CSV.exists()
    with INDEX_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(INDEX_COLS)
        writer.writerow([
            meta["ts"], meta["scenario"], meta["set_hash"], meta["models"]["report"],
            meta["pred_type"], meta["pred_service"], meta["pred_severity"],
            "" if correct is None else ("O" if correct else "X"),
            meta["latency_s"], str(run_dir.relative_to(EVAL_DIR)),
        ])


async def _run_scenario(scenario: str, texts, hashes, set_hash) -> None:
    bundle = _load_fixture(scenario)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    started = time.monotonic()
    result = await orchestrator.run(0, bundle)  # job_id=0 (eval 전용)
    elapsed = time.monotonic() - started

    run_dir, meta = _record(scenario, result, elapsed, texts, hashes, set_hash, ts)

    correct: bool | None = None
    verdict = ""
    if score_result is not None:
        correct, verdict = score_result(scenario, result)
    _append_index(meta, run_dir, correct)

    mark = {True: "O", False: "X", None: "-"}[correct]
    print(
        f"[{scenario}] {mark} type={result.type} service={result.service} "
        f"({elapsed:.1f}s) → {run_dir.relative_to(EVAL_DIR)}"
        + (f"  {verdict}" if verdict else "")
    )


async def _main_async(scenarios: list[str], repeat: int) -> None:
    texts, hashes, set_hash = _prompt_snapshot()
    print(f"프롬프트 set_hash={set_hash} · report_model={settings.openai_model_report}")
    for scenario in scenarios:
        for _ in range(repeat):
            await _run_scenario(scenario, texts, hashes, set_hash)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="프롬프트 평가 러너")
    # choices는 두지 않는다 — nargs="*" + 리스트 default + choices 조합은 Python
    # < 3.14에서 default 리스트 전체를 하나의 선택지로 검증해 실패한다(bpo-9625).
    # 아래에서 픽스처 존재 여부로 어차피 걸러 없는 시나리오는 스킵하므로 검증도 중복.
    parser.add_argument(
        "scenarios", nargs="*", default=list(SCENARIOS),
        help="평가할 시나리오(기본: 픽스처가 있는 것 전부). 선택: " + ", ".join(SCENARIOS),
    )
    parser.add_argument("--repeat", type=int, default=1, help="시나리오당 반복 횟수")
    parser.add_argument("--serve", action="store_true", help="평가 후 뷰어 서버를 이어서 실행")
    parser.add_argument("--port", type=int, default=8899, help="--serve 시 뷰어 포트 (기본 8899)")
    args = parser.parse_args(argv)

    # 픽스처가 있는 것만 남긴다(없으면 안내 후 스킵).
    available = [s for s in args.scenarios if (FIXTURES_DIR / f"{s}.json").exists()]
    missing = [s for s in args.scenarios if s not in available]
    for s in missing:
        print(f"[스킵] {s}: 픽스처 없음 ({FIXTURES_DIR / f'{s}.json'})")
    if not available:
        print("실행할 픽스처가 없습니다 — capture_sink로 먼저 캡처하세요.")
        return 1

    asyncio.run(_main_async(available, args.repeat))
    if args.serve:
        from eval.serve import serve  # 지연 import — 서빙 안 하면 불필요

        print(f"\n평가 완료 — 뷰어 서버를 띄웁니다 (:{args.port})")
        serve(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
