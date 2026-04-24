"""Tests for polaris.cells.roles.kernel.internal.turn_engine.results."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.roles.kernel.internal.turn_engine.results import (
    build_stream_complete_result,
    make_error_result,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult


class TestMakeErrorResult:
    def test_creates_error_result(self) -> None:
        result = make_error_result(
            error="something failed",
            profile_version="v1",
            prompt_fingerprint=None,
            tool_policy_id="tp1",
        )
        assert isinstance(result, RoleTurnResult)
        assert result.error == "something failed"
        assert result.content == ""
        assert result.is_complete is False
        assert result.profile_version == "v1"
        assert result.tool_policy_id == "tp1"

    def test_includes_metadata(self) -> None:
        result = make_error_result(
            error="fail",
            profile_version="v1",
            prompt_fingerprint=None,
            tool_policy_id="tp1",
            metadata={"key": "value"},
        )
        assert result.metadata == {"key": "value"}


class TestBuildStreamCompleteResult:
    def test_basic_result(self) -> None:
        profile = MagicMock()
        profile.version = "v1"
        profile.tool_policy_id = "tp1"
        result = build_stream_complete_result(
            content="hello",
            thinking=None,
            all_tool_calls=[],
            all_tool_results=[],
            profile=profile,
            fingerprint=None,
            rounds=0,
        )
        assert isinstance(result, RoleTurnResult)
        assert result.content == "hello"
        assert result.execution_stats["stream_tool_rounds"] == 0
        assert result.execution_stats["tool_calls_count"] == 0

    def test_with_tool_calls(self) -> None:
        profile = MagicMock()
        profile.version = "v1"
        profile.tool_policy_id = "tp1"
        result = build_stream_complete_result(
            content="done",
            thinking="thought",
            all_tool_calls=[{"tool": "read"}],
            all_tool_results=[{"ok": True}],
            profile=profile,
            fingerprint=None,
            rounds=1,
        )
        assert result.execution_stats["tool_calls_count"] == 1
        assert result.execution_stats["tool_results_count"] == 1
        assert result.thinking == "thought"

    def test_turn_count_in_stats(self) -> None:
        profile = MagicMock()
        profile.version = "v1"
        profile.tool_policy_id = "tp1"
        result = build_stream_complete_result(
            content="done",
            thinking=None,
            all_tool_calls=[],
            all_tool_results=[],
            profile=profile,
            fingerprint=None,
            rounds=0,
            turn_count=5,
        )
        assert result.execution_stats["turn_count"] == 5

    def test_detects_tool_failure(self) -> None:
        profile = MagicMock()
        profile.version = "v1"
        profile.tool_policy_id = "tp1"
        result = build_stream_complete_result(
            content="done",
            thinking=None,
            all_tool_calls=[],
            all_tool_results=[{"ok": False, "error_type": "timeout"}],
            profile=profile,
            fingerprint=None,
            rounds=1,
        )
        assert result.tool_execution_error == "timeout"

    def test_no_profile_uses_empty_policy_id(self) -> None:
        result = build_stream_complete_result(
            content="done",
            thinking=None,
            all_tool_calls=[],
            all_tool_results=[],
            profile=None,
            fingerprint=None,
            rounds=0,
        )
        assert result.tool_policy_id == ""
