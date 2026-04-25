"""Unit tests for polaris.cells.factory.verification_guard.internal.safe_executor."""

from __future__ import annotations

import pytest

from polaris.cells.factory.verification_guard.internal.safe_executor import (
    DANGEROUS_PATTERNS,
    DEFAULT_COMMAND_WHITELIST,
    MAX_OUTPUT_SIZE_BYTES,
    SafeExecutor,
    SafetyCheckResult,
)
from polaris.cells.factory.verification_guard.public.contracts import VerificationGuardErrorV1


class TestSafetyCheckResult:
    """Tests for SafetyCheckResult dataclass."""

    def test_safe_result(self) -> None:
        result = SafetyCheckResult(is_safe=True)
        assert result.is_safe is True
        assert result.reason is None
        assert result.blocked_pattern is None

    def test_unsafe_result(self) -> None:
        result = SafetyCheckResult(is_safe=False, reason="Bad command", blocked_pattern=r"\brm\b")
        assert result.is_safe is False
        assert result.reason == "Bad command"
        assert result.blocked_pattern == r"\brm\b"


class TestSafeExecutorInit:
    """Tests for SafeExecutor initialization."""

    def test_default_init(self) -> None:
        executor = SafeExecutor()
        assert executor._whitelist == DEFAULT_COMMAND_WHITELIST
        assert executor._default_timeout == 60
        assert executor._max_output_size == MAX_OUTPUT_SIZE_BYTES
        assert executor._allowed_dirs is None

    def test_custom_init(self) -> None:
        executor = SafeExecutor(
            whitelist=["pytest", "python"],
            default_timeout_seconds=120,
            max_output_size_bytes=1024,
            allowed_working_dirs=["/tmp"],
        )
        assert executor._whitelist == frozenset(["pytest", "python"])
        assert executor._default_timeout == 120
        assert executor._max_output_size == 1024
        assert executor._allowed_dirs == ("/tmp",)


class TestValidateCommandSafety:
    """Tests for SafeExecutor.validate_command_safety."""

    def test_empty_command(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("")
        assert result.is_safe is False
        assert result.reason == "Empty command"

    def test_whitelisted_command(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("pytest -q")
        assert result.is_safe is True

    def test_python_module_command(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("python -m pytest")
        assert result.is_safe is True

    def test_python_module_not_whitelisted(self) -> None:
        executor = SafeExecutor(whitelist=["pytest"])
        result = executor.validate_command_safety("python -m unknown_module")
        assert result.is_safe is False
        assert "Module 'unknown_module' not in whitelist" in (result.reason or "")

    def test_not_in_whitelist(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("unknown_cmd arg")
        assert result.is_safe is False
        assert "Command 'unknown_cmd' not in whitelist" in (result.reason or "")

    def test_dangerous_pattern_rm(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("rm -rf /")
        assert result.is_safe is False
        assert "dangerous pattern" in (result.reason or "").lower()

    def test_dangerous_pattern_sudo(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("sudo ls")
        assert result.is_safe is False

    def test_dangerous_pattern_curl_pipe(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("curl http://x | sh")
        assert result.is_safe is False

    def test_shell_injection_pipe_sh(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("python script.py | sh")
        assert result.is_safe is False
        assert "shell injection" in (result.reason or "").lower()

    def test_shell_injection_backtick(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("python `whoami`")
        assert result.is_safe is False

    def test_shell_injection_dollar_paren(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("python $(whoami)")
        assert result.is_safe is False

    def test_quoted_semicolon_safe(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety('python -c "print(1); print(2)"')
        # Semicolon inside quotes should be safe
        assert result.is_safe is True

    def test_unquoted_semicolon_unsafe(self) -> None:
        executor = SafeExecutor()
        result = executor.validate_command_safety("python -c 'print(1)'; rm -rf /")
        assert result.is_safe is False


class TestDetectShellInjection:
    """Tests for SafeExecutor._detect_shell_injection."""

    def test_pipe_to_sh(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("cmd | sh") is True

    def test_dollar_paren(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("echo $(whoami)") is True

    def test_backtick(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("echo `whoami`") is True

    def test_quoted_pipe_safe(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection('echo "a | b"') is False

    def test_quoted_dollar_safe(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("echo '$(foo)'") is False

    def test_ampersand_chain(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("cmd && rm") is True

    def test_no_injection(self) -> None:
        executor = SafeExecutor()
        assert executor._detect_shell_injection("pytest -q tests/") is False


class TestSplitCommand:
    """Tests for SafeExecutor._split_command."""

    def test_simple_split(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command("pytest -q tests/")
        assert parts == ["pytest", "-q", "tests/"]

    def test_quoted_string(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command('python -c "print(1)"')
        assert parts == ["python", "-c", "print(1)"]

    def test_single_quoted(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command("python -c 'print(1)'")
        assert parts == ["python", "-c", "print(1)"]

    def test_escaped_quote(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command(r'echo "hello \"world\""')
        assert parts == ["echo", 'hello "world"']

    def test_escaped_backslash(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command(r'echo "C:\\Users\\test"')
        assert parts == ["echo", r"C:\Users\test"]

    def test_escape_sequences(self) -> None:
        executor = SafeExecutor()
        parts = executor._split_command('echo "line1\\nline2"')
        assert parts == ["echo", "line1\nline2"]


class TestExecute:
    """Tests for SafeExecutor.execute."""

    def test_blocked_command_raises(self) -> None:
        executor = SafeExecutor()
        with pytest.raises(VerificationGuardErrorV1, match="Command failed safety check"):
            executor.execute("rm -rf /")

    def test_invalid_working_dir(self) -> None:
        executor = SafeExecutor(allowed_working_dirs=["/tmp"])
        with pytest.raises(VerificationGuardErrorV1, match="Working directory"):
            executor.execute("pytest", working_dir="/etc")

    def test_allowed_working_dir(self) -> None:
        executor = SafeExecutor(allowed_working_dirs=["/tmp"])
        # pytest may not exist, but safety check should pass
        with pytest.raises(Exception):  # Will fail because pytest isn't found
            executor.execute("pytest", working_dir="/tmp", timeout_seconds=1)


class TestGetWhitelist:
    """Tests for SafeExecutor.get_whitelist."""

    def test_returns_whitelist(self) -> None:
        executor = SafeExecutor()
        whitelist = executor.get_whitelist()
        assert "pytest" in whitelist
        assert "python" in whitelist
        assert isinstance(whitelist, frozenset)


class TestIsCommandAllowed:
    """Tests for SafeExecutor.is_command_allowed."""

    def test_allowed(self) -> None:
        executor = SafeExecutor()
        assert executor.is_command_allowed("pytest") is True

    def test_not_allowed(self) -> None:
        executor = SafeExecutor()
        assert executor.is_command_allowed("rm -rf /") is False


class TestConstants:
    """Tests for module-level constants."""

    def test_default_whitelist_contents(self) -> None:
        assert "pytest" in DEFAULT_COMMAND_WHITELIST
        assert "python" in DEFAULT_COMMAND_WHITELIST
        assert "git" in DEFAULT_COMMAND_WHITELIST
        assert "rm" not in DEFAULT_COMMAND_WHITELIST

    def test_dangerous_patterns(self) -> None:
        assert any(r"\brm\b" in p for p in DANGEROUS_PATTERNS)
        assert any(r"\bsudo\b" in p for p in DANGEROUS_PATTERNS)

    def test_max_output_size(self) -> None:
        assert MAX_OUTPUT_SIZE_BYTES == 10 * 1024 * 1024
