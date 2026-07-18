"""시스템 프롬프트 로더.

에이전트당 md 파일 1개(공통 규칙은 _common.md). 프롬프트 수정 = md 파일 수정 —
코드 변경·재배포 없이 리뷰 가능한 단위가 된다.

md는 순수 고정 지침만 담는다(플레이스홀더 없음). 가변 데이터(번들 raw 등)는
user 메시지로 넣는다 — OpenAI 프롬프트 캐싱이 고정 접두(시스템)에 걸리도록
`시스템(고정) → raw(가변)` 배치를 유지하기 위함.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent

# 로드 가능한 프롬프트 이름(오타를 즉시 잡기 위한 화이트리스트)
PROMPT_NAMES = ("planner", "scan", "log", "metric", "trace", "report")


@cache
def load_prompt(name: str) -> str:
    """`_common.md` + `<name>.md`를 결합한 시스템 프롬프트 반환. 1회만 읽음."""
    if name not in PROMPT_NAMES:
        raise ValueError(f"알 수 없는 프롬프트: {name!r} (허용: {PROMPT_NAMES})")
    common = (_PROMPT_DIR / "_common.md").read_text(encoding="utf-8")
    body = (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    return f"{common.strip()}\n\n{body.strip()}"
