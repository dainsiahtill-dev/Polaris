"""Tests for polaris.cells.roles.profile.internal.builtin_profiles.

Covers the BUILTIN_PROFILES data structure and role configuration invariants.
All tests are pure — no I/O, no mocking required.
"""

from __future__ import annotations

from typing import Any

import pytest

from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES


class TestBuiltinProfilesStructure:
    """Tests for the overall BUILTIN_PROFILES structure."""

    def test_profiles_is_list(self) -> None:
        assert isinstance(BUILTIN_PROFILES, list)

    def test_has_six_roles(self) -> None:
        assert len(BUILTIN_PROFILES) == 6

    def test_all_items_are_dicts(self) -> None:
        assert all(isinstance(p, dict) for p in BUILTIN_PROFILES)

    def test_all_have_role_id(self) -> None:
        assert all("role_id" in p for p in BUILTIN_PROFILES)

    def test_all_have_display_name(self) -> None:
        assert all("display_name" in p for p in BUILTIN_PROFILES)

    def test_all_have_version(self) -> None:
        assert all("version" in p for p in BUILTIN_PROFILES)

    def test_role_ids_are_unique(self) -> None:
        role_ids = [p["role_id"] for p in BUILTIN_PROFILES]
        assert len(role_ids) == len(set(role_ids))


class TestPmProfile:
    """Tests for the PM profile."""

    @pytest.fixture
    def pm(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "pm")

    def test_role_id(self, pm: dict[str, Any]) -> None:
        assert pm["role_id"] == "pm"

    def test_display_name(self, pm: dict[str, Any]) -> None:
        assert "PM" in pm["display_name"]

    def test_prompt_policy_output_format(self, pm: dict[str, Any]) -> None:
        assert pm["prompt_policy"]["output_format"] == "json"

    def test_tool_policy_allow_code_write_false(self, pm: dict[str, Any]) -> None:
        assert pm["tool_policy"]["allow_code_write"] is False

    def test_tool_policy_allow_command_execution_false(self, pm: dict[str, Any]) -> None:
        assert pm["tool_policy"]["allow_command_execution"] is False

    def test_tool_policy_whitelist_not_empty(self, pm: dict[str, Any]) -> None:
        assert len(pm["tool_policy"]["whitelist"]) > 0

    def test_context_policy_max_tokens_positive(self, pm: dict[str, Any]) -> None:
        assert pm["context_policy"]["max_context_tokens"] > 0

    def test_data_policy_encoding_utf8(self, pm: dict[str, Any]) -> None:
        assert pm["data_policy"]["encoding"] == "utf-8"

    def test_responsibilities_is_list(self, pm: dict[str, Any]) -> None:
        assert isinstance(pm["responsibilities"], list)
        assert len(pm["responsibilities"]) > 0


class TestDirectorProfile:
    """Tests for the Director profile."""

    @pytest.fixture
    def director(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "director")

    def test_role_id(self, director: dict[str, Any]) -> None:
        assert director["role_id"] == "director"

    def test_allow_code_write_true(self, director: dict[str, Any]) -> None:
        assert director["tool_policy"]["allow_code_write"] is True

    def test_allow_command_execution_true(self, director: dict[str, Any]) -> None:
        assert director["tool_policy"]["allow_command_execution"] is True

    def test_blacklist_has_delete_file(self, director: dict[str, Any]) -> None:
        assert "delete_file" in director["tool_policy"]["blacklist"]

    def test_allow_file_delete_false(self, director: dict[str, Any]) -> None:
        assert director["tool_policy"]["allow_file_delete"] is False

    def test_output_format_search_replace(self, director: dict[str, Any]) -> None:
        assert director["prompt_policy"]["output_format"] == "search_replace"


class TestQaProfile:
    """Tests for the QA profile."""

    @pytest.fixture
    def qa(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "qa")

    def test_role_id(self, qa: dict[str, Any]) -> None:
        assert qa["role_id"] == "qa"

    def test_allow_code_write_false(self, qa: dict[str, Any]) -> None:
        assert qa["tool_policy"]["allow_code_write"] is False

    def test_allow_command_execution_true(self, qa: dict[str, Any]) -> None:
        assert qa["tool_policy"]["allow_command_execution"] is True

    def test_data_policy_retention_long(self, qa: dict[str, Any]) -> None:
        assert qa["data_policy"]["retention_days"] == 180


class TestArchitectProfile:
    """Tests for the Architect profile."""

    @pytest.fixture
    def architect(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "architect")

    def test_allow_code_write_false(self, architect: dict[str, Any]) -> None:
        assert architect["tool_policy"]["allow_code_write"] is False

    def test_data_policy_retention_180(self, architect: dict[str, Any]) -> None:
        assert architect["data_policy"]["retention_days"] == 180


class TestChiefEngineerProfile:
    """Tests for the Chief Engineer profile."""

    @pytest.fixture
    def ce(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "chief_engineer")

    def test_allow_code_write_false(self, ce: dict[str, Any]) -> None:
        assert ce["tool_policy"]["allow_code_write"] is False

    def test_max_tool_calls_15(self, ce: dict[str, Any]) -> None:
        assert ce["tool_policy"]["max_tool_calls_per_turn"] == 15


class TestScoutProfile:
    """Tests for the Scout profile."""

    @pytest.fixture
    def scout(self) -> dict[str, Any]:
        return next(p for p in BUILTIN_PROFILES if p["role_id"] == "scout")

    def test_allow_code_write_false(self, scout: dict[str, Any]) -> None:
        assert scout["tool_policy"]["allow_code_write"] is False

    def test_max_context_tokens_highest(self, scout: dict[str, Any]) -> None:
        assert scout["context_policy"]["max_context_tokens"] == 16000

    def test_max_tool_calls_50(self, scout: dict[str, Any]) -> None:
        assert scout["tool_policy"]["max_tool_calls_per_turn"] == 50


class TestProfileInvariants:
    """Cross-profile invariant tests."""

    def test_all_versions_are_1_0_0(self) -> None:
        assert all(p["version"] == "1.0.0" for p in BUILTIN_PROFILES)

    def test_all_data_policies_use_utf8(self) -> None:
        assert all(p["data_policy"]["encoding"] == "utf-8" for p in BUILTIN_PROFILES)

    def test_all_prompt_policies_include_thinking(self) -> None:
        assert all(p["prompt_policy"]["include_thinking"] is True for p in BUILTIN_PROFILES)

    def test_only_director_can_write_code(self) -> None:
        writers = [p["role_id"] for p in BUILTIN_PROFILES if p["tool_policy"]["allow_code_write"]]
        assert writers == ["director"]

    def test_only_director_and_qa_can_execute_commands(self) -> None:
        executors = [p["role_id"] for p in BUILTIN_PROFILES if p["tool_policy"]["allow_command_execution"]]
        assert set(executors) == {"director", "qa"}
