"""Unit tests for DirectorAdapter pure logic (no I/O, no LLM).

Covers:
- _select_execution_strategy
- _apply_intelligent_correction
- _build_director_message
- _build_materialized_metadata
- _resolve_execution_backend_request
- get_capabilities / role_id
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.director.runtime.public.repair_kernel_contracts import (
    build_substantive_node_test_script as _build_substantive_node_test_script,
    is_overstrict_node_test_script_contract as _is_overstrict_node_test_script_contract,
    remove_patch_residue_lines as _remove_patch_residue_lines,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.roles.adapters.internal.director import (
    execute_method as execute_method_module,
    quality_gate as quality_gate_module,
)
from polaris.cells.roles.adapters.internal.director.adapter import (
    DirectorAdapter,
    _build_director_blueprint_handoff_lines,
    _director_actual_interface_injection_enabled,
    _load_ce_blueprint_contract_payload,
    _merge_ce_blueprint_contract_payload,
    _normalize_director_role_response,
    _prepare_role_dialogue_context,
)
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _CURRENT_FILE_RETRY_CHAR_CAP,
    _build_empty_write_content_retry_message,
    _build_existing_workspace_task_evidence,
    _build_no_write_materialization_retry_message,
    _can_accept_existing_workspace_scope,
    _deterministic_repair_profile_summary_from_tool_results,
    _deterministic_repair_source_tools_from_tool_results,
    _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled,
    _emit_director_adapter_cognitive_receipt,
    _empty_write_content_retry_needed,
    _execution_attempt_authority_from_context,
    _extract_task_target_path_candidates,
    _finalize_claimed_execution,
    _handle_claim_required,
    _materialization_task_boundary_triage_summary,
    _no_effect_write_retry_needed,
    _no_write_materialization_retry_needed,
    _no_write_materialization_retry_tool_definitions,
    _pin_file_schema_to_declared_targets,
    _quality_error_preferred_paths,
    _resolve_claim_external_task_id,
    _run_empty_write_content_materialization_retry,
    _select_no_write_materialization_retry_tool,
    _suspend_claimed_execution_for_cancellation,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
    _task_runtime_heartbeat_exception_signal,
    _task_runtime_heartbeat_failed_signal,
    _unique_similar_export,
    _with_decision_signals,
    _with_task_runtime_finalize_evidence,
    execute_director_task,
)
from polaris.cells.roles.adapters.internal.director.execute_method_repair_bridge import (
    run_patch_residue_cleanup,
    run_python_runtime_smoke,
    run_python_static_smoke,
)
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_failure_evidence_context,
    _build_materialization_quality_workspace_evidence_context,
    _extract_task_interface_contract,
    _go_runtime_smoke_repair_target_files,
    _materialization_interface_discrepancy_evidence,
    _materialization_interface_discrepancy_retry_authorized,
    _materialization_plan_probe_requires_task_boundary_triage,
    _quality_repair_edit_file_tool_definition,
    _quality_repair_execute_command_tool_definition,
    _quality_repair_write_file_tool_definition,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.adapters.public import service as roles_adapters_public_service
from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
)
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority
from polaris.kernelone.events.final_request_evidence import (
    looks_like_ce_blueprint_payload,
    looks_like_failed_gate_evidence_context_payload,
    looks_like_pm_contract_payload,
    looks_like_workspace_quality_evidence_payload,
)
from polaris.kernelone.quality import scan_workspace_artifact_quality

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_go_materialization_quality_schedule(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str] | tuple[str, ...] = (),
    advisor_notes: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    """Run Go materialization repair through the typed roles adapter boundary."""

    workspace = Path(adapter.workspace)
    result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task={},
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            advisor_notes=tuple(advisor_notes),
            execution_attempt=_test_execution_attempt(workspace, task_id),
        )
    )
    return _project_deferred_repair_results_for_test(
        workspace,
        [dict(item) for item in result.tool_results],
    )


def _make_adapter(tmp_path: Any, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    workspace = Path(tmp_path)
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="director-adapter-pure-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    if task_runtime is None:
        adapter = DirectorAdapter(workspace=str(workspace))
    else:
        adapter = DirectorAdapter(workspace=str(workspace), task_runtime=task_runtime)
    return adapter


from ._execution_attempt_helpers import (
    _project_deferred_repair_results_for_test,
    _test_execution_attempt,
    _test_execution_attempt_context,
)


def _install_test_deferred_projection(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    workspace: Path,
) -> None:
    """Wrap one bridge so old planner tests consume deferred effects safely."""

    original = module.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _install_all_test_deferred_projections(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    """Project every Director repair bridge only for isolated legacy tests."""

    from polaris.cells.roles.adapters.internal.director import (
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        runtime_repair_tool_adapter,
    )

    original = runtime_repair_tool_adapter.run_runtime_repair_with_director_tools

    def _run(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        task_id = str(kwargs.get("task_id") or "director-repair-test")
        if type(kwargs.get("execution_attempt")) is not TaskRuntimeExecutionAttemptIdentityV1:
            kwargs["execution_attempt"] = _test_execution_attempt(workspace, task_id)
        return _project_deferred_repair_results_for_test(
            workspace,
            original(*args, **kwargs),
        )

    for module in (
        runtime_repair_tool_adapter,
        execute_method_repair_bridge,
        materialization_quality_callback_ports,
        post_execution_repair_bridge,
        quality_gate_module,
    ):
        monkeypatch.setattr(module, "run_runtime_repair_with_director_tools", _run)


def _run_test_materialization_quality_repair_schedule(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run production planning and project its deferred effects in test scope."""

    workspace = Path(adapter.workspace)
    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=_test_execution_attempt(workspace, task_id),
    )
    return _project_deferred_repair_results_for_test(workspace, results), summary


def test_materialization_quality_scheduler_bridge_projects_callback_receipts_without_inflating_kernel() -> None:
    from polaris.cells.director.runtime.public import DirectorRepairMaterializationQualityStepV1
    from polaris.cells.roles.adapters.internal.director import (
        materialization_quality_evidence_ports,
    )

    tool_results = [
        {
            "tool": "edit_file",
            "tool_name": "edit_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_typescript_materialization_repair",
                "file": "src/main.ts",
                "operation": "edit_file",
                "bridge_step_id": "materialization.typescript_compiler",
                "phase": "compiler",
                "priority": 20,
                "round_number": 1,
                "max_rounds": 3,
                "repair_kernel": {
                    "receipts": [
                        {
                            "receipt_id": "native-typescript-receipt",
                            "plan_id": "native-typescript-plan",
                            "source_tool": "deterministic_typescript_materialization_repair",
                            "status": "applied",
                            "authoritative": True,
                            "files_changed": ["src/main.ts"],
                            "before_hashes": {"src/main.ts": "before-ts"},
                            "after_hashes": {"src/main.ts": "after-ts"},
                            "round_number": 1,
                            "revalidation_evidence": {
                                "command": ["rtk", "npm", "test"],
                                "exit_code": 0,
                                "errors_after": 0,
                            },
                        }
                    ],
                },
                "callback_receipt_projection": {
                    "receipt_id": "callback-explicit-receipt",
                    "receipt_authority": "non_authoritative_callback_receipt_projection",
                    "authoritative": True,
                    "typed_receipt_path_available": True,
                    "revalidation_evidence": {
                        "command": ["rtk", "npm", "test"],
                        "exit_code": 0,
                    },
                },
            },
        },
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_node_manifest_materialization_repair",
                "file": "package.json",
                "operation": "write_file",
                "bridge_step_id": "materialization.node_manifest",
                "phase": "manifest",
                "priority": 30,
                "round_number": 1,
                "scheduler_max_rounds": 3,
                "receipt_projections": [
                    {
                        "receipt_id": "callback-runtime-receipt",
                        "receipt_authority": "non_authoritative_callback_projection",
                        "authoritative": False,
                        "projection_only": True,
                        "typed_receipt_path_available": True,
                        "round_number": 1,
                        "max_rounds": 3,
                        "revalidation_evidence_present": True,
                    }
                ],
                "revalidation": {
                    "command": ["rtk", "npm", "test"],
                    "exit_code": 0,
                    "errors_after": 0,
                },
            },
        },
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_target_runtime_materialization_repair",
                "file": "scripts/smoke.mjs",
                "operation": "write_file",
                "bridge_step_id": "materialization.target_runtime",
                "phase": "runtime_smoke",
                "priority": 50,
                "round_number": 2,
                "max_rounds": 3,
                "adapter_projection_bridge": True,
                "adapter_callback_bridge": False,
                "produces_tool_results_only": True,
                "typed_receipt_path_available": False,
                "revalidation": {
                    "command": ["rtk", "node", "scripts/smoke.mjs"],
                    "exit_code": 0,
                    "errors_after": 0,
                },
            },
        },
    ]
    ordered_steps = (
        DirectorRepairMaterializationQualityStepV1(
            step_id="materialization.typescript_compiler",
            language="typescript",
            phase="compiler",
            priority=20,
            source_tool="deterministic_typescript_materialization_repair",
        ),
        DirectorRepairMaterializationQualityStepV1(
            step_id="materialization.node_manifest",
            language="javascript",
            phase="manifest",
            priority=30,
            source_tool="deterministic_node_manifest_materialization_repair",
        ),
        DirectorRepairMaterializationQualityStepV1(
            step_id="materialization.target_runtime",
            language="multi",
            phase="runtime_smoke",
            priority=50,
            source_tool="deterministic_target_runtime_materialization_repair",
        ),
    )

    summary = materialization_quality_evidence_ports._annotate_materialization_quality_summary(
        step_summaries={},
        tool_results=tool_results,
        artifact_quality_errors=["src/main.ts(1,1): error TS2304: Cannot find name 'Demo'."],
        ordered_steps=ordered_steps,
        coverage_preaudit={
            "total_diagnostics": 1,
            "uncovered_diagnostic_count": 0,
            "rule_discovery_required": False,
        },
    )

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["schema_version"] == "director.materialization_quality_scheduler_bridge.v1"
    assert scheduler_bridge["mode"] == "adapter_projection_bridge"
    assert scheduler_bridge["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert (
        scheduler_bridge["schedule_source"]
        == "director.runtime.public.query_director_repair_materialization_quality_schedule"
    )
    assert scheduler_bridge["runner_binding_owner"] == "roles.adapters"
    assert [step["step_id"] for step in scheduler_bridge["step_order"]] == [
        "materialization.typescript_compiler",
        "materialization.node_manifest",
        "materialization.target_runtime",
    ]
    assert scheduler_bridge["active_step_ids"] == [
        "materialization.node_manifest",
        "materialization.target_runtime",
        "materialization.typescript_compiler",
    ]
    assert scheduler_bridge["observed_max_round"] == 2
    assert scheduler_bridge["configured_max_rounds"] == 3
    assert scheduler_bridge["tool_result_count"] == 3
    assert scheduler_bridge["source_tools"] == [
        "deterministic_node_manifest_materialization_repair",
        "deterministic_target_runtime_materialization_repair",
        "deterministic_typescript_materialization_repair",
    ]
    assert scheduler_bridge["phases"] == {"compiler": 1, "manifest": 1, "runtime_smoke": 1}
    assert scheduler_bridge["priorities"] == {"20": 1, "30": 1, "50": 1}
    assert scheduler_bridge["rounds"] == {"1": 2, "2": 1}
    assert scheduler_bridge["receipt_count"] == summary["repair_kernel"]["receipt_count"] == 3
    assert scheduler_bridge["receipts_with_revalidation"] == 3
    assert scheduler_bridge["callback_receipt_projection_count"] == 3
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipt_authority_values"] == [
        "non_authoritative_callback_projection",
        "non_authoritative_callback_receipt_projection",
        "non_authoritative_callback_tool_result_projection",
    ]
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 3
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 2
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert (
        scheduler_bridge["migration_blocker"]
        == "adapter schedule runners still return tool_results instead of RepairReceipt"
    )
    assert scheduler_bridge["repair_kernel_migration_debt"] == summary["repair_kernel_migration_debt"]
    assert scheduler_bridge["adapter_projection_debt"] == summary["adapter_projection_debt"]

    debt_by_step = {item["step_id"]: item for item in summary["adapter_projection_debt"]}
    assert debt_by_step["materialization.typescript_compiler"]["verifier_evidence_present"] is True
    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts}.isdisjoint(
        {"callback-explicit-receipt", "callback-runtime-receipt"}
    )
    assert all("callback_receipt_projection" not in receipt for receipt in repair_kernel_receipts)
    assert all("receipt_projections" not in receipt for receipt in repair_kernel_receipts)


def test_materialization_quality_scheduler_bridge_prefers_public_result_callback_receipt_projections(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.cells.director.runtime.public as director_runtime_public
    from polaris.cells.director.runtime.public.contracts import (
        DirectorRepairMaterializationQualityFacadeResultV1,
        QueryDirectorRepairMaterializationQualityScheduleV1,
    )
    from polaris.cells.director.runtime.public.service import (
        query_director_repair_materialization_quality_schedule,
    )
    from polaris.cells.roles.adapters.internal.director import materialization_quality_callback_ports

    runtime_schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    ordered_steps = tuple(runtime_schedule.items)
    runtime_step_ids = tuple(step.step_id for step in ordered_steps)
    node_manifest_step = next(step for step in ordered_steps if step.step_id == "materialization.node_manifest")
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": node_manifest_step.source_tool,
            "file": "package.json",
            "operation": "write_file",
            "bridge_step_id": node_manifest_step.step_id,
            "round_number": 1,
            "receipt_projections": [
                {
                    "receipt_id": "payload-materialization-projection",
                    "receipt_authority": "payload_should_not_win",
                    "authoritative": True,
                    "typed_receipt_path_available": False,
                },
                {
                    "receipt_id": "payload-conflicting-materialization-projection",
                    "receipt_authority": "conflicting_payload_should_not_win",
                    "authoritative": True,
                    "typed_receipt_path_available": True,
                },
            ],
        },
    }
    public_projection = {
        "projection_id": "materialization-public-projection",
        "receipt_id": "materialization-public-receipt",
        "receipt_authority": "non_authoritative_callback_projection",
        "schedule_kind": "materialization_quality",
        "step_id": node_manifest_step.step_id,
        "source_tool": node_manifest_step.source_tool,
        "round_number": 2,
        "max_rounds": 3,
        "projection_only": True,
        "authoritative": True,
        "typed_receipt_path_available": True,
        "revalidation_evidence_present": True,
    }

    def fake_facade_result(
        *,
        artifact_quality_errors: Any,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        plan_probe_preaudit: Any = None,
        convergence_verifier_present: bool = False,
        max_rounds: int = 1,
    ) -> DirectorRepairMaterializationQualityFacadeResultV1:
        del artifact_quality_errors, runner, plan_probe_preaudit, convergence_verifier_present
        assert runner_step_ids == runtime_step_ids
        return DirectorRepairMaterializationQualityFacadeResultV1(
            schema_version="director.materialization_quality_repair_facade_result.v1",
            source="director.runtime.repair_kernel.materialization_quality_facade",
            ordered_steps=ordered_steps,
            tool_results=(tool_result,),
            receipt_projections=(public_projection,),
            coverage_preaudit={
                "total_diagnostics": 1,
                "uncovered_diagnostic_count": 0,
                "rule_discovery_required": False,
            },
            plan_probe_preaudit={},
            schedule_reconciliation={
                "exact_match": True,
                "runtime_step_ids": runtime_step_ids,
                "runner_step_ids": runtime_step_ids,
                "schedule_result_step_ids": runtime_step_ids,
            },
            schedule_summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 2,
                "receipt_projection_count": 1,
            },
            summary={
                "schema_version": "director.materialization_quality_repair_facade_summary.v1",
                "stage": "deterministic_quality_repair",
                "attempted": True,
                "success": False,
                "success_reason": "repair_actions_require_quality_gate_rerun",
                "tool_results": 1,
                "write_tool_evidence": True,
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 2,
                "receipt_projection_count": 1,
                "runtime_facade_owner": "director.runtime",
                "runner_binding_owner": "roles.adapters",
            },
            max_rounds=max_rounds,
            rounds_run=2,
            convergence_status="cycle_broken",
            stopped_reason="test_public_projection_precedence",
        )

    monkeypatch.setattr(
        director_runtime_public,
        "run_director_materialization_quality_repair_facade",
        fake_facade_result,
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_project_coverage_preaudit",
        lambda _errors: {
            "total_diagnostics": 1,
            "uncovered_diagnostic_count": 0,
            "rule_discovery_required": False,
        },
    )

    _, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["package.json"]},
        task_id="task-public-materialization-projection",
        artifact_quality_errors=["package.json manifest repair required"],
    )

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipt_authority_values"] == ["non_authoritative_callback_projection"]
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 1
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 1
    assert scheduler_bridge["observed_max_round"] == 2
    assert scheduler_bridge["configured_max_rounds"] == 3
    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts}.isdisjoint(
        {
            "materialization-public-receipt",
            "payload-materialization-projection",
            "payload-conflicting-materialization-projection",
        }
    )


@pytest.mark.asyncio
async def test_phase_pre_materialization_quality_records_post_execution_kernel_summary(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")

    def fake_runtime_repair_with_director_tools(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        if source_tool != "deterministic_rust_dependency_repair":
            return []
        cargo = Path(str(adapter.workspace)) / "Cargo.toml"
        assert workspace_path == Path(str(adapter.workspace)).resolve()
        cargo_text = cargo.read_text(encoding="utf-8")
        if 'serde = "1"' in cargo_text:
            return []
        cargo.write_text(
            cargo_text + '\n[dependencies]\nserde = "1"\n',
            encoding="utf-8",
        )
        return [
            {
                "ok": True,
                "success": True,
                "tool_name": "deterministic_rust_dependency_repair",
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "path": "Cargo.toml",
                    "action": "add_dependency",
                    "phase": "dependency_resolution",
                    "priority": 0,
                    "round_number": 1,
                    "revalidation": {
                        "command": ["cargo", "check", "--quiet"],
                        "exit_code": 0,
                        "errors_before": 2,
                        "errors_after": 0,
                        "net_error_reduction": 2,
                    },
                },
            }
        ]

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_runtime_repair_with_director_tools",
        fake_runtime_repair_with_director_tools,
    )
    monkeypatch.setattr(
        execute_method_module,
        "_collect_workspace_code_diff",
        lambda *args, **kwargs: ({}, [], ["Cargo.toml"], ["Cargo.toml"]),
    )

    quality_repair_attempts: list[dict[str, Any]] = []
    resident_agi_overlay = {
        "schema_version": "resident.agi_repair_advisory_overlay.v1",
        "source": "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
        "status": "ready",
        "eligible_for_director_injection": True,
        "advisory_only": True,
        "authoritative": False,
        "agi_execution_authority": False,
        "director_runtime_contract": "director.repair_advisory_policy.v1",
        "advisor_notes": [
            {
                "advisor_source": "resident_agi",
                "message": "Suggest future deterministic Rust repair coverage.",
                "confidence": 0.7,
                "authoritative": False,
                "suggested_rules": [
                    {
                        "name": "rust_receiver_self",
                        "pattern": "found `&)` near method receiver",
                        "fix_template": "replace receiver marker",
                    }
                ],
                "metadata": {"source_role": "resident_agi"},
            }
        ],
    }
    (
        state,
        _evidence,
        _can_accept,
        _write_evidence,
        summary,
    ) = await execute_method_module._phase_pre_materialization_quality(
        SimpleNamespace(workspace=str(tmp_path)),
        baseline_files={},
        can_accept_existing_scope=True,
        context={"metadata": {"resident_agi_repair_advisory_overlay": resident_agi_overlay}},
        existing_contract_evidence={"ok": True},
        primary_llm_summary=None,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=None,
        requires_fresh_materialization=False,
        target_task_id="task-1",
        task={"id": "task-1"},
        workspace_name=tmp_path.name,
        write_tool_evidence=True,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=["src/lib.rs"],
            all_affected_files=["src/lib.rs"],
            tool_results=[],
        ),
    )

    assert summary is not None
    kernel_summary = summary["post_execution_repair_kernel"]
    assert kernel_summary["authoritative"] is True
    assert kernel_summary["agi_advisory"]["active"] is True
    assert kernel_summary["agi_advisory"]["advisor_note_count"] == 1
    assert kernel_summary["agi_advisory"]["suggested_rule_count"] == 1
    assert kernel_summary["agi_advisory"]["advisor_notes"][0]["advisor_source"] == "resident_agi"
    assert kernel_summary["receipt_count"] == 1
    receipt = kernel_summary["receipts"][0]
    assert receipt["source_tool"] == "deterministic_rust_dependency_repair"
    assert receipt["round_number"] == 1
    assert receipt["errors_before"] == 2
    assert receipt["errors_after"] == 0
    assert receipt["revalidation_evidence"]["metadata"]["max_rounds"] == 1
    shadow = kernel_summary["dark_launch_comparison"]
    assert shadow["matched"] is True
    assert shadow["baseline_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["kernel_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["metadata"]["writes_performed"] is False
    assert shadow["metadata"]["comparison_mode"] == "receipt_projection_self_check"
    assert shadow["comparison_mode"] == "receipt_projection_self_check"
    assert shadow["cutover_ready"] is False
    assert shadow["cutover_blockers"] == ["independent_shadow_required"]
    assert shadow["independent_shadow_required"] is True
    assert shadow["independent_shadow_satisfied"] is False
    assert quality_repair_attempts[0]["schema_version"] == "director.post_execution_repair_kernel.v1"
    scheduler_bridge = quality_repair_attempts[0]["scheduler_bridge"]
    assert scheduler_bridge["schema_version"] == "director.post_execution_scheduler_bridge.v1"
    assert scheduler_bridge["mode"] == "adapter_projection_bridge"
    assert scheduler_bridge["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert (
        scheduler_bridge["schedule_source"] == "director.runtime.public.query_director_repair_post_execution_schedule"
    )
    assert scheduler_bridge["runner_binding_owner"] == "roles.adapters"
    assert [step["step_id"] for step in scheduler_bridge["step_order"]] == [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    assert scheduler_bridge["active_step_ids"] == ["rust.dependency_resolution"]
    assert scheduler_bridge["observed_max_round"] == 1
    assert scheduler_bridge["configured_max_rounds"] == 1
    assert scheduler_bridge["source_tools"] == ["deterministic_rust_dependency_repair"]
    assert scheduler_bridge["phases"] == {"dependency_resolution": 1}
    assert scheduler_bridge["priorities"] == {"0": 1}
    assert scheduler_bridge["rounds"] == {"1": 1}
    assert scheduler_bridge["receipts_with_revalidation"] == 1
    assert scheduler_bridge["resident_agi_advisory_active"] is True
    assert scheduler_bridge["resident_agi_advisory_note_count"] == 1
    assert scheduler_bridge["resident_agi_suggested_rule_count"] == 1
    assert quality_repair_attempts[0]["resident_agi_repair_advisory_overlay"]["active"] is True
    assert state.modified_files == ["Cargo.toml"]


@pytest.mark.asyncio
async def test_phase_pre_materialization_quality_passes_artifact_quality_convergence_verifier(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_factory(
        workspace: str | Path,
        *,
        task_id: str,
        relative_paths: Any = None,
        log_root: str | Path | None = None,
    ) -> Any:
        captured["factory"] = {
            "workspace": Path(workspace),
            "task_id": task_id,
            "relative_paths": tuple(relative_paths or ()),
            "log_root": log_root,
        }
        return sentinel_verifier

    def fake_post_execution_repairs(
        adapter: Any,
        *,
        task_id: str,
        resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
        convergence_verifier: Any = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, resident_agi_repair_advisory_overlay, execution_attempt
        captured["bridge"] = {
            "task_id": task_id,
            "convergence_verifier": convergence_verifier,
        }
        return [], None

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_factory)
    monkeypatch.setattr(execute_method_module, "run_post_execution_language_repairs", fake_post_execution_repairs)

    absolute_inside = tmp_path / "lib" / "model.ts"
    (
        state,
        _evidence,
        _can_accept,
        _write_evidence,
        _summary,
    ) = await execute_method_module._phase_pre_materialization_quality(
        SimpleNamespace(workspace=str(tmp_path)),
        baseline_files={},
        can_accept_existing_scope=True,
        context={},
        existing_contract_evidence={"ok": True},
        primary_llm_summary=None,
        quality_repair_attempts=[],
        quality_repair_summary=None,
        requires_fresh_materialization=False,
        target_task_id="task-42",
        task={"id": "task-42"},
        workspace_name=tmp_path.name,
        write_tool_evidence=True,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=["src/app.ts"],
            all_affected_files=[
                "src/app.ts",
                str(absolute_inside),
                "../outside.ts",
                "/etc/passwd",
                "src/app.ts",
            ],
            tool_results=[],
        ),
    )

    assert state.all_affected_files == [
        "src/app.ts",
        str(absolute_inside),
        "../outside.ts",
        "/etc/passwd",
        "src/app.ts",
    ]
    assert captured["factory"]["workspace"] == tmp_path.resolve()
    assert captured["factory"]["task_id"] == "task-42"
    assert captured["factory"]["relative_paths"] == ("src/app.ts", "lib/model.ts")
    assert captured["factory"]["log_root"] is None
    assert captured["bridge"]["task_id"] == "task-42"
    assert captured["bridge"]["convergence_verifier"] is sentinel_verifier


@pytest.mark.asyncio
async def test_phase_pre_materialization_quality_passes_verifier_to_materialization_bridge(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {"factory_calls": []}

    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_factory(
        workspace: str | Path,
        *,
        task_id: str,
        relative_paths: Any = None,
        log_root: str | Path | None = None,
    ) -> Any:
        captured["factory_calls"].append(
            {
                "workspace": Path(workspace),
                "task_id": task_id,
                "relative_paths": tuple(relative_paths or ()),
                "log_root": log_root,
            }
        )
        return sentinel_verifier

    def fake_collect_materialization_quality_errors(*args: Any, **kwargs: Any) -> list[str]:
        del args
        captured["scan_paths"] = tuple(kwargs.get("all_affected_files") or ())
        return ['Go syntax check failed: cmd/app/main.go:3:1: expected declaration, found "fmt"']

    def fake_materialization_repairs(
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
        convergence_verifier: Any = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del adapter, execution_attempt
        captured["materialization_bridge"] = {
            "task": task,
            "task_id": task_id,
            "artifact_quality_errors": tuple(artifact_quality_errors),
            "convergence_verifier": convergence_verifier,
        }
        return [], {"stage": "deterministic_quality_repair", "success": False}

    def fake_post_execution_repairs(
        adapter: Any,
        *,
        task_id: str,
        resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
        convergence_verifier: Any = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, task_id, resident_agi_repair_advisory_overlay, convergence_verifier, execution_attempt
        return [], None

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_factory)
    monkeypatch.setattr(
        execute_method_module,
        "_collect_materialization_quality_errors",
        fake_collect_materialization_quality_errors,
    )
    monkeypatch.setattr(
        execute_method_module, "_run_materialization_quality_public_boundary", fake_materialization_repairs
    )
    monkeypatch.setattr(execute_method_module, "run_post_execution_language_repairs", fake_post_execution_repairs)

    await execute_method_module._phase_pre_materialization_quality(
        SimpleNamespace(workspace=str(tmp_path)),
        baseline_files={},
        can_accept_existing_scope=False,
        context={},
        existing_contract_evidence={"ok": False},
        primary_llm_summary=None,
        quality_repair_attempts=[],
        quality_repair_summary=None,
        requires_fresh_materialization=True,
        target_task_id="task-go-1",
        task={"id": "task-go-1", "target_files": ["cmd/app/main.go"]},
        workspace_name=tmp_path.name,
        write_tool_evidence=True,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {"ok": True, "file": "cmd/app/main.go"},
                }
            ],
        ),
    )

    assert captured["scan_paths"] == ("cmd/app/main.go",)
    assert captured["factory_calls"][0]["workspace"] == tmp_path.resolve()
    assert captured["factory_calls"][0]["task_id"] == "task-go-1"
    assert captured["factory_calls"][0]["relative_paths"] == ("cmd/app/main.go",)
    assert captured["factory_calls"][0]["log_root"] is None
    assert captured["materialization_bridge"]["task_id"] == "task-go-1"
    assert captured["materialization_bridge"]["artifact_quality_errors"] == (
        'Go syntax check failed: cmd/app/main.go:3:1: expected declaration, found "fmt"',
    )
    assert captured["materialization_bridge"]["convergence_verifier"] is sentinel_verifier


@pytest.mark.asyncio
async def test_phase_pre_materialization_quality_omits_verifier_when_factory_fails(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def failing_factory(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("factory unavailable")

    def fake_post_execution_repairs(
        adapter: Any,
        *,
        task_id: str,
        resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
        convergence_verifier: Any = None,
        execution_attempt: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, task_id, resident_agi_repair_advisory_overlay, execution_attempt
        captured["convergence_verifier"] = convergence_verifier
        return [], None

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", failing_factory)
    monkeypatch.setattr(execute_method_module, "run_post_execution_language_repairs", fake_post_execution_repairs)

    await execute_method_module._phase_pre_materialization_quality(
        SimpleNamespace(workspace=str(tmp_path)),
        baseline_files={},
        can_accept_existing_scope=True,
        context={},
        existing_contract_evidence={"ok": True},
        primary_llm_summary=None,
        quality_repair_attempts=[],
        quality_repair_summary=None,
        requires_fresh_materialization=False,
        target_task_id="task-43",
        task={"id": "task-43"},
        workspace_name=tmp_path.name,
        write_tool_evidence=True,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=["src/app.ts"],
            all_affected_files=["src/app.ts"],
            tool_results=[],
        ),
    )

    assert captured["convergence_verifier"] is None


def test_post_execution_agi_advisory_overlay_validates_nested_suggested_rules() -> None:
    from polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge import (
        _normalize_resident_agi_repair_advisory_overlay,
    )

    overlay = _normalize_resident_agi_repair_advisory_overlay(
        {
            "status": "ready",
            "eligible_for_director_injection": True,
            "advisory_only": True,
            "authoritative": False,
            "agi_execution_authority": False,
            "advisor_notes": [
                {
                    "advisor_source": "resident_agi",
                    "message": "Suggest a future deterministic rule.",
                    "confidence": 0.8,
                    "suggested_rules": [
                        {
                            "name": "rust_receiver_self",
                            "pattern": "found `&)` near method receiver",
                            "fix_template": "replace receiver marker",
                            "evidence": ["rustc E0424"],
                        }
                    ],
                    "metadata": {"source_role": "resident_agi"},
                },
                {
                    "advisor_source": "resident_agi",
                    "message": "This must be rejected.",
                    "confidence": 0.9,
                    "suggested_rules": [
                        {
                            "pattern": "bad",
                            "fix_template": "bad",
                            "patch": "*** Begin Patch",
                            "success_verdict": "pass",
                        }
                    ],
                },
            ],
        }
    )

    assert overlay["active"] is True
    assert overlay["advisor_note_count"] == 1
    assert overlay["suggested_rule_count"] == 1
    assert overlay["validation_error_count"] == 1
    assert "forbidden authoritative fields" in overlay["validation_errors"][0]
    assert overlay["advisor_notes"][0]["authoritative"] is False
    assert overlay["advisor_notes"][0]["suggested_rules"][0]["name"] == "rust_receiver_self"
    assert "patch" not in overlay["advisor_notes"][0]["suggested_rules"][0]
    assert "success_verdict" not in overlay["advisor_notes"][0]["suggested_rules"][0]


def test_empty_write_content_retry_needed_only_for_blank_write() -> None:
    assert (
        _empty_write_content_retry_needed(
            [
                {
                    "tool_name": "write_file",
                    "arguments": {"file": "src/app.py", "content": ""},
                    "status": "error",
                }
            ]
        )
        is True
    )
    assert (
        _empty_write_content_retry_needed(
            [
                {
                    "tool_name": "write_file",
                    "arguments": {"file": "src/app.py", "content": "print('ok')\n"},
                    "status": "success",
                }
            ]
        )
        is False
    )
    assert _empty_write_content_retry_needed([{"tool_name": "read_file", "status": "success"}]) is False


def test_empty_write_retry_uses_concrete_scope_path_when_target_files_missing() -> None:
    task = {
        "subject": "实现交互式 CLI 入口与 REPL 循环",
        "scope_paths": ["main.py"],
    }

    assert _extract_task_target_path_candidates(task) == ["main.py"]
    message = _build_empty_write_content_retry_message(
        task,
        original_message="[mode:materialize]\n范围: main.py",
        tool_results=[{"tool_name": "write_file", "arguments": {"file": "main.py", "content": ""}}],
    )

    assert "Allowed target files: main.py." in message


def test_no_write_materialization_retry_needed_only_after_successful_no_write(tmp_path: Any) -> None:
    task = {
        "subject": "Create app module",
        "target_files": ["src/app.py"],
        "scope_paths": ["src/app.py"],
    }

    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": True},
            task=task,
            tool_results=[{"tool_name": "repo_tree", "success": True}],
            workspace=str(tmp_path),
        )
        is True
    )
    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": False},
            task=task,
            tool_results=[{"tool_name": "repo_tree", "success": True}],
            workspace=str(tmp_path),
        )
        is False
    )
    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": True},
            task=task,
            tool_results=[
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/app.py", "content": "print('ok')\n"},
                    "result": {"path": "src/app.py", "ok": True},
                }
            ],
            workspace=str(tmp_path),
        )
        is False
    )


def test_no_write_retry_needed_after_mutation_bypass_on_existing_targets(tmp_path: Any) -> None:
    """Live L2-11: TASK-1-entrypoints already had src/index.js, quality scan
    reported missing ESM named exports, and the first MATERIALIZE_CHANGES
    turn only called read_file. Kernel sealed mutation_bypass_blocked /
    no_write_tool_available (success=False). The adapter then skipped the
    forced write retry because it required success=True and missing files.
    Existing declared targets with a no-write miss must still retry.
    """

    target = tmp_path / "src" / "index.js"
    target.parent.mkdir(parents=True)
    target.write_text("export function existing() { return 1; }\n", encoding="utf-8")
    task = {
        "subject": "runtime entrypoints and exports",
        "target_files": ["src/index.js"],
        "scope_paths": ["src/index.js"],
    }
    read_only = [{"tool_name": "read_file", "success": True, "status": "success"}]

    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={
                "success": False,
                "error": "no_write_tool_available",
                "metadata": {
                    "blocked_reason": "no_write_tool_available",
                    "workflow_reason": "mutation_bypass_blocked",
                },
            },
            task=task,
            tool_results=read_only,
            workspace=str(tmp_path),
        )
        is True
    )
    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": False, "error": "provider_stream_timeout"},
            task=task,
            tool_results=read_only,
            workspace=str(tmp_path),
        )
        is False
    )


def test_no_write_materialization_retry_message_pins_declared_targets() -> None:
    task = {
        "subject": "Create TypeScript modules",
        "target_files": ["package.json", "src/index.ts"],
        "scope_paths": ["src"],
    }
    message = _build_no_write_materialization_retry_message(
        task,
        original_message="[mode:materialize]\n目标文件: package.json, src/index.ts",
        tool_results=[{"tool_name": "repo_tree", "success": True}],
    )

    assert "previous Director turn completed without any write/edit receipt" in message
    assert "Allowed target files: package.json, src/index.ts." in message
    assert "Do not call read, search, tree, or shell tools" in message
    assert "write_file or edit_file" in message


def test_no_write_retry_message_includes_named_export_quality_island() -> None:
    message = _build_no_write_materialization_retry_message(
        {
            "subject": "runtime entrypoints",
            "target_files": ["src/index.js"],
        },
        original_message="[mode:materialize]\n范围: src/index.js",
        tool_results=[{"tool_name": "read_file", "success": True}],
        quality_errors=[
            "src/index.js: SyntaxError: The requested module './engine/rules.js' "
            "does not provide an export named 'computeVerdict'"
        ],
    )

    assert "does not provide an export named 'computeVerdict'" in message
    assert "Quality diagnostics" in message


def test_no_write_retry_existing_targets_force_edit_file(tmp_path: Any) -> None:
    """Live L2-11: existing src/index.js + write_file rewrite invented
    src/domains/*.js and decideMatch. Existing declared targets must force
    edit_file, not whole-file write_file.
    """

    target = tmp_path / "src" / "index.js"
    target.parent.mkdir(parents=True)
    target.write_text("import { evaluateCandidate } from './engine/rules.js';\n", encoding="utf-8")
    task = {"subject": "entrypoints", "target_files": ["src/index.js"]}

    tool_name, exact = _select_no_write_materialization_retry_tool(task, workspace=str(tmp_path))
    assert tool_name == "edit_file"
    assert exact is True

    missing_task = {"subject": "create", "target_files": ["src/missing.js"]}
    create_tool, create_exact = _select_no_write_materialization_retry_tool(
        missing_task,
        workspace=str(tmp_path),
    )
    assert create_tool == "write_file"
    assert create_exact is True


def test_no_write_retry_needed_when_successful_read_leaves_quality_hole(tmp_path: Any) -> None:
    """Live L2-11 epoch 9 TASK-2: first call succeeded with read_file +
    execute_command only. Targets already existed, so the retry gate
    treated the turn as done and settled director_no_materialized_changes
    without the forced edit_file that carries sibling exports.
    """

    src = tmp_path / "src"
    src.mkdir()
    (src / "clue.js").write_text("export const CLUE_KIND = Object.freeze({});\n", encoding="utf-8")
    (src / "index.js").write_text(
        "import { CLUE_KINDS } from './clue.js';\nexport { CLUE_KINDS };\n",
        encoding="utf-8",
    )
    task = {"subject": "verify", "target_files": ["src/index.js"]}
    read_only = [
        {"tool_name": "read_file", "success": True, "status": "success"},
        {"tool_name": "execute_command", "success": True, "status": "success"},
    ]
    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": True},
            task=task,
            tool_results=read_only,
            workspace=str(tmp_path),
        )
        is True
    )
    (src / "index.js").write_text(
        "import { CLUE_KIND } from './clue.js';\nexport { CLUE_KIND };\n",
        encoding="utf-8",
    )
    assert (
        _no_write_materialization_retry_needed(
            primary_llm_summary={"success": True},
            task=task,
            tool_results=read_only,
            workspace=str(tmp_path),
        )
        is False
    )


def test_no_write_retry_message_includes_current_utf8_target() -> None:
    """Live L2-11 TASK-2: forced edit_file x4 produced DEO no-effect because
    the retry prompt lacked the current test file. Existing-file recovery
    must carry the UTF-8 target body plus the quality island.
    """

    message = _build_no_write_materialization_retry_message(
        {"subject": "verify", "target_files": ["tests/product.test.js"]},
        original_message="[mode:materialize]\n范围: tests/product.test.js",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        quality_errors=[
            "tests/product.test.js: SyntaxError: The requested module '../src/clue.js' "
            "does not provide an export named 'CLUE_KINDS'"
        ],
        current_files={
            "tests/product.test.js": "import { CLUE_KINDS } from '../src/clue.js';\n",
        },
    )

    assert "import { CLUE_KINDS } from '../src/clue.js';" in message
    assert "Current UTF-8" in message
    assert "CLUE_KINDS" in message


def test_no_write_retry_message_lists_existing_sibling_exports() -> None:
    """Live L2-11 TASK-2: edit_file kept CLUE_KINDS and invented DEFAULT_CLUES.
    Retry must list sibling named exports so the model remaps to CLUE_KIND.
    """

    message = _build_no_write_materialization_retry_message(
        {"subject": "verify", "target_files": ["tests/product.test.js"]},
        original_message="fix tests",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        quality_errors=[
            "tests/product.test.js: SyntaxError: The requested module '../src/clue.js' "
            "does not provide an export named 'CLUE_KINDS'"
        ],
        existing_exports={"../src/clue.js": ["CLUE_KIND", "validateClue", "createClue"]},
    )

    assert "Existing named exports in '../src/clue.js'" in message
    assert "CLUE_KIND" in message
    assert "do not invent new import names" in message


def test_no_write_retry_edit_file_instruction_rejects_empty_search() -> None:
    """Live L2-11 epoch 10: forced edit_file still used the write_file body
    instruction, so MiniMax emitted empty search and DEO dead-lettered.
    """

    message = _build_no_write_materialization_retry_message(
        {
            "subject": "verify",
            "target_files": ["package.json", "tests/product.test.js", "README.md"],
        },
        original_message="fix tests",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        quality_errors=[
            "Artifact quality scan failed: unresolved import symbol 'CLUE_KINDS' "
            "from '../src/clue.js' in tests/product.test.js (sibling module does not define it)"
        ],
        current_files={
            "tests/product.test.js": "import { CLUE_KINDS } from '../src/clue.js';\n",
        },
        existing_exports={"../src/clue.js": ["CLUE_KIND", "validateClue"]},
        allowed_target_files=["tests/product.test.js"],
    )

    assert "Emit valid edit_file tool calls now" in message
    assert "Empty search is invalid" in message
    assert "Each write_file call must use a complete non-empty UTF-8 file body" not in message
    assert "Allowed target files: tests/product.test.js." in message
    assert "package.json" not in message.split("Original task follows:")[0]
    assert "Suggested remaps (existing exports only):" in message
    assert "CLUE_KINDS from '../src/clue.js' -> CLUE_KIND" in message
    assert "Exact import islands" in message
    assert "import { CLUE_KINDS } from '../src/clue.js';" in message


def test_no_write_retry_message_forbids_invented_default_catalogs() -> None:
    message = _build_no_write_materialization_retry_message(
        {"subject": "verify", "target_files": ["tests/product.test.js"]},
        original_message="fix tests",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        quality_errors=[
            "tests/product.test.js: unresolved catalog fixture 'DEFAULT_LOST_ITEMS' "
            "(sibling modules do not export it; construct fixtures with existing create* factories; "
            "do not invent DEFAULT_* domain exports)"
        ],
    )

    assert "Do not add DEFAULT_* exports to domain modules" in message
    assert "createLost/createAlien/createClue/createGalaxy" in message


def test_no_write_retry_message_covers_python_acceptance_traps() -> None:
    message = _build_no_write_materialization_retry_message(
        {
            "subject": "verify",
            "target_files": ["tests/product.test.js", "tests/test_product.py"],
        },
        original_message="fix tests",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        allowed_target_files=["tests/product.test.js", "tests/test_product.py"],
    )

    assert "Python acceptance tests are in scope" in message
    assert "REQUIRED_TERM_PAIRS" in message
    assert "REQUIRED_TERMS" in message
    assert "unknown-command exit 1" in message


def test_no_write_retry_keeps_full_utf8_body_above_legacy_12k_cap() -> None:
    """Live L2-11 epoch 10: 13391-char test file was clipped at 12000 chars."""

    body = "import { CLUE_KINDS } from '../src/clue.js';\n" + ("x" * 13000) + "\nTRAILER_OK\n"
    assert len(body) > 12000
    assert len(body) < _CURRENT_FILE_RETRY_CHAR_CAP
    message = _build_no_write_materialization_retry_message(
        {"subject": "verify", "target_files": ["tests/product.test.js"]},
        original_message="fix tests",
        tool_results=[{"tool_name": "read_file", "success": True}],
        forced_tool_name="edit_file",
        strict_write_only=True,
        current_files={"tests/product.test.js": body},
    )
    assert "TRAILER_OK" in message
    assert "[truncated after" not in message


def test_quality_preferred_paths_and_no_effect_followup_gate() -> None:
    declared = ["package.json", "tests/product.test.js", "README.md"]
    errors = [
        "Artifact quality scan failed: unresolved import symbol 'CLUE_KINDS' "
        "from '../src/clue.js' in tests/product.test.js (sibling module does not define it)"
    ]
    assert _quality_error_preferred_paths(declared, errors) == ["tests/product.test.js"]
    assert _unique_similar_export("CLUE_KINDS", ["CLUE_KIND", "validateClue"]) == "CLUE_KIND"
    assert _unique_similar_export("DEFAULT_CLUES", ["CLUE_KIND", "validateClue"]) is None
    assert (
        _no_effect_write_retry_needed(
            {"error": "decoded_tool_calls=4; error_types=director_write_no_effect,deo_member_soft_denied"},
            [],
        )
        is True
    )
    assert (
        _no_effect_write_retry_needed(
            {"error": "ok"},
            [{"tool_name": "edit_file", "status": "success", "success": True, "result": {"ok": True}}],
        )
        is False
    )


def test_target_candidates_include_explicit_scope_directories_with_target_files() -> None:
    task = {
        "target_files": ["package.json", "README.md"],
        "scope_paths": ["package.json", "README.md", "src", "tests"],
    }

    assert _extract_task_target_path_candidates(task) == ["package.json", "README.md", "src", "tests"]


@pytest.mark.asyncio
async def test_execute_retries_blank_write_content_with_materialize_prompt(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
    task_id = str(task["id"])
    seen_messages: list[str] = []
    seen_contexts: list[dict[str, Any]] = []

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        seen_messages.append(message)
        raw_context = kwargs.get("context")
        seen_contexts.append(raw_context if isinstance(raw_context, dict) else {})
        if len(seen_messages) == 1:
            return {
                "content": "I will create src/app.py.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "status": "success",
                        "success": True,
                        "arguments": {"file": "src/app.py", "content": ""},
                        "result": {"path": "src/app.py", "ok": True},
                    }
                ],
            }
        return {
            "content": (
                "src/app.py\n"
                "```python\n"
                "APP_STATUS = 'ok'\n"
                "\n"
                "\n"
                "def main() -> str:\n"
                "    return APP_STATUS\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    print(main())\n"
                "```\n"
            ),
            "success": True,
            "tool_results": [
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/app.py", "content": ""},
                    "result": {"path": "src/app.py", "ok": True},
                }
            ],
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-empty-write-retry"},
    )

    assert (tmp_path / "src" / "app.py").exists() is False
    assert len(seen_messages) >= 2
    assert "previous write tool call had blank content" in seen_messages[1]
    assert seen_contexts[1]["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "write_file"},
    }
    forced_defs = seen_contexts[1]["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
    assert seen_contexts[1]["_transaction_kernel_force_exact_tools"] is True
    assert seen_contexts[1]["director_empty_write_retry"]["write_only_single_target"] == {
        "tool": "write_file",
        "target_file": "src/app.py",
    }
    _assert_retry_text_fallback_is_non_authoritative(
        adapter=adapter,
        task_id=task_id,
        result=result,
        summary_key="empty_write_content_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_no_write_probe_with_write_only_materialize_prompt(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"], "phase": "implementation"},
    )
    task_id = str(task["id"])
    seen_messages: list[str] = []
    seen_contexts: list[dict[str, Any]] = []

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        seen_messages.append(message)
        raw_context = kwargs.get("context")
        seen_contexts.append(raw_context if isinstance(raw_context, dict) else {})
        if len(seen_messages) == 1:
            return {
                "content": "I inspected the workspace and will implement next.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "repo_tree",
                        "tool_name": "repo_tree",
                        "status": "success",
                        "success": True,
                        "result": {"tree": ".\n"},
                    },
                    {
                        "tool": "execute_command",
                        "tool_name": "execute_command",
                        "status": "success",
                        "success": True,
                        "result": {"stdout": "requirements.md\n", "stderr": "", "returncode": 0},
                    },
                ],
            }
        return {
            "content": (
                "src/app.py\n"
                "```python\n"
                "APP_STATUS = 'ok'\n"
                "\n"
                "\n"
                "def main() -> str:\n"
                "    return APP_STATUS\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    print(main())\n"
                "```\n"
            ),
            "success": True,
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-no-write-probe-retry"},
    )

    assert (tmp_path / "src" / "app.py").exists() is False
    assert len(seen_messages) >= 2
    assert "completed without any write/edit receipt" in seen_messages[1]
    assert "Do not call read, search, tree, or shell tools" in seen_messages[1]
    assert seen_contexts[1]["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "write_file"},
    }
    forced_defs = seen_contexts[1]["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
    file_schema = forced_defs[0]["function"]["parameters"]["properties"]["file"]
    assert file_schema["enum"] == ["src/app.py"]
    assert seen_contexts[1]["director_no_write_materialization_retry"]["write_only_declared_targets"] == {
        "tool": "write_file",
        "target_files": ["src/app.py"],
    }
    _assert_retry_text_fallback_is_non_authoritative(
        adapter=adapter,
        task_id=task_id,
        result=result,
        summary_key="no_write_materialization_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_multi_file_no_write_with_mutation_tools_only(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create application modules",
        description="Create src/app.py and src/utils.py with a runnable entry point.",
        metadata={
            "target_files": ["src/app.py", "src/utils.py"],
            "scope_paths": ["src/app.py", "src/utils.py"],
            "phase": "implementation",
        },
    )
    task_id = str(task["id"])
    seen_messages: list[str] = []
    seen_contexts: list[dict[str, Any]] = []

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        seen_messages.append(message)
        raw_context = kwargs.get("context")
        seen_contexts.append(raw_context if isinstance(raw_context, dict) else {})
        if len(seen_messages) == 1:
            return {
                "content": "I inspected the workspace and will implement next.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "repo_tree",
                        "tool_name": "repo_tree",
                        "status": "success",
                        "success": True,
                        "result": {"tree": ".\n"},
                    }
                ],
            }
        return {
            "content": (
                "src/utils.py\n"
                "```python\n"
                "def status() -> str:\n"
                "    return 'ok'\n"
                "```\n"
                "src/app.py\n"
                "```python\n"
                "from src.utils import status\n"
                "\n"
                "\n"
                "def main() -> str:\n"
                "    return status()\n"
                "```\n"
            ),
            "success": True,
            "tool_results": [],
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-multi-no-write-probe-retry"},
    )

    assert (tmp_path / "src" / "utils.py").exists() is False
    assert (tmp_path / "src" / "app.py").exists() is False
    assert len(seen_messages) >= 2
    assert "completed without any write/edit receipt" in seen_messages[1]
    assert "Do not call read, search, tree, or shell tools" in seen_messages[1]
    assert "write_file or edit_file" in seen_messages[1]
    assert seen_contexts[1]["_transaction_kernel_forced_tool_choice"] == "required"
    assert seen_contexts[1].get("_transaction_kernel_force_exact_tools") is not True
    forced_defs = seen_contexts[1]["_transaction_kernel_forced_tool_definitions"]
    forced_names = {
        item["function"]["name"]
        for item in forced_defs
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    }
    assert forced_names == {"write_file", "edit_file"}
    write_def = next(item for item in forced_defs if item["function"]["name"] == "write_file")
    edit_def = next(item for item in forced_defs if item["function"]["name"] == "edit_file")
    assert write_def["function"]["parameters"]["properties"]["file"]["enum"] == ["src/app.py", "src/utils.py"]
    # R127: edit_file must not receive path enums (qualification-safe write_file only).
    assert "enum" not in edit_def["function"]["parameters"]["properties"]["file"]
    assert seen_contexts[1]["director_no_write_materialization_retry"]["multi_file_declared_targets"] == {
        "required_write_tools": ["edit_file", "write_file"],
        "target_files": ["src/app.py", "src/utils.py"],
    }
    _assert_retry_text_fallback_is_non_authoritative(
        adapter=adapter,
        task_id=task_id,
        result=result,
        summary_key="no_write_materialization_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_read_only_materialization_with_forced_write(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
    task_id = str(task["id"])
    seen_messages: list[str] = []
    seen_contexts: list[dict[str, Any]] = []

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        seen_messages.append(message)
        raw_context = kwargs.get("context")
        seen_contexts.append(raw_context if isinstance(raw_context, dict) else {})
        if len(seen_messages) == 1:
            return {
                "content": "Workspace inspected; no source files are present yet.",
                "success": True,
                "tool_results": [{"tool": "repo_tree", "tool_name": "repo_tree", "status": "success", "success": True}],
            }
        return {
            "content": (
                "src/app.py\n"
                "```python\n"
                "APP_STATUS = 'ok'\n"
                "\n"
                "\n"
                "def main() -> str:\n"
                "    return APP_STATUS\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    print(main())\n"
                "```\n"
            ),
            "success": True,
            "tool_results": [],
        }

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-no-write-retry"},
    )

    assert (tmp_path / "src" / "app.py").exists() is False
    assert len(seen_messages) >= 2
    assert "previous Director turn completed without any write/edit receipt" in seen_messages[1]
    assert seen_contexts[1]["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "write_file"},
    }
    forced_defs = seen_contexts[1]["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
    assert forced_defs[0]["function"]["parameters"]["properties"]["file"]["enum"] == ["src/app.py"]
    assert seen_contexts[1]["_transaction_kernel_force_exact_tools"] is True
    assert seen_contexts[1]["director_no_write_materialization_retry"]["write_only_declared_targets"] == {
        "tool": "write_file",
        "target_files": ["src/app.py"],
    }
    _assert_retry_text_fallback_is_non_authoritative(
        adapter=adapter,
        task_id=task_id,
        result=result,
        summary_key="no_write_materialization_retry",
    )


@pytest.mark.asyncio
async def test_empty_write_retry_existing_target_forces_edit_blocks(tmp_path: Any) -> None:
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    class _Execution:
        def __init__(self) -> None:
            self.allowed_tool_names: set[str] | None = None
            self.allow_patch_fallback: bool | None = None

        @staticmethod
        def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
            del result
            return []

        async def execute_tools(
            self,
            content: str,
            target_task_id: str,
            update_task_progress: Any,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            del content, target_task_id, update_task_progress
            self.allowed_tool_names = kwargs.get("allowed_tool_names")
            self.allow_patch_fallback = kwargs.get("allow_patch_fallback")
            return []

    class _Adapter:
        workspace = str(tmp_path)
        _update_task_progress = staticmethod(lambda *args, **kwargs: None)

        def __init__(self) -> None:
            self._execution = _Execution()
            self.retry_message = ""
            self.retry_context: dict[str, Any] = {}

        async def _invoke_role_dialogue_with_timeout(
            self,
            message: str,
            *,
            context: dict[str, Any],
            timeout_seconds: float,
            stage_label: str,
        ) -> dict[str, Any]:
            del timeout_seconds, stage_label
            self.retry_message = message
            self.retry_context = context
            return {"content": "calculator.py\n```python\npass\n```", "success": True}

    adapter = _Adapter()

    _, summary = await _run_empty_write_content_materialization_retry(
        adapter,
        task={"target_files": ["calculator.py"]},
        target_task_id="PM-0001-1-S1-fill2",
        context={"run_id": "run-existing-empty-write"},
        original_message="[mode:materialize]\n范围: calculator.py",
        tool_results=[{"tool_name": "write_file", "arguments": {"file": "calculator.py", "content": ""}}],
        llm_call_timeout=10,
    )

    assert adapter.retry_context["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "edit_blocks"},
    }
    forced_defs = adapter.retry_context["_transaction_kernel_forced_tool_definitions"]
    assert forced_defs and forced_defs[0]["function"]["name"] == "edit_blocks"
    assert adapter.retry_context["_transaction_kernel_force_exact_tools"] is True
    assert adapter.retry_context["director_empty_write_retry"]["write_only_single_target"] == {
        "tool": "edit_blocks",
        "target_file": "calculator.py",
    }
    assert "valid edit_blocks tool call" in adapter.retry_message
    assert "valid write_file tool call" not in adapter.retry_message
    assert adapter._execution.allowed_tool_names == {"edit_blocks"}
    assert adapter._execution.allow_patch_fallback is False
    assert summary["attempted"] is True


def test_validate_generated_output_allows_todo_status_enum_value(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export enum TaskStatus {\n"
        "  Todo = 'TODO',\n"
        "  InProgress = 'IN_PROGRESS',\n"
        "  Done = 'DONE',\n"
        "}\n\n"
        "export interface Task {\n"
        "  id: string;\n"
        "  tenant_id: string;\n"
        "  status: TaskStatus;\n"
        "  version: number;\n"
        "}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is None


def test_validate_generated_output_rejects_todo_comment(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "models" / "task.model.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "// TODO implement task versioning\nexport interface Task {\n  tenant_id: string;\n  version: number;\n}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Task model version status",
            "description": "Implement tenant task status and version model",
        },
        ["src/models/task.model.ts"],
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


def test_validate_generated_output_allows_title_case_todo_product_heading(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Todo API\n\n"
        "This FastAPI service implements authenticated todo creation, listing, completion, and deletion.\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "tests" / "test_e2e.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "# Todo workflow coverage\ndef test_todo_create_and_complete_flow():\n    assert 'todo'.upper() == 'TODO'\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Todo API integration verification",
            "description": "Verify todo create and complete workflow with README instructions",
        },
        ["README.md", "tests/test_e2e.py"],
    )

    assert error is None


def test_validate_generated_output_uses_target_path_as_domain_signal_for_config(tmp_path: Any) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    config = tmp_path / "tsconfig.json"
    config.write_text(
        "{\n"
        '  "compilerOptions": {\n'
        '    "target": "ES2022",\n'
        '    "module": "ES2022",\n'
        '    "rootDir": "src",\n'
        '    "outDir": "dist",\n'
        '    "strict": true\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Create tsconfig.json",
            "description": "test -f tsconfig.json && npx tsc --noEmit",
        },
        ["tsconfig.json"],
    )

    assert error is None


def test_validate_generated_output_allows_forbidden_token_list_naming_notimplemented(tmp_path: Any) -> None:
    """A test that NAMES notimplemented/stub as forbidden tokens is not a placeholder.

    Regression (factory-bench L1-02 r10): the Director wrote a correct
    anti-placeholder test whose FORBIDDEN_TOKENS list contained "notimplemented";
    the bare NotImplemented scan flagged it as placeholder content, failing
    materialization quality and trapping the Director in an unfixable rewrite loop.
    The string-literal naming must pass; a genuine ``raise NotImplementedError``
    must still be rejected.
    """
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "tests" / "test_product.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '"""Product quality gate tests for the dream-note alchemy project."""\n'
        "import unittest\n\n"
        'FORBIDDEN_TOKENS = ("todo", "fixme", "notimplemented", "no test specified", "stub")\n\n\n'
        "class ProductScriptTest(unittest.TestCase):\n"
        "    def test_no_forbidden_tokens(self) -> None:\n"
        '        source = open("src/index.js", encoding="utf-8").read().lower()\n'
        "        for token in FORBIDDEN_TOKENS:\n"
        "            self.assertNotIn(token, source)\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Dream note alchemy product quality test",
            "description": "Implement tests/test_product.py validating the dream alchemy product",
        },
        ["tests/test_product.py"],
    )

    assert error is None


def test_validate_generated_output_rejects_real_notimplemented_body(tmp_path: Any) -> None:
    """A genuine ``raise NotImplementedError`` body is still placeholder content."""
    executor = DirectorPatchExecutor(str(tmp_path))
    target = tmp_path / "src" / "engine" / "rules.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "def fuse_dream_recipe(dream, recipe):\n    raise NotImplementedError\n",
        encoding="utf-8",
    )

    error = executor.validate_generated_output(
        {
            "subject": "Dream recipe fusion rules",
            "description": "Implement src/engine/rules.py dream recipe fusion",
        },
        ["src/engine/rules.py"],
    )

    assert error is not None
    assert "generic/placeholder content detected" in error


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


class TestSelectExecutionStrategy:
    """_select_execution_strategy is a pure function of directive + task + context."""

    def test_architect_concern_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context = {
            "metadata": {
                "architect_constraints": [{"type": "concern", "detail": "risky"}],
            }
        }
        result = adapter._select_execution_strategy("do something", {}, context)
        assert result == "conservative"

    def test_large_scope_and_complex_directive_triggers_incremental(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 5, "scope_paths": ["b"] * 6}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "incremental"

    def test_refactor_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("refactor the module", {}, {})
        assert result == "conservative"

    def test_verify_triggers_focused(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("verify the test suite", {}, {})
        assert result == "focused"

    def test_medium_scope_and_complex_triggers_aggressive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "x" * 301
        task = {"target_files": ["a"] * 3, "scope_paths": ["b"] * 3}
        result = adapter._select_execution_strategy(directive, task, {})
        assert result == "aggressive"

    def test_simple_directive_returns_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("fix bug", {}, {})
        assert result == "default"

    def test_refactor_zh_triggers_conservative(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._select_execution_strategy("重构代码", {}, {})
        assert result == "conservative"


class TestApplyIntelligentCorrection:
    """_apply_intelligent_correction analyzes failure patterns."""

    def test_success_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._apply_intelligent_correction({"success": True}, [])
        assert result["success"] is True
        assert "_correction_hints" not in result

    def test_timeout_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "LLM timeout"},
            {"error": "timeout after 30s"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" in result
        assert any("smaller steps" in h for h in result["_correction_hints"])

    def test_syntax_error_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "SyntaxError"},
            {"error": "语法错误"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("syntax" in h.lower() for h in result["_correction_hints"])

    def test_missing_dependency_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "module not found"},
            {"error": "找不到文件"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("dependencies" in h.lower() for h in result["_correction_hints"])

    def test_permission_pattern_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [
            {"error": "permission denied"},
            {"error": "权限不足"},
        ]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert any("permissions" in h.lower() for h in result["_correction_hints"])

    def test_single_failure_no_hint(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        previous = [{"error": "timeout"}]
        result = adapter._apply_intelligent_correction({"success": False}, previous)
        assert "_correction_hints" not in result


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestDeterministicRepairEvidence:
    """Hard-coded repair evidence must be complete and machine-readable."""

    def test_extracts_deterministic_source_tools_from_all_tool_result_shapes(self) -> None:
        tool_results: list[dict[str, Any]] = [
            {"source_tool": "deterministic_patch_residue_cleanup"},
            {"result": {"source_tool": "deterministic_rust_post_repair"}},
            {"payload": {"source_tool": "deterministic_future_repair"}},
            {"result": {"source_tool": "not_deterministic"}},
            {"result": {"source_tool": "deterministic_rust_post_repair"}},
        ]

        assert _deterministic_repair_source_tools_from_tool_results(tool_results) == [
            "deterministic_patch_residue_cleanup",
            "deterministic_rust_post_repair",
            "deterministic_future_repair",
        ]
        summary = _deterministic_repair_profile_summary_from_tool_results(tool_results)
        assert summary["schema_version"] == "director.deterministic_repair_profile_summary.v1"
        assert summary["source_tools"] == [
            "deterministic_patch_residue_cleanup",
            "deterministic_rust_post_repair",
            "deterministic_future_repair",
        ]
        assert summary["registered"] is False
        assert summary["count"] == 3
        assert summary["source_tool_profiles"][-1]["concern"] == "unregistered"

    def test_finalize_materialization_records_deterministic_profiles_from_all_tool_results(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "src" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const ok = true;\n", encoding="utf-8")
        captured_receipt_payload: dict[str, Any] = {}

        def fake_emit_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            payload = kwargs.get("payload")
            captured_receipt_payload.update(payload if isinstance(payload, dict) else {})
            return {"ok": True, "receipt_id": "receipt-1"}

        monkeypatch.setattr(execute_method_module, "_emit_director_adapter_cognitive_receipt", fake_emit_receipt)
        adapter = SimpleNamespace(
            workspace=str(tmp_path),
            _update_task_progress=MagicMock(),
        )
        state = execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=["src/app.ts"],
            all_affected_files=["src/app.ts"],
            tool_results=[
                {"result": {"source_tool": "deterministic_rust_post_repair"}},
                {"result": {"source_tool": "deterministic_rust_post_repair"}},
                {"result": {"source_tool": "deterministic_future_repair"}},
            ],
        )

        result = execute_method_module._phase_finalize_materialization(
            adapter,
            board_claim_applied=False,
            context={},
            decision_signals=[],
            direct_fallback_summary=None,
            empty_write_content_retry_summary=None,
            materialization_mode="materialize_changes",
            primary_llm_summary=None,
            quality_repair_attempts=[],
            quality_repair_summary=None,
            run_id="run-1",
            semantic_quality_repair_attempts=[],
            semantic_quality_repair_summary=None,
            target_task_id="task-1",
            task={"id": "task-1"},
            task_claim_session_id="",
            write_tool_evidence=True,
            state=state,
        )

        repair_profiles = result["deterministic_repair_profiles"]
        assert result["success"] is True
        assert repair_profiles["source_tools"] == [
            "deterministic_rust_post_repair",
            "deterministic_future_repair",
        ]
        assert repair_profiles["registered"] is False
        assert repair_profiles["source_tool_profiles"][0]["language"] == "rust"
        assert repair_profiles["source_tool_profiles"][1]["risk_level"] == "high"
        assert captured_receipt_payload["deterministic_repair_profiles"] == repair_profiles


class TestDeterministicPythonRuntimeSmokeLongRunningBoundary:
    """Runtime smoke is deferred to the authoritative Director command port.

    The adapter only detects eligible scripts and emits an attempt-bound command
    request. Timeout classification and process cleanup belong to the physical
    tool lifecycle and its receipt, never to an adapter-local subprocess.
    """

    def test_long_running_main_block_is_not_a_failure(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # A server-style main block: bind a TCP socket, accept
        # forever. Behaves like ``serve_forever()`` in stdlib
        # http.server — exactly the L4-23 pattern.
        (tmp_path / "server.py").write_text(
            "import socket\n"
            "if __name__ == '__main__':\n"
            "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            "    s.bind(('127.0.0.1', 0))\n"
            "    s.listen(5)\n"
            "    while True:\n"
            "        s.accept()\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-server-bb-1",
            all_affected_files=["server.py"],
            context=_test_execution_attempt_context(tmp_path, "task-server-bb-1"),
            timeout_seconds=1.0,
        )

        assert errors == [], errors
        assert len(tool_results) == 1
        request = tool_results[0]["result"]["deferred_request"]
        assert request.command.endswith("server.py")
        assert request.timeout_seconds == 1

    def test_clean_main_block_still_passes(self, tmp_path: Any) -> None:
        """A clean main that exits within timeout is still a pass."""
        adapter = _make_adapter(tmp_path)
        (tmp_path / "ok.py").write_text(
            "if __name__ == '__main__':\n    print('done')\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-ok-bb-1",
            all_affected_files=["ok.py"],
            context=_test_execution_attempt_context(tmp_path, "task-ok-bb-1"),
            timeout_seconds=1.0,
        )

        assert errors == [], errors
        assert len(tool_results) == 1

    def test_scheduling_never_spawns_an_adapter_child_process(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "server.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.5)\n",
            encoding="utf-8",
        )

        errors, tool_results = run_python_runtime_smoke(
            adapter,
            task_id="task-zombie-bb-1",
            all_affected_files=["server.py"],
            context=_test_execution_attempt_context(tmp_path, "task-zombie-bb-1"),
            timeout_seconds=0.5,
        )
        assert errors == [], errors
        assert len(tool_results) == 1
        assert tool_results[0]["result"]["deferred_request"].timeout_seconds == 1


class TestDeterministicPythonStaticSmoke:
    """Director py_compiles every .py file the model touches, not just declared targets.

    Live factory-bench L2-07 (2026-06-17, after runtime-smoke fix): the
    model wrote 13 .py files, 10 of which were in the task's declared
    target list and py_compile-checked by the existing quality gate.
    The remaining 3 (including ``src/ledger/ui/stats_view.py``)
    contained a ``SyntaxError: keyword argument repeated: columns`` —
    the model wrote ``columns=(...)`` twice in the same ``Treeview``
    constructor. The platform marked the run as PASS, and the
    downstream task-market integration_qa was not even invoked for
    that file. A rigid ruler must py_compile every .py artifact the
    model wrote, regardless of whether the contract asked for it.
    """

    def test_python_static_smoke_catches_syntax_error_in_undeclared_file(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Duplicate keyword argument — a real, deterministic Python
        # syntax error. ``def f(x, x):`` raises SyntaxError at compile
        # time. The model emitted the same kind of bug in
        # L2-07 ``stats_view.py`` (duplicate ``columns=``).
        (tmp_path / "stats_view.py").write_text(
            "def f(x, x):\n    return x\n",
            encoding="utf-8",
        )

        errors = run_python_static_smoke(
            adapter,
            all_affected_files=["stats_view.py"],
        )

        assert len(errors) == 1, errors
        assert "stats_view.py" in errors[0]
        # Error message should mention the syntax issue
        assert "syntax" in errors[0].lower() or "invalid" in errors[0].lower()

    def test_python_static_smoke_passes_clean_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "clean_a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "clean_b.py").write_text(
            "def hello() -> str:\n    return 'hi'\n",
            encoding="utf-8",
        )

        errors = run_python_static_smoke(
            adapter,
            all_affected_files=["clean_a.py", "clean_b.py"],
        )

        assert errors == [], errors

    def test_python_static_smoke_skips_non_python_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "readme.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "config.toml").write_text("x = 1\n", encoding="utf-8")

        errors = run_python_static_smoke(
            adapter,
            all_affected_files=["readme.md", "config.toml"],
        )

        assert errors == [], errors

    def test_python_static_smoke_catches_multiple_broken_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Two distinct, real Python syntax errors + one clean file.
        (tmp_path / "broken_a.py").write_text("def f(x, x):\n    return x\n", encoding="utf-8")
        (tmp_path / "broken_b.py").write_text("class 123Bad:\n    pass\n", encoding="utf-8")
        (tmp_path / "clean.py").write_text("z = 3\n", encoding="utf-8")

        errors = run_python_static_smoke(
            adapter,
            all_affected_files=["broken_a.py", "broken_b.py", "clean.py"],
        )

        # Both broken files should be reported; clean is silent.
        assert len(errors) == 2, errors
        assert any("broken_a.py" in e for e in errors)
        assert any("broken_b.py" in e for e in errors)


class TestBuildMaterializedMetadata:
    """_build_materialized_metadata is a pure dict transformation."""

    def test_basic_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", {"goal": "g", "scope": "s", "steps": ["a"]})
        assert meta["goal"] == "g"
        assert meta["scope"] == "s"
        assert meta["steps"] == ["a"]
        assert meta["phase"] == "implementation"
        assert meta["pm_task_id"] == "req-1"
        assert meta["source"] == "director_adapter.materialized_orchestration_task"

    def test_input_metadata_merged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "req-1",
            {"metadata": {"custom": "v", "projection": {"x": 1}}},
        )
        assert meta["custom"] == "v"
        assert "projection" not in meta  # projection key is stripped

    def test_none_input_data(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata("req-1", None)  # type: ignore[arg-type]
        assert meta["pm_task_id"] == "req-1"

    def test_nested_pm_task_metadata_preserves_execution_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        meta = adapter._build_materialized_metadata(
            "task-0-director",
            {
                "metadata": {
                    "id": "T01-001",
                    "goal": "Create the TypeScript foundation",
                    "target_files": ["package.json", "src/index.ts"],
                    "scope_paths": ["src/config"],
                    "blueprint_id": "ce_T01-001",
                }
            },
        )

        assert meta["pm_task_id"] == "T01-001"
        assert meta["target_files"] == ["package.json", "src/index.ts"]
        assert meta["scope_paths"] == ["src/config"]
        assert meta["blueprint_id"] == "ce_T01-001"


# ---------------------------------------------------------------------------
# Execution backend resolution
# ---------------------------------------------------------------------------


class TestResolveExecutionBackendRequest:
    """_resolve_execution_backend_request delegates to resolve_director_execution_backend."""

    def test_defaults_to_code_edit(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={},
            input_data={},
            context={},
        )
        assert req.execution_backend == "code_edit"
        assert req.is_supported is True
        assert req.is_projection_backend is False

    def test_projection_hint_in_request(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        req = adapter._resolve_execution_backend_request(
            task_id="t1",
            task={"metadata": {"execution_backend": "projection_generate", "projection": {"scenario_id": "s1"}}},
            input_data={},
            context={},
        )
        assert req.execution_backend == "projection_generate"
        assert req.scenario_id == "s1"
        assert req.is_projection_backend is True


# ---------------------------------------------------------------------------
# Persist metadata
# ---------------------------------------------------------------------------


class TestPersistExecutionBackendMetadata:
    """_persist_execution_backend_metadata delegates to _update_board_task."""

    def test_noop_when_task_id_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Should not raise even with no task_runtime
        adapter._persist_execution_backend_metadata("", MagicMock())

    def test_calls_update_board_task(self, tmp_path: Any) -> None:
        mock_runtime = MagicMock()
        mock_runtime.task_exists.return_value = True
        mock_runtime.update_task_row.return_value = {"id": 1}
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="code_edit")
        adapter._persist_execution_backend_metadata("task-1", req)
        mock_runtime.update_task_row.assert_called_once()


# ---------------------------------------------------------------------------
# WS2: _update_task_progress must only write progress statuses as metadata
# ---------------------------------------------------------------------------


class TestUpdateTaskProgressMetadataOnlyStatusProjection:
    """WS2: progress trace statuses must not propagate to TaskRow status.

    Under the WS2 Execution Ledger SSoT, the TaskRow status column is owned
    exclusively by ``TaskRuntimeService`` claim/complete/fail transitions.
    Progress trace events (running, in_progress, claimed, completed, failed,
    etc.) are preserved as adapter metadata and never written to the TaskRow
    status column.
    """

    @pytest.mark.parametrize(
        "event_status",
        ["running", "in_progress", "claimed"],
    )
    def test_execution_like_status_is_metadata_only(
        self,
        tmp_path: Any,
        event_status: str,
    ) -> None:
        mock_runtime = MagicMock()
        mock_runtime.task_exists.return_value = True
        mock_runtime.update_task_row.return_value = {"id": 1}
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        adapter._update_task_progress(
            "1",
            "executing",
            event_status=event_status,
        )
        mock_runtime.update_task_row.assert_called_once()
        call_args = mock_runtime.update_task_row.call_args
        assert call_args[1].get("status") is None
        metadata = call_args[1].get("metadata", {})
        assert metadata["adapter_phase"] == "executing"
        assert metadata["adapter_event_status"] == event_status

    @pytest.mark.parametrize(
        "event_status",
        ["completed", "failed", "cancelled", "timeout"],
    )
    def test_terminal_status_is_metadata_only(
        self,
        tmp_path: Any,
        event_status: str,
    ) -> None:
        mock_runtime = MagicMock()
        mock_runtime.task_exists.return_value = True
        mock_runtime.update_task_row.return_value = {"id": 1}
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        adapter._update_task_progress(
            "1",
            "executing",
            event_status=event_status,
        )
        mock_runtime.update_task_row.assert_called_once()
        call_args = mock_runtime.update_task_row.call_args
        assert call_args[1].get("status") is None
        metadata = call_args[1].get("metadata", {})
        assert metadata["adapter_phase"] == "executing"
        assert metadata["adapter_event_status"] == event_status

    def test_metadata_only_progress_still_writes_metadata(
        self,
        tmp_path: Any,
    ) -> None:
        mock_runtime = MagicMock()
        mock_runtime.task_exists.return_value = True
        mock_runtime.update_task_row.return_value = {"id": 1}
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        adapter._update_task_progress(
            "1",
            "analysis",
            current_file="src/app.py",
            event_code="file_open",
        )
        mock_runtime.update_task_row.assert_called_once()
        call_kwargs = mock_runtime.update_task_row.call_args
        assert call_kwargs[0][0] == 1  # normalized task_id
        assert call_kwargs[1].get("status") is None
        metadata = call_kwargs[1].get("metadata", {})
        assert metadata["adapter_phase"] == "analysis"
        assert metadata["adapter_current_file"] == "src/app.py"
        assert metadata["adapter_event_code"] == "file_open"

    def test_non_execution_event_status_writes_as_metadata(
        self,
        tmp_path: Any,
    ) -> None:
        """A non-terminal, non-execution event_status is still stored as
        metadata (not as TaskRow status)."""
        mock_runtime = MagicMock()
        mock_runtime.task_exists.return_value = True
        mock_runtime.update_task_row.return_value = {"id": 1}
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        adapter._update_task_progress(
            "1",
            "executing",
            event_status="custom_advisory",
        )
        mock_runtime.update_task_row.assert_called_once()
        call_kwargs = mock_runtime.update_task_row.call_args
        assert call_kwargs[1].get("status") is None
        metadata = call_kwargs[1].get("metadata", {})
        assert metadata["adapter_event_status"] == "custom_advisory"


# ---------------------------------------------------------------------------
# Capabilities / role_id
# ---------------------------------------------------------------------------


class TestDirectorAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "director"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "execute_task" in caps
        assert "sequential_execution" in caps
        assert "adaptive_strategy_selection" in caps


class TestDirectorRuntimeFallback:
    @pytest.mark.asyncio
    async def test_role_dialogue_uses_role_runtime_context_os_path_first(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        captured: dict[str, Any] = {}

        class FakeRoleRuntimeService:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                captured["command"] = command
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="done",
                    usage={"tokens": 10},
                    metadata={"provider_id": "anthropic_compat-test", "model": "kimi-for-coding"},
                )

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FakeRoleRuntimeService,
        )

        result = await adapter._invoke_role_dialogue(
            "write src/app.ts",
            context={"run_id": "run-runtime-first", "task_id": "task-runtime-first"},
        )

        assert result["success"] is True
        assert result["metadata"]["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
        assert result["metadata"]["context_os_expected"] is True
        command = captured["command"]
        assert isinstance(command, ExecuteRoleSessionCommandV1)
        assert command.role == "director"
        assert command.domain == "code"
        assert command.stream is False
        assert command.run_id == "run-runtime-first"
        assert command.task_id == "task-runtime-first"
        assert command.metadata["role_runtime_required"] is True
        assert command.metadata["cognitive_runtime_required"] is True

    @pytest.mark.asyncio
    async def test_role_dialogue_keeps_observed_tool_calls_out_of_tool_results(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class FakeRoleRuntimeService:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="director",
                    workspace=str(tmp_path),
                    task_id=command.task_id,
                    session_id=command.session_id,
                    run_id=command.run_id,
                    output="I will call write_file.",
                    tool_calls=("write_file",),
                    metadata={"provider_id": "anthropic_compat-test"},
                    error_code="tool_dispatch_dropped",
                    error_message="tool_dispatch_dropped",
                )

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FakeRoleRuntimeService,
        )

        result = await adapter._invoke_role_dialogue(
            "write package.json",
            context={"run_id": "run-observed-tool", "task_id": "task-observed-tool"},
        )

        assert result["success"] is False
        assert result["error"] == "tool_dispatch_dropped"
        assert result["tool_calls"] == []
        assert result["tool_results"] == []
        assert result["metadata"]["observed_tool_calls"] == ["write_file"]
        assert result["raw_response"]["observed_tool_calls"] == ["write_file"]

    @pytest.mark.asyncio
    async def test_role_dialogue_fails_closed_when_runtime_boundary_unavailable(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class UnavailableRoleRuntimeService:
            def __init__(self, **_kwargs: Any) -> None:
                raise ImportError("runtime boundary unavailable")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            UnavailableRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="director_role_runtime_boundary_unavailable"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_execution_failure_does_not_fallback_to_legacy(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        adapter = _make_adapter(tmp_path)

        class FailingRoleRuntimeService:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                del command
                raise RuntimeError("runtime provider failed")

        monkeypatch.setattr(
            "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
            FailingRoleRuntimeService,
        )

        with pytest.raises(RuntimeError, match="runtime provider failed"):
            await adapter._invoke_role_dialogue("write src/app.ts")

    @pytest.mark.asyncio
    async def test_direct_runtime_provider_bypass_is_removed(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        with pytest.raises(RuntimeError, match="director_runtime_provider_bypass_removed"):
            await adapter._invoke_direct_runtime_provider("write a file", timeout_seconds=3)


# ---------------------------------------------------------------------------
# Integration with execution backend module (pure helpers)
# ---------------------------------------------------------------------------


class TestDirectorExecutionBackendPure:
    """Tests for the pure helper functions in director_execution_backend."""

    def test_normalize_backend(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_backend

        assert _normalize_backend("code_edit") == "code_edit"
        assert _normalize_backend("projection_generate") == "projection_generate"
        assert _normalize_backend("") == "code_edit"
        assert _normalize_backend("unknown") == "unknown"

    def test_normalize_project_slug(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_project_slug

        assert _normalize_project_slug("My Project", default_value="default") == "my_project"
        assert _normalize_project_slug("", default_value="default") == "default"

    def test_normalize_bool(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import _normalize_bool

        assert _normalize_bool(True, default=False) is True
        assert _normalize_bool("1", default=False) is True
        assert _normalize_bool("false", default=True) is False
        assert _normalize_bool(None, default=True) is True

    def test_request_to_task_metadata(self) -> None:
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="projection_generate", scenario_id="s1")
        meta = req.to_task_metadata()
        assert meta["execution_backend"] == "projection_generate"
        assert meta["projection"]["scenario_id"] == "s1"


def test_scaffold_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 integrity: a declared TS target with no clean nearby source must never
    be fabricated — and the historical opt-in env vars are now permanently inert,
    so setting them re-arms nothing (the re-activation footgun is gone)."""
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )
    task = {
        "subject": "Define tenant model",
        "description": "Create the tenant model file.",
        "target_files": ["src/models/tenant.model.ts"],
    }
    artifact_quality_errors = [
        "Artifact quality scan failed: declared target file missing 'src/models/tenant.model.ts'",
    ]

    def _run() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return _run_test_materialization_quality_repair_schedule(
            adapter,
            task=task,
            task_id="task-1",
            artifact_quality_errors=artifact_quality_errors,
        )

    # Default (env unset): nothing fabricated.
    monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
    monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)
    results, summary = _run()
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "models" / "tenant.model.ts").exists()

    # Env vars are now permanently inert: opting in fabricates nothing either.
    monkeypatch.setenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", "1")
    monkeypatch.setenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", "1")
    results, summary = _run()
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "models" / "tenant.model.ts").exists()


def test_business_contract_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 regression: Director must not fabricate business-domain service code
    unless a legacy benchmark explicitly opts into the synthesizer."""
    monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)
    task = {
        "subject": "Audit service",
        "description": "Create audit service and middleware.",
        "target_files": [
            "src/services/audit.service.ts",
            "src/middleware/audit.middleware.ts",
        ],
    }
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    errors = [
        "unresolved relative import './audit.entity' in src/services/audit.service.ts",
    ]

    results, summary = _run_test_materialization_quality_repair_schedule(
        adapter,
        task=task,
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "services" / "audit.service.ts").exists()
    assert not (tmp_path / "src" / "middleware" / "audit.middleware.ts").exists()

    # The opt-in env var is now permanently inert: setting it fabricates nothing.
    monkeypatch.setenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", "1")
    results, summary = _run_test_materialization_quality_repair_schedule(
        adapter,
        task=task,
        task_id="task-1",
        artifact_quality_errors=errors,
    )
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "src" / "services" / "audit.service.ts").exists()
    assert not (tmp_path / "src" / "middleware" / "audit.middleware.ts").exists()


def test_typescript_relative_import_case_repair_rewrites_importer_only(tmp_path: Any) -> None:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    garden = src / "Garden.ts"
    moon = src / "moon.ts"
    garden.write_text(
        "import { Moon } from './Moon';\nexport class Garden { moon = new Moon(); }\n",
        encoding="utf-8",
    )
    moon.write_text("export class Moon {}\n", encoding="utf-8")
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = _run_test_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/Garden.ts", "src/moon.ts"]},
        task_id="task-2",
        artifact_quality_errors=["Artifact quality scan failed: unresolved relative import './Moon' in src/Garden.ts"],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_relative_import_case_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "from './moon'" in garden.read_text(encoding="utf-8")
    assert moon.read_text(encoding="utf-8") == "export class Moon {}\n"
