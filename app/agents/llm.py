"""LLM 공통 계층 — 모델 팩토리 + 전역 동시성 제한 + 입력 절단.

동시성·레이트리밋 설계(docs/agent-design.md):
  - 429 재시도는 langchain-openai 내장(지수 백오프, Retry-After 존중)에 위임.
    호출별 재시도 횟수를 코드에서 따로 관리하지 않는다.
  - 전역 세마포어가 워커·그래프 구조와 무관하게 동시 in-flight LLM 호출 수를 제한.
  - 입력 절단은 최후 방어선 — 기본 전략은 압축(bundle_compression) 쪽에 있다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Literal

from langchain_openai import ChatOpenAI

from app.core.config import settings

# reasoning effort 차등: router·scan low / 심층 medium / report high
Effort = Literal["low", "medium", "high"]

# 절단 발생 시 프롬프트에 삽입되는 고지 문구. 에이전트가 부분 관측임을 인지하게 한다.
TRUNCATION_NOTICE = "[... 입력 상한 초과로 이 지점의 데이터가 절단됨 ...]"


def _timeout_for(effort: Effort) -> int:
    """effort 등급별 요청 timeout(초) 조회."""
    return {
        "low": settings.llm_timeout_low_seconds,
        "medium": settings.llm_timeout_medium_seconds,
        "high": settings.llm_timeout_high_seconds,
    }[effort]


def make_llm(model: str, effort: Effort) -> ChatOpenAI:
    """역할별 ChatOpenAI 인스턴스 생성.

    max_retries: 429·일시 오류에 대한 SDK 내장 지수 백오프 재시도 횟수.
    timeout: 죽은 연결을 조기에 포기시키는 요청별 상한. 전체 소요 총량 보장은
        여기가 아니라 job_queue의 rca_overall_timeout_seconds가 담당한다.
    """
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        reasoning_effort=effort,
        max_retries=settings.openai_max_retries,
        timeout=_timeout_for(effort),
    )


# 전역 세마포어는 첫 사용 시 생성(지연 초기화).
# import 시점에 만들면 설정 오버라이드(테스트)나 이벤트 루프 바인딩 문제가 생긴다.
_semaphore: asyncio.Semaphore | None = None


def llm_limit() -> asyncio.Semaphore:
    """모든 LLM 호출을 감싸는 전역 세마포어. `async with llm_limit():` 로 사용."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.openai_max_concurrency)
    return _semaphore


def _find_focus(text: str, trigger_time: str | None) -> int:
    """절단 시 보존 중심점 — 트리거 시각(HH:MM:SS)이 등장하는 위치.

    못 찾으면 중앙(번들 윈도가 트리거 중심으로 잘려 오므로 무난한 기본값).
    """
    if trigger_time:
        m = re.search(r"\d{2}:\d{2}:\d{2}", trigger_time)
        if m:
            idx = text.find(m.group(0))
            if idx >= 0:
                return idx
    return len(text) // 2


def truncate_input(
    text: str,
    max_chars: int | None = None,
    trigger_time: str | None = None,
) -> str:
    """입력 상한 초과 시 트리거 시각 주변을 우선 보존하며 절단(최후 방어선).

    잘려나간 경계에 TRUNCATION_NOTICE를 삽입해 절단 사실을 프롬프트에 명시한다.
    """
    limit = max_chars if max_chars is not None else settings.openai_max_input_chars
    if len(text) <= limit:
        return text

    # 고지 문구(앞뒤 최대 2개) 자리를 뺀 본문 예산
    budget = max(1, limit - 2 * (len(TRUNCATION_NOTICE) + 2))
    focus = _find_focus(text, trigger_time)
    start = max(0, focus - budget // 2)
    end = min(len(text), start + budget)
    start = max(0, end - budget)  # 끝쪽에 몰렸을 때 예산 재확보

    parts: list[str] = []
    if start > 0:
        parts.append(TRUNCATION_NOTICE)
    parts.append(text[start:end])
    if end < len(text):
        parts.append(TRUNCATION_NOTICE)
    return "\n".join(parts)
