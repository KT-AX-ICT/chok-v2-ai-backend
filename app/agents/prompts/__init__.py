"""시스템 프롬프트 로더.

에이전트당 md 파일 1개(공통 규칙은 _common.md). 프롬프트 수정 = md 파일 수정 —
코드 변경·재배포 없이 리뷰 가능한 단위가 된다.

md는 순수 고정 지침만 담는다(플레이스홀더 없음). 가변 데이터(번들 raw 등)는
user 메시지로 넣는다 — OpenAI 프롬프트 캐싱이 고정 접두(시스템)에 걸리도록
`시스템(고정) → raw(가변)` 배치를 유지하기 위함.

버전 표기: 파일 첫 줄에 `<!-- version: ... -->` 주석으로 남긴다(git으로도 추적되지만,
실험 중 어떤 프롬프트 버전으로 호출했는지 결과 JSON에 같이 남기기 쉽게 하기 위함).
이 주석은 LLM에 전달되는 실제 텍스트에서는 제거한다 — 지침이 아니라 개발자용 메타이므로.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent
_VERSION_COMMENT_RE = re.compile(r"^<!--.*-->\s*$", re.MULTILINE)

# 로드 가능한 프롬프트 이름(오타를 즉시 잡기 위한 화이트리스트)
PROMPT_NAMES = ("router", "scan", "log", "metric", "trace", "report")


def _strip_version_comments(text: str) -> str:
    """`<!-- version: ... -->` 같은 한 줄 주석을 제거. LLM에는 순수 지침만 전달."""
    return _VERSION_COMMENT_RE.sub("", text).strip()


@cache
def load_prompt(name: str) -> str:
    """`_common.md` + `<name>.md`를 결합한 시스템 프롬프트 반환. 1회만 읽음."""
    if name not in PROMPT_NAMES:
        raise ValueError(f"알 수 없는 프롬프트: {name!r} (허용: {PROMPT_NAMES})")
    common = _strip_version_comments((_PROMPT_DIR / "_common.md").read_text(encoding="utf-8"))
    body = _strip_version_comments((_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8"))
    return f"{common.strip()}\n\n{body.strip()}"


def prompt_version(name: str) -> str | None:
    """`<name>.md` 첫 줄의 버전 주석 원문(있으면). 실험 결과 JSON에 메타로 남길 때 사용."""
    body = (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    m = re.match(r"^<!--\s*(.*?)\s*-->", body)
    return m.group(1) if m else None
