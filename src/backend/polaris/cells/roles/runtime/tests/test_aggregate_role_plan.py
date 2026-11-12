from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.runtime.public.contracts import (
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
)
from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
    audit_aggregate_runtime_integrations,
    query_aggregate_role_plan,
)


@pytest.mark.asyncio
async def test_retired_legacy_role_scenarios_stay_closed(tmp_path: Path) -> None:
    """The legacy role scenarios have no live subject after the retirement.

    These scenarios drove the legacy role execution kernel, which is
    retired: a new engineering run has exactly one owner, the engineering run
    service. The scenarios are replaced by the retirement contract, so the
    legacy write entry cannot silently come back.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        session_id="retired-legacy-role-scenarios",
        workspace=str(tmp_path),
    )


@pytest.mark.asyncio
async def test_default_aggregate_role_plan_exposes_core_lobes(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Treat Polaris roles as one aggregate coding model.",
            domain="code",
        )
    )

    assert result.ok is True
    assert result.aggregate_model_id == "polaris.aggregate_llm.v1"
    assert result.current_role_ids == ("pm", "architect", "chief_engineer", "director", "qa")
    lobe_ids = {lobe.lobe_id for lobe in result.lobes}
    assert {
        "constraint_boundary_generator",
        "dialectic_self_heal_loop",
        "hippocampus_controller",
        "tool_commit_guard",
        "task_market_allocator",
    } <= lobe_ids
    assert "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory" in result.required_capability_refs
    assert "polaris.kernelone.context.context_os.StateFirstContextOS" in result.required_capability_refs
    assert len(result.cognitive_ledger) == len(result.lobes)
    assert len(result.runtime_integrations) == 16
    assert all(integration.entrypoint_checks for integration in result.runtime_integrations)
    assert all(integration.entrypoints_verified for integration in result.runtime_integrations)
    wired_p0 = {
        integration.tech_id
        for integration in result.runtime_integrations
        if integration.status == "wired" and integration.priority == "p0"
    }
    assert {
        "turn_transaction_kernel_ledger",
        "context_plane_isolation",
        "strategy_profile_overlay_fingerprint",
        "cognitive_runtime_receipt_handoff",
    } <= wired_p0
    assert result.cognitive_ledger[0].lobe_id == "constraint_boundary_generator"
    assert result.cognitive_ledger[0].handoff_to == ("dialectic_self_heal_loop",)
    assert result.compute_policy["default_priority"] == "local_self_heal_first_after_compiler_feedback"
    assert "compile_failure" in result.compute_policy["local_priority_conditions"]


@pytest.mark.asyncio
async def test_aggregate_plan_marks_virtual_lobes_without_claiming_role_profiles(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Add a red-team critic before local execution.",
        )
    )

    self_heal = next(lobe for lobe in result.lobes if lobe.lobe_id == "dialectic_self_heal_loop")
    assert self_heal.role_ids == ("chief_engineer", "qa")
    assert self_heal.virtual_role_ids == ("adversarial_critic",)
    assert "virtual_role_ids_are_not_current_role_profiles" in result.warnings
    assert "virtual lobes" in str(result.metadata["truthful_migration"])


@pytest.mark.asyncio
async def test_aggregate_plan_can_disable_virtual_lobes(tmp_path) -> None:
    result = await query_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Build a strict current-role-only plan.",
            include_virtual_lobes=False,
        )
    )

    assert all(lobe.virtual_role_ids == () for lobe in result.lobes)
    assert "virtual_role_ids_are_not_current_role_profiles" not in result.warnings


@pytest.mark.asyncio
async def test_aggregate_plan_marks_partial_lobes_for_constrained_role_set(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Only plan the local execution lobe.",
            role_ids=("director",),
        )
    )

    hippocampus = next(lobe for lobe in result.lobes if lobe.lobe_id == "hippocampus_controller")
    constraint = next(lobe for lobe in result.lobes if lobe.lobe_id == "constraint_boundary_generator")
    assert result.current_role_ids == ("director",)
    assert hippocampus.status == "active"
    assert constraint.status == "partial"
    assert constraint.missing_role_ids == ("architect", "qa")
    assert any(warning.startswith("partial_lobes:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_builds_takeover_directive_from_failure_signal(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a local compiler failure.",
            failure_signals=("compile_failure",),
        )
    )

    assert result.takeover_directive is not None
    assert result.takeover_directive.trigger == "compile_failure"
    assert result.takeover_directive.lobe_id == "dialectic_self_heal_loop"
    assert result.takeover_directive.compute_tier == "cloud_critic_local_retry"
    assert result.takeover_directive.action_contract == "self_healed_blueprint_v1"
    assert "compiler_output" in result.takeover_directive.evidence_keys
    assert result.metadata["failure_signals"] == ("compile_failure",)
    assert result.metadata["takeover_evidence_status"]["complete"] is False
    assert "compiler_output" in result.metadata["takeover_evidence_status"]["missing_keys"]
    assert any(warning.startswith("missing_takeover_evidence:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_records_structured_failure_evidence_for_takeover(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a local compiler failure.",
            failure_signals=("compile_failure",),
            failure_evidence={
                "compiler_output": "error TS2322: Type mismatch",
                "changed_files": ["src/app.ts"],
                "test_command": "npm test",
            },
        )
    )

    assert result.metadata["failure_evidence"]["compiler_output"] == "error TS2322: Type mismatch"
    assert result.metadata["takeover_evidence_status"]["complete"] is True
    assert result.metadata["takeover_evidence_status"]["missing_keys"] == ()
    assert not any(warning.startswith("missing_takeover_evidence:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_preserves_failure_evidence_list_from_context(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a platform lifecycle failure.",
            context={
                "failure_evidence": [
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "failure_class": "MISSING_EFFECT_RECEIPT",
                        "responsible_layer": "platform",
                        "evidence_refs": ["tool_lifecycle:turn-1"],
                    }
                ]
            },
        )
    )

    failure_evidence = result.metadata["failure_evidence"]
    assert failure_evidence["items"] == [
        {
            "schema_version": "polaris.failure_evidence.v1",
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "responsible_layer": "platform",
            "evidence_refs": ["tool_lifecycle:turn-1"],
        }
    ]
    assert failure_evidence["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert failure_evidence["evidence_refs"] == ("tool_lifecycle:turn-1",)


@pytest.mark.asyncio
async def test_aggregate_runtime_audit_distinguishes_wired_available_and_planned(tmp_path) -> None:
    result = await audit_aggregate_runtime_integrations(
        AuditAggregateRuntimeIntegrationsQueryV1(workspace=str(tmp_path))
    )

    assert result.ok is True
    assert result.wired_count == 16
    assert result.available_count == 0
    assert result.planned_bridge_count == 0
    assert result.verified_entrypoint_count >= 53
    assert result.missing_entrypoint_count == 0
    assert len(result.integrations) == 16
    assert result.warnings == ()
    assert {
        "turn_transaction_kernel_ledger",
        "context_plane_isolation",
        "strategy_profile_overlay_fingerprint",
        "cognitive_runtime_receipt_handoff",
    } <= set(result.priority_wired)
    assert all(integration.production_entrypoints for integration in result.integrations)
    assert all(integration.entrypoint_checks for integration in result.integrations)
    assert all(integration.entrypoints_verified for integration in result.integrations)
    assert any(
        check.check_type == "http_route" and check.ok
        for integration in result.integrations
        for check in integration.entrypoint_checks
    )


@pytest.mark.asyncio
async def test_aggregate_runtime_audit_fails_when_runtime_entrypoint_is_not_verifiable(tmp_path) -> None:
    missing_workspace = tmp_path / "missing-workspace"

    result = await audit_aggregate_runtime_integrations(
        AuditAggregateRuntimeIntegrationsQueryV1(workspace=str(missing_workspace))
    )

    strategy = next(item for item in result.integrations if item.tech_id == "strategy_profile_overlay_fingerprint")
    assert result.ok is False
    assert result.missing_entrypoint_count >= 1
    assert "runtime/strategy_runs" in strategy.missing_entrypoints
    assert strategy.entrypoints_verified is False
    assert any(warning.startswith("missing_entrypoints:") for warning in result.warnings)
