"""Tests for polaris.delivery.cli.cli_compat module."""

from __future__ import annotations

import logging
import sys
import warnings
from unittest.mock import patch

import pytest
from polaris.delivery.cli.cli_compat import (
    _LEGACY_ENTRY_POINTS,
    check_compat,
    emit_compat_warnings,
    warn_if_no_workspace,
    warn_if_old_runtime_mode,
)


class TestEmitCompatWarnings:
    """Tests for emit_compat_warnings function."""

    def test_legacy_entry_point_polaris_director(self) -> None:
        """Test warning emitted for 'polaris-director' entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["polaris-director"])
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "polaris-director" in str(w[0].message)

    def test_legacy_entry_point_polaris_pm(self) -> None:
        """Test warning emitted for 'polaris-pm' entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["polaris-pm"])
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "polaris-pm" in str(w[0].message)

    def test_legacy_entry_point_polaris_cli(self) -> None:
        """Test warning emitted for 'polaris-cli' entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["polaris-cli"])
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "polaris-cli" in str(w[0].message)

    def test_no_warning_for_current_entry_point(self) -> None:
        """Test no warning for non-legacy entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["polaris-lazy"])
            assert len(w) == 0

    def test_empty_argv(self) -> None:
        """Test no warning with empty argv."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings([])
            assert len(w) == 0

    def test_path_separator_handling_unix(self) -> None:
        """Test Unix path separator handling."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["/usr/bin/polaris-director"])
            assert len(w) == 1
            assert "polaris-director" in str(w[0].message)

    def test_path_separator_handling_windows(self) -> None:
        """Test Windows path separator handling."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["C:\\Program Files\\polaris-director.exe"])
            assert len(w) == 1
            assert "polaris-director" in str(w[0].message)

    def test_exe_suffix_stripped(self) -> None:
        """Test .exe suffix is stripped before checking."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            emit_compat_warnings(["polaris-pm.exe"])
            assert len(w) == 1
            assert "polaris-pm" in str(w[0].message)

    def test_logs_warning_via_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that warning is also logged."""
        with caplog.at_level(logging.WARNING):
            emit_compat_warnings(["polaris-director"])
        assert "polaris-director" in caplog.text
        assert "deprecated" in caplog.text.lower()


class TestWarnIfOldRuntimeMode:
    """Tests for warn_if_old_runtime_mode function."""

    def test_deprecated_mode_rich(self) -> None:
        """Test warning for deprecated 'rich' mode."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("rich")
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "rich" in str(w[0].message)

    def test_deprecated_mode_textual(self) -> None:
        """Test warning for deprecated 'textual' mode."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("textual")
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "textual" in str(w[0].message)

    def test_deprecated_mode_server(self) -> None:
        """Test warning for deprecated 'server' mode."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("server")
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "server" in str(w[0].message)

    def test_no_warning_for_current_mode_interactive(self) -> None:
        """Test no warning for 'interactive' mode."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("interactive")
            assert len(w) == 0

    def test_no_warning_for_current_mode_console(self) -> None:
        """Test no warning for 'console' mode."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("console")
            assert len(w) == 0

    def test_case_sensitivity(self) -> None:
        """Test that mode matching is case-sensitive."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_old_runtime_mode("RICH")
            assert len(w) == 0


class TestWarnIfNoWorkspace:
    """Tests for warn_if_no_workspace function."""

    def test_warning_when_workspace_none(self) -> None:
        """Test warning when workspace is None."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_no_workspace(None)
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)
            assert "--workspace" in str(w[0].message)

    def test_warning_when_workspace_empty_string(self) -> None:
        """Test warning when workspace is empty string."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_no_workspace("")
            assert len(w) == 1
            assert issubclass(w[0].category, UserWarning)

    def test_no_warning_when_workspace_provided(self) -> None:
        """Test no warning when workspace is provided."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_no_workspace("/path/to/workspace")
            assert len(w) == 0

    def test_no_warning_when_workspace_whitespace(self) -> None:
        """Test no warning when workspace is whitespace only (truthy string)."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warn_if_no_workspace("   ")
            assert len(w) == 0

    def test_logs_via_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that workspace warning is also logged."""
        with caplog.at_level(logging.WARNING):
            warn_if_no_workspace(None)
        assert "workspace" in caplog.text.lower()


class TestCheckCompat:
    """Tests for check_compat top-level function."""

    def test_uses_provided_argv(self) -> None:
        """Test that provided argv is used."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(["polaris-director"])
            assert len(w) == 1
            assert "polaris-director" in str(w[0].message)

    def test_uses_sys_argv_when_none(self) -> None:
        """Test fallback to sys.argv when argv is None."""
        with patch.object(sys, "argv", ["polaris-pm"]), warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(None)
            assert len(w) == 1
            assert "polaris-pm" in str(w[0].message)

    def test_empty_list_when_no_sys_argv(self) -> None:
        """Test handling when sys.argv is missing."""
        with patch.object(sys, "argv", [], create=True), warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(None)
            assert len(w) == 0

    def test_non_legacy_argv_via_check_compat(self) -> None:
        """Test check_compat with non-legacy entry point."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            check_compat(["polaris-lazy", "--help"])
            assert len(w) == 0


class TestLegacyEntryPointsConstant:
    """Tests for _LEGACY_ENTRY_POINTS constant."""

    def test_contains_expected_values(self) -> None:
        """Test that constant contains expected legacy entry points."""
        assert "polaris-director" in _LEGACY_ENTRY_POINTS
        assert "polaris-pm" in _LEGACY_ENTRY_POINTS
        assert "polaris-cli" in _LEGACY_ENTRY_POINTS

    def test_is_set(self) -> None:
        """Test that constant is a set."""
        assert isinstance(_LEGACY_ENTRY_POINTS, set)

    def test_expected_count(self) -> None:
        """Test that there are exactly 3 legacy entry points."""
        assert len(_LEGACY_ENTRY_POINTS) == 3
