"""Tests for polaris.cells.roles.profile.internal.builtin_profiles.

Covers structure validation, policy consistency, and data integrity
of the built-in role profiles.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES


class TestBuiltinProfilesStructure:
    """Validate the overall structure of BUILTIN_PROFILES."""

    def test_is_non_empty_list(self) -> None:
        assert isinstance(BUILTIN_PROFILES, list)
        assert len(BUILTIN_PROFILES) > 0

    def test_all_items_are_dicts(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile, dict)


class TestRequiredFields:
    """Every profile must contain the expected top-level keys."""

    REQUIRED_TOP_LEVEL_FIELDS = [
        "role_id",
        "display_name",
        "description",
        "responsibilities",
        "prompt_policy",
        "tool_policy",
        "context_policy",
        "data_policy",
        "library_policy",
        "version",
    ]

    @pytest.mark.parametrize("field", REQUIRED_TOP_LEVEL_FIELDS)
    def test_all_profiles_have_required_field(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            assert field in profile, f"Profile {profile.get('role_id', '?')} missing field: {field}"

    def test_role_id_is_string(self) -> None:
        for profile in BUILTIN_PROFILES:
            role_id = profile["role_id"]
            assert isinstance(role_id, str) and role_id.strip(), f"Invalid role_id in profile: {role_id}"

    def test_display_name_is_string(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["display_name"], str)

    def test_description_is_string(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["description"], str)

    def test_responsibilities_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            resp = profile["responsibilities"]
            assert isinstance(resp, list)
            for item in resp:
                assert isinstance(item, str)


class TestRoleIdUniqueness:
    """Role IDs must be unique across all builtin profiles."""

    def test_role_ids_are_unique(self) -> None:
        role_ids = [p["role_id"] for p in BUILTIN_PROFILES]
        assert len(role_ids) == len(set(role_ids)), f"Duplicate role_ids found: {role_ids}"

    def test_expected_roles_present(self) -> None:
        expected = {"pm", "architect", "chief_engineer", "director", "qa", "scout"}
        actual = {p["role_id"] for p in BUILTIN_PROFILES}
        assert expected <= actual, f"Missing roles: {expected - actual}"


class TestPromptPolicy:
    """Validate prompt_policy structure across all profiles."""

    REQUIRED_PROMPT_POLICY_FIELDS = [
        "core_template_id",
        "allow_appendix",
        "allow_override",
        "output_format",
        "include_thinking",
        "quality_checklist",
    ]

    @pytest.mark.parametrize("field", REQUIRED_PROMPT_POLICY_FIELDS)
    def test_prompt_policy_has_required_fields(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["prompt_policy"]
            assert field in policy, f"Profile {profile['role_id']} missing prompt_policy.{field}"

    def test_core_template_id_matches_role_id(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert profile["prompt_policy"]["core_template_id"] == profile["role_id"]

    def test_allow_appendix_is_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["prompt_policy"]["allow_appendix"], bool)

    def test_allow_override_is_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["prompt_policy"]["allow_override"], bool)

    def test_output_format_is_valid(self) -> None:
        valid_formats = {"json", "text", "search_replace", "markdown"}
        for profile in BUILTIN_PROFILES:
            fmt = profile["prompt_policy"]["output_format"]
            assert fmt in valid_formats, f"Profile {profile['role_id']} has invalid output_format: {fmt}"

    def test_quality_checklist_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            checklist = profile["prompt_policy"]["quality_checklist"]
            assert isinstance(checklist, list)
            for item in checklist:
                assert isinstance(item, str)


class TestToolPolicy:
    """Validate tool_policy structure and consistency."""

    REQUIRED_TOOL_POLICY_FIELDS = [
        "whitelist",
        "blacklist",
        "allow_code_write",
        "allow_command_execution",
        "allow_file_delete",
        "max_tool_calls_per_turn",
        "tool_timeout_seconds",
    ]

    @pytest.mark.parametrize("field", REQUIRED_TOOL_POLICY_FIELDS)
    def test_tool_policy_has_required_fields(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["tool_policy"]
            assert field in policy, f"Profile {profile['role_id']} missing tool_policy.{field}"

    def test_whitelist_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            whitelist = profile["tool_policy"]["whitelist"]
            assert isinstance(whitelist, list)
            for tool in whitelist:
                assert isinstance(tool, str)

    def test_blacklist_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            blacklist = profile["tool_policy"]["blacklist"]
            assert isinstance(blacklist, list)
            for tool in blacklist:
                assert isinstance(tool, str)

    def test_no_tool_in_both_whitelist_and_blacklist(self) -> None:
        for profile in BUILTIN_PROFILES:
            whitelist = set(profile["tool_policy"]["whitelist"])
            blacklist = set(profile["tool_policy"]["blacklist"])
            overlap = whitelist & blacklist
            assert not overlap, f"Profile {profile['role_id']} has tools in both lists: {overlap}"

    def test_allow_flags_are_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["tool_policy"]
            assert isinstance(policy["allow_code_write"], bool)
            assert isinstance(policy["allow_command_execution"], bool)
            assert isinstance(policy["allow_file_delete"], bool)

    def test_max_tool_calls_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            max_calls = profile["tool_policy"]["max_tool_calls_per_turn"]
            assert isinstance(max_calls, int) and max_calls > 0

    def test_tool_timeout_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            timeout = profile["tool_policy"]["tool_timeout_seconds"]
            assert isinstance(timeout, int) and timeout > 0

    def test_director_is_only_role_with_code_write(self) -> None:
        for profile in BUILTIN_PROFILES:
            role_id = profile["role_id"]
            allow_write = profile["tool_policy"]["allow_code_write"]
            if role_id == "director":
                assert allow_write is True, "Director must allow code write"
            else:
                assert allow_write is False, f"{role_id} must NOT allow code write"

    def test_command_execution_restricted(self) -> None:
        allowed_roles = {"director", "qa"}
        for profile in BUILTIN_PROFILES:
            role_id = profile["role_id"]
            allow_exec = profile["tool_policy"]["allow_command_execution"]
            if role_id in allowed_roles:
                assert allow_exec is True, f"{role_id} should allow command execution"
            else:
                assert allow_exec is False, f"{role_id} must NOT allow command execution"

    def test_file_delete_always_false(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert profile["tool_policy"]["allow_file_delete"] is False


class TestContextPolicy:
    """Validate context_policy structure."""

    REQUIRED_CONTEXT_FIELDS = [
        "max_context_tokens",
        "max_history_turns",
        "include_project_structure",
        "include_code_snippets",
        "max_code_lines",
        "include_task_history",
        "compression_strategy",
    ]

    @pytest.mark.parametrize("field", REQUIRED_CONTEXT_FIELDS)
    def test_context_policy_has_required_fields(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["context_policy"]
            assert field in policy, f"Profile {profile['role_id']} missing context_policy.{field}"

    def test_max_context_tokens_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            tokens = profile["context_policy"]["max_context_tokens"]
            assert isinstance(tokens, int) and tokens > 0

    def test_max_history_turns_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            turns = profile["context_policy"]["max_history_turns"]
            assert isinstance(turns, int) and turns > 0

    def test_max_code_lines_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            lines = profile["context_policy"]["max_code_lines"]
            assert isinstance(lines, int) and lines > 0

    def test_compression_strategy_is_string(self) -> None:
        for profile in BUILTIN_PROFILES:
            strategy = profile["context_policy"]["compression_strategy"]
            assert isinstance(strategy, str) and strategy


class TestDataPolicy:
    """Validate data_policy structure."""

    REQUIRED_DATA_FIELDS = [
        "data_subdir",
        "encoding",
        "atomic_write",
        "backup_before_write",
        "retention_days",
        "encrypt_at_rest",
        "allowed_extensions",
    ]

    @pytest.mark.parametrize("field", REQUIRED_DATA_FIELDS)
    def test_data_policy_has_required_fields(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["data_policy"]
            assert field in policy, f"Profile {profile['role_id']} missing data_policy.{field}"

    def test_encoding_is_utf8(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert profile["data_policy"]["encoding"] == "utf-8"

    def test_data_subdir_is_string(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["data_policy"]["data_subdir"], str)

    def test_atomic_write_is_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["data_policy"]["atomic_write"], bool)

    def test_backup_before_write_is_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["data_policy"]["backup_before_write"], bool)

    def test_retention_days_is_positive_int(self) -> None:
        for profile in BUILTIN_PROFILES:
            days = profile["data_policy"]["retention_days"]
            assert isinstance(days, int) and days > 0

    def test_encrypt_at_rest_is_bool(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["data_policy"]["encrypt_at_rest"], bool)

    def test_allowed_extensions_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            exts = profile["data_policy"]["allowed_extensions"]
            assert isinstance(exts, list)
            for ext in exts:
                assert isinstance(ext, str)
                assert ext.startswith("."), f"Extension '{ext}' must start with dot"


class TestLibraryPolicy:
    """Validate library_policy structure."""

    REQUIRED_LIBRARY_FIELDS = [
        "core_libraries",
        "optional_libraries",
        "forbidden_libraries",
        "version_constraints",
    ]

    @pytest.mark.parametrize("field", REQUIRED_LIBRARY_FIELDS)
    def test_library_policy_has_required_fields(self, field: str) -> None:
        for profile in BUILTIN_PROFILES:
            policy = profile["library_policy"]
            assert field in policy, f"Profile {profile['role_id']} missing library_policy.{field}"

    def test_core_libraries_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            libs = profile["library_policy"]["core_libraries"]
            assert isinstance(libs, list)
            for lib in libs:
                assert isinstance(lib, str)

    def test_optional_libraries_is_list_of_strings(self) -> None:
        for profile in BUILTIN_PROFILES:
            libs = profile["library_policy"]["optional_libraries"]
            assert isinstance(libs, list)
            for lib in libs:
                assert isinstance(lib, str)

    def test_forbidden_libraries_is_list(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["library_policy"]["forbidden_libraries"], list)

    def test_version_constraints_is_dict(self) -> None:
        for profile in BUILTIN_PROFILES:
            assert isinstance(profile["library_policy"]["version_constraints"], dict)


class TestVersion:
    """Validate version field."""

    def test_all_profiles_have_semantic_version(self) -> None:
        for profile in BUILTIN_PROFILES:
            version = profile["version"]
            assert isinstance(version, str)
            parts = version.split(".")
            assert len(parts) == 3, f"Version '{version}' must be semantic (MAJOR.MINOR.PATCH)"
            for part in parts:
                assert part.isdigit(), f"Version part '{part}' must be numeric"

    def test_all_profiles_same_version(self) -> None:
        versions = {p["version"] for p in BUILTIN_PROFILES}
        assert len(versions) == 1, f"All profiles should share the same version, found: {versions}"


class TestProfileSpecificPolicies:
    """Validate role-specific policy expectations."""

    def test_pm_has_project_management_tools(self) -> None:
        pm = next(p for p in BUILTIN_PROFILES if p["role_id"] == "pm")
        whitelist = set(pm["tool_policy"]["whitelist"])
        assert "task_create" in whitelist
        assert "task_update" in whitelist
        assert "todo_read" in whitelist
        assert "todo_write" in whitelist

    def test_director_has_edit_tools(self) -> None:
        director = next(p for p in BUILTIN_PROFILES if p["role_id"] == "director")
        whitelist = set(director["tool_policy"]["whitelist"])
        assert "precision_edit" in whitelist
        assert "repo_apply_diff" in whitelist
        assert "search_replace" in whitelist
        assert "write_file" in whitelist

    def test_director_has_delete_file_in_blacklist(self) -> None:
        director = next(p for p in BUILTIN_PROFILES if p["role_id"] == "director")
        assert "delete_file" in director["tool_policy"]["blacklist"]

    def test_qa_has_testing_libraries(self) -> None:
        qa = next(p for p in BUILTIN_PROFILES if p["role_id"] == "qa")
        core = set(qa["library_policy"]["core_libraries"])
        assert "pytest" in core
        assert "coverage" in core

    def test_scout_has_largest_tool_allowance(self) -> None:
        scout = next(p for p in BUILTIN_PROFILES if p["role_id"] == "scout")
        max_calls = scout["tool_policy"]["max_tool_calls_per_turn"]
        other_max = [p["tool_policy"]["max_tool_calls_per_turn"] for p in BUILTIN_PROFILES if p["role_id"] != "scout"]
        assert max_calls >= max(other_max), "Scout should have the highest tool call allowance"

    def test_scout_has_highest_context_tokens(self) -> None:
        scout = next(p for p in BUILTIN_PROFILES if p["role_id"] == "scout")
        tokens = scout["context_policy"]["max_context_tokens"]
        other_tokens = [p["context_policy"]["max_context_tokens"] for p in BUILTIN_PROFILES if p["role_id"] != "scout"]
        assert tokens >= max(other_tokens), "Scout should have the highest context token limit"

    def test_architect_has_adr_extension(self) -> None:
        architect = next(p for p in BUILTIN_PROFILES if p["role_id"] == "architect")
        exts = architect["data_policy"]["allowed_extensions"]
        assert ".adr" in exts

    def test_chief_engineer_has_blueprint_extension(self) -> None:
        ce = next(p for p in BUILTIN_PROFILES if p["role_id"] == "chief_engineer")
        exts = ce["data_policy"]["allowed_extensions"]
        assert ".blueprint" in exts

    def test_qa_has_audit_extension(self) -> None:
        qa = next(p for p in BUILTIN_PROFILES if p["role_id"] == "qa")
        exts = qa["data_policy"]["allowed_extensions"]
        assert ".audit" in exts


class TestProfileLookup:
    """Test helper functions for profile retrieval."""

    def test_get_profile_by_role_id(self) -> None:
        for role_id in ("pm", "architect", "chief_engineer", "director", "qa", "scout"):
            profile = next((p for p in BUILTIN_PROFILES if p["role_id"] == role_id), None)
            assert profile is not None
            assert profile["role_id"] == role_id

    def test_no_duplicate_role_ids(self) -> None:
        role_ids = [p["role_id"] for p in BUILTIN_PROFILES]
        assert len(role_ids) == len(set(role_ids))
