"""Tests for LLM caller tool helper parsing."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import extract_native_tool_calls


class TestExtractNativeToolCalls:
    """Tests for native-only tool extraction."""

    def test_gemma_inline_tool_call_is_not_native_tool_call(self) -> None:
        """Textual tool protocols must not be promoted to native tool calls."""
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

        assert calls == []
        assert provider == "openai"

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
    build_tool_filter_audit,
    resolve_from_scratch_write_target,
    restrict_tool_definitions_to_write,
    should_use_weak_director_slim_tool_schema,
)


def _tools(*names: str) -> list[dict]:
    return [{"type": "function", "function": {"name": n}} for n in names]


class TestToolFilterAudit:
    def test_structured_contract_required_tool_removal_fails_closed(self) -> None:
        audit = build_tool_filter_audit(
            filter_reason="weak_director_slim_tool_schema",
            original_tool_definitions=_tools("read_file", "repo_rg", "write_file"),
            filtered_tool_definitions=_tools("write_file"),
            messages=[
                {
                    "role": "user",
                    "content": "Required tools: repo_rg",
                    "metadata": {"tool_contract": {"required_tools": ["repo_rg"]}},
                }
            ],
        )

        assert audit["status"] == "conflict"
        assert audit["fail_closed"] is True
        assert audit["removed_contract_required_tool_names"] == ["repo_rg"]
        assert audit["removed_prompt_required_tool_names"] == ["repo_rg"]

    def test_text_only_required_tool_removal_is_audit_only(self) -> None:
        audit = build_tool_filter_audit(
            filter_reason="weak_director_slim_tool_schema",
            original_tool_definitions=_tools("read_file", "repo_rg", "write_file"),
            filtered_tool_definitions=_tools("write_file"),
            messages=[
                {
                    "role": "user",
                    "content": "Required tools (at least once): repo_rg\nCreate the target file.",
                }
            ],
        )

        assert audit["status"] == "pass"
        assert audit["fail_closed"] is False
        assert audit["removed_contract_required_tool_names"] == []
        assert audit["removed_text_required_tool_names"] == ["repo_rg"]
        assert audit["removed_prompt_required_tool_names"] == ["repo_rg"]


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
    def test_keeps_only_mutation_execution_tools_with_write(self) -> None:
        kept = restrict_tool_definitions_to_write(
            _tools("read_file", "repo_tree", "repo_rg", "write_file", "execute_command", "glob", "scout_probe")
        )
        names = {d["function"]["name"] for d in kept}
        assert names == {"write_file", "execute_command"}

    def test_keeps_all_mutation_tools(self) -> None:
        kept = restrict_tool_definitions_to_write(_tools("write_file", "edit_file", "edit_blocks", "read_file"))
        names = {d["function"]["name"] for d in kept}
        assert names == {"write_file", "edit_file", "edit_blocks"}

    def test_drops_deprecated_exact_edit_from_active_write_restriction(self) -> None:
        kept = restrict_tool_definitions_to_write(_tools("read_file", "write_file", "precision_edit"))
        names = {d["function"]["name"] for d in kept}
        assert names == {"write_file"}

    def test_returns_original_when_no_write_tool_survives(self) -> None:
        original = _tools("read_file", "repo_rg")
        assert restrict_tool_definitions_to_write(original) is original


class TestWeakDirectorSlimToolSchema:
    def test_qwen_director_materialize_uses_slim_tools(self) -> None:
        profile = type("Profile", (), {"provider_id": "openai_compat", "model": "qwen3.6-27b-code-gpu0"})()

        assert should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"delivery_mode": "materialize_changes"},
        )

    def test_strong_director_materialize_keeps_full_tools(self) -> None:
        profile = type("Profile", (), {"provider_id": "openai", "model": "gpt-5.3-codex"})()

        assert not should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"delivery_mode": "materialize_changes"},
        )

    def test_configured_full_profile_overrides_model_name_hint(self) -> None:
        profile = type(
            "Profile",
            (),
            {
                "provider_id": "openai_compat",
                "model": "qwen3.6-27b-code-gpu0",
                "tool_schema_profile": "full",
            },
        )()

        assert not should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"delivery_mode": "materialize_changes"},
        )

    def test_configured_slim_profile_enables_unknown_model(self) -> None:
        profile = type(
            "Profile",
            (),
            {
                "provider_id": "openai_compat",
                "model": "custom-local-model",
                "tool_schema_profile": "slim",
            },
        )()

        assert should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"delivery_mode": "materialize_changes"},
        )

    def test_explicit_context_flag_enables_unknown_director_model(self) -> None:
        profile = type("Profile", (), {"provider_id": "local", "model": "custom-model"})()

        assert should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={
                "delivery_mode": "materialize_changes",
                "director_slim_tool_schema": True,
            },
        )

    def test_forced_tool_definitions_are_not_overridden(self) -> None:
        profile = type("Profile", (), {"provider_id": "openai_compat", "model": "qwen3.6-27b-code-gpu0"})()

        assert not should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={
                "delivery_mode": "materialize_changes",
                "_transaction_kernel_forced_tool_definitions": [
                    {"type": "function", "function": {"name": "write_file"}}
                ],
            },
        )

    def test_non_execution_turn_keeps_full_tools(self) -> None:
        profile = type("Profile", (), {"provider_id": "openai_compat", "model": "qwen3.6-27b-code-gpu0"})()

        assert not should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"context_os_snapshot": {}},
        )

    def test_tool_schema_pressure_enables_unknown_model_without_name_hint(self) -> None:
        profile = type("Profile", (), {"provider_id": "local", "model": "custom-model"})()

        assert should_use_weak_director_slim_tool_schema(
            role="director",
            profile=profile,
            context_override={"delivery_mode": "materialize_changes"},
            tool_definitions=_tools(
                "read_file",
                "repo_rg",
                "glob",
                "scout",
                "write_file",
                "edit_file",
                "append_to_file",
                "execute_command",
            ),
        )


# ============ R7: repair-preserving edit restriction (I3-r28) ============

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (  # noqa: E402
    resolve_repair_edit_target,
    restrict_tool_definitions_to_edit,
)


def _repair_co(target: str, *, error: str = "main.js:42 Unexpected token ';'", **step: object) -> dict:
    return {
        "construction_step": {"step_id": "S", "target_file": target, **step},
        "last_failure": {"error_code": "QA_syntax_failed", "error_message": error},
    }


class TestResolveRepairEditTarget:
    def test_existing_file_with_failure_returns_target(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        assert resolve_repair_edit_target(_repair_co("main.js"), str(tmp_path)) == "main.js"

    def test_absent_file_returns_none(self, tmp_path: Path) -> None:
        # No file on disk → this is from-scratch territory, not repair.
        assert resolve_repair_edit_target(_repair_co("main.js"), str(tmp_path)) is None

    def test_no_last_failure_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        co = {"construction_step": {"step_id": "S", "target_file": "main.js"}}
        assert resolve_repair_edit_target(co, str(tmp_path)) is None

    def test_edit_on_prior_existing_file_returns_target(self, tmp_path: Path) -> None:
        # A CE-split fill (edit_on_prior) on an existing skeleton file is hard-forced
        # to edit even without a last_failure (I3-r29).
        (tmp_path / "main.js").write_text("function init(){}\n", encoding="utf-8")
        co = {"construction_step": {"step_id": "S-fill1", "target_file": "main.js", "edit_on_prior": True}}
        assert resolve_repair_edit_target(co, str(tmp_path)) == "main.js"

    def test_edit_on_prior_absent_file_returns_none(self, tmp_path: Path) -> None:
        # File not yet created → from-scratch territory, not an edit.
        co = {"construction_step": {"step_id": "S-fill1", "target_file": "main.js", "edit_on_prior": True}}
        assert resolve_repair_edit_target(co, str(tmp_path)) is None

    def test_blank_error_message_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        assert resolve_repair_edit_target(_repair_co("main.js", error="  "), str(tmp_path)) is None

    def test_dot_slash_target_normalized(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        assert resolve_repair_edit_target(_repair_co("./main.js"), str(tmp_path)) == "main.js"

    def test_mutually_exclusive_with_from_scratch(self, tmp_path: Path) -> None:
        # Same fixture: file present → repair fires, from-scratch does not.
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        co = _repair_co("main.js")
        assert resolve_repair_edit_target(co, str(tmp_path)) == "main.js"
        assert resolve_from_scratch_write_target(co, str(tmp_path)) is None

    def test_no_construction_step_returns_none(self, tmp_path: Path) -> None:
        assert resolve_repair_edit_target({"last_failure": {"error_message": "x"}}, str(tmp_path)) is None
        assert resolve_repair_edit_target(None, str(tmp_path)) is None

    def test_existing_director_quality_repair_target_returns_target(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "lib.rs").write_text("pub mod engine;\n", encoding="utf-8")
        context = {
            "director_quality_repair": {
                "repair_target_files": ["src/lib.rs"],
                "write_only_single_target": {"target_file": "src/lib.rs"},
            }
        }
        assert resolve_repair_edit_target(context, str(tmp_path)) == "src/lib.rs"

    def test_absent_director_quality_repair_target_returns_none(self, tmp_path: Path) -> None:
        context = {"director_quality_repair": {"repair_target_files": ["src/lib.rs"]}}
        assert resolve_repair_edit_target(context, str(tmp_path)) is None

    def test_env_off_disables(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "main.js").write_text("// real game\n", encoding="utf-8")
        monkeypatch.setenv("KERNELONE_REPAIR_PRESERVE_EDIT", "off")
        assert resolve_repair_edit_target(_repair_co("main.js"), str(tmp_path)) is None


class TestRestrictToolDefinitionsToEdit:
    def test_drops_rewrite_verbs_keeps_edit_and_read(self) -> None:
        kept = restrict_tool_definitions_to_edit(
            _tools("read_file", "write_file", "append_to_file", "edit_blocks", "execute_command")
        )
        names = {d["function"]["name"] for d in kept}
        # write_file/append_to_file removed; reads kept (needed to anchor the edit).
        assert names == {"read_file", "edit_blocks", "execute_command"}

    def test_fail_open_when_no_anchored_edit_tool(self) -> None:
        original = _tools("read_file", "write_file", "append_to_file")
        assert restrict_tool_definitions_to_edit(original) is original

    def test_keeps_diff_and_treesitter_edit_tools(self) -> None:
        kept = restrict_tool_definitions_to_edit(_tools("write_file", "repo_apply_diff", "treesitter_replace_node"))
        names = {d["function"]["name"] for d in kept}
        assert names == {"repo_apply_diff", "treesitter_replace_node"}

    def test_drops_deprecated_exact_edit_when_active_anchored_edit_exists(self) -> None:
        kept = restrict_tool_definitions_to_edit(_tools("write_file", "precision_edit", "edit_blocks"))
        names = {d["function"]["name"] for d in kept}
        assert names == {"edit_blocks"}

    def test_does_not_mutate_input_in_place(self) -> None:
        original = _tools("write_file", "edit_blocks")
        before = [dict(d) for d in original]
        restrict_tool_definitions_to_edit(original)
        assert original == before
