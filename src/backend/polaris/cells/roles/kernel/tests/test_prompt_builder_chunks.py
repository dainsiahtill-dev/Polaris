"""Tests for PromptBuilder chunk assembly integration."""

from __future__ import annotations

import types

from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder


def _make_profile():
    class _PromptPolicy:
        core_template_id = "pm"
        quality_checklist: list[str] = []

    class _ToolPolicy:
        whitelist: list[str] = []
        blacklist: list[str] = []
        allow_code_write = False
        allow_command_execution = False
        allow_file_delete = False

        @property
        def policy_id(self) -> str:
            return "stub-policy"

    return types.SimpleNamespace(
        role_id="director",
        display_name="Director",
        description="Execution role",
        version="1.0.0",
        responsibilities=["deliver work"],
        prompt_policy=_PromptPolicy(),
        tool_policy=_ToolPolicy(),
    )


def test_build_system_prompt_emits_chunk_receipt() -> None:
    builder = PromptBuilder()
    profile = _make_profile()

    prompt = builder.build_system_prompt(
        profile,
        prompt_appendix="请优先输出可执行步骤。",
        domain="document",
    )
    receipt = builder.get_last_request_receipt()

    assert "额外上下文" in prompt
    assert "领域模式" in prompt
    # Chunk cache-control blocks must be flattened into plain text, not Python repr strings.
    assert "[{'type': 'text'" not in prompt
    assert "cache_control" not in prompt
    assert receipt is not None
    assert receipt.chunk_count >= 4
    assert receipt.role_id == "director"
    assert receipt.strategy is not None
    assert receipt.strategy.domain == "document"


def test_build_system_prompt_fallback_to_legacy_join(monkeypatch) -> None:
    builder = PromptBuilder()
    profile = _make_profile()

    def _raise(**_kwargs: object) -> str:
        raise RuntimeError("chunk path unavailable")

    monkeypatch.setattr(builder, "_assemble_with_chunks", _raise)
    prompt = builder.build_system_prompt(profile, prompt_appendix="补充说明")

    assert "安全边界" in prompt
    assert "输出格式规范" in prompt
    assert "额外上下文" in prompt
