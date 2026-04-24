"""Tests for polaris.kernelone.context.control_plane_noise."""

from __future__ import annotations

from polaris.kernelone.context.control_plane_noise import (
    is_control_plane_noise,
    is_signal_role,
    normalize_control_plane_text,
)


class TestNormalizeControlPlaneText:
    def test_collapses_whitespace(self) -> None:
        assert normalize_control_plane_text("  hello   world  ") == "hello world"

    def test_replaces_newlines(self) -> None:
        assert normalize_control_plane_text("hello\nworld") == "hello world"

    def test_none_returns_empty(self) -> None:
        assert normalize_control_plane_text(None) == ""


class TestIsControlPlaneNoise:
    def test_tool_result_tag(self) -> None:
        assert is_control_plane_noise("<tool_result>") is True

    def test_tool_result_close_tag(self) -> None:
        assert is_control_plane_noise("</tool_result>") is True

    def test_system_warning(self) -> None:
        assert is_control_plane_noise("[system warning]") is True

    def test_circuit_breaker(self) -> None:
        assert is_control_plane_noise("[circuit breaker]") is True

    def test_normal_text(self) -> None:
        assert is_control_plane_noise("Hello world") is False

    def test_empty_text(self) -> None:
        assert is_control_plane_noise("") is False


class TestIsSignalRole:
    def test_user(self) -> None:
        assert is_signal_role("user") is True

    def test_assistant(self) -> None:
        assert is_signal_role("assistant") is True

    def test_system(self) -> None:
        assert is_signal_role("system") is False

    def test_none(self) -> None:
        assert is_signal_role(None) is False
