"""프롬프트 로더(app/agents/prompts) 단위 테스트."""

import pytest

from app.agents.prompts import PROMPT_NAMES, load_prompt


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_loads_all_prompts_with_common_prefix(name):
    prompt = load_prompt(name)
    assert prompt.startswith("# 공통 규칙")  # _common.md 결합
    assert "# 역할:" in prompt  # 각 에이전트 지침 본문


def test_unknown_name_rejected():
    with pytest.raises(ValueError, match="알 수 없는 프롬프트"):
        load_prompt("nope")


def test_cached_returns_same_object():
    assert load_prompt("planner") is load_prompt("planner")
