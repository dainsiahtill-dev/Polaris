"""Unit tests for TurnEngine core functionality.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

Tests cover TurnEngine configuration, artifacts, and safety policies.
"""

from __future__ import annotations

import os

from polaris.cells.roles.kernel.internal.turn_engine import (
    AssistantTurnArtifacts,
    TurnEngineConfig,
    _BracketToolWrapperFilter,
)


class TestTurnEngineConfig:
    def test_default_values(self) -> None:
        config = TurnEngineConfig()
        assert config.max_turns == 64
        assert config.max_total_tool_calls == 64
        assert config.max_stall_cycles == 2
        assert config.max_wall_time_seconds == 900
        assert config.enable_streaming is True

    def test_from_env_with_custom_values(self) -> None:
        env_backup = os.environ.copy()
        try:
            os.environ["KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS"] = "100"
            os.environ["KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES"] = "5"
            os.environ["KERNELONE_TURN_ENGINE_STREAM"] = "false"
            config = TurnEngineConfig.from_env()
            assert config.max_turns == 100
            assert config.max_total_tool_calls == 100
            assert config.max_stall_cycles == 5
            assert config.enable_streaming is False
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_from_env_with_invalid_values_uses_defaults(self) -> None:
        env_backup = os.environ.copy()
        try:
            os.environ["KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS"] = "invalid"
            config = TurnEngineConfig.from_env()
            assert config.max_turns == 64
        finally:
            os.environ.clear()
            os.environ.update(env_backup)


class TestAssistantTurnArtifacts:
    def test_creation_with_required_fields(self) -> None:
        artifacts = AssistantTurnArtifacts(
            raw_content="Test content",
            clean_content="Test content",
        )
        assert artifacts.raw_content == "Test content"
        assert artifacts.thinking is None
        assert artifacts.native_tool_calls == ()
        assert artifacts.native_tool_provider == "auto"

    def test_creation_with_all_fields(self) -> None:
        tool_calls = [{"name": "test_tool", "args": {}}]
        artifacts = AssistantTurnArtifacts(
            raw_content="Raw content",
            clean_content="Clean content",
            thinking="Some thinking",
            native_tool_calls=tuple(tool_calls),
            native_tool_provider="openai",
        )
        assert artifacts.thinking == "Some thinking"
        assert len(artifacts.native_tool_calls) == 1


class TestBracketToolWrapperFilter:
    def test_empty_feed_returns_empty(self) -> None:
        filter_obj = _BracketToolWrapperFilter()
        result = filter_obj.feed("")
        assert result == ""

    def test_plain_text_passes_through(self) -> None:
        filter_obj = _BracketToolWrapperFilter()
        result = filter_obj.feed("Hello, world!")
        assert result == "Hello, world!"

    def test_strips_open_wrapper(self) -> None:
        filter_obj = _BracketToolWrapperFilter()
        result = filter_obj.feed("[TOOL_CALL]")
        assert result == ""
        assert filter_obj._inside_wrapper is True

    def test_strips_close_wrapper(self) -> None:
        filter_obj = _BracketToolWrapperFilter()
        filter_obj._inside_wrapper = True
        result = filter_obj.feed("[/TOOL_CALL]")
        assert result == ""
        assert filter_obj._inside_wrapper is False

    def test_case_insensitive_wrappers(self) -> None:
        filter_obj = _BracketToolWrapperFilter()
        filter_obj._inside_wrapper = True
        result = filter_obj.feed("[Tool_Result]")
        assert result == ""
