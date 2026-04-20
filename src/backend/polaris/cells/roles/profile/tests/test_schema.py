"""Unit tests for `roles.profile` schema types.

Tests frozen dataclasses, enums, fingerprint generation,
serialisation round-trips, and RBAC model matching logic.
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.profile.internal.schema import (
    Action,
    Policy,
    PolicyEffect,
    PromptFingerprint,
    Resource,
    ResourceType,
    RoleContextPolicy,
    RoleDataPolicy,
    RoleExecutionMode,
    RoleLibraryPolicy,
    RoleProfile,
    RolePromptPolicy,
    RoleToolPolicy,
    RoleTurnRequest,
    RoleTurnResult,
    SequentialBudget,
    SequentialConfig,
    SequentialMode,
    SequentialStatsResult,
    SequentialTraceLevel,
    Subject,
    SubjectType,
    profile_from_dict,
    profile_to_dict,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestSequentialMode:
    def test_values(self) -> None:
        assert SequentialMode.DISABLED.value == "disabled"
        assert SequentialMode.ENABLED.value == "enabled"
        assert SequentialMode.REQUIRED.value == "required"


class TestSequentialTraceLevel:
    def test_values(self) -> None:
        assert SequentialTraceLevel.OFF.value == "off"
        assert SequentialTraceLevel.SUMMARY.value == "summary"
        assert SequentialTraceLevel.DETAILED.value == "detailed"


class TestRoleExecutionMode:
    def test_values(self) -> None:
        assert RoleExecutionMode.CHAT.value == "chat"
        assert RoleExecutionMode.WORKFLOW.value == "workflow"


class TestSubjectType:
    def test_values(self) -> None:
        assert SubjectType.ROLE.value == "role"
        assert SubjectType.USER.value == "user"
        assert SubjectType.SERVICE.value == "service"


class TestResourceType:
    def test_values(self) -> None:
        assert ResourceType.FILE.value == "file"
        assert ResourceType.DIRECTORY.value == "directory"
        assert ResourceType.TOOL.value == "tool"


class TestAction:
    def test_values(self) -> None:
        assert Action.READ.value == "read"
        assert Action.WRITE.value == "write"
        assert Action.DELETE.value == "delete"


class TestPolicyEffect:
    def test_values(self) -> None:
        assert PolicyEffect.ALLOW.value == "allow"
        assert PolicyEffect.DENY.value == "deny"


# ---------------------------------------------------------------------------
# Policy dataclasses
# ---------------------------------------------------------------------------


class TestRolePromptPolicy:
    def test_frozen(self) -> None:
        policy = RolePromptPolicy(core_template_id="pm")
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            policy.allow_override = False  # type: ignore[misc]

    def test_default_values(self) -> None:
        policy = RolePromptPolicy(core_template_id="pm")
        assert policy.allow_appendix is True
        assert policy.allow_override is False
        assert policy.output_format == "json"
        assert policy.include_thinking is True
        assert policy.quality_checklist == []

    def test_full_values(self) -> None:
        policy = RolePromptPolicy(
            core_template_id="architect",
            allow_appendix=False,
            allow_override=False,
            output_format="text",
            include_thinking=False,
            quality_checklist=["check1", "check2"],
        )
        assert policy.output_format == "text"
        assert len(policy.quality_checklist) == 2


class TestRoleToolPolicy:
    def test_frozen(self) -> None:
        policy = RoleToolPolicy(whitelist=["read_file"])
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            policy.whitelist = ["grep"]  # type: ignore[misc]

    def test_policy_id_is_deterministic(self) -> None:
        p1 = RoleToolPolicy(whitelist=["a", "b"], allow_code_write=True)
        p2 = RoleToolPolicy(whitelist=["b", "a"], allow_code_write=True)
        assert p1.policy_id == p2.policy_id

    def test_policy_id_differs_on_content(self) -> None:
        p1 = RoleToolPolicy(whitelist=["a"])
        p2 = RoleToolPolicy(whitelist=["b"])
        assert p1.policy_id != p2.policy_id

    def test_policy_id_format(self) -> None:
        p = RoleToolPolicy(whitelist=["read_file"])
        assert len(p.policy_id) == 16


class TestRoleContextPolicy:
    def test_defaults(self) -> None:
        policy = RoleContextPolicy()
        assert policy.max_context_tokens == 8000
        assert policy.max_history_turns == 10
        assert policy.compression_strategy == "sliding_window"


class TestRoleDataPolicy:
    def test_defaults(self) -> None:
        policy = RoleDataPolicy(data_subdir="pm")
        assert policy.encoding == "utf-8"
        assert policy.atomic_write is True
        assert policy.retention_days == 90
        assert ".json" in policy.allowed_extensions


class TestRoleLibraryPolicy:
    def test_defaults(self) -> None:
        policy = RoleLibraryPolicy()
        assert policy.core_libraries == []
        assert policy.forbidden_libraries == []
        assert policy.version_constraints == {}

    def test_full(self) -> None:
        policy = RoleLibraryPolicy(
            core_libraries=["pytest"],
            optional_libraries=["coverage"],
            forbidden_libraries=["os"],
            version_constraints={"pytest": ">=7.0"},
        )
        assert policy.core_libraries == ["pytest"]
        assert policy.version_constraints["pytest"] == ">=7.0"


# ---------------------------------------------------------------------------
# Sequential types
# ---------------------------------------------------------------------------


class TestSequentialBudget:
    def test_defaults(self) -> None:
        b = SequentialBudget()
        assert b.max_steps == 12
        assert b.max_no_progress_steps == 3
        assert b.max_wall_time_seconds == 120


class TestSequentialConfig:
    def test_to_dict(self) -> None:
        cfg = SequentialConfig(
            mode=SequentialMode.ENABLED,
            budget=SequentialBudget(max_steps=20),
            trace_level=SequentialTraceLevel.DETAILED,
        )
        d = cfg.to_dict()
        assert d["mode"] == "enabled"
        assert d["budget"]["max_steps"] == 20
        assert d["trace_level"] == "detailed"

    def test_to_dict_without_budget(self) -> None:
        cfg = SequentialConfig(mode=SequentialMode.DISABLED)
        d = cfg.to_dict()
        assert d["budget"] == {}


class TestSequentialStatsResult:
    def test_defaults(self) -> None:
        stats = SequentialStatsResult()
        assert stats.steps == 0
        assert stats.budget_exhausted is False


# ---------------------------------------------------------------------------
# RoleProfile
# ---------------------------------------------------------------------------


class TestRoleProfile:
    def test_frozen(self) -> None:
        rp = RoleProfile(role_id="pm", display_name="PM", description="desc")
        with pytest.raises(AttributeError):
            rp.display_name = "changed"  # type: ignore[misc]

    def test_default_policies(self) -> None:
        rp = RoleProfile(role_id="pm", display_name="PM", description="desc")
        assert rp.prompt_policy.core_template_id == "default"
        assert isinstance(rp.tool_policy, RoleToolPolicy)
        assert rp.data_policy.data_subdir == "default"

    def test_profile_fingerprint(self) -> None:
        rp = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="desc",
            prompt_policy=RolePromptPolicy(core_template_id="pm"),
            tool_policy=RoleToolPolicy(whitelist=["read_file"]),
        )
        fp = rp.profile_fingerprint
        assert len(fp) == 16

    def test_profile_fingerprint_is_deterministic(self) -> None:
        rp1 = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="desc",
            tool_policy=RoleToolPolicy(whitelist=["read_file"]),
        )
        rp2 = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="desc",
            tool_policy=RoleToolPolicy(whitelist=["read_file"]),
        )
        assert rp1.profile_fingerprint == rp2.profile_fingerprint

    def test_profile_fingerprint_differs_on_policy(self) -> None:
        rp1 = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="desc",
            tool_policy=RoleToolPolicy(whitelist=["read_file"]),
        )
        rp2 = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="desc",
            tool_policy=RoleToolPolicy(whitelist=["grep"]),
        )
        assert rp1.profile_fingerprint != rp2.profile_fingerprint


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestProfileFromDict:
    def test_minimal(self) -> None:
        # All required fields: role_id, display_name, plus required sub-policy fields.
        rp = profile_from_dict(
            {
                "role_id": "qa",
                "display_name": "QA",
                "prompt_policy": {"core_template_id": "qa"},
                "data_policy": {"data_subdir": "qa"},
            }
        )
        assert rp.role_id == "qa"
        assert rp.display_name == "QA"
        assert rp.prompt_policy.core_template_id == "qa"
        assert rp.version == "1.0.0"

    def test_all_fields(self) -> None:
        data = {
            "role_id": "architect",
            "display_name": "Architect",
            "description": "Architecture design",
            "responsibilities": ["design", "review"],
            "provider_id": "openai",
            "model": "gpt-4",
            "prompt_policy": {"core_template_id": "architect", "allow_override": False},
            "tool_policy": {"whitelist": ["read_file"]},
            "context_policy": {"max_context_tokens": 10000},
            "data_policy": {"data_subdir": "architect"},
            "library_policy": {"core_libraries": ["pyyaml"]},
            "version": "2.0.0",
        }
        rp = profile_from_dict(data)
        assert rp.version == "2.0.0"
        assert rp.prompt_policy.core_template_id == "architect"
        assert rp.tool_policy.whitelist == ["read_file"]
        assert rp.context_policy.max_context_tokens == 10000


class TestProfileToDict:
    def test_roundtrip(self) -> None:
        original = RoleProfile(
            role_id="pm",
            display_name="PM",
            description="Project management",
            responsibilities=["plan", "track"],
            tool_policy=RoleToolPolicy(whitelist=["read_file", "grep"]),
        )
        data = profile_to_dict(original)
        restored = profile_from_dict(data)
        assert restored.role_id == original.role_id
        assert restored.display_name == original.display_name
        assert restored.tool_policy.whitelist == original.tool_policy.whitelist

    def test_includes_all_policy_fields(self) -> None:
        rp = RoleProfile(
            role_id="qa",
            display_name="QA",
            description="desc",
            prompt_policy=RolePromptPolicy(
                core_template_id="qa",
                allow_override=False,
                quality_checklist=["check1"],
            ),
        )
        data = profile_to_dict(rp)
        assert "quality_checklist" in data["prompt_policy"]
        assert data["prompt_policy"]["quality_checklist"] == ["check1"]


# ---------------------------------------------------------------------------
# RoleTurnRequest / RoleTurnResult
# ---------------------------------------------------------------------------


class TestRoleTurnRequest:
    def test_defaults(self) -> None:
        req = RoleTurnRequest()
        assert req.mode == RoleExecutionMode.CHAT
        assert req.message == ""
        assert req.domain == "code"
        assert req.history == []
        assert req.validate_output is True
        assert req.max_retries == 1
        assert req.sequential_mode == SequentialMode.DISABLED


class TestRoleTurnResult:
    def test_post_init_sets_execution_stats(self) -> None:
        result = RoleTurnResult()
        assert result.execution_stats["platform_retry_count"] == 0
        assert result.is_complete is True

    def test_full_construction(self) -> None:
        result = RoleTurnResult(
            content="done",
            tool_calls=[{"name": "read_file"}],
            quality_score=85.0,
            is_complete=True,
        )
        assert result.content == "done"
        assert len(result.tool_calls) == 1


# ---------------------------------------------------------------------------
# RBAC models
# ---------------------------------------------------------------------------


class TestSubject:
    def test_to_dict(self) -> None:
        s = Subject(type=SubjectType.ROLE, id="pm")
        d = s.to_dict()
        assert d["type"] == "role"
        assert d["id"] == "pm"


class TestResource:
    def test_to_dict(self) -> None:
        r = Resource(type=ResourceType.FILE, pattern="*.py")
        d = r.to_dict()
        assert d["type"] == "file"
        assert d["pattern"] == "*.py"


class TestPolicyMatchesSubject:
    def test_wildcard_id_matches_all(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="*")],
            resources=[Resource(type=ResourceType.TOOL, pattern="*")],
            actions=[Action.READ],
        )
        assert p.matches_subject(Subject(type=SubjectType.ROLE, id="pm"))
        assert p.matches_subject(Subject(type=SubjectType.ROLE, id="architect"))

    def test_specific_id_matches_only_that(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="pm")],
            resources=[Resource(type=ResourceType.TOOL, pattern="*")],
            actions=[Action.READ],
        )
        assert p.matches_subject(Subject(type=SubjectType.ROLE, id="pm"))
        assert not p.matches_subject(Subject(type=SubjectType.ROLE, id="architect"))


class TestPolicyMatchesResource:
    def test_wildcard_pattern_matches_all(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="*")],
            resources=[Resource(type=ResourceType.FILE, pattern="*")],
            actions=[Action.READ],
        )
        assert p.matches_resource(Resource(type=ResourceType.FILE, pattern="*.py"))
        assert p.matches_resource(Resource(type=ResourceType.FILE, pattern="*.md"))

    def test_specific_pattern_matches_subset(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="*")],
            resources=[Resource(type=ResourceType.FILE, pattern="*.py")],
            actions=[Action.READ],
        )
        assert p.matches_resource(Resource(type=ResourceType.FILE, pattern="test.py"))
        assert not p.matches_resource(Resource(type=ResourceType.FILE, pattern="test.md"))


class TestPolicyMatchesAction:
    def test_admin_action_matches_all(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="*")],
            resources=[Resource(type=ResourceType.TOOL, pattern="*")],
            actions=[Action.ADMIN],
        )
        assert p.matches_action(Action.READ)
        assert p.matches_action(Action.WRITE)
        assert p.matches_action(Action.DELETE)

    def test_specific_actions_match_only_those(self) -> None:
        p = Policy(
            id="p1",
            name="test",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="*")],
            resources=[Resource(type=ResourceType.TOOL, pattern="*")],
            actions=[Action.READ],
        )
        assert p.matches_action(Action.READ)
        assert not p.matches_action(Action.WRITE)


class TestPolicyToDict:
    def test_to_dict(self) -> None:
        p = Policy(
            id="p1",
            name="Read-only",
            effect=PolicyEffect.ALLOW,
            subjects=[Subject(type=SubjectType.ROLE, id="pm")],
            resources=[Resource(type=ResourceType.FILE, pattern="*.py")],
            actions=[Action.READ],
            priority=10,
            enabled=True,
        )
        d = p.to_dict()
        assert d["id"] == "p1"
        assert d["effect"] == "allow"
        assert len(d["subjects"]) == 1
        assert len(d["actions"]) == 1
        assert d["priority"] == 10


# ---------------------------------------------------------------------------
# PromptFingerprint
# ---------------------------------------------------------------------------


class TestPromptFingerprint:
    def test_full_hash_auto_computed(self) -> None:
        fp = PromptFingerprint(
            core_hash="abc123",
            appendix_hash="def456",
            profile_fingerprint="xyz789",
        )
        assert fp.full_hash != ""
        assert len(fp.full_hash) == 16

    def test_full_hash_deterministic(self) -> None:
        fp1 = PromptFingerprint(
            core_hash="abc",
            appendix_hash="def",
            profile_fingerprint="xyz",
        )
        fp2 = PromptFingerprint(
            core_hash="abc",
            appendix_hash="def",
            profile_fingerprint="xyz",
        )
        assert fp1.full_hash == fp2.full_hash
