"""Tests for polaris.cells.director.execution.internal.tools.security.

Covers shell metacharacter detection, command safety validation,
and batch command validation.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.execution.internal.tools.security import (
    validate_command_safety,
    validate_commands_batch,
)


class TestValidateCommandSafety:
    """Tests for validate_command_safety."""

    def test_safe_command_passes(self) -> None:
        validate_command_safety("python script.py")

    def test_safe_command_with_args_passes(self) -> None:
        validate_command_safety("pytest -q test_foo.py")

    def test_semicolon_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("echo hello; rm -rf /")

    def test_double_ampersand_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("cmd1 && cmd2")

    def test_pipe_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("cat file | grep foo")

    def test_backtick_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("echo `whoami`")

    def test_dollar_paren_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("echo $(date)")

    def test_redirect_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("echo foo > /etc/passwd")

    def test_newline_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell metacharacters"):
            validate_command_safety("echo hello\nrm -rf /")

    def test_empty_command_passes(self) -> None:
        validate_command_safety("")

    def test_none_treated_as_empty(self) -> None:
        validate_command_safety(None)  # type: ignore[arg-type]


class TestValidateCommandsBatch:
    """Tests for validate_commands_batch."""

    def test_all_safe_returns_empty(self) -> None:
        result = validate_commands_batch(["python a.py", "pytest b.py"])
        assert result == []

    def test_detects_unsafe_commands(self) -> None:
        result = validate_commands_batch(["python a.py", "echo foo; rm -rf /", "pytest b.py"])
        assert result == ["echo foo; rm -rf /"]

    def test_all_unsafe_returns_all(self) -> None:
        result = validate_commands_batch(["a | b", "c; d"])
        assert result == ["a | b", "c; d"]

    def test_empty_list_returns_empty(self) -> None:
        result = validate_commands_batch([])
        assert result == []
