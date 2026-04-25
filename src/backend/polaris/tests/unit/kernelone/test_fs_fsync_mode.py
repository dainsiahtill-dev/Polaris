"""Tests for polaris.kernelone.fs.fsync_mode."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.fs.fsync_mode import (
    _DISABLED_TOKENS,
    IO_FSYNC_ENV,
    is_fsync_enabled,
    resolve_fsync_mode,
)


class TestResolveFsyncMode:
    def test_explicit_strict(self) -> None:
        assert resolve_fsync_mode("strict") == "strict"

    def test_explicit_relaxed(self) -> None:
        assert resolve_fsync_mode("relaxed") == "relaxed"

    def test_explicit_none_defaults_to_strict(self) -> None:
        assert resolve_fsync_mode(None) == "strict"

    def test_strips_whitespace(self) -> None:
        assert resolve_fsync_mode("  STRICT  ") == "strict"

    def test_lowercases(self) -> None:
        assert resolve_fsync_mode("STRICT") == "strict"

    def test_empty_string_defaults_to_strict(self) -> None:
        assert resolve_fsync_mode("") == "strict"

    def test_from_env(self) -> None:
        with patch("polaris.kernelone.fs.fsync_mode.resolve_env_str", return_value="relaxed"):
            assert resolve_fsync_mode() == "relaxed"

    def test_from_env_none_defaults_strict(self) -> None:
        with patch("polaris.kernelone.fs.fsync_mode.resolve_env_str", return_value=None):
            assert resolve_fsync_mode() == "strict"


class TestIsFsyncEnabled:
    def test_strict_enabled(self) -> None:
        assert is_fsync_enabled("strict") is True

    def test_always_enabled(self) -> None:
        assert is_fsync_enabled("always") is True

    def test_disabled_tokens(self) -> None:
        for token in _DISABLED_TOKENS:
            assert is_fsync_enabled(token) is False

    def test_disabled_variations(self) -> None:
        assert is_fsync_enabled("FALSE") is False
        assert is_fsync_enabled("No") is False
        assert is_fsync_enabled("OFF") is False

    def test_none_uses_env(self) -> None:
        with patch("polaris.kernelone.fs.fsync_mode.resolve_env_str", return_value="strict"):
            assert is_fsync_enabled() is True

    def test_none_uses_env_disabled(self) -> None:
        with patch("polaris.kernelone.fs.fsync_mode.resolve_env_str", return_value="0"):
            assert is_fsync_enabled() is False


class TestConstants:
    def test_io_fsync_env_value(self) -> None:
        assert IO_FSYNC_ENV == "KERNELONE_IO_FSYNC_MODE"

    def test_disabled_tokens_set(self) -> None:
        expected = {"0", "false", "no", "off", "relaxed", "skip", "disabled"}
        assert expected == _DISABLED_TOKENS
