"""Tests for polaris.infrastructure.accel.verify.verify.formatters module."""

from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.accel.verify.verify.formatters import (
    command_binary,
    command_workdir,
    effective_shell_command,
    extract_node_script,
    extract_pytest_targets,
    extract_python_module,
    normalize_bool,
    normalize_optional_positive_float,
    normalize_positive_int,
    parse_command_tokens,
    resolve_windows_compatible_command,
    split_pytest_target,
)


class TestCommandBinary:
    """Tests for command_binary function."""

    def test_simple_command(self) -> None:
        """Should extract binary from simple command."""
        assert command_binary("pytest") == "pytest"
        assert command_binary("ruff check") == "ruff"

    def test_command_with_path(self) -> None:
        """Should extract binary with path."""
        assert command_binary("/usr/bin/python") == "/usr/bin/python"
        assert command_binary(".venv/bin/python") == ".venv/bin/python"

    def test_command_with_args(self) -> None:
        """Should extract binary from command with arguments."""
        assert command_binary("pytest tests/ --cov") == "pytest"
        assert command_binary("ruff check src/ --fix") == "ruff"

    def test_empty_command(self) -> None:
        """Empty command should return empty string."""
        assert command_binary("") == ""
        assert command_binary("   ") == ""


class TestEffectiveShellCommand:
    """Tests for effective_shell_command function."""

    def test_no_chaining(self) -> None:
        """Should return command unchanged if no chaining."""
        cmd = "pytest tests/"
        assert effective_shell_command(cmd) == cmd

    def test_ampersand_chaining(self) -> None:
        """Should return last command in chain."""
        assert effective_shell_command("cd dir && pytest") == "pytest"
        assert effective_shell_command("cd a && cd b && pytest") == "pytest"

    def test_whitespace_handling(self) -> None:
        """Should handle whitespace in chain."""
        assert effective_shell_command("cd dir  &&  pytest") == "pytest"

    def test_empty_chain(self) -> None:
        """Should handle empty chain."""
        assert effective_shell_command("") == ""


class TestCommandWorkdir:
    """Tests for command_workdir function."""

    def test_no_cd_command(self, tmp_path: Path) -> None:
        """Should return project_dir if no cd command."""
        assert command_workdir(tmp_path, "pytest") == tmp_path

    def test_cd_to_subdir(self, tmp_path: Path) -> None:
        """Should resolve cd to subdirectory."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        result = command_workdir(tmp_path, f"cd {subdir} && pytest")
        assert result == subdir

    def test_cd_with_quotes(self, tmp_path: Path) -> None:
        """Should handle quoted paths."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        result = command_workdir(tmp_path, f'cd "{subdir}" && pytest')
        assert result == subdir

    def test_relative_cd(self, tmp_path: Path) -> None:
        """Should handle relative cd path."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = command_workdir(tmp_path, "cd subdir && pytest")
        assert result == subdir.resolve()


class TestExtractPythonModule:
    """Tests for extract_python_module function."""

    def test_valid_module(self) -> None:
        """Should extract module from python -m command."""
        assert extract_python_module("python -m pytest") == "pytest"
        assert extract_python_module("python -m ruff check") == "ruff"
        assert extract_python_module("python -m mypy src/") == "mypy"

    def test_no_module_flag(self) -> None:
        """Should return empty string without -m flag."""
        assert extract_python_module("python pytest") == ""
        assert extract_python_module("pytest") == ""

    def test_too_few_parts(self) -> None:
        """Should return empty string with too few parts."""
        assert extract_python_module("python -m") == ""
        assert extract_python_module("python") == ""


class TestParseCommandTokens:
    """Tests for parse_command_tokens function."""

    def test_simple_command(self) -> None:
        """Should parse simple command."""
        tokens = parse_command_tokens("pytest tests/")
        assert "pytest" in tokens
        assert "tests/" in tokens

    def test_quoted_strings(self) -> None:
        """Should handle quoted strings."""
        tokens = parse_command_tokens('pytest -k "test_name"')
        # Should contain test_name (with or without quotes depending on platform)
        assert any("test_name" in t for t in tokens)

    def test_invalid_shell(self) -> None:
        """Should handle invalid shell syntax gracefully."""
        tokens = parse_command_tokens('echo "unclosed')
        # Should not raise, may return partial tokens
        assert isinstance(tokens, list)

    def test_empty_command(self) -> None:
        """Should handle empty command."""
        tokens = parse_command_tokens("")
        assert tokens == []


class TestExtractNodeScript:
    """Tests for extract_node_script function."""

    def test_npm_script(self) -> None:
        """Should extract npm script name."""
        assert extract_node_script("npm test") == "test"
        assert extract_node_script("npm run build") == "build"

    def test_pnpm_script(self) -> None:
        """Should extract pnpm script name."""
        assert extract_node_script("pnpm lint") == "lint"

    def test_yarn_script(self) -> None:
        """Should extract yarn script name."""
        assert extract_node_script("yarn test") == "test"

    def test_script_with_args(self) -> None:
        """Should extract script ignoring arguments."""
        assert extract_node_script("npm run test -- --watch") == "test"

    def test_non_node_command(self) -> None:
        """Should return empty for non-node commands."""
        assert extract_node_script("pytest") == ""
        assert extract_node_script("python test") == ""


class TestSplitPytestTarget:
    """Tests for split_pytest_target function."""

    def test_no_class_marker(self) -> None:
        """Should return token unchanged if no :: marker."""
        assert split_pytest_target("tests/test_file.py") == "tests/test_file.py"

    def test_with_class_marker(self) -> None:
        """Should extract file path before :: marker."""
        assert split_pytest_target("tests/test_file.py::TestClass") == "tests/test_file.py"
        assert split_pytest_target("tests/test_file.py::TestClass::test_method") == "tests/test_file.py"

    def test_quoted_target(self) -> None:
        """Should handle quoted targets."""
        assert split_pytest_target('"tests/test_file.py"') == "tests/test_file.py"
        assert split_pytest_target("'tests/test_file.py'") == "tests/test_file.py"


class TestExtractPytestTargets:
    """Tests for extract_pytest_targets function."""

    def test_finds_pytest_targets(self) -> None:
        """Should find all .py files after pytest."""
        result = extract_pytest_targets("pytest tests/test_file.py tests/other.py")
        assert "tests/test_file.py" in result
        assert "tests/other.py" in result

    def test_ignores_options(self) -> None:
        """Should ignore options starting with -."""
        result = extract_pytest_targets("pytest -v --cov tests/test.py")
        assert "tests/test.py" in result
        assert "-v" not in result

    def test_handles_multiple_pytest_calls(self) -> None:
        """Should handle chained pytest calls."""
        # extract_pytest_targets should find .py files in chained commands
        result = extract_pytest_targets("cd dir && pytest tests/test_file.py")
        assert "tests/test_file.py" in result

    def test_normalizes_backslashes(self) -> None:
        """Should normalize backslashes to forward slashes."""
        result = extract_pytest_targets(r"pytest tests\test_file.py")
        assert "\\" not in result


class TestResolveWindowsCompatibleCommand:
    """Tests for resolve_windows_compatible_command function."""

    def test_non_windows(self, tmp_path: Path) -> None:
        """Should return command unchanged on non-Windows."""
        import os

        original = os.name
        try:
            os.name = "posix"
            result = resolve_windows_compatible_command(tmp_path, "make install-pre-commit-hooks")
            assert result[0] == "make install-pre-commit-hooks"
            assert result[1] == ""
        finally:
            os.name = original

    def test_empty_command(self, tmp_path: Path) -> None:
        """Should handle empty command."""
        result = resolve_windows_compatible_command(tmp_path, "")
        assert result[0] == ""
        assert result[1] == ""

    def test_non_make_command(self, tmp_path: Path) -> None:
        """Should return non-make commands unchanged."""
        result = resolve_windows_compatible_command(tmp_path, "pytest")
        assert result[0] == "pytest"


class TestNormalizePositiveInt:
    """Tests for normalize_positive_int function."""

    def test_positive_int_unchanged(self) -> None:
        """Positive integers should remain unchanged."""
        assert normalize_positive_int(42, 10) == 42
        assert normalize_positive_int(100, 10) == 100

    def test_zero_becomes_minimum(self) -> None:
        """Zero should become minimum (1)."""
        assert normalize_positive_int(0, 10) == 1

    def test_negative_becomes_minimum(self) -> None:
        """Negative numbers should become minimum (1)."""
        assert normalize_positive_int(-5, 10) == 1

    def test_string_int_parsed(self) -> None:
        """String integers should be parsed."""
        assert normalize_positive_int("42", 10) == 42
        assert normalize_positive_int("0", 10) == 1

    def test_invalid_type_returns_default(self) -> None:
        """Invalid types should return default value."""
        assert normalize_positive_int("abc", 10) == 10
        assert normalize_positive_int(None, 10) == 10


class TestNormalizeBool:
    """Tests for normalize_bool function."""

    def test_true_values(self) -> None:
        """Should recognize true-like values."""
        assert normalize_bool(True, False) is True
        assert normalize_bool("true", False) is True
        assert normalize_bool("TRUE", False) is True
        assert normalize_bool("1", False) is True
        assert normalize_bool("yes", False) is True
        assert normalize_bool("on", False) is True

    def test_false_values(self) -> None:
        """Should recognize false-like values."""
        assert normalize_bool(False, False) is False
        assert normalize_bool("false", False) is False
        assert normalize_bool("FALSE", False) is False
        assert normalize_bool("0", False) is False
        assert normalize_bool("no", False) is False
        assert normalize_bool("off", False) is False

    def test_default_value(self) -> None:
        """Should return default for unknown values and None."""
        # "unknown" is not in the recognized true values set, so it returns False
        assert normalize_bool("unknown", True) is False
        # None specifically returns the default value
        assert normalize_bool(None, True) is True
        assert normalize_bool(None, False) is False
        # Numeric non-zero is not recognized (not a bool or in true set)
        assert normalize_bool(123, True) is False


class TestNormalizeOptionalPositiveFloat:
    """Tests for normalize_optional_positive_float function."""

    def test_valid_positive_float(self) -> None:
        """Should return positive floats."""
        assert normalize_optional_positive_float(1.5) == 1.5
        assert normalize_optional_positive_float(0.001) == 0.001

    def test_none_returns_none(self) -> None:
        """None should return None."""
        assert normalize_optional_positive_float(None) is None

    def test_zero_returns_none(self) -> None:
        """Zero should return None."""
        assert normalize_optional_positive_float(0) is None
        assert normalize_optional_positive_float(0.0) is None

    def test_negative_returns_none(self) -> None:
        """Negative values should return None."""
        assert normalize_optional_positive_float(-1.0) is None
        assert normalize_optional_positive_float(-0.001) is None

    def test_string_float_parsed(self) -> None:
        """Should parse string floats."""
        assert normalize_optional_positive_float("1.5") == 1.5
        assert normalize_optional_positive_float("0.5") == 0.5
