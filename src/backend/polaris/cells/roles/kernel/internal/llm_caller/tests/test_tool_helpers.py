"""Tests for LLM caller tool helper parsing."""

from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import extract_native_tool_calls


class TestExtractNativeToolCalls:
    """Tests for native and text fallback tool extraction."""

    def test_gemma_inline_tool_call_preserves_outer_tool_name(self) -> None:
        """Regression: Gemma inline protocol should use the outer call name."""
        response_text = (
            '<|tool_call>call:write_file{content:<|"|>{\n'
            '  "name": "bootstrap-project",\n'
            '  "version": "1.0.0"\n'
            '}<|"|>,file:<|"|>package.json<|"|>}<tool_call|>'
        )

        calls, provider = extract_native_tool_calls(
            {},
            provider_id="openai_compat-1780683130410",
            model="gemma-4-12B-it-Q8_0",
            response_text=response_text,
        )

        assert provider == "text_fallback"
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "write_file"
        arguments = json.loads(calls[0]["function"]["arguments"])
        assert arguments["file"] == "package.json"
        assert '"name": "bootstrap-project"' in arguments["content"]

    def test_plain_package_json_text_is_not_native_tool_call(self) -> None:
        """Regression: plain package.json content must stay data, not control."""
        calls, provider = extract_native_tool_calls(
            {},
            provider_id="openai_compat-1780683130410",
            model="gemma-4-12B-it-Q8_0",
            response_text='{"name": "polaris-project", "version": "1.0.0"}',
        )

        assert calls == []
        assert provider == "openai"


# ============ Prong A: from-scratch first-turn write restriction (I3-r23) ============

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (  # noqa: E402
    resolve_from_scratch_write_target,
    restrict_tool_definitions_to_write,
)


def _tools(*names: str) -> list[dict]:
    return [{"type": "function", "function": {"name": n}} for n in names]


class TestResolveFromScratchWriteTarget:
    def test_from_scratch_leaf_returns_target(self, tmp_path: Path) -> None:
        co = {"construction_step": {"step_id": "S3", "target_file": "main.js", "verify": "node --check main.js"}}
        assert resolve_from_scratch_write_target(co, str(tmp_path)) == "main.js"

    def test_edit_on_prior_returns_none(self, tmp_path: Path) -> None:
        co = {"construction_step": {"step_id": "S", "target_file": "main.js", "edit_on_prior": True}}
        assert resolve_from_scratch_write_target(co, str(tmp_path)) is None

    def test_existing_target_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("// prior\n", encoding="utf-8")
        co = {"construction_step": {"step_id": "S", "target_file": "main.js"}}
        assert resolve_from_scratch_write_target(co, str(tmp_path)) is None

    def test_dot_slash_target_normalized(self, tmp_path: Path) -> None:
        co = {"construction_step": {"step_id": "S", "target_file": "./main.js"}}
        assert resolve_from_scratch_write_target(co, str(tmp_path)) == "main.js"

    def test_no_construction_step_returns_none(self, tmp_path: Path) -> None:
        assert resolve_from_scratch_write_target({}, str(tmp_path)) is None
        assert resolve_from_scratch_write_target(None, str(tmp_path)) is None

    def test_env_off_disables(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_FIRST_TURN_WRITE", "off")
        co = {"construction_step": {"step_id": "S", "target_file": "main.js"}}
        assert resolve_from_scratch_write_target(co, str(tmp_path)) is None


class TestRestrictToolDefinitionsToWrite:
    def test_drops_read_tools_keeps_write(self) -> None:
        kept = restrict_tool_definitions_to_write(
            _tools("read_file", "repo_rg", "write_file", "execute_command", "glob")
        )
        names = {d["function"]["name"] for d in kept}
        assert names == {"write_file", "execute_command"}

    def test_keeps_all_mutation_tools(self) -> None:
        kept = restrict_tool_definitions_to_write(_tools("write_file", "edit_file", "edit_blocks", "read_file"))
        names = {d["function"]["name"] for d in kept}
        assert names == {"write_file", "edit_file", "edit_blocks"}

    def test_returns_original_when_no_write_tool_survives(self) -> None:
        original = _tools("read_file", "repo_rg")
        assert restrict_tool_definitions_to_write(original) is original
