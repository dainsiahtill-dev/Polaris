"""Tests for WriteToolPhases — write tool three-phase semantics."""

from __future__ import annotations

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

    def test_prepare_uses_file_exists(self) -> None:
        """Prepare should map write tools to file_exists for readonly validation."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_write"),
            tool_name="write_file",
            arguments={"path": "src/main.py", "content": "print('hello')"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.tool_name == "file_exists"
        assert prepare.effect_type == ToolEffectType.READ
        assert prepare.execution_mode == ToolExecutionMode.READONLY_PARALLEL

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
        assert prepare.call_id == ToolCallId("prepare_call_123")

    def test_prepare_preserves_path(self) -> None:
        """Prepare should pass through path argument."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/app.py", "content": "code"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.arguments.get("path") == "src/app.py"

    def test_prepare_adds_content_length(self) -> None:
        """Prepare should add content_length for schema validation signal."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py", "content": "hello world"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        prepare = WriteToolPhases.build_prepare_invocation(invocation)
        assert prepare.arguments.get("content_length") == 11  # len("hello world")

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
        # content_length is not added when content key is absent
        assert prepare.arguments.get("content_length") is None

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
        assert prepare.arguments.get("content_length") == 0


class TestBuildValidateInvocation:
    """Tests for build_validate_invocation() — Validate phase."""

    def test_validate_uses_file_exists(self) -> None:
        """Validate should also map to file_exists for schema checking."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_validate"),
            tool_name="write_file",
            arguments={"path": "src/main.py", "content": "code"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        validate = WriteToolPhases.build_validate_invocation(invocation)
        assert validate.tool_name == "file_exists"
        assert validate.effect_type == ToolEffectType.READ
        assert validate.execution_mode == ToolExecutionMode.READONLY_PARALLEL

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
        assert validate.call_id == ToolCallId("validate_call_456")

    def test_validate_adds_validate_content_flag(self) -> None:
        """Validate should add validate_content=True signal."""
        invocation = ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "a.py", "content": "test content"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        validate = WriteToolPhases.build_validate_invocation(invocation)
        assert validate.arguments.get("validate_content") is True


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
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
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
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        commit = WriteToolPhases.build_commit_invocation(invocation)
        assert commit.execution_mode == ToolExecutionMode.WRITE_SERIAL
