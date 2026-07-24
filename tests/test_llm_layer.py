"""LLM 공통 계층(app/agents/llm.py) 단위 테스트 — LLM 실호출 없음."""

import asyncio

import app.agents.llm as llm_mod
from app.agents.llm import TRUNCATION_NOTICE, llm_limit, make_llm, truncate_input


def test_make_llm_applies_model_effort_retries():
    llm = make_llm("gpt-5.4-mini-2026-03-17", "medium")
    assert llm.model_name == "gpt-5.4-mini-2026-03-17"
    assert llm.reasoning_effort == "medium"
    assert llm.max_retries == 3  # settings.openai_max_retries 기본값


def test_make_llm_applies_effort_timeout():
    # effort 등급별 요청 timeout(초) — 죽은 연결 조기 포기용(settings 기본값 기준).
    assert make_llm("m", "low").request_timeout == 60
    assert make_llm("m", "medium").request_timeout == 180
    assert make_llm("m", "high").request_timeout == 300


def test_llm_limit_is_singleton_with_configured_capacity(monkeypatch):
    monkeypatch.setattr(llm_mod, "_semaphore", None)  # 지연 초기화 리셋
    sem = llm_limit()
    assert sem is llm_limit()  # 전역 1개 재사용
    assert sem._value == 4  # settings.openai_max_concurrency 기본값


async def test_llm_limit_caps_concurrency(monkeypatch):
    monkeypatch.setattr(llm_mod, "_semaphore", asyncio.Semaphore(2))
    peak = 0
    active = 0

    async def call():
        nonlocal peak, active
        async with llm_limit():
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(call() for _ in range(6)))
    assert peak <= 2


def test_truncate_noop_under_limit():
    text = "짧은 입력"
    assert truncate_input(text, max_chars=100) is text


def test_truncate_preserves_trigger_region_and_marks_notice():
    # [filler-A ...][트리거 시각 라인][filler-B ...] 구조의 긴 텍스트
    filler_a = "\n".join(f"[10:00:{i % 60:02d}.000] A라인 {i}" for i in range(500))
    trigger_line = "[10:01:30.000] ERROR 트리거 지점"
    filler_b = "\n".join(f"[10:02:{i % 60:02d}.000] B라인 {i}" for i in range(500))
    text = f"{filler_a}\n{trigger_line}\n{filler_b}"

    out = truncate_input(text, max_chars=2000, trigger_time="2026-01-15T10:01:30Z")
    assert len(out) <= 2000
    assert trigger_line in out  # 트리거 주변 보존
    assert out.count(TRUNCATION_NOTICE) == 2  # 앞뒤 모두 잘렸음을 명시


def test_truncate_fallback_keeps_middle_without_trigger_match():
    text = "x" * 10_000
    out = truncate_input(text, max_chars=1000, trigger_time=None)
    assert len(out) <= 1000
    assert TRUNCATION_NOTICE in out
