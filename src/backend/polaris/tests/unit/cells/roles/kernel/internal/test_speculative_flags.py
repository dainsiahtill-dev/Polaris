"""Tests for polaris.cells.roles.kernel.internal.speculative_flags."""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.speculative_flags import (
    _parse_bool,
    is_speculative_execution_enabled,
)


class TestParseBool:
    def test_none_returns_default(self) -> None:
        assert _parse_bool(None, default=True) is True
        assert _parse_bool(None, default=False) is False

    def test_true_values(self) -> None:
        for val in ("1", "true", "yes", "on", "TRUE", " True "):
            assert _parse_bool(val, default=False) is True

    def test_false_values(self) -> None:
        for val in ("0", "false", "no", "off", "", "maybe"):
            assert _parse_bool(val, default=True) is False


class TestIsSpeculativeExecutionEnabled:
    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Default flipped to enabled in 5cd27a13 (2026-06-04, speculation GA);
        # explicit "0"/"off" remains the way to opt out.
        monkeypatch.delenv("ENABLE_SPECULATIVE_EXECUTION", raising=False)
        monkeypatch.delenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", raising=False)
        assert is_speculative_execution_enabled() is True

    def test_primary_env_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "1")
        monkeypatch.delenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", raising=False)
        assert is_speculative_execution_enabled() is True

    def test_primary_env_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "0")
        monkeypatch.delenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", raising=False)
        assert is_speculative_execution_enabled() is False

    def test_compat_env_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ENABLE_SPECULATIVE_EXECUTION", raising=False)
        monkeypatch.setenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", "yes")
        assert is_speculative_execution_enabled() is True

    def test_primary_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "0")
        monkeypatch.setenv("KERNELONE_ENABLE_SPECULATIVE_EXECUTION", "1")
        assert is_speculative_execution_enabled() is False
