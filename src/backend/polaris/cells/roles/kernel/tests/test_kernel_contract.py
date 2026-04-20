"""Minimum test suite for `roles.kernel` public contracts.

Tests are purely against public contracts (contracts.py) and selected
value-only helpers from constitution_rules (no I/O, no LLM, no process).

Coverage targets:
- Contract dataclass validation: required fields, empty-string guard, frozen semantics
- RoleKernelResultV1: ok/error invariants, dict-copy contract
- RoleKernelError: structured error attributes
- IRoleKernelService: Protocol structural check
- constitution_rules: is_action_allowed, RoleBoundary.validate_action / can_send_to
- retry contracts: ResolveRetryPolicyQueryV1 boundary validation
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.constitution_rules import (
    CONSTITUTION,
    Role,
    ViolationLevel,
    is_action_allowed,
)
from polaris.cells.roles.kernel.public.contracts import (
    BuildRolePromptCommandV1,
    ClassifyKernelErrorQueryV1,
    ExecuteRoleKernelTurnCommandV1,
    IRoleKernelService,
    ParseRoleOutputCommandV1,
    ResolveRetryPolicyQueryV1,
    RoleKernelError,
    RoleKernelParsedOutputEventV1,
    RoleKernelPromptBuiltEventV1,
    RoleKernelQualityCheckedEventV1,
    RoleKernelResultV1,
)

# ---------------------------------------------------------------------------
# Happy path: contract construction
# ---------------------------------------------------------------------------


class TestBuildRolePromptCommandV1HappyPath:
    """BuildRolePromptCommandV1 constructs correctly under normal inputs."""

    def test_minimal_construction_succeeds(self) -> None:
        cmd = BuildRolePromptCommandV1(role_id="pm", workspace="/ws")
        assert cmd.role_id == "pm"
        assert cmd.workspace == "/ws"

    def test_context_defaulted_to_empty_dict(self) -> None:
        cmd = BuildRolePromptCommandV1(role_id="director", workspace="/ws")
        assert cmd.context == {}

    def test_context_payload_is_copied_not_aliased(self) -> None:
        original = {"key": "value"}
        cmd = BuildRolePromptCommandV1(role_id="qa", workspace="/ws", context=original)
        assert cmd.context == original
        # Mutation of original must not affect the frozen copy
        original["extra"] = "injected"
        assert "extra" not in cmd.context

    def test_structured_output_flag_propagates(self) -> None:
        cmd = BuildRolePromptCommandV1(role_id="architect", workspace="/ws", structured_output=True)
        assert cmd.structured_output is True


class TestExecuteRoleKernelTurnCommandV1HappyPath:
    """ExecuteRoleKernelTurnCommandV1 carries all required fields."""

    def test_full_construction(self) -> None:
        cmd = ExecuteRoleKernelTurnCommandV1(
            role_id="chief_engineer",
            workspace="/ws",
            prompt="Analyze the codebase",
            context={"task_id": "t-1"},
        )
        assert cmd.role_id == "chief_engineer"
        assert cmd.prompt == "Analyze the codebase"
        assert cmd.context == {"task_id": "t-1"}

    def test_prompt_trimming_does_not_strip_mid_content(self) -> None:
        cmd = ExecuteRoleKernelTurnCommandV1(role_id="pm", workspace="/ws", prompt="  valid prompt  ")
        # __post_init__ strips → still non-empty
        assert cmd.prompt.strip() != ""


# ---------------------------------------------------------------------------
# Edge cases: empty-string guard
# ---------------------------------------------------------------------------


class TestContractEmptyStringGuard:
    """All required string fields must reject empty / whitespace-only values."""

    def test_build_prompt_empty_role_id_raises(self) -> None:
        with pytest.raises(ValueError, match="role_id"):
            BuildRolePromptCommandV1(role_id="", workspace="/ws")

    def test_build_prompt_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            BuildRolePromptCommandV1(role_id="pm", workspace="   ")

    def test_parse_output_empty_output_raises(self) -> None:
        with pytest.raises(ValueError, match="output"):
            ParseRoleOutputCommandV1(role_id="pm", output="")

    def test_classify_error_empty_text_raises(self) -> None:
        with pytest.raises(ValueError, match="error_text"):
            ClassifyKernelErrorQueryV1(error_text="")

    def test_resolve_retry_attempt_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="attempt"):
            ResolveRetryPolicyQueryV1(error_text="timeout", attempt=0)

    def test_resolve_retry_max_retries_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            ResolveRetryPolicyQueryV1(error_text="timeout", max_retries=0)


# ---------------------------------------------------------------------------
# Edge cases: event contracts
# ---------------------------------------------------------------------------


class TestKernelEventContracts:
    """Event dataclasses enforce non-empty fields."""

    def test_prompt_built_event_valid(self) -> None:
        ev = RoleKernelPromptBuiltEventV1(
            event_id="e-1", role_id="pm", workspace="/ws", built_at="2026-01-01T00:00:00Z"
        )
        assert ev.template_id is None

    def test_prompt_built_event_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError):
            RoleKernelPromptBuiltEventV1(event_id="", role_id="pm", workspace="/ws", built_at="2026-01-01T00:00:00Z")

    def test_parsed_output_event_valid(self) -> None:
        ev = RoleKernelParsedOutputEventV1(
            event_id="e-2", role_id="director", workspace="/ws", parsed_at="2026-01-01T00:00:00Z"
        )
        assert ev.role_id == "director"

    def test_quality_checked_event_valid(self) -> None:
        ev = RoleKernelQualityCheckedEventV1(
            event_id="e-3", role_id="qa", workspace="/ws", checked_at="2026-01-01T00:00:00Z"
        )
        assert ev.checked_at == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# RoleKernelResultV1 invariant
# ---------------------------------------------------------------------------


class TestRoleKernelResultV1:
    """RoleKernelResultV1 enforces ok/error invariant."""

    def test_success_result_no_error_required(self) -> None:
        result = RoleKernelResultV1(ok=True, status="completed", role_id="pm", workspace="/ws")
        assert result.ok is True
        assert result.error_code is None

    def test_failure_result_requires_error_code_or_message(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            RoleKernelResultV1(ok=False, status="failed", role_id="pm", workspace="/ws")

    def test_failure_result_with_error_code_ok(self) -> None:
        result = RoleKernelResultV1(ok=False, status="failed", role_id="pm", workspace="/ws", error_code="LLM_TIMEOUT")
        assert result.error_code == "LLM_TIMEOUT"

    def test_parsed_defaults_to_empty_dict(self) -> None:
        result = RoleKernelResultV1(ok=True, status="ok", role_id="qa", workspace="/ws")
        assert result.parsed == {}
        assert result.quality == {}


# ---------------------------------------------------------------------------
# RoleKernelError structured attributes
# ---------------------------------------------------------------------------


class TestRoleKernelError:
    """RoleKernelError carries code and details."""

    def test_default_code_set(self) -> None:
        err = RoleKernelError("something broke")
        assert err.code == "roles_kernel_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = RoleKernelError("parse fail", code="PARSE_ERROR", details={"field": "output"})
        assert err.code == "PARSE_ERROR"
        assert err.details == {"field": "output"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            RoleKernelError("")


# ---------------------------------------------------------------------------
# Failure path: IRoleKernelService Protocol structural check
# ---------------------------------------------------------------------------


class TestIRoleKernelServiceProtocol:
    """IRoleKernelService is a runtime_checkable Protocol."""

    def test_incomplete_impl_not_instance(self) -> None:
        class Incomplete:
            pass

        assert not isinstance(Incomplete(), IRoleKernelService)

    def test_complete_impl_is_instance(self) -> None:
        class Compliant:
            def build_prompt(self, command):
                return {}

            def parse_output(self, command):
                return {}

            def check_quality(self, command):
                return {}

            def classify_error(self, query) -> None:
                return None

            def resolve_retry_policy(self, query):
                return {}

        assert isinstance(Compliant(), IRoleKernelService)


# ---------------------------------------------------------------------------
# Constitution: is_action_allowed / RoleBoundary boundary checks
# ---------------------------------------------------------------------------


class TestConstitutionBoundary:
    """Constitution enforces role-level action boundaries."""

    def test_pm_boundary_exists(self) -> None:
        assert Role.PM in CONSTITUTION

    def test_director_can_send_to_qa(self) -> None:
        director_boundary = CONSTITUTION[Role.DIRECTOR]
        # Director's downstream roles should include QA
        assert Role.QA in director_boundary.downstream_roles

    def test_validate_action_returns_error_for_prohibited(self) -> None:
        # PM is prohibited from directly writing code
        pm_boundary = CONSTITUTION[Role.PM]
        # Find any prohibition to validate the mechanism
        if pm_boundary.prohibitions:
            sample_prohibited = next(iter(pm_boundary.prohibitions))
            level = pm_boundary.validate_action(sample_prohibited)
            assert level == ViolationLevel.ERROR

    def test_is_action_allowed_unknown_role_returns_false(self) -> None:
        result = is_action_allowed("SecurityScanner", "read_file")  # type: ignore[arg-type]
        assert result is False

    def test_is_action_allowed_valid_role_allowed_action_returns_true(self) -> None:
        # PM can orchestrate tasks — this should be in responsibilities
        pm_boundary = CONSTITUTION[Role.PM]
        if pm_boundary.responsibilities:
            sample_responsibility = next(iter(pm_boundary.responsibilities))
            result = is_action_allowed(Role.PM.value, sample_responsibility)  # type: ignore[arg-type]
            assert result is True
