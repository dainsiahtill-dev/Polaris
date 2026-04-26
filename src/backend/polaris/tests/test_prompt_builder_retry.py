from __future__ import annotations

from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder


def test_build_retry_prompt_tolerates_none_data() -> None:
    builder = PromptBuilder(workspace=".")

    prompt = builder.build_retry_prompt(
        base_prompt="BASE_PROMPT",
        last_validation={
            "errors": ["JSON解析错误: missing field"],
            "suggestions": ["请补全必填字段"],
            "data": None,
        },
        attempt=1,
    )

    assert "BASE_PROMPT" in prompt
    assert "上一次的输出存在问题" in prompt
    assert "JSON解析错误" in prompt


def test_build_retry_prompt_normalizes_scalar_fields() -> None:
    builder = PromptBuilder(workspace=".")

    prompt = builder.build_retry_prompt(
        base_prompt="BASE_PROMPT",
        last_validation={
            "errors": "工具执行失败: timeout",
            "suggestions": None,
            "data": "unexpected",
        },
        attempt=1,
    )

    assert "BASE_PROMPT" in prompt
    assert "工具执行失败" in prompt
