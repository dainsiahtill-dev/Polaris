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






class TestDirectorFailureClosureA:
    def test_completion_projection_accepts_runtime_numeric_alias_for_external_task(self) -> None:
        projection = execute_method_module._task_completion_projection_from_context(
            {
                "metadata": {
                    "task_completion_projection": {
                        "schema_version": "polaris.task_completion_projection.v1",
                        "task_id": "TASK-1",
                    }
                }
            },
            target_task_id="1",
        )

        assert projection is not None
        assert projection["task_id"] == "TASK-1"

    def test_receipt_bound_preflight_appends_completed_task_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """No-provider retry must supersede an older mutation-bypass verdict."""

        paths = ["tests/product.test.js", "tests/test_product.py", "README.md"]
        for path in paths:
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"verified {path}\n", encoding="utf-8")
        appended: list[Any] = []
        lifecycle_appended: list[Any] = []
        monkeypatch.setattr(execute_method_module, "append_run_ledger_event", appended.append)
        monkeypatch.setattr(
            execute_method_module,
            "append_tool_call_lifecycle_event",
            lifecycle_appended.append,
        )
        refs = [f"receipt://{index}" for index in range(3)]

        verdict = execute_method_module._append_receipt_bound_preflight_task_boundary(
            SimpleNamespace(workspace=str(tmp_path)),
            context={
                "metadata": {
                    "task_completion_projection": {
                        "schema_version": "polaris.task_completion_projection.v1",
                        "task_id": "TASK-2",
                        "project_id": "L1-02",
                        "run_id": "factory-run-1",
                        "project_contract_hash": "c" * 64,
                        "owned_artifacts": [
                            {
                                "applicability": "required",
                                "obligation_id": f"artifact-{index}",
                                "owner_task_id": "TASK-2",
                                "path": path,
                            }
                            for index, path in enumerate(paths)
                        ],
                    }
                }
            },
            target_task_id="16",
            run_id="director-retry-1",
            finalize_result={"identity": {"external_task_id": "TASK-2"}},
            receipt_evidence={
                "schema_version": "polaris.current_task_project_artifact_receipt_evidence.v1",
                "authority": "runtime.execution_broker.project_artifact_receipt.v1",
                "ok": True,
                "required_artifact_count": 3,
                "receipt_count": 3,
                "receipt_paths": paths,
                "receipt_refs": refs,
            },
        )

        assert verdict["status"] == "completed_verified"
        assert verdict["task_id"] == "TASK-2"
        assert verdict["evidence_refs"] == refs
        assert len(lifecycle_appended) == 1
        lifecycle = lifecycle_appended[0].lifecycle_receipt
        assert lifecycle["ok"] is True
        assert lifecycle["dispatch_status"] == "verified_existing_artifacts"
        assert lifecycle["dispatched_tool_calls_count"] == 0
        assert [item["receipt_ref"] for item in lifecycle["effect_receipt_refs"]] == refs
        assert len(appended) == 1
        event = appended[0].event
        assert event["task_boundary_verdict"]["status"] == "completed_verified"
        assert event["job_token"]["project_id"] == "L1-02"

    def test_cross_task_completion_projection_settles_failed_without_leaking_lease(
        self,
        tmp_path: Path,
    ) -> None:
        settled: list[Any] = []
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=1,
            external_task_id="TASK-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="director-run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        def settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            settled.append(command)
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            )

        authority = create_task_runtime_execution_attempt_authority(execution_attempt, settle=settle)
        projection = execute_method_module._task_completion_projection_from_context(
            {
                "metadata": {
                    "task_completion_projection": {
                        "schema_version": "polaris.task_completion_projection.v1",
                        "project_id": "project-1",
                        "run_id": "factory-run-1",
                        "project_contract_hash": "c" * 64,
                        "task_id": "TASK-2",
                        "owned_artifacts": [],
                    }
                }
            },
            target_task_id="1",
        )

        result = _finalize_claimed_execution(
            SimpleNamespace(workspace=str(tmp_path)),
            target_task_id="1",
            authority=authority,
            outcome="completed",
            result_summary="done",
            task_completion_projection=projection,
        )

        assert result["success"] is False
        assert result["reason"] == "project_artifact_receipt_failed"
        assert len(settled) == 1
        assert settled[0].outcome == "failed"
        assert "owner does not match claimed task" in settled[0].metadata["project_artifact_receipt_error"]

    def test_finalization_records_exact_project_artifact_before_task_runtime_settlement(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        artifact_path = tmp_path / "src" / "main.py"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("print('ok')\n", encoding="utf-8")
        events: list[str] = []
        settled_metadata: dict[str, Any] = {}
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=1,
            external_task_id="TASK-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="director-run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        def record_project_artifact(command: Any) -> Any:
            events.append("artifact_receipt")
            assert command.workspace == str(tmp_path.resolve())
            assert command.owner_task_id == "TASK-1"
            assert command.path == "src/main.py"
            return SimpleNamespace(
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
                owner_task_id=command.owner_task_id,
                path=command.path,
                artifact_hash="a" * 64,
                receipt_hash="b" * 64,
                receipt_ref="execution-broker://project-verification/artifact/" + "b" * 64,
            )

        def settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            events.append("task_runtime_settle")
            settled_metadata.update(command.metadata)
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            )

        monkeypatch.setattr(
            execute_method_module,
            "record_project_artifact",
            record_project_artifact,
            raising=False,
        )
        authority = create_task_runtime_execution_attempt_authority(execution_attempt, settle=settle)

        result = _finalize_claimed_execution(
            SimpleNamespace(workspace=str(tmp_path)),
            target_task_id="1",
            authority=authority,
            outcome="completed",
            result_summary="done",
            metadata={"adapter_phase": "completed"},
            task_completion_projection={
                "schema_version": "polaris.task_completion_projection.v1",
                "project_id": "project-1",
                "run_id": "factory-run-1",
                "project_contract_hash": "c" * 64,
                "task_id": "TASK-1",
                "owned_artifacts": [
                    {
                        "obligation_id": "artifact-main",
                        "owner_task_id": "TASK-1",
                        "path": "src/main.py",
                    }
                ],
            },
        )

        assert result["success"] is True
        assert events == ["artifact_receipt", "task_runtime_settle"]
        assert settled_metadata["project_artifact_receipts"] == [
            {
                "obligation_id": "artifact-main",
                "path": "src/main.py",
                "artifact_hash": "a" * 64,
                "receipt_hash": "b" * 64,
                "receipt_ref": "execution-broker://project-verification/artifact/" + "b" * 64,
            }
        ]

    def test_retry_row_identity_records_receipt_by_immutable_external_task_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A retried local row id must not replace its PM/CE contract identity."""

        artifact_path = tmp_path / "src" / "main.py"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("print('ok')\n", encoding="utf-8")
        recorded_owner_ids: list[str] = []
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=6,
            external_task_id="TASK-1",
            session_id="session-retry-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="director-run-retry-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        def record_project_artifact(command: Any) -> Any:
            recorded_owner_ids.append(command.owner_task_id)
            return SimpleNamespace(
                project_id=command.project_id,
                run_id=command.run_id,
                completion_contract_hash=command.completion_contract_hash,
                obligation_id=command.obligation_id,
                owner_task_id=command.owner_task_id,
                path=command.path,
                artifact_hash="a" * 64,
                receipt_hash="b" * 64,
                receipt_ref="execution-broker://project-verification/artifact/" + "b" * 64,
            )

        monkeypatch.setattr(execute_method_module, "record_project_artifact", record_project_artifact)
        authority = create_task_runtime_execution_attempt_authority(
            execution_attempt,
            settle=lambda command: TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            ),
        )

        result = _finalize_claimed_execution(
            SimpleNamespace(workspace=str(tmp_path)),
            target_task_id="6",
            authority=authority,
            outcome="completed",
            result_summary="done",
            task_completion_projection={
                "schema_version": "polaris.task_completion_projection.v1",
                "project_id": "project-1",
                "run_id": "factory-run-1",
                "project_contract_hash": "c" * 64,
                "task_id": "TASK-1",
                "owned_artifacts": [
                    {
                        "obligation_id": "artifact-main",
                        "owner_task_id": "TASK-1",
                        "path": "src/main.py",
                    }
                ],
            },
        )

        assert result["success"] is True
        assert recorded_owner_ids == ["TASK-1"]

    def test_artifact_receipt_failure_settles_task_failed_without_leaking_lease(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        artifact_path = tmp_path / "src" / "main.py"
        artifact_path.parent.mkdir(parents=True)
        artifact_path.write_text("print('ok')\n", encoding="utf-8")
        settled: list[Any] = []
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=1,
            external_task_id="TASK-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="director-run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        monkeypatch.setattr(
            execute_method_module,
            "record_project_artifact",
            MagicMock(side_effect=RuntimeError("receipt owner unavailable")),
        )

        def settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            settled.append(command)
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            )

        authority = create_task_runtime_execution_attempt_authority(execution_attempt, settle=settle)
        result = _finalize_claimed_execution(
            SimpleNamespace(workspace=str(tmp_path)),
            target_task_id="TASK-1",
            authority=authority,
            outcome="completed",
            result_summary="done",
            task_completion_projection={
                "schema_version": "polaris.task_completion_projection.v1",
                "project_id": "project-1",
                "run_id": "factory-run-1",
                "project_contract_hash": "c" * 64,
                "task_id": "TASK-1",
                "owned_artifacts": [
                    {
                        "obligation_id": "artifact-main",
                        "owner_task_id": "TASK-1",
                        "path": "src/main.py",
                    }
                ],
            },
        )

        assert result["success"] is False
        assert result["reason"] == "project_artifact_receipt_failed"
        assert len(settled) == 1
        assert settled[0].outcome == "failed"
        assert settled[0].metadata["project_artifact_receipt_error"] == "receipt owner unavailable"

    def test_finalization_uses_renewed_identity_from_public_authority(self) -> None:
        initial = TaskRuntimeExecutionAttemptIdentityV1(
            workspace="/workspace",
            task_id=1,
            external_task_id="TASK-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )
        renewed = TaskRuntimeExecutionAttemptIdentityV1(
            **{**initial.to_record(), "lease_expires_at": "2030-01-01T00:02:00+00:00"}
        )

        def heartbeat(command: Any) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
            return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                success=True,
                code="heartbeat_renewed",
                workspace=command.workspace,
                identity=command.identity,
                renewed_identity=renewed,
            )

        def settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            assert command.identity == renewed
            assert command.identity != initial
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=True,
                code="settled",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
            )

        authority = create_task_runtime_execution_attempt_authority(initial, heartbeat=heartbeat, settle=settle)
        heartbeat_result = authority.heartbeat(lease_ttl_seconds=30, lock_timeout_seconds=0.5)
        assert heartbeat_result.success is True

        result = _finalize_claimed_execution(
            SimpleNamespace(),
            target_task_id="TASK-1",
            authority=_execution_attempt_authority_from_context(
                {"task_runtime_execution_attempt_authority": authority}
            ),
            outcome="completed",
            result_summary="completed after renewal",
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_suspend_claimed_execution_for_cancellation_emits_failure_evidence(self) -> None:
        captured_event: dict[str, Any] = {}
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace="/workspace",
            task_id=1,
            external_task_id="TASK-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        def settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            assert command.outcome == "suspended"
            assert command.summary == "director_execution_cancelled"
            assert command.metadata == {"adapter_phase": "pending"}
            assert command.identity == execution_attempt
            return TaskRuntimeExecutionAttemptSettlementVerdictV1(
                success=False,
                code="execution_event_append_failed",
                workspace=command.workspace,
                identity=command.identity,
                outcome=command.outcome,
                evidence={
                    "failure_class": "ledger_append_failed",
                    "execution_event": {
                        "ok": False,
                        "event_type": "suspended",
                        "error": "fact stream unavailable",
                    },
                },
            )

        class FakeAdapter:
            async def _emit_task_trace_event(self, **kwargs: Any) -> None:
                captured_event.update(kwargs)

        authority = create_task_runtime_execution_attempt_authority(execution_attempt, settle=settle)

        result = await _suspend_claimed_execution_for_cancellation(
            FakeAdapter(),
            target_task_id="TASK-1",
            run_id="run-1",
            authority=authority,
        )

        expected_settlement_projection = {
            "success": False,
            "code": "settlement_rejected",
            "reason": "execution_event_append_failed",
            "identity": execution_attempt.to_record(),
            "outcome": "suspended",
            "callback_error_type": "",
            "task_runtime_verdict": {
                "success": False,
                "code": "execution_event_append_failed",
                "reason": "execution_event_append_failed",
                "workspace": "/workspace",
                "identity": execution_attempt.to_record(),
                "outcome": "suspended",
                "idempotent": False,
                "evidence": {
                    "failure_class": "ledger_append_failed",
                    "execution_event": {
                        "ok": False,
                        "event_type": "suspended",
                        "error": "fact stream unavailable",
                    },
                },
            },
        }
        assert {key: result[key] for key in expected_settlement_projection} == expected_settlement_projection
        assert result["task_runtime_verdict"] == {
            "success": False,
            "code": "execution_event_append_failed",
            "reason": "execution_event_append_failed",
            "workspace": "/workspace",
            "identity": execution_attempt.to_record(),
            "outcome": "suspended",
            "idempotent": False,
            "evidence": {
                "failure_class": "ledger_append_failed",
                "execution_event": {
                    "ok": False,
                    "event_type": "suspended",
                    "error": "fact stream unavailable",
                },
            },
        }
        assert result["task_runtime_suspend_failed"] is True
        assert captured_event == {
            "task_id": "TASK-1",
            "phase": "executing",
            "step_kind": "task_runtime",
            "step_title": "Director cancellation suspend failed",
            "step_detail": "execution_event_append_failed",
            "status": "failed",
            "run_id": "run-1",
            "code": "director_task_runtime_suspend_failed",
            "reason": "execution_event_append_failed",
            "refs": {
                "task_runtime_suspend_result": expected_settlement_projection,
                "task_runtime_session_id": "session-1",
            },
        }

    @pytest.mark.asyncio
    async def test_handle_claim_required_projects_claim_attempt_evidence(self) -> None:
        captured_event: dict[str, Any] = {}

        class FakeAdapter:
            async def _emit_task_trace_event(self, **kwargs: Any) -> None:
                captured_event.update(kwargs)

        claim_attempts = [
            {
                "attempt": 1,
                "task_id": "TASK-1",
                "selection_source": "task_id_lookup",
                "claimed": False,
                "reason": "lease_conflict",
                "session_id": "",
            }
        ]

        result = await _handle_claim_required(
            FakeAdapter(),
            "TASK-1",
            "run-1",
            "TASK-1",
            "task_id_lookup",
            True,
            "Demo task",
            {"ready": 1},
            {"ready": 1, "running": 1},
            claim_attempts,
        )

        expected_evidence = {
            "requested_task_id": "TASK-1",
            "selected_task_id": "TASK-1",
            "selection_source": "task_id_lookup",
            "selected_from_board": True,
            "selected_subject": "Demo task",
            "taskboard_before": {"ready": 1},
            "taskboard_after_claim": {"ready": 1, "running": 1},
            "board_claim_applied": False,
            "claim_attempts": claim_attempts,
            "claim_failure_reason": "lease_conflict",
        }

        assert result["success"] is False
        assert result["error_code"] == "director.task_claim_required"
        assert result["task_runtime_claim_required"] is True
        assert result["task_runtime_claim_evidence"] == expected_evidence
        assert result["task_runtime_claim_attempts"] == claim_attempts
        assert result["task_runtime_claim_failure_reason"] == "lease_conflict"
        assert result["decision_signals"] == [
            {
                "code": "director.taskboard.claim_required",
                "severity": "error",
                "detail": "taskboard_claim_required_before_execution_with_retries_exhausted",
                "claim_failure_reason": "lease_conflict",
                "claim_attempt_count": 1,
            }
        ]
        assert captured_event["refs"] == expected_evidence

    def test_task_runtime_heartbeat_failure_projects_decision_signal(self) -> None:
        heartbeat_result = {
            "success": False,
            "reason": "execution_event_append_failed",
            "failure_class": "ledger_append_failed",
            "execution_event": {
                "ok": False,
                "event_type": "heartbeat_renewed",
                "error": "fact stream unavailable",
            },
        }

        signal = _task_runtime_heartbeat_failed_signal(heartbeat_result)

        assert signal["code"] == "director_task_runtime_heartbeat_failed"
        assert signal["severity"] == "error"
        assert signal["reason"] == "execution_event_append_failed"
        assert signal["failure_class"] == "ledger_append_failed"
        assert signal["heartbeat_result"] == heartbeat_result

    def test_task_runtime_heartbeat_exception_projects_decision_signal(self) -> None:
        exc = RuntimeError("heartbeat ledger unavailable")

        signal = _task_runtime_heartbeat_exception_signal(exc)

        assert signal == {
            "code": "director_task_runtime_heartbeat_failed",
            "severity": "error",
            "detail": "heartbeat ledger unavailable",
            "reason": "task_runtime_heartbeat_exception",
            "heartbeat_result": {
                "success": False,
                "reason": "task_runtime_heartbeat_exception",
                "error": "heartbeat ledger unavailable",
                "exception_type": "RuntimeError",
            },
        }

    def test_with_decision_signals_appends_without_overwriting_existing_signals(self) -> None:
        result = {"success": True, "decision_signals": [{"code": "existing"}]}
        merged = _with_decision_signals(result, [{"code": "heartbeat"}])

        assert merged is not result
        assert merged["success"] is True
        assert merged["decision_signals"] == [{"code": "existing"}, {"code": "heartbeat"}]
        assert result["decision_signals"] == [{"code": "existing"}]

    def test_with_task_runtime_finalize_evidence_preserves_original_failure(self) -> None:
        result = {
            "success": False,
            "error_code": "director.materialization.no_physical_files",
            "failure_stage": "director_materialization",
            "decision_signals": [{"code": "original_failure"}],
        }
        finalize_result = {
            "success": False,
            "reason": "execution_event_append_failed",
            "failure_class": "ledger_append_failed",
            "execution_event": {
                "ok": False,
                "event_type": "failed",
                "error": "fact stream unavailable",
            },
        }

        merged = _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )

        assert merged["success"] is False
        assert merged["error_code"] == "director.materialization.no_physical_files"
        assert merged["failure_stage"] == "director_materialization"
        assert merged["control_plane_failure_code"] == "director_task_runtime_finalization_failed"
        assert merged["control_plane_failure_stage"] == "director_task_runtime_finalization"
        assert merged["task_runtime_finalization_failed"] is True
        assert merged["task_runtime_finalize_result"] == finalize_result
        assert merged["decision_signals"] == [
            {"code": "original_failure"},
            {
                "code": "director_task_runtime_finalization_failed",
                "severity": "error",
                "detail": "execution_event_append_failed",
                "requested_outcome": "failed",
                "reason": "execution_event_append_failed",
                "failure_class": "ledger_append_failed",
            },
        ]

    def test_finalize_claimed_execution_reports_terminal_transition_failure(self) -> None:
        execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
            workspace="/workspace",
            task_id=1,
            external_task_id="task-1",
            session_id="session-1",
            attempt=1,
            role_id="director",
            worker_id="director-worker",
            run_id="run-1",
            lease_expires_at="2030-01-01T00:00:00+00:00",
        )

        def failing_settle(command: Any) -> TaskRuntimeExecutionAttemptSettlementVerdictV1:
            assert command.outcome == "completed"
            assert command.identity == execution_attempt
            raise RuntimeError("Cannot transition task from 'failed' to 'completed'")

        authority = create_task_runtime_execution_attempt_authority(execution_attempt, settle=failing_settle)

        finalize_result = _finalize_claimed_execution(
            SimpleNamespace(),
            target_task_id="task-1",
            authority=authority,
            outcome="completed",
            result_summary="done",
            metadata={"adapter_phase": "completed"},
        )
        result = _task_runtime_finalization_failed_result(
            target_task_id="task-1",
            requested_outcome="completed",
            finalize_result=finalize_result,
            tool_results=[
                {
                    "result": {
                        "source_tool": "deterministic_rust_post_repair",
                    }
                }
            ],
        )

        assert finalize_result["success"] is False
        assert finalize_result["reason"] == "task_runtime_terminal_transition_failed"
        assert result["success"] is False
        assert result["error_code"] == "director_task_runtime_finalization_failed"
        assert result["root_cause_hint"] == "task_runtime_terminal_transition_failed"
        assert result["deterministic_repair_profiles"]["source_tools"] == ["deterministic_rust_post_repair"]

    def test_role_response_normalization_keeps_kernel_errors_failed(self) -> None:
        result = _normalize_director_role_response(
            {
                "response": "[ROLE_EXECUTION_ERROR] provider failed",
                "success": True,
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
            }
        )

        assert result["success"] is False
        assert "provider failed" in result["error"]
        assert result["provider"] == "anthropic_compat-test"
        assert result["model"] == "kimi-for-coding"

    def test_role_response_normalization_preserves_batch_receipt(self) -> None:
        receipt = {
            "results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "result": {"path": "src/app.ts"},
                }
            ]
        }
        result = _normalize_director_role_response(
            {
                "response": "done",
                "provider": "anthropic_compat-test",
                "model": "kimi-for-coding",
                "batch_receipt": receipt,
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt

    def test_role_response_normalization_promotes_runtime_metadata_receipts(self) -> None:
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

        result = _normalize_director_role_response(
            {
                "response": "done",
                "success": True,
                "metadata": {
                    "batch_receipt": receipt,
                    "tool_results": tool_results,
                },
            }
        )

        assert result["success"] is True
        assert result["batch_receipt"] == receipt
        assert result["tool_results"] == tool_results

    def test_direct_text_patch_flag_resolves_from_context(self, tmp_path: Any) -> None:
        del tmp_path
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "true"}) is True
        assert _director_direct_text_patch_only_enabled({"director_direct_text_patch_only": "0"}) is False

    def test_existing_scope_preflight_defaults_enabled_and_can_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KERNELONE_DIRECTOR_EXISTING_SCOPE_PREFLIGHT", raising=False)
        assert _director_existing_scope_preflight_enabled({}) is True
        assert _director_existing_scope_preflight_enabled({"director_existing_scope_preflight": "off"}) is False

    @pytest.mark.asyncio
    async def test_role_dialogue_runtime_error_returns_failed_payload(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)

        async def _boom_dialogue(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
            del message, context
            raise RuntimeError("kernel contract retry failed")

        adapter._invoke_role_dialogue = _boom_dialogue  # type: ignore[method-assign]

        result = await adapter._invoke_role_dialogue_with_timeout(
            "write files",
            context={},
            timeout_seconds=1.0,
            stage_label="unit",
        )

        assert result["success"] is False
        assert "kernel contract retry failed" in str(result.get("error") or "")

    @pytest.mark.asyncio
    async def test_role_dialogue_watchdog_preserves_task_execution_budget_for_receipt_settlement(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The outer watchdog must not cancel a live canonical transaction.

        Regression for L1-01 r42: the Director turn committed a write receipt while
        the adapter watchdog was expiring, but the watchdog discarded the runtime
        result and Factory settled the task failed.  The provider keeps the narrow
        call budget; the outer watchdog must preserve the enclosing TaskRuntime
        execution budget so DEO/tool receipts can finish projecting.
        """

        from polaris.cells.roles.adapters.internal.director import adapter as adapter_module

        adapter = _make_adapter(tmp_path)
        monkeypatch.setattr(adapter_module, "_ROLE_DIALOGUE_SETTLEMENT_GRACE_SECONDS", 0.01)

        async def _commit_before_task_execution_deadline(
            message: str,
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            del message, context
            await asyncio.sleep(0.15)
            return {
                "content": "done",
                "success": True,
                "tool_results": [
                    {
                        "tool_name": "edit_file",
                        "status": "success",
                        "effect_receipt": {"receipt_outcome": "succeeded"},
                    }
                ],
            }

        adapter._invoke_role_dialogue = _commit_before_task_execution_deadline  # type: ignore[method-assign]

        result = await adapter._invoke_role_dialogue_with_timeout(
            "repair the failed verifier",
            context={"request_timeout_seconds": 0.5},
            timeout_seconds=0.1,
            stage_label="quality_repair",
        )

        assert result["success"] is True
        assert result["tool_results"][0]["effect_receipt"]["receipt_outcome"] == "succeeded"

    @pytest.mark.asyncio
    async def test_primary_write_call_projects_bounded_output_budget_to_runtime(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        captured: dict[str, Any] = {}

        async def _capture_dialogue(message: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
            captured["message"] = message
            captured["context"] = dict(context or {})
            return {"content": "done", "success": True}

        adapter._invoke_role_dialogue = _capture_dialogue  # type: ignore[method-assign]

        result = await adapter._invoke_role_dialogue_with_timeout(
            "materialize files",
            context={
                "target_files": ["go.mod", "main.go", "models/pet.go"],
                "task_execution_profile": {
                    "schema_version": "task.execution_profile.v1",
                    "task_type": "write_code",
                },
                "task_execution_contract": {
                    "schema_version": "task.execution_contract.v1",
                    "context_budget": {"output_budget_tokens": 128_000},
                },
            },
            timeout_seconds=42.0,
            stage_label="first_call",
        )

        assert result["success"] is True
        assert captured["message"] == "materialize files"
        runtime_context = captured["context"]
        assert runtime_context["llm_max_tokens"] == 7000
        assert runtime_context["director_forced_write_output_budget"]["stage_label"] == "first_call"

    @pytest.mark.asyncio
    async def test_execute_fails_claimed_task_on_unhandled_runtime_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="实现核心模块",
            description="创建文件",
            metadata={"scope": "src/core.ts", "steps": ["写入核心文件"]},
        )
        task_id = str(task["id"])

        async def _boom_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise RuntimeError("director kernel exploded")

        adapter._invoke_role_dialogue_with_timeout = _boom_call  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-fail-closed"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director.runtime.exception"
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"

    @pytest.mark.asyncio
    async def test_execute_rejects_workspace_diff_without_write_tool_receipt(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Repair failing TypeScript test",
            description="Apply the smallest code change and verify npm test behavior.",
            metadata={
                "scope": "src/types/domain.ts",
                "steps": ["Update the domain type contract"],
                "acceptance": ["The TypeScript test failure is repaired"],
            },
        )
        task_id = str(task["id"])
        captured: dict[str, Any] = {}

        async def _mutating_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            captured["context"] = kwargs.get("context")
            target = tmp_path / "src" / "types" / "domain.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export type DomainState = 'ready';\n", encoding="utf-8")
            return {"content": "Applied directly by runtime provider.", "success": True}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after ambiguous workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _mutating_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-diff-evidence"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_missing_write_receipt"
        assert result["materialization_mode"] == "workspace_diff_without_write_tool"
        assert captured["context"]["run_id"] == "run-director-diff-evidence"
        assert any(
            signal.get("code") == "director_missing_write_receipt"
            for signal in result.get("decision_signals", [])
            if isinstance(signal, dict)
        )
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_file_count") == 1
        assert adapter_result.get("write_tool_evidence") is False
        assert adapter_result.get("materialization_error") == "director_missing_write_receipt"

    @pytest.mark.asyncio
    async def test_execute_rejects_off_target_workspace_diff_as_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Implement browser networking client",
            description="Update the declared network client target file.",
            metadata={
                "target_files": ["src/client/network-client.ts"],
                "scope_paths": ["src"],
                "steps": ["Implement src/client/network-client.ts"],
                "acceptance": ["src/client/network-client.ts is changed"],
            },
        )
        task_id = str(task["id"])

        async def _off_target_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            off_target = tmp_path / "src" / "server" / "moderation.ts"
            off_target.parent.mkdir(parents=True, exist_ok=True)
            off_target.write_text("export const moderationReady = true;\n", encoding="utf-8")
            return {"content": "Changed a different file.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _off_target_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-off-target-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("modified_files") == []
        assert adapter_result.get("out_of_scope_files") == ["src/server/moderation.ts"]

    @pytest.mark.asyncio
    async def test_execute_keeps_failed_no_write_separate_from_sibling_diff(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Implement garden simulator module",
            description="Update only the declared garden module.",
            metadata={
                "target_files": ["src/garden.ts"],
                "scope_paths": ["src/garden.ts"],
                "steps": ["Implement src/garden.ts"],
                "acceptance": ["src/garden.ts is changed"],
            },
        )
        task_id = str(task["id"])

        async def _failed_dialogue_with_sibling_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            sibling = tmp_path / "scripts" / "verify.js"
            sibling.parent.mkdir(parents=True, exist_ok=True)
            sibling.write_text("console.log('sibling task changed this file');\n", encoding="utf-8")
            return {"content": "", "success": False, "error": "single_batch_contract_violation"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _failed_dialogue_with_sibling_diff  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-sibling-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
        assert adapter_result.get("failure_class") == FailureClassV1.INCOMPLETE_MATERIALIZATION.value
        assert adapter_result.get("out_of_scope_files") == ["scripts/verify.js"]

    def test_no_materialized_changes_ignores_sibling_diff_after_failed_write_tool(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
        task_id = str(task["id"])
        state = MaterializationState(
            current_files={"tsconfig.json": "sibling-fingerprint"},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[
                {
                    "tool": "write_file",
                    "success": False,
                    "args": {"file": "package.json", "content": ""},
                    "error": "Director write policy denied",
                }
            ],
        )

        result = _phase_no_materialized_changes(
            adapter,
            baseline_files={},
            board_claim_applied=False,
            can_accept_existing_scope=False,
            context={},
            direct_fallback_summary=None,
            empty_write_content_retry_summary=None,
            no_write_materialization_retry_summary=None,
            existing_contract_evidence={},
            primary_llm_summary={"success": True},
            requires_fresh_materialization=True,
            run_id="run-failed-write-sibling-diff",
            target_task_id=task_id,
            task={"target_files": ["package.json"], "scope_paths": ["package.json"]},
            task_claim_session_id="",
            workspace_name=tmp_path.name,
            write_tool_evidence=False,
            state=state,
        )

        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == FailureClassV1.INCOMPLETE_MATERIALIZATION.value

    def test_no_materialized_changes_preserves_primary_tool_dispatch_failure(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
        task_id = str(task["id"])
        state = MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[],
        )

        result = _phase_no_materialized_changes(
            adapter,
            baseline_files={},
            board_claim_applied=False,
            can_accept_existing_scope=False,
            context={},
            direct_fallback_summary=None,
            empty_write_content_retry_summary=None,
            no_write_materialization_retry_summary=None,
            existing_contract_evidence={},
            primary_llm_summary={
                "success": False,
                "error": "tool_dispatch_dropped: required write tool was not dispatched before completion",
            },
            requires_fresh_materialization=True,
            run_id="run-tool-dispatch-dropped",
            target_task_id=task_id,
            task={"target_files": ["package.json"], "scope_paths": ["package.json"]},
            task_claim_session_id="",
            workspace_name=tmp_path.name,
            write_tool_evidence=False,
            state=state,
        )

        assert result is not None
        assert result["success"] is False
        assert result["error"] == "tool_dispatch_dropped"
        assert result["error_code"] == "tool_dispatch_dropped"
        assert result["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
        assert result["responsible_layer"] == "execution_control_plane"
        assert result["failure_stage"] == "director_tool_lifecycle"
        assert result["root_cause_hint"] == "required_tool_without_dispatch_receipt"
        assert result["decision_signals"][0]["detail"].startswith("Director role runtime reported")
        failure_evidence = result["failure_evidence"][0]
        assert failure_evidence["schema_version"] == "failure_evidence.v1"
        assert failure_evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
        assert failure_evidence["responsible_layer"] == "execution_control_plane"
        assert failure_evidence["reason"] == (
            "Director role runtime reported required/native tool calls without dispatch/effect receipt."
        )
        assert failure_evidence["evidence_refs"]
        assert failure_evidence["metadata"] == {
            "error": "tool_dispatch_dropped",
            "error_code": "tool_dispatch_dropped",
            "failure_stage": "director_tool_lifecycle",
            "root_cause_hint": "required_tool_without_dispatch_receipt",
            "materialization_mode": "tool_dispatch_dropped",
            "run_id": "run-tool-dispatch-dropped",
            "task_id": task_id,
        }
        assert result["failure_evidence_summary"] == {
            "count": 1,
            "latest_failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        }

    def test_no_materialized_changes_preserves_primary_llm_provider_timeout(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
        task_id = str(task["id"])
        state = MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[],
        )

        result = _phase_no_materialized_changes(
            adapter,
            baseline_files={},
            board_claim_applied=False,
            can_accept_existing_scope=False,
            context={},
            direct_fallback_summary=None,
            empty_write_content_retry_summary=None,
            no_write_materialization_retry_summary=None,
            existing_contract_evidence={},
            primary_llm_summary={
                "success": False,
                "provider": "openai_compat-local",
                "model": "gemma-local",
                "error_category": "timeout",
                "error": ("HTTPConnectionPool(host='127.0.0.1', port=8000): ConnectTimeoutError: Connection timed out"),
            },
            requires_fresh_materialization=True,
            run_id="run-provider-timeout",
            target_task_id=task_id,
            task={"target_files": ["package.json"], "scope_paths": ["package.json"]},
            task_claim_session_id="",
            workspace_name=tmp_path.name,
            write_tool_evidence=False,
            state=state,
        )

        assert result is not None
        assert result["success"] is False
        assert result["error"] == "model_provider_timeout"
        assert result["error_code"] == "model_provider_timeout"
        assert result["failure_class"] == FailureClassV1.MODEL_PROVIDER_TIMEOUT.value
        assert result["responsible_layer"] == "model_provider"
        assert result["failure_stage"] == "director_llm_call"
        failure_evidence = result["failure_evidence"][0]
        assert failure_evidence["failure_class"] == FailureClassV1.MODEL_PROVIDER_TIMEOUT.value
        assert failure_evidence["responsible_layer"] == "model_provider"
        assert failure_evidence["metadata"]["materialization_mode"] == "llm_call_failed"

    def test_primary_tool_dispatch_failure_does_not_substring_match_error_text(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _primary_llm_tool_dispatch_failure,
        )

        assert (
            _primary_llm_tool_dispatch_failure(
                {
                    "success": False,
                    "error": "unrelated failure text mentions tool_dispatch_dropped only as a note",
                }
            )
            is None
        )
        assert _primary_llm_tool_dispatch_failure({"error_code": "tool-dispatch-dropped"}) == {
            "error": "tool_dispatch_dropped",
            "error_code": "tool_dispatch_dropped",
            "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
            "responsible_layer": "execution_control_plane",
            "materialization_mode": "tool_dispatch_dropped",
            "failure_stage": "director_tool_lifecycle",
            "root_cause_hint": "required_tool_without_dispatch_receipt",
            "detail": "Director role runtime reported required/native tool calls without dispatch/effect receipt.",
        }

    def test_primary_tool_dispatch_failure_from_metadata_lifecycle_receipt(self) -> None:
        """Lifecycle receipt in metadata.tool_call_lifecycle_receipt with
        dispatch_status=dropped and failure_class=tool_dispatch_dropped should
        be identified even without error/error_code fields.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _primary_llm_tool_dispatch_failure,
        )

        summary: dict[str, Any] = {
            "success": False,
            "metadata": {
                "tool_call_lifecycle_receipt": {
                    "dispatch_status": "dropped",
                    "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 1,
                    "dispatched_tool_calls_count": 0,
                    "dropped": True,
                    "ok": False,
                },
            },
        }
        result = _primary_llm_tool_dispatch_failure(summary)
        assert result is not None
        assert result["error"] == "tool_dispatch_dropped"
        assert result["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value

    def test_primary_tool_dispatch_failure_lifecycle_non_dropped_not_misclassified(self) -> None:
        """Lifecycle receipt with failure_class ≠ tool_dispatch_dropped must
        not return a tool_dispatch_dropped payload.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _primary_llm_tool_dispatch_failure,
        )

        summary: dict[str, Any] = {
            "success": False,
            "metadata": {
                "tool_call_lifecycle_receipt": {
                    "dispatch_status": "failed",
                    "failure_class": FailureClassV1.TOOL_LIFECYCLE_FAILED.value,
                    "dropped": False,
                    "ok": False,
                },
            },
        }
        assert _primary_llm_tool_dispatch_failure(summary) is None

    def test_primary_tool_dispatch_failure_from_tool_lifecycle_summary(self) -> None:
        """Already-summarized tool_lifecycle_summary with dropped_count > 0
        should be detected via project_tool_lifecycle_failure_status.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _primary_llm_tool_dispatch_failure,
        )

        summary: dict[str, Any] = {
            "success": False,
            "tool_lifecycle_summary": {
                "ok": False,
                "event_count": 1,
                "native_tool_calls_count": 1,
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "tool_result_count": 0,
                "effect_receipt_count": 0,
                "native_tool_call_names": ["write_file"],
                "dropped_count": 1,
                "failed_count": 0,
                "failure_evidence": [],
                "events": [
                    {
                        "status": "dropped",
                        "dropped": True,
                        "failed": False,
                        "native_tool_calls_count": 1,
                    },
                ],
            },
        }
        result = _primary_llm_tool_dispatch_failure(summary)
        assert result is not None
        assert result["error"] == "tool_dispatch_dropped"
        assert result["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
        assert result["failure_stage"] == "director_tool_lifecycle"

    def test_llm_stage_summary_carries_provider_failure_fields(self) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _summarize_llm_stage_result,
        )

        summary = _summarize_llm_stage_result(
            {
                "success": False,
                "error": "ConnectTimeoutError: Connection timed out",
                "raw_response": {
                    "provider": "openai_compat-local",
                    "model": "gemma-local",
                    "metadata": {
                        "error_category": "timeout",
                        "last_transport_error": "connect timeout",
                        "platform_retry_exhausted": True,
                    },
                },
            },
            stage="first_call",
        )

        assert summary["success"] is False
        assert summary["provider"] == "openai_compat-local"
        assert summary["model"] == "gemma-local"
        assert summary["error_category"] == "timeout"
        assert summary["last_transport_error"] == "connect timeout"
        assert summary["platform_retry_exhausted"] is True

    def test_llm_stage_summary_carries_tool_lifecycle_evidence(self) -> None:
        """Stage summary must preserve lifecycle evidence for later attribution.

        The Director adapter receives a compact ``primary_llm_summary`` from
        quality_gate. Tool dispatch classification must be able to consume the
        Run Ledger lifecycle evidence from that summary without reparsing error
        text or reaching back into the full raw provider response.
        """
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _summarize_llm_stage_result,
        )

        metadata = {
            "tool_call_lifecycle_receipt": {
                "dispatch_status": "dropped",
                "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
            },
            "provider": "test-provider",
        }
        lifecycle_summary = {
            "ok": False,
            "native_tool_calls_count": 1,
            "dispatched_tool_calls_count": 0,
            "dropped_count": 1,
        }

        summary = _summarize_llm_stage_result(
            {
                "success": False,
                "content": "director response",
                "raw_response": {
                    "metadata": metadata,
                    "tool_lifecycle_summary": lifecycle_summary,
                },
            },
            stage="primary",
        )

        assert summary["stage"] == "primary"
        assert summary["metadata"] is metadata
        assert summary["tool_lifecycle_summary"] is lifecycle_summary

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_test_file_keeps_placeholder_arithmetic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = adapter.task_runtime.create_task_row(
            subject="Replace placeholder Card3D unit tests",
            description="Remove trivial arithmetic placeholder tests and replace them with domain assertions.",
            metadata={
                "target_files": ["tests/unit/card-rules.test.ts"],
                "steps": ["Replace or remove existing trivial arithmetic placeholder tests"],
                "acceptance": ["No trivial arithmetic placeholder tests remain"],
            },
        )
        task_id = str(task["id"])

        async def _append_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            with test_file.open("a", encoding="utf-8") as handle:
                handle.write("test('domain rule', () => expect(resolveCardRule()).toBeDefined());\n")
            return {
                "content": "Appended replacement tests.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "tests/unit/card-rules.test.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _append_only_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-artifact-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_quality_failed"
        assert any("tests/unit/card-rules.test.ts" in item for item in result["artifact_quality_errors"])
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_repairs_npm_default_failing_test_script_before_failing_quality_gate(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Build web e2e testing workspace",
            description="Create a runnable web e2e workspace with source code and tests.",
            metadata={
                "target_files": [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ],
                "scope_paths": ["package.json", "src", "tests", "scripts"],
                "steps": ["Create package scripts", "Create source module", "Create executable tests"],
                "acceptance": ["npm test exits 0 and exercises the web e2e source module"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _gemma_like_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1",
    "start": "node src/index.js"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            if stage_labels[-1] == "quality_repair":
                package_json.write_text(
                    """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs",
    "start": "node src/index.js"
  }
}
""".strip()
                    + "\n",
                    encoding="utf-8",
                )
                src = tmp_path / "src" / "index.js"
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text(
                    "export function createWebE2eStatus() {\n  return { name: 'web-e2e-workspace', ready: true };\n}\n",
                    encoding="utf-8",
                )
                tests = tmp_path / "tests" / "index.test.js"
                tests.parent.mkdir(parents=True, exist_ok=True)
                tests.write_text(
                    "import { createWebE2eStatus } from '../src/index.js';\n"
                    "export function runWebE2eChecks() {\n"
                    "  const status = createWebE2eStatus();\n"
                    "  if (!status.ready) throw new Error('web e2e status not ready');\n"
                    "}\n",
                    encoding="utf-8",
                )
                script = tmp_path / "scripts" / "test.mjs"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text(
                    "import { runWebE2eChecks } from '../tests/index.test.js';\n"
                    "runWebE2eChecks();\n"
                    "console.log('web e2e checks passed');\n",
                    encoding="utf-8",
                )
                changed = [
                    "package.json",
                    "src/index.js",
                    "tests/index.test.js",
                    "scripts/test.mjs",
                ]
            else:
                changed = ["package.json"]
            return {
                "content": "Wrote workspace files.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": path},
                    }
                    for path in changed
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after authoritative write evidence")

        adapter._invoke_role_dialogue_with_timeout = _gemma_like_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-package-quality-repair"},
        )

        assert result["success"] is True
        assert stage_labels == ["first_call", "quality_repair"]
        assert result["tools_executed"] >= 5
        assert "package.json" in result["changed_files"]
        assert "Error: no test specified" not in (tmp_path / "package.json").read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_execute_does_not_repair_npm_default_test_script_with_manifest_only_check(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Create package manifest",
            description="Create a package.json with a runnable local test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test runs a local package manifest check"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _bad_package_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            package_json = tmp_path / "package.json"
            package_json.write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 0"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package manifest.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_package_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-package-deterministic-test-script-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        assert result["success"] is False
        assert stage_labels[0] == "first_call"
        assert "Error: no test specified" in package_text
        assert "package manifest check passed" not in package_text

    @pytest.mark.asyncio
    async def test_execute_does_not_repair_invalid_npm_test_script_with_manifest_only_check(
        self,
        tmp_path: Any,
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Create package manifest",
            description="Create a package.json with a syntactically valid npm test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test parses and exits 0"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _bad_package_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            (tmp_path / "package.json").write_text(
                """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"console.log('unterminated npm script')"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote package manifest.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "package.json"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_package_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-package-invalid-script-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
        assert result["success"] is False
        assert stage_labels[0] == "first_call"
        assert quality_errors
        assert "unterminated npm script" in package_text
        assert "package manifest check passed" not in package_text

    @pytest.mark.asyncio
    async def test_execute_repairs_typescript_return_object_property_semicolon(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Create task model summary",
            description="Create a task model summary function with valid TypeScript syntax.",
            metadata={
                "target_files": ["src/models/task.ts"],
                "scope_paths": ["src/models/task.ts"],
                "steps": ["Create task model"],
                "acceptance": ["src/models/task.ts typechecks"],
            },
        )
        task_id = str(task["id"])

        async def _bad_typescript_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                """
export function summary() {
  const lanes: Record<string, number> = {};
  return {
    total: 1,
    lanes;
  };
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _bad_typescript_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-typescript-return-object-semicolon-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task.ts").read_text(encoding="utf-8")
        assert result["success"] is True
        assert "    lanes,\n" in repaired
        assert "    lanes;\n" not in repaired
        assert "src/models/task.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_runtime_dependency_when_quality_repair_repeats_undeclared_import(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Define tenant model",
            description="Create the tenant model with runtime imports declared in package.json.",
            metadata={
                "target_files": ["src/models/tenant.model.ts"],
                "scope_paths": ["src/models/tenant.model.ts", "package.json"],
                "steps": ["Create tenant model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _repeating_gemma_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Entity, OneToMany, PrimaryColumn } from 'typeorm';\n"
                "@Entity('tenants')\n"
                "export class TenantModel {\n"
                "  @PrimaryColumn()\n"
                "  id: string;\n"
                "\n"
                "  @OneToMany(() => Task, (task) => task.tenant)\n"
                "  tasks: Task[];\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _repeating_gemma_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-undeclared-import-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        tenant_text = (tmp_path / "src" / "models" / "tenant.model.ts").read_text(encoding="utf-8")
        source_tools = _source_tools_from_tool_results(result["tool_results"])
        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert '"typeorm": "^0.3.20"' in package_text
        assert "from 'typeorm'" not in tenant_text
        assert "@Entity" not in tenant_text
        assert "tasks: unknown[] = [];" in tenant_text
        assert "deterministic_typeorm_model_normalization_repair" in source_tools
        assert "src/models/tenant.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_mongoose_runtime_dependency_for_audit_log_model(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Tenant Context & Audit Log Middleware",
            description="Implement immutable audit log model with tenant context.",
            metadata={
                "target_files": ["src/models/auditlog.ts"],
                "scope_paths": ["src/models/auditlog.ts", "package.json"],
                "steps": ["Create audit log model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _mongoose_audit_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "models" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Schema, model, Document } from 'mongoose';\n\n"
                "export interface IAuditLog extends Document {\n"
                "  actor_id: string;\n"
                "  tenant_id: string;\n"
                "  action: 'CREATE' | 'UPDATE' | 'DELETE';\n"
                "  target_entity: string;\n"
                "  delta: Record<string, unknown>;\n"
                "  timestamp: Date;\n"
                "}\n\n"
                "const AuditLogSchema = new Schema<IAuditLog>({\n"
                "  actor_id: { type: String, required: true },\n"
                "  tenant_id: { type: String, required: true },\n"
                "  action: { type: String, enum: ['CREATE', 'UPDATE', 'DELETE'], required: true },\n"
                "  target_entity: { type: String, required: true },\n"
                "  delta: { type: Object, required: true },\n"
                "  timestamp: { type: Date, default: Date.now },\n"
                "});\n\n"
                "export const AuditLog = model<IAuditLog>('AuditLog', AuditLogSchema);\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _mongoose_audit_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-mongoose-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"mongoose":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/models/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_uuid_and_winston_runtime_dependencies_for_audit_log(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        (tmp_path / "package.json").write_text(
            """
{
  "name": "workflow-audit-service",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {}
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Immutable Audit Logging Implementation",
            description="Create a TypeScript audit log service with stable event IDs and structured logging.",
            metadata={
                "target_files": ["src/services/auditlog.ts"],
                "scope_paths": ["src/services/auditlog.ts", "package.json"],
                "steps": ["Create the audit log service"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _audit_log_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "services" / "auditlog.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { v4 as uuidv4 } from 'uuid';\n"
                "import winston from 'winston';\n\n"
                "export interface AuditEvent {\n"
                "  id: string;\n"
                "  action: string;\n"
                "  targetId: string;\n"
                "  createdAt: string;\n"
                "}\n\n"
                "const logger = winston.createLogger({\n"
                "  transports: [new winston.transports.Console()],\n"
                "});\n\n"
                "export function recordAuditEvent(action: string, targetId: string): AuditEvent {\n"
                "  const event = { id: uuidv4(), action, targetId, createdAt: new Date().toISOString() };\n"
                "  logger.info('audit.event', event);\n"
                "  return event;\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote audit log service.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/services/auditlog.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _audit_log_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-audit-log-runtime-dependency-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/services/auditlog.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert quality_errors == []
        assert '"uuid":' in package_text
        assert '"winston":' in package_text
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "package.json" in result["changed_files"]
        assert "src/services/auditlog.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_tenant_middleware_escaped_newline_and_node_types(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        (tmp_path / "package.json").write_text(
            """
{
  "name": "tenant-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "express": "^4.18.2"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Tenant Context Middleware",
            description="Create request-scoped tenant context middleware for an Express service.",
            metadata={
                "target_files": ["src/middleware/auth.ts"],
                "scope_paths": ["src/middleware/auth.ts", "package.json"],
                "steps": ["Create tenant middleware"],
                "acceptance": ["TypeScript exports remain reachable and Node builtin typings are declared"],
            },
        )
        task_id = str(task["id"])
        stage_labels: list[str] = []

        async def _gemma_escaped_newline_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_labels.append(str(kwargs.get("stage_label") or ""))
            target = tmp_path / "src" / "middleware" / "auth.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { Request, Response, NextFunction } from 'express';\n"
                "import { AsyncLocalStorage } from 'async_hooks';\n\n"
                "export interface TenantContext {\n"
                "  tenantId: string;\n"
                "}\n\n"
                "// Context for storing tenant information across the request lifecycle\\n"
                "export const tenantContext = new AsyncLocalStorage<TenantContext>();\n\n"
                "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void {\n"
                "  const tenantId = String(req.headers['x-tenant-id'] || 'default');\n"
                "  tenantContext.run({ tenantId }, () => next());\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant middleware.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/middleware/auth.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _gemma_escaped_newline_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-tenant-middleware-escaped-newline-repair"},
        )

        package_payload = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        repaired = (tmp_path / "src" / "middleware" / "auth.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/middleware/auth.ts", "package.json"],
        )
        source_tools: list[str] = []
        for item in result["tool_results"]:
            if not isinstance(item, dict):
                continue
            raw_tool_result = item.get("result")
            if isinstance(raw_tool_result, dict):
                source_tools.append(str(raw_tool_result.get("source_tool") or ""))

        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert "lifecycle\\nexport const tenantContext" not in repaired
        assert "\nexport const tenantContext" in repaired
        assert package_payload["devDependencies"]["@types/node"] == "^22.10.0"
        assert quality_errors == []
        assert "deterministic_typescript_escaped_newline_repair" in source_tools
        assert "deterministic_runtime_dependency_repair" in source_tools
        assert "src/middleware/auth.ts" in result["changed_files"]
        assert "package.json" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_repairs_zod_type_class_name_collision(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        (tmp_path / "package.json").write_text(
            """
{
  "name": "task-definition-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  },
  "dependencies": {
    "zod": "^3.23.8"
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        _write_substantive_node_test_script(tmp_path)
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Task Definition Model",
            description="Create zod-backed task definition model.",
            metadata={
                "target_files": ["src/models/task_definition.ts"],
                "scope_paths": ["src/models/task_definition.ts", "package.json"],
                "steps": ["Create task definition schema and model"],
                "acceptance": ["TypeScript typecheck accepts schema and class exports"],
            },
        )
        task_id = str(task["id"])

        async def _zod_collision_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "task_definition.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { z } from 'zod';\n\n"
                "export const TaskDefinitionSchema = z.object({\n"
                "  id: z.string().uuid().optional(),\n"
                "  name: z.string().min(1),\n"
                "});\n\n"
                "type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;\n\n"
                "export class TaskDefinition {\n"
                "  constructor(public data: TaskDefinition) {}\n\n"
                "  static validate(data: any): TaskDefinition {\n"
                "    const result = TaskDefinitionSchema.safeParse(data);\n"
                "    if (!result.success) throw new Error('Validation failed');\n"
                "    return new TaskDefinition(result.data);\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote task definition model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/task_definition.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _zod_collision_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-zod-type-class-collision-repair"},
        )

        repaired = (tmp_path / "src" / "models" / "task_definition.ts").read_text(encoding="utf-8")
        quality_errors = scan_workspace_artifact_quality(
            str(tmp_path),
            relative_paths=["src/models/task_definition.ts", "package.json"],
        )
        source_tools = _source_tools_from_tool_results(result["tool_results"])

        assert result["success"] is True, result
        assert "type TaskDefinitionData = z.infer<typeof TaskDefinitionSchema>;" in repaired
        assert "constructor(public data: TaskDefinitionData)" in repaired
        assert quality_errors == []
        assert "deterministic_typescript_zod_type_class_collision_repair" in source_tools
        assert "src/models/task_definition.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_fails_missing_declared_target_without_runtime_fabrication(
        self,
        tmp_path: Any,
    ) -> None:
        existing_task = tmp_path / "src" / "models" / "task.ts"
        existing_task.parent.mkdir(parents=True, exist_ok=True)
        existing_task.write_text(
            "export interface TaskModel {\n  id: string;\n  tenantId: string;\n  title: string;\n}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Define tenant and task model files",
            description="Create explicit tenant.model.ts and task.model.ts model files.",
            metadata={
                "target_files": ["src/models/tenant.model.ts", "src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant and task model files"],
                "acceptance": ["Both declared target model files exist"],
            },
        )
        task_id = str(task["id"])

        async def _tenant_only_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target = tmp_path / "src" / "models" / "tenant.model.ts"
            target.write_text(
                "export interface TenantModel {\n  id: string;\n  name: string;\n}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote tenant model.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/models/tenant.model.ts"},
                    }
                ],
            }

        adapter._invoke_role_dialogue_with_timeout = _tenant_only_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-missing-target-nearby-repair"},
        )

        repaired_task = tmp_path / "src" / "models" / "task.model.ts"
        assert result["success"] is False
        assert result["error_code"] == "director_materialization_quality_failed"
        assert result["artifact_quality_errors"] == [
            "Artifact quality scan failed: declared target file missing 'src/models/task.model.ts'"
        ]
        assert repaired_task.exists() is False
        assert existing_task.read_text(encoding="utf-8") == (
            "export interface TaskModel {\n  id: string;\n  tenantId: string;\n  title: string;\n}\n"
        )

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_file_has_no_domain_signal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "fish" / "arena.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        task = adapter.task_runtime.create_task_row(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["No generic unrelated implementation remains"],
            },
        )
        task_id = str(task["id"])

        async def _write_unrelated_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            target.write_text(
                "export function calculateInvoiceTotal(values: number[]): number {\n"
                "  return values.reduce((total, value) => total + value, 0);\n"
                "}\n",
                encoding="utf-8",
            )
            return {
                "content": "Wrote an implementation.",
                "success": True,
                "tool_results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {"path": "src/fish/arena.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _write_unrelated_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-semantic-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_semantic_quality_failed"
        assert "no project-domain signal" in result["semantic_quality_error"]
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_semantic_quality_failed"

    @pytest.mark.asyncio
