"""Tests for WriteToolPhases — write tool three-phase semantics."""

from __future__ import annotations

import inspect

from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)


class TestIsWriteTool:
    """Tests for is_write_tool() classification."""

    def test_write_file_is_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("write_file") is True

    def test_edit_file_is_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("edit_file") is True

    def test_delete_file_is_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("delete_file") is True

    def test_apply_patch_is_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("apply_patch") is True

    def test_read_file_is_not_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("read_file") is False

    def test_glob_is_not_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("glob") is False

    def test_repo_rg_is_not_write_tool(self) -> None:
        assert WriteToolPhases.is_write_tool("repo_rg") is False

    def test_normalizes_hyphenated_names(self) -> None:
        """Hyphenated tool names should be normalized to underscores."""
        assert WriteToolPhases.is_write_tool("apply-patch") is True
        assert WriteToolPhases.is_write_tool("write-file") is True

    def test_normalizes_case(self) -> None:
        """Tool names should be case-insensitive."""
        assert WriteToolPhases.is_write_tool("WRITE_FILE") is True
        assert WriteToolPhases.is_write_tool("Write_File") is True
        assert WriteToolPhases.is_write_tool("Edit_File") is True

    def test_strips_whitespace(self) -> None:
        assert WriteToolPhases.is_write_tool("  write_file  ") is True


class TestBuildPrepareInvocation:
    """Tests for build_prepare_invocation() — Prepare phase."""

    def test_prepare_uses_synthetic_shadow_tool(self) -> None:
        """Prepare should emit a non-registered sentinel tool name (not file_exists).

        Using a real registered tool name here would let a model-emitted
        file_exists collide in the spec_key hash and either satisfy the prepare
        shadow or block a legitimate read. Sentinel naming is the §6.6-equivalent
        guard for the speculation registry seam.
        """
        invocation = ToolInvocation(
            call_id=ToolCallId("call_write"),
            tool_name="write_file",
            arguments={"path": "src/main.py", "content": "print('hello')"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.canonical_tool_name == "__prepare_shadow__"
        assert prepare.canonical_tool_name != "file_exists", (
            "Prepare must NOT use a registered tool name; real file_exists calls would collide in the spec_key hash."
        )
        assert isinstance(prepare, SyntheticShadowToolKeyV1)
        assert not hasattr(prepare, "effect_type")
        assert not hasattr(prepare, "arguments")

    def test_prepare_tool_name_does_not_collide_with_file_exists(self) -> None:
        """Adversarial: a model-emitted file_exists must not be mistaken for a
        prepare shadow in the spec_key. Assert the prepare tool name is structurally
        distinct (not just case-different) from any registered read tool.
        """
        invocation = ToolInvocation(
            call_id=ToolCallId("call_x"),
            tool_name="write_file",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        # Sentinel must be a non-registered identifier
        assert prepare.canonical_tool_name not in {"file_exists", "read_file", "glob", "repo_rg"}
        # And the sentinel must be stable (deterministic)
        prepare2 = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare == prepare2

    def test_prepare_id_prefix(self) -> None:
        """Prepare call_id should be prefixed with 'prepare_'."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_123"),
            tool_name="write_file",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.source_tool_call_id == "call_123"

    def test_prepare_key_is_argument_free_and_call_bound(self) -> None:
        """Private key identity is the source call, not mutable tool arguments."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/app.py", "content": "code"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert WriteToolPhases.build_prepare_invocation(invocation).shadow_key_hash == prepare.shadow_key_hash

        other_path = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "src/other.py", "content": "code"},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert other_path.shadow_key_hash == prepare.shadow_key_hash
        other_call = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_2"),
                tool_name="write_file",
                arguments={"path": "src/app.py", "content": "code"},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert other_call.shadow_key_hash != prepare.shadow_key_hash

    def test_prepare_does_not_embed_content_identity(self) -> None:
        """Synthetic keys contain no argument shape or content identity."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py", "content": "AAAA"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        different_content = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "a.py", "content": "BBBB"},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert prepare.shadow_key_hash == different_content.shadow_key_hash
        assert WriteToolPhases.build_prepare_invocation(invocation).shadow_key_hash == prepare.shadow_key_hash
        assert "AAAA" not in repr(prepare)

    def test_prepare_missing_content_key(self) -> None:
        """Prepare does not add content_length when content key is absent."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        with_content = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "a.py", "content": ""},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert prepare.shadow_key_hash == with_content.shadow_key_hash

    def test_prepare_handles_non_string_content(self) -> None:
        """Prepare should handle non-string content gracefully."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py", "content": 123},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        empty_string = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "a.py", "content": ""},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert empty_string.shadow_key_hash == prepare.shadow_key_hash
        boolean_content = WriteToolPhases.build_prepare_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "a.py", "content": True},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert boolean_content.shadow_key_hash == prepare.shadow_key_hash


class TestBuildValidateInvocation:
    """Tests for build_validate_invocation() — Validate phase."""

    def test_validate_uses_synthetic_shadow_tool(self) -> None:
        """Validate should emit a non-registered sentinel tool name distinct from
        the prepare sentinel (so prepare/validate spec_keys cannot collide).
        """
        invocation = ToolInvocation(
            call_id=ToolCallId("call_validate"),
            tool_name="write_file",
            arguments={"path": "src/main.py", "content": "code"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        validate = WriteToolPhases.build_validate_invocation(invocation)
        assert validate.canonical_tool_name == "__validate_shadow__"
        assert validate.canonical_tool_name != "__prepare_shadow__", (
            "Validate must use a distinct sentinel from Prepare to prevent cross-phase spec_key collisions."
        )
        assert validate.canonical_tool_name != "file_exists", (
            "Validate must NOT use a registered tool name; real file_exists calls would collide in the spec_key hash."
        )
        assert isinstance(validate, SyntheticShadowToolKeyV1)
        assert not hasattr(validate, "execution_mode")
        assert not hasattr(validate, "arguments")

    def test_validate_id_prefix(self) -> None:
        """Validate call_id should be prefixed with 'validate_'."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_456"),
            tool_name="write_file",
            arguments={"path": "b.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        validate = WriteToolPhases.build_validate_invocation(invocation)
        assert validate.source_tool_call_id == "call_456"

    def test_validate_key_is_argument_free_and_phase_distinct(self) -> None:
        """Validate key ignores arguments but remains distinct from prepare."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py", "content": "test content"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        validate = WriteToolPhases.build_validate_invocation(invocation)
        without_content = WriteToolPhases.build_validate_invocation(
            ToolInvocation(
                call_id=ToolCallId("call_1"),
                tool_name="write_file",
                arguments={"path": "a.py"},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            )
        )
        assert without_content.shadow_key_hash == validate.shadow_key_hash
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.shadow_key_hash != validate.shadow_key_hash


def test_synthetic_builders_cannot_materialize_dispatch_types() -> None:
    for builder in (
        WriteToolPhases.build_prepare_invocation,
        WriteToolPhases.build_validate_invocation,
        WriteToolPhases.build_prepare_shadow_key,
    ):
        source = inspect.getsource(builder)
        assert "ToolInvocation(" not in source
        assert "ToolBatch(" not in source
        assert "gateway" not in source.lower()
        assert "dispatch" not in source.lower()


class TestBuildCommitInvocation:
    """Tests for build_commit_invocation() — Commit phase (authoritative only)."""

    def test_commit_preserves_original_tool(self) -> None:
        """Commit should preserve the original write tool name."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_commit"),
            tool_name="write_file",
            arguments={"path": "src/main.py", "content": "final code"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.tool_name == "write_file"

    def test_commit_preserves_call_id(self) -> None:
        """Commit should use the original call_id (not prefixed)."""
        invocation = ToolInvocation(
            call_id=ToolCallId("original_call"),
            tool_name="write_file",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.call_id == ToolCallId("original_call")

    def test_commit_preserves_arguments(self) -> None:
        """Commit should preserve all original arguments."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="edit_file",
            arguments={"path": "a.py", "old_text": "foo", "new_text": "bar"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.arguments == {"path": "a.py", "old_text": "foo", "new_text": "bar"}

    def test_commit_sets_write_effect_type(self) -> None:
        """Commit should set effect_type to WRITE."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="apply_patch",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.effect_type == ToolEffectType.WRITE

    def test_commit_sets_write_serial_mode(self) -> None:
        """Commit should set execution_mode to WRITE_SERIAL."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.execution_mode == ToolExecutionMode.WRITE_SERIAL
