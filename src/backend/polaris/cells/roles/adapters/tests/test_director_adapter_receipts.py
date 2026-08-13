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
    _no_write_materialization_retry_needed,
    _no_write_materialization_retry_tool_definitions,
    _pin_file_schema_to_declared_targets,
    _resolve_claim_external_task_id,
    _run_empty_write_content_materialization_retry,
    _suspend_claimed_execution_for_cancellation,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
    _task_runtime_heartbeat_exception_signal,
    _task_runtime_heartbeat_failed_signal,
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


def _test_execution_attempt(workspace: Path, task_id: str) -> TaskRuntimeExecutionAttemptIdentityV1:
    """Return exact attempt identity for test-only deferred-effect projection."""

    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace=workspace.resolve().as_posix(),
        task_id=91,
        external_task_id=task_id,
        session_id=f"session-{task_id}",
        attempt=1,
        role_id="director",
        worker_id="director-test-worker",
        run_id=f"run-{task_id}",
        lease_expires_at="2099-01-01T00:00:00Z",
    )


def _test_execution_attempt_context(workspace: Path, task_id: str) -> dict[str, Any]:
    return {
        "task_runtime_execution_attempt_authority": create_task_runtime_execution_attempt_authority(
            _test_execution_attempt(workspace, task_id)
        )
    }


def _project_deferred_repair_results_for_test(
    workspace: Path,
    tool_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply deferred effects only inside the test fixture.

    Production remains plan-only at the roles adapter boundary.  These tests
    retain their planner/content assertions by projecting forward effects in
    their isolated temporary workspace.
    """

    projected: list[dict[str, Any]] = []
    for item in tool_results:
        result = item.get("result")
        result_payload = dict(result) if isinstance(result, dict) else {}
        request = result_payload.get("deferred_request")
        if item.get("tool_name") != "deferred_director_repair" or request is None:
            projected.append(dict(item))
            continue
        repair_kernel = dict(result_payload.get("repair_kernel") or {})
        planning = dict(repair_kernel.get("planning") or {})
        repair_kernel.update(
            {
                "status": "applied",
                "planning_preflight": planning,
                "metadata": {"requires_revalidation": True},
            }
        )
        for effect in request.plan.effects:
            if effect.contingency_kind != "forward":
                continue
            arguments = dict(effect.arguments)
            target = workspace / effect.target_path
            if effect.tool_name == "write_file":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(arguments["content"]), encoding="utf-8")
            elif effect.tool_name == "edit_file":
                original = target.read_text(encoding="utf-8")
                search = str(arguments["search"])
                assert search in original
                target.write_text(original.replace(search, str(arguments["replace"]), 1), encoding="utf-8")
            else:
                target.unlink()
            projected.append(
                {
                    "tool": effect.tool_name,
                    "tool_name": effect.tool_name,
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": request.plan.source_tool,
                        "file": effect.target_path,
                        "repair_kernel": repair_kernel,
                    },
                }
            )
    return projected


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




class TestDirectorAdapterCognitiveRuntimeReceipt:
    """Director materialization must leave Cognitive Runtime evidence."""

    def test_role_runtime_metadata_requires_context_os_and_repo_intelligence(self) -> None:
        metadata = DirectorAdapter._build_role_runtime_metadata(
            {
                "run_id": "run-1",
                "task_id": "TASK-1",
                "metadata": {"source": "caller"},
            },
            max_retries=2,
        )

        assert metadata["role_runtime_required"] is True
        assert metadata["cognitive_runtime_required"] is True
        assert metadata["context_os_expected"] is True
        assert metadata["use_repo_intelligence"] is True
        assert metadata["repo_intel_max_files"] == 20
        assert metadata["repo_intel_max_symbols"] == 40
        assert metadata["run_id"] == "run-1"
        assert metadata["task_id"] == "TASK-1"
        assert metadata["source"] == "caller"
        assert metadata["cognitive_runtime_approval_mode"] == "auto_accept"
        assert metadata["cognitive_runtime_approval"] == {
            "mode": "auto_accept",
            "source": "roles.adapters.director",
            "scope": "director_execution_preflight",
            "approved_by": "director_adapter",
        }

    def test_director_verification_commands_are_promoted_to_runtime_context(self) -> None:
        context: dict[str, Any] = {
            "task_id": "TASK-GO",
            "construction_step": {"verify": "go test ./..."},
            "metadata": {
                "acceptance_criteria": ["`go run .` returns success"],
            },
        }

        commands = DirectorAdapter._ensure_director_verification_commands(
            message="Acceptance: `go test ./...` and `go run .` must pass.",
            context=context,
        )

        assert commands == ["go test ./...", "go run ."]
        assert context["verification_commands"] == ["go test ./...", "go run ."]
        assert context["metadata"]["verification_commands"] == ["go test ./...", "go run ."]

    def test_director_execution_profile_is_added_to_runtime_context_and_metadata(self, tmp_path: Any) -> None:
        context = {
            "task_id": "TASK-1",
            "run_id": "RUN-1",
            "target_files": ["src/App.tsx"],
            "metadata": {
                "description": "Implement a React TypeScript UI component.",
                "task_type": "implement",
            },
        }
        metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=1)

        profile = DirectorAdapter._ensure_director_execution_profile(
            message="Implement src/App.tsx",
            context=context,
            metadata=metadata,
            workspace=str(tmp_path),
        )

        assert profile["schema_version"] == "task.execution_profile.v1"
        assert profile["task_type"] == "write_code"
        assert profile["language"] == "typescript"
        assert profile["framework"] == "react"
        assert profile["target_files"] == ["src/App.tsx"]
        assert metadata["director_execution_profile"] == profile
        assert metadata["task_execution_profile"] == profile
        assert context["director_execution_profile"] == profile
        assert context["task_execution_profile"] == profile

    def test_task_contract_fields_are_promoted_before_role_runtime_profile(self, tmp_path: Any) -> None:
        task = {
            "subject": "Implement requested TypeScript modules",
            "description": "Create the contracted source files.",
            "metadata": {
                "target_files": ["package.json", "src/modules/primary.ts", "src/modules/secondary.ts"],
                "scope_paths": ["src/modules"],
                "project_declared_target_files": [
                    "package.json",
                    "src/index.ts",
                    "src/modules/primary.ts",
                    "src/modules/secondary.ts",
                ],
                "project_declared_source_targets": [
                    "src/index.ts",
                    "src/modules/primary.ts",
                    "src/modules/secondary.ts",
                ],
                "phase": "implementation",
                "project_type": "typescript_service",
                "task_id": "TASK-1",
                "pm_task_id": "TASK-1",
                "blueprint_id": "ce_TASK-1",
                "blueprint_path": ".polaris/blueprints/ce_TASK-1.json",
                "pm_contract_hash": "pm-contract-hash",
                "blueprint_hash": "ce-blueprint-hash",
                "delivery_depth_contract": {
                    "schema_version": "polaris.delivery_depth_contract.v1",
                    "minimums": {"min_prod_files": 6, "min_prod_lines": 500},
                },
                "manifest_entrypoint_contract": {
                    "schema_version": "polaris.manifest_entrypoint_contract.v1",
                    "allowed_local_entrypoints": [
                        "package.json",
                        "src/index.ts",
                        "src/modules/primary.ts",
                        "src/modules/secondary.ts",
                    ],
                },
                "ce_handoff_decision": {"allowed": True, "decision_hash": "handoff-hash"},
            },
        }
        context: dict[str, Any] = {"run_id": "RUN-1", "metadata": {"task_type": "implement"}}

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )
        metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=1)
        profile = DirectorAdapter._ensure_director_execution_profile(
            message="Implement the requested files.",
            context=context,
            metadata=metadata,
            workspace=str(tmp_path),
        )

        assert profile["target_files"] == ["package.json", "src/modules/primary.ts", "src/modules/secondary.ts"]
        assert profile["project_type"] != "api"
        assert profile["signal_evidence"]["project_type_source"] == "metadata"
        assert profile["task_type"] == "write_code"
        assert context["metadata"]["target_files"] == profile["target_files"]
        assert metadata["target_files"] == profile["target_files"]
        assert metadata["delivery_depth_contract"]["minimums"]["min_prod_files"] == 6
        assert metadata["project_declared_target_files"] == [
            "package.json",
            "src/index.ts",
            "src/modules/primary.ts",
            "src/modules/secondary.ts",
        ]
        assert metadata["manifest_entrypoint_contract"]["allowed_local_entrypoints"] == [
            "package.json",
            "src/index.ts",
            "src/modules/primary.ts",
            "src/modules/secondary.ts",
        ]
        assert metadata["ce_handoff_decision"] == {"allowed": True, "decision_hash": "handoff-hash"}
        envelope = metadata["director_execution_envelope"]
        assert envelope["authorization"]["allowed_write_paths"] == [
            "package.json",
            "src/modules/primary.ts",
            "src/modules/secondary.ts",
        ]
        assert "src/index.ts" not in envelope["authorization"]["allowed_write_paths"]
        assert envelope["pm_contract"]["hash"] == "pm-contract-hash"
        assert envelope["ce_blueprint"]["hash"] == "ce-blueprint-hash"
        assert envelope["handoff_decision"]["allowed"] is True

    def test_promote_task_runtime_governance_deadlines_into_director_context(self, tmp_path: Any) -> None:
        task = {
            "subject": "Implement project entrypoint",
            "metadata": {
                "task_id": "TASK-1",
                "pm_task_id": "TASK-1",
                "target_files": ["src/index.ts"],
                "scope_paths": ["src/index.ts"],
                "factory_run_deadline_epoch_seconds": 150.0,
                "factory_director_execution_deadline_epoch_seconds": 120.0,
                "factory_run_deadline_safety_seconds": 15.0,
            },
        }
        context: dict[str, Any] = {
            "factory_run_deadline_epoch_seconds": 140.0,
            "metadata": {"task_type": "implement"},
        }

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )

        assert context["factory_run_deadline_epoch_seconds"] == 140.0
        assert context["factory_director_execution_deadline_epoch_seconds"] == 120.0
        assert context["factory_run_deadline_safety_seconds"] == 15.0
        assert context["metadata"]["factory_run_deadline_epoch_seconds"] == 150.0
        assert context["metadata"]["factory_director_execution_deadline_epoch_seconds"] == 120.0
        assert context["metadata"]["factory_run_deadline_safety_seconds"] == 15.0

    def test_promote_task_contract_replaces_summary_slots_with_structured_evidence(self, tmp_path: Any) -> None:
        BlueprintPersistence(str(tmp_path)).save(
            "ce_TASK-1",
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "task_id": "TASK-1",
                "target_files": ["package.json", "src/index.ts"],
                "scope_paths": ["package.json", "src/index.ts"],
                "execution_checklist": ["Create package.json", "Create src/index.ts"],
                "module_interface_contract": {
                    "schema_version": "chief_engineer.module_interface_contract.v1",
                    "modules": [{"path": "src/index.ts", "role": "entrypoint"}],
                },
            },
        )
        task = {
            "subject": "Implement project entrypoint",
            "metadata": {
                "task_id": "TASK-1",
                "pm_task_id": "TASK-1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["package.json", "src/index.ts"],
                "scope_paths": ["package.json", "src/index.ts"],
                "acceptance_criteria": ["Entrypoint compiles"],
            },
        }
        context: dict[str, Any] = {
            "task_contract": "PM contract summary text already projected",
            "ce_blueprint": "CE blueprint summary text already projected",
            "metadata": {
                "task_contract": "PM contract summary text already projected",
                "ce_blueprint": "CE blueprint summary text already projected",
            },
        }

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )

        assert isinstance(context["pm_contract"], dict)
        assert isinstance(context["task_contract"], dict)
        assert isinstance(context["ce_blueprint"], dict)
        assert isinstance(context["chief_engineer_blueprint"], dict)
        assert looks_like_pm_contract_payload(context["pm_contract"])
        assert looks_like_pm_contract_payload(context["task_contract"])
        assert looks_like_ce_blueprint_payload(context["ce_blueprint"])
        assert looks_like_ce_blueprint_payload(context["chief_engineer_blueprint"])
        assert isinstance(context["metadata"]["pm_contract"], dict)
        assert isinstance(context["metadata"]["task_contract"], dict)
        assert isinstance(context["metadata"]["ce_blueprint"], dict)

    def test_promote_task_contract_replaces_non_authoritative_dict_slots(self, tmp_path: Any) -> None:
        BlueprintPersistence(str(tmp_path)).save(
            "ce_TASK-1",
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": "ce_TASK-1",
                "task_id": "TASK-1",
                "target_files": ["package.json", "src/index.ts"],
                "scope_paths": ["package.json", "src/index.ts"],
                "execution_checklist": ["Create package.json", "Create src/index.ts"],
                "module_interface_contract": {
                    "schema_version": "chief_engineer.module_interface_contract.v1",
                    "modules": [{"path": "src/index.ts", "role": "entrypoint"}],
                },
            },
        )
        task = {
            "subject": "Implement project entrypoint",
            "metadata": {
                "task_id": "TASK-1",
                "pm_task_id": "TASK-1",
                "blueprint_id": "ce_TASK-1",
                "target_files": ["package.json", "src/index.ts"],
                "scope_paths": ["package.json", "src/index.ts"],
                "acceptance_criteria": ["Entrypoint compiles"],
            },
        }
        context: dict[str, Any] = {
            "pm_contract": {"summary": "non-authoritative prompt projection"},
            "ce_blueprint": {"summary": "non-authoritative prompt projection"},
            "module_interface_contract": {"summary": "non-authoritative prompt projection"},
            "metadata": {
                "pm_contract": {"summary": "non-authoritative prompt projection"},
                "ce_blueprint": {"summary": "non-authoritative prompt projection"},
                "module_interface_contract": {"summary": "non-authoritative prompt projection"},
            },
        }

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )

        assert looks_like_pm_contract_payload(context["pm_contract"])
        assert looks_like_ce_blueprint_payload(context["ce_blueprint"])
        assert context["module_interface_contract"]["modules"][0]["path"] == "src/index.ts"
        assert looks_like_pm_contract_payload(context["metadata"]["pm_contract"])
        assert looks_like_ce_blueprint_payload(context["metadata"]["ce_blueprint"])
        assert context["metadata"]["module_interface_contract"]["modules"][0]["path"] == "src/index.ts"

    def test_ce_blueprint_does_not_expand_claimed_task_write_boundary(self, tmp_path: Any) -> None:
        BlueprintPersistence(str(tmp_path)).save(
            "ce_TASK-1-source-core",
            {
                "blueprint_id": "ce_TASK-1-source-core",
                "task_id": "TASK-1-source-core",
                "summary": "Blueprint includes a related test file for guidance.",
                "target_files": [
                    "src/engine/rules.js",
                    "src/engine/runner.js",
                    "tests/behavior.test.js",
                ],
                "scope_paths": [
                    "src/engine/rules.js",
                    "src/engine/runner.js",
                    "tests/behavior.test.js",
                ],
                "job_token": {
                    "token_id": "job-TASK-1-source-core",
                    "capability_audit": {"ok": True, "issues": []},
                    "allowed_write_paths": [
                        "src/engine/rules.js",
                        "src/engine/runner.js",
                    ],
                },
                "module_interface_contract": {
                    "schema_version": "chief_engineer.module_interface_contract.v1",
                    "modules": [{"path": "src/engine/rules.js", "role": "core_engine"}],
                },
            },
        )
        task = {
            "subject": "Implement core engine modules",
            "metadata": {
                "task_id": "TASK-1-source-core",
                "pm_task_id": "TASK-1-source-core",
                "external_task_id": "TASK-1-source-core",
                "blueprint_id": "ce_TASK-1-source-core",
                "target_files": ["src/engine/rules.js", "src/engine/runner.js"],
                "scope_paths": ["src/engine/rules.js", "src/engine/runner.js"],
                "language": "javascript",
                "phase": "implementation",
            },
        }
        context: dict[str, Any] = {"run_id": "RUN-1", "metadata": {"task_type": "implement"}}

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )
        metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=1)
        DirectorAdapter._ensure_director_execution_profile(
            message="Implement core engine modules.",
            context=context,
            metadata=metadata,
            workspace=str(tmp_path),
        )

        assert context["target_files"] == ["src/engine/rules.js", "src/engine/runner.js"]
        assert context["scope_paths"] == ["src/engine/rules.js", "src/engine/runner.js"]
        assert context["metadata"]["target_files"] == ["src/engine/rules.js", "src/engine/runner.js"]
        assert context["metadata"]["scope_paths"] == ["src/engine/rules.js", "src/engine/runner.js"]
        assert context["job_token"]["token_id"] == "job-TASK-1-source-core"
        assert (
            metadata["director_execution_envelope"]["authorization"]["capability_token_ref"] == "job-TASK-1-source-core"
        )
        assert (
            "tests/behavior.test.js"
            not in metadata["director_execution_envelope"]["authorization"]["allowed_write_paths"]
        )
        assert metadata["module_interface_contract"]["modules"][0]["path"] == "src/engine/rules.js"

    def test_ce_blueprint_payload_cannot_expand_missing_task_write_boundary(self) -> None:
        merged = _merge_ce_blueprint_contract_payload(
            {
                "task_id": "TASK-1-source-core",
                "acceptance_criteria": ["Implement the core engine"],
            },
            {
                "blueprint_id": "ce_TASK-1-source-core",
                "task_id": "TASK-1-source-core",
                "target_files": ["src/core.js", "tests/core.test.js"],
                "scope_paths": ["src/core.js", "tests/core.test.js"],
                "execution_checklist": ["Write source and tests"],
            },
        )

        assert "target_files" not in merged
        assert "scope_paths" not in merged
        assert merged["execution_checklist"] == ["Write source and tests"]
        assert merged["ce_blueprint"]["target_files"] == ["src/core.js", "tests/core.test.js"]

    def test_explicit_ce_blueprint_id_still_requires_matching_task_token(self, tmp_path: Any) -> None:
        BlueprintPersistence(str(tmp_path)).save(
            "ce_TASK-2",
            {
                "blueprint_id": "ce_TASK-2",
                "task_id": "TASK-2",
                "target_files": ["src/other.js"],
            },
        )
        task = {
            "id": "TASK-1-source-core",
            "metadata": {
                "task_id": "TASK-1-source-core",
                "pm_task_id": "TASK-1-source-core",
                "blueprint_id": "ce_TASK-2",
            },
        }

        assert _load_ce_blueprint_contract_payload(str(tmp_path), task) == {}

    def test_existing_director_execution_profile_is_preserved(self, tmp_path: Any) -> None:
        existing_profile = {
            "schema_version": "task.execution_profile.v1",
            "source": "test",
            "task_type": "review",
            "language": "python",
        }
        context = {
            "metadata": {
                "director_execution_profile": existing_profile,
            },
        }
        metadata = DirectorAdapter._build_role_runtime_metadata(context, max_retries=1)

        profile = DirectorAdapter._ensure_director_execution_profile(
            message="Implement src/App.tsx",
            context=context,
            metadata=metadata,
            workspace=str(tmp_path),
        )

        assert profile["schema_version"] == existing_profile["schema_version"]
        assert profile["source"] == existing_profile["source"]
        assert profile["task_type"] == existing_profile["task_type"]
        assert profile["language"] == existing_profile["language"]
        assert profile["output_contract_id"] == "director.patch_file.v1"
        assert profile["temperature_source"] == "task.execution_profile.v1"
        assert metadata["director_execution_profile"] == profile
        assert context["director_execution_profile"] == profile

    @pytest.mark.asyncio
    async def test_role_runtime_session_promotes_metadata_tool_receipts(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import polaris.cells.roles.runtime.public.service as runtime_service_module

        adapter = _make_adapter(tmp_path)
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        tool_results = [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {"path": "src/app.ts"},
            }
        ]

        class FakeRuntimeService:
            def __init__(self, **_kwargs: Any) -> None:
                pass

            async def execute_role_session(self, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
                assert command.role == "director"
                assert command.stream is False
                profile = command.metadata["director_execution_profile"]
                assert profile["schema_version"] == "task.execution_profile.v1"
                assert profile["task_type"] == "write_code"
                assert command.context["director_execution_profile"] == profile
                assert command.metadata["task_execution_profile"] == profile
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="director",
                    workspace=str(tmp_path),
                    session_id=command.session_id,
                    task_id=command.task_id,
                    run_id=command.run_id,
                    output="done",
                    tool_calls=("write_file",),
                    metadata={
                        "batch_receipt": receipt,
                        "tool_results": tool_results,
                    },
                )

        monkeypatch.setattr(runtime_service_module, "RoleRuntimeService", FakeRuntimeService)

        result = await adapter._invoke_role_runtime_session(
            "write src/app.ts",
            context={
                "task_id": "TASK-1",
                "run_id": "RUN-1",
                "target_files": ["src/app.ts"],
                "metadata": {"task_type": "implement"},
            },
            max_retries=1,
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results
        assert result["raw_response"]["batch_receipt"] == receipt

    def test_emit_cognitive_runtime_receipt_records_and_exports_handoff(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _Service:
            def record_runtime_receipt(self, command: Any) -> Any:
                captured["receipt_command"] = command
                return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

            def export_handoff_pack(self, command: Any) -> None:
                captured["handoff_command"] = command

            def close(self) -> None:
                captured["closed"] = True

        service = _Service()
        monkeypatch.setattr(
            "polaris.cells.factory.cognitive_runtime.public.service.get_cognitive_runtime_public_service",
            lambda: service,
        )
        adapter = SimpleNamespace(workspace=str(tmp_path))

        receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task={"metadata": {"session_id": "session-1"}},
            target_task_id="TASK-1",
            run_id="run-1",
            context={"metadata": {"turn_envelope": {"turn_id": "turn-1"}}},
            receipt_type="director_adapter_materialization_completed",
            payload={"status": "completed", "changed_files": ["src/app.py"]},
            export_handoff=True,
        )

        assert receipt["ok"] is True
        assert receipt["receipt_id"] == "receipt-1"
        receipt_command = captured["receipt_command"]
        assert receipt_command.receipt_type == "director_adapter_materialization_completed"
        assert receipt_command.session_id == "session-1"
        assert receipt_command.run_id == "run-1"
        assert receipt_command.payload["source"] == "roles.adapters.director"
        assert receipt_command.payload["context_os_expected"] is True
        assert receipt_command.payload["changed_files"] == ["src/app.py"]
        handoff_command = captured["handoff_command"]
        assert handoff_command.session_id == "session-1"
        assert handoff_command.turn_envelope["receipt_ids"] == ["receipt-1"]
        assert captured["closed"] is True


# ---------------------------------------------------------------------------
# Intelligent correction
# ---------------------------------------------------------------------------


