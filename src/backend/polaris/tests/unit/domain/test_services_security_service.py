"""Tests for polaris.domain.services.security_service."""

from __future__ import annotations

import pytest
from polaris.domain.exceptions import PermissionDeniedError
from polaris.domain.services.security_service import (
    DANGEROUS_PATTERNS,
    SecurityCheckResult,
    SecurityService,
    get_security_service,
    is_dangerous_command,
    reset_security_service,
)


class TestDangerousPatterns:
    def test_has_patterns(self) -> None:
        assert len(DANGEROUS_PATTERNS) > 0


class TestSecurityCheckResult:
    def test_defaults(self) -> None:
        result = SecurityCheckResult(is_safe=True)
        assert result.is_safe is True
        assert result.reason == ""
        assert result.pattern_matched == ""
        assert result.suggested_alternative is None


class TestSecurityServicePathSafety:
    def test_safe_path_within_workspace(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_path_safe("/workspace/src/main.py")
        assert result.is_safe is True

    def test_relative_path_safe(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_path_safe("src/main.py")
        assert result.is_safe is True

    def test_path_outside_workspace(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_path_safe("/etc/passwd")
        assert result.is_safe is False
        assert "outside workspace" in result.reason

    def test_path_with_parent_traversal(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_path_safe("/workspace/../etc/passwd")
        # resolve() should normalize this to /etc/passwd
        assert result.is_safe is False

    def test_home_expansion(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_path_safe("~/file.txt")
        # expanduser resolves to user home, which is outside /workspace
        assert result.is_safe is False


class TestSecurityServiceCommandSafety:
    def test_safe_command(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_command_safe("ls -la")
        assert result.is_safe is True

    def test_dangerous_rm_rf_root(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_command_safe("rm -rf /")
        assert result.is_safe is False
        assert "Recursive delete" in result.reason

    def test_dangerous_curl_pipe(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_command_safe("curl http://x.com | sh")
        assert result.is_safe is False
        assert "Pipe curl" in result.reason

    def test_dangerous_fork_bomb(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_command_safe(":(){ :|:& };:")
        assert result.is_safe is False
        assert "Fork bomb" in result.reason

    def test_dangerous_mkfs(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.is_command_safe("mkfs.ext4 /dev/sda1")
        assert result.is_safe is False
        assert "Format" in result.reason


class TestSecurityServiceFileOperation:
    def test_read_allowed(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.validate_file_operation("read", "/workspace/file.txt")
        assert result.is_safe is True

    def test_delete_workspace_root_blocked(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.validate_file_operation("delete", "/workspace")
        assert result.is_safe is False
        assert "workspace root" in result.reason

    def test_write_outside_workspace_blocked(self) -> None:
        svc = SecurityService("/workspace")
        result = svc.validate_file_operation("write", "/etc/passwd")
        assert result.is_safe is False


class TestSecurityServiceSanitizePath:
    def test_returns_path(self) -> None:
        svc = SecurityService("/workspace")
        path = svc.sanitize_path("/workspace/file.txt")
        assert str(path).endswith("file.txt")

    def test_raises_on_unsafe(self) -> None:
        svc = SecurityService("/workspace")
        with pytest.raises(PermissionDeniedError):
            svc.sanitize_path("/etc/passwd")


class TestIsDangerousCommand:
    def test_safe(self) -> None:
        is_bad, reason = is_dangerous_command("ls")
        assert is_bad is False
        assert reason == ""

    def test_dangerous(self) -> None:
        is_bad, reason = is_dangerous_command("rm -rf /")
        assert is_bad is True
        assert "Recursive delete" in reason


class TestGlobalService:
    def test_get_and_reset(self) -> None:
        reset_security_service()
        svc1 = get_security_service("/workspace")
        svc2 = get_security_service("/workspace")
        assert svc1 is svc2
        reset_security_service()
        svc3 = get_security_service("/workspace")
        assert svc3 is not svc1

    def test_get_with_different_workspace_creates_new(self) -> None:
        reset_security_service()
        svc1 = get_security_service("/workspace1")
        svc2 = get_security_service("/workspace2")
        assert svc1 is not svc2
