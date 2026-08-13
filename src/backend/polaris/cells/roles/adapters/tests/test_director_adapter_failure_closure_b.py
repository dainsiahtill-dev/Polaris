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






class TestDirectorFailureClosureB:
    async def test_execute_repairs_semantic_quality_failure_before_final_fail(self, tmp_path: Any) -> None:
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
                "acceptance": ["Arena code contains fish domain behavior"],
            },
        )
        task_id = str(task["id"])
        stages: list[str] = []
        repair_contexts: list[dict[str, Any]] = []

        async def _repairing_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args
            stage_label = str(kwargs.get("stage_label") or "primary")
            stages.append(stage_label)
            raw_context = kwargs.get("context")
            context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
            if stage_label.startswith("quality_repair"):
                repair_contexts.append(context)
                target.write_text(
                    "export function renderFishArena(predators: number, prey: number): string {\n"
                    "  const balance = prey - predators;\n"
                    "  return `fish arena predator prey balance ${balance}`;\n"
                    "}\n",
                    encoding="utf-8",
                )
                return {
                    "content": "Rewrote arena implementation.",
                    "success": True,
                    "tool_results": [
                        {
                            "tool": "write_file",
                            "success": True,
                            "result": {"path": "src/fish/arena.ts", "file": "src/fish/arena.ts"},
                        }
                    ],
                }

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
                        "result": {"path": "src/fish/arena.ts", "file": "src/fish/arena.ts"},
                    }
                ],
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct fallback should not run after workspace diff evidence")

        adapter._invoke_role_dialogue_with_timeout = _repairing_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-semantic-quality-repair"},
        )

        assert result["success"] is True
        assert stages.count("quality_repair") == 1
        assert "fish arena predator prey" in target.read_text(encoding="utf-8")
        assert repair_contexts[0]["director_quality_repair"]["artifact_quality_errors"]
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert len(adapter_result.get("semantic_quality_repair_attempts") or []) == 1

    @pytest.mark.asyncio
    async def test_execute_fails_autofix_declared_scope_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )
        task_id = str(task["id"])

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct runtime provider bypass must not be called")

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-scaffold"},
        )

        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        assert target.exists() is False
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
        assert adapter_result.get("failure_class") == "INCOMPLETE_MATERIALIZATION"
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"
        assert adapter_result.get("direct_fallback", {}).get("skipped_reason") == "runtime_provider_bypass_removed"

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_scope_after_read_only_mutation_guard(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n".join(
                [
                    "import http from 'http';",
                    "",
                    "export const server = http.createServer((_req, res) => {",
                    "  res.writeHead(200, { 'Content-Type': 'application/json' });",
                    "  res.end(JSON.stringify({ status: 'ok' }));",
                    "});",
                    "",
                    "export function startServer(port = 3000): void {",
                    "  server.listen(port);",
                    "}",
                    "",
                    "export default server;",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
                "steps": ["Implement src/server/app.ts"],
                "acceptance": ["npm run build verifies src/server/app.ts"],
            },
        )
        task_id = str(task["id"])

        async def _read_only_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "mutation requested but no write tool invocation in decision batch."
                ),
            }

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct runtime provider bypass must not be called")

        adapter._invoke_role_dialogue_with_timeout = _read_only_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-existing-scope-after-read-only"},
        )

        assert result["success"] is False
        assert result["error"] == "director_no_materialized_changes"
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("existing_contract_evidence", {}).get("ok") is True
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
        assert adapter_result.get("primary_llm", {}).get("error", "").startswith("TransactionKernel execution failed")
        assert adapter_result.get("direct_fallback", {}).get("skipped_reason") == "runtime_provider_bypass_removed"

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_scope_after_read_write_batch_violation(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "session-store.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export class SessionStore {\n"
            "  private readonly rows = new Map<string, string>();\n"
            "  save(roomId: string, value: string): void { this.rows.set(roomId, value); }\n"
            "  load(roomId: string): string | undefined { return this.rows.get(roomId); }\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Extend multiplayer session persistence",
            description="Implement multiplayer session persistence.",
            metadata={
                "phase": "core",
                "scope_paths": ["src/server/session-store.ts"],
                "target_files": ["src/server/session-store.ts"],
                "acceptance": ["src/server/session-store.ts exposes persistence methods"],
            },
        )
        task_id = str(task["id"])

        async def _batch_contract_violation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {
                "content": "",
                "success": False,
                "error": (
                    "TransactionKernel execution failed: single_batch_contract_violation: "
                    "Cannot mix Read tools (read_file) and Write tools (write_file) in the same parallel batch."
                ),
            }

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _batch_contract_violation  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-existing-scope-after-batch-violation"},
        )

        assert result["success"] is False
        assert result["error"] == "director_no_materialized_changes"
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("existing_contract_evidence", {}).get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_scope_after_successful_no_diff_response(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const serverReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
            },
        )
        task_id = str(task["id"])

        async def _successful_no_diff_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "Verified existing backend entrypoint.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _successful_no_diff_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-existing-scope-after-successful-no-diff"},
        )

        assert result["success"] is False
        assert result["error"] == "director_no_materialized_changes"
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"

    @pytest.mark.asyncio
    async def test_execute_preflights_existing_verification_scope(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "room-state.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "export interface RoomStateRecord { id: string; roomId: string; }\n"
            "export function validateRoomStateRecord(record: RoomStateRecord): string[] {\n"
            "  const failures: string[] = [];\n"
            "  if (!record.id) failures.push('missing id');\n"
            "  if (!record.roomId) failures.push('missing roomId');\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        target = tmp_path / "tests" / "integration" / "multiplayer-flow.test.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "import { validateRoomStateRecord } from '../../src/server/room-state';\n"
            "\n"
            "export function runMultiplayerFlowIntegrationChecks(): string[] {\n"
            "  const failures: string[] = [];\n"
            "  const issues = validateRoomStateRecord({ id: 'room-1', roomId: 'room-1' });\n"
            "  if (issues.length > 0) failures.push(issues.join(','));\n"
            "  return failures;\n"
            "}\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Verify existing multiplayer card integration tests",
            description="Verify multiplayer card integration tests according to acceptance criteria.",
            metadata={
                "phase": "verify",
                "qa_rework_verification_only": True,
                "scope_paths": ["tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["No placeholder tests remain"],
            },
        )
        task_id = str(task["id"])

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing verification scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-existing-verification-scope-preflight"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "preflight_verified_existing_workspace_scope"
        raw_evidence = result.get("existing_contract_evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        assert evidence.get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_does_not_preflight_accept_overstrict_node_test_contract(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "import { readFileSync } from 'node:fs';\n"
            "const text = readFileSync('src/analytics/match-analytics.ts', 'utf8');\n"
            "if (!/validate[A-Za-z]+Record/.test(text)) {\n"
            "  throw new Error('missing validation contract in src/analytics/match-analytics.ts');\n"
            "}\n",
            encoding="utf-8",
        )
        source_paths = [
            "src/analytics/match-analytics.ts",
            "src/animation/card-animations.ts",
            "src/assets/card-assets.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/client/three-scene.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/lobby/lobby-service.ts",
            "src/physics/table-layout.ts",
            "src/server/app.ts",
            "src/server/matchmaking.ts",
            "src/server/moderation.ts",
            "src/server/realtime-gateway.ts",
            "src/server/room-state.ts",
            "src/shared/protocol.ts",
            "src/shared/telemetry.ts",
        ]
        for index, rel_path in enumerate(source_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"export const module{index}Ready = true;\n", encoding="utf-8")

        required_test_paths = [
            "tests/unit/card-rules.test.ts",
            "tests/unit/deck-builder.test.ts",
            "tests/integration/multiplayer-flow.test.ts",
            "tests/integration/realtime-sync.test.ts",
            "tests/e2e/card-table-3d.test.ts",
        ]
        for index, rel_path in enumerate(required_test_paths):
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "import { module0Ready } from '../../src/analytics/match-analytics';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!module0Ready) failures.push('module not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Strengthen multiplayer card integration test runner",
            description="Replace the brittle scripts/test.mjs validation-contract gate with substantive test checks.",
            metadata={
                "phase": "verify",
                "scope_paths": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["npm run test verifies the Card3D behavior test suite"],
            },
        )
        task_id = str(task["id"])
        dialogue_calls = 0

        async def _quality_repair_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal dialogue_calls
            del args, kwargs
            dialogue_calls += 1
            return {"content": "", "success": False, "error": "quality_repair_required"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _quality_repair_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-node-test-script-contract-repair"},
        )

        rewritten = script.read_text(encoding="utf-8")
        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert dialogue_calls >= 1
        assert any(signal.get("code") == "incomplete_materialization" for signal in result["decision_signals"])
        assert "missing validation contract" in rewritten

    def test_detects_legacy_overstrict_node_export_contract(self) -> None:
        legacy_script = (
            "for (const file of sourceFiles) {\n"
            "  const text = readFileSync(file, 'utf8');\n"
            "  if (!/export\\s+(class|function|const|interface|type)/.test(text)) {\n"
            "    throw new Error('missing export in ' + file);\n"
            "  }\n"
            "}\n"
        )

        assert _is_overstrict_node_test_script_contract(legacy_script) is True
        assert _is_overstrict_node_test_script_contract(_build_substantive_node_test_script()) is False

    def test_narrative_npm_acceptance_is_not_step_verify_command(self) -> None:
        from polaris.cells.roles.adapters.internal.director.contract_verify import (
            resolve_contract_step_verify_command,
        )

        assert (
            resolve_contract_step_verify_command(
                {"acceptance": ["npm run test verifies the Card3D behavior test suite"], "language": "typescript"}
            )
            == ""
        )
        assert (
            resolve_contract_step_verify_command(
                {"acceptance": ["`npm run test` verifies the Card3D behavior test suite"], "language": "typescript"}
            )
            == "npm run test"
        )

    def test_downstream_validation_hygiene_defers_npm_test_step_verify(self) -> None:
        from polaris.cells.roles.adapters.internal.director.contract_verify import (
            resolve_contract_step_verify_command,
        )

        context = {
            "acceptance": ["`npm test` passes the smoke suite"],
            "language": "javascript",
            "metadata": {
                "validation_contract_hygiene": {
                    "reason": "test_acceptance_deferred_to_downstream_validation_task",
                    "downstream_validation_targets": ["tests/smoke.test.js"],
                }
            },
        }

        assert resolve_contract_step_verify_command(context) == ""

    def test_explicit_project_test_verify_is_owned_by_declared_test_task(self) -> None:
        from polaris.cells.roles.adapters.internal.director.contract_verify import (
            resolve_contract_step_verify,
        )

        context = {
            "construction_step": {"verify": "npm run test"},
            "language": "typescript",
            "project_declared_target_files": [
                "src/index.ts",
                "tests/simulation.test.ts",
            ],
        }

        source_resolution = resolve_contract_step_verify(
            context,
            task={"target_files": ["src/index.ts"]},
        )
        test_resolution = resolve_contract_step_verify(
            context,
            task={"target_files": ["tests/simulation.test.ts"]},
        )

        assert source_resolution.command == ""
        assert source_resolution.disposition == "deferred"
        assert source_resolution.reason == "project_test_targets_not_owned_by_current_task"
        assert source_resolution.downstream_validation_targets == ("tests/simulation.test.ts",)
        assert test_resolution.command == "npm run test"
        assert test_resolution.disposition == "run"

    def test_substantive_node_test_script_accepts_named_export_blocks(self, tmp_path: Any) -> None:
        script = tmp_path / "scripts" / "test.mjs"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(_build_substantive_node_test_script(), encoding="utf-8")

        for relative in [
            "src/server/app.ts",
            "src/client/three-scene.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/server/realtime-gateway.ts",
            "src/server/matchmaking.ts",
            "src/server/room-state.ts",
            "src/server/session-store.ts",
            "src/server/moderation.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/shared/protocol.ts",
            "src/shared/player-presence.ts",
            "src/shared/telemetry.ts",
            "src/assets/card-assets.ts",
            "src/animation/card-animations.ts",
            "src/auth/session-auth.ts",
        ]:
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "src/server/app.ts":
                target.write_text(
                    "const server = { listen() {} };\n"
                    "const sessions = new Map<string, string>();\n"
                    "export { server, sessions };\n",
                    encoding="utf-8",
                )
            else:
                stem = target.stem.replace("-", "_")
                target.write_text(f"export const {stem}Ready = true;\n", encoding="utf-8")

        for index, relative in enumerate(
            [
                "tests/unit/card-rules.test.ts",
                "tests/unit/deck-builder.test.ts",
                "tests/integration/multiplayer-flow.test.ts",
                "tests/integration/realtime-sync.test.ts",
                "tests/e2e/card-table-3d.test.ts",
            ]
        ):
            test_file = tmp_path / relative
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(
                "import { card_catalogReady } from '../../src/game/card-catalog';\n"
                f"export function runCard3DChecks{index}(): string[] {{\n"
                "  const failures: string[] = [];\n"
                "  if (!card_catalogReady) failures.push('catalog not ready');\n"
                "  return failures;\n"
                "}\n",
                encoding="utf-8",
            )

        result = subprocess.run(
            ["node", "scripts/test.mjs", "--watch=false"],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "card3d behavior checks passed" in result.stdout

    def test_deterministic_patch_residue_cleanup_removes_declared_marker(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "export const cardAssetsReady = true;\n"
            ">>>> REPLACE src/assets/card-assets.ts\n"
            "export const assetCount = 52;\n",
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)

        results = run_patch_residue_cleanup(
            adapter,
            task={
                "metadata": {
                    "target_files": ["src/assets/card-assets.ts"],
                    "scope_paths": ["src/assets/card-assets.ts"],
                }
            },
            task_id="PM-CARD3D-ASSETS-18",
        )

        cleaned = target.read_text(encoding="utf-8")
        assert len(results) == 1
        assert results[0]["tool"] == "edit_file"
        assert results[0]["result"]["source_tool"] == "deterministic_patch_residue_cleanup"
        assert results[0]["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
        assert results[0]["result"]["repair_kernel"]["metadata"]["requires_revalidation"] is True
        assert ">>>> REPLACE" not in cleaned
        assert "export const cardAssetsReady = true;" in cleaned
        assert "export const assetCount = 52;" in cleaned
        assert _remove_patch_residue_lines(cleaned) == cleaned

    def test_deterministic_patch_residue_cleanup_ignores_unscoped_files(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "assets" / "card-assets.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = "export const cardAssetsReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n"
        target.write_text(original, encoding="utf-8")
        adapter = _make_adapter(tmp_path)

        results = run_patch_residue_cleanup(
            adapter,
            task={"metadata": {"target_files": ["src/server/app.ts"]}},
            task_id="PM-CARD3D-SERVER-01",
        )

        assert results == []
        assert target.read_text(encoding="utf-8") == original

    @pytest.mark.asyncio
    async def test_execute_completes_scaffold_marker_cleanup_without_llm_call(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_runtime.create_task_row(
            subject="Clean deterministic scaffold residue",
            description="Remove deterministic scaffold residue before QA.",
            metadata={
                "target_files": ["src/server/app.ts"],
                "scope_paths": ["src/server/app.ts"],
                "steps": ["Clean deterministic scaffold residue"],
                "acceptance": ["Declared files contain no audit-seed or deterministic scaffold markers"],
                "autofix_reason": "deterministic_scaffold_residue_cleanup",
            },
        )
        task_id = str(task["id"])

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("cleanup task should complete without invoking Gemma")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-scaffold-marker-cleanup"},
        )

        assert result["success"] is True
        assert result["tools_executed"] >= 1
        assert "src/server/app.ts" in result["changed_files"]
        source_text = source.read_text(encoding="utf-8")
        assert "audit-seed" not in source_text
        assert "planning scenario" not in source_text

    @pytest.mark.asyncio
    async def test_ready_queue_fallback_claim_preserves_selected_task_identity(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "combat" / "combat-system.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const combatReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        combat = adapter.task_runtime.create_task_row(
            subject="Audit turn based combat system scope",
            description="Materialize combat scope.",
            metadata={
                "external_task_id": "PM-AUTO-COMBAT",
                "source_task_id": "PM-AUTO-COMBAT",
                "target_files": ["src/combat/combat-system.ts"],
            },
        )
        combat_id = str(combat["id"])

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id="PM-AUTO-AI",
            input_data={"task_id": "PM-AUTO-AI"},
            context={"run_id": "run-director-identity"},
        )

        assert result["success"] is True
        updated = adapter.task_runtime.get_task(combat_id)
        assert updated is not None
        metadata_raw = updated.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution")
        runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
        assert metadata["external_task_id"] == "PM-AUTO-COMBAT"
        assert runtime_execution["external_task_id"] == "PM-AUTO-COMBAT"

    @pytest.mark.asyncio
    async def test_task_market_claim_materializes_requested_external_id_without_ready_queue_fallback(
        self, tmp_path: Any
    ) -> None:
        existing_target = tmp_path / "worker_3.py"
        existing_target.write_text('MARKER = "D4-SAT-3"\n', encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        sibling = adapter.task_runtime.create_task_row(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
                "scope_paths": ["worker_3.py"],
            },
        )
        sibling_id = str(sibling["id"])

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id="D4-SAT-2",
            input_data={
                "task_id": "D4-SAT-2",
                "subject": "Create independent saturation Python file 2",
                "description": "Create worker_2.py.",
                "input": "Create worker_2.py with MARKER D4-SAT-2.",
                "target_files": ["worker_2.py"],
                "scope_paths": ["worker_2.py"],
                "metadata": {
                    "task_market_task_id": "D4-SAT-2",
                    "pm_task_id": "D4-SAT-2",
                    "source": "runtime.task_market.pending_exec",
                },
            },
            context={"run_id": "run-director-task-market-exact"},
        )

        assert result["task_id"] != sibling_id
        materialized = adapter.task_runtime.get_task("D4-SAT-2")
        assert materialized is not None
        assert materialized["metadata"]["external_task_id"] == "D4-SAT-2"
        sibling_after = adapter.task_runtime.get_task(sibling_id)
        assert sibling_after is not None
        assert sibling_after["status"] != "completed"

    def test_claim_external_task_id_prefers_selected_task_source(self) -> None:
        assert (
            _resolve_claim_external_task_id(
                {
                    "id": 4,
                    "metadata": {
                        "external_task_id": "PM-AUTO-AI",
                        "source_task_id": "PM-AUTO-COMBAT",
                    },
                },
                "PM-AUTO-AI",
            )
            == "PM-AUTO-COMBAT"
        )

    def test_get_task_resolves_external_task_id_before_queue_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        adapter.task_runtime.create_task_row(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
            },
        )
        adapter.task_runtime.create_task_row(
            subject="Create independent saturation Python file 2",
            description="Create worker_2.py.",
            metadata={
                "external_task_id": "D4-SAT-2",
                "source_task_id": "D4-SAT-2",
                "target_files": ["worker_2.py"],
            },
        )

        selected = adapter._get_task("D4-SAT-2")

        assert selected is not None
        metadata_raw = selected.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        assert metadata["external_task_id"] == "D4-SAT-2"
        assert metadata["target_files"] == ["worker_2.py"]

    @pytest.mark.asyncio
    async def test_execute_rejects_existing_autofix_scaffold_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            'export const gameViewScaffoldVersion = "deterministic-declared-scope-v1";\n',
            encoding="utf-8",
        )
        task = adapter.task_runtime.create_task_row(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )
        task_id = str(task["id"])

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=task_id,
            input_data={"task_id": task_id},
            context={"run_id": "run-director-existing-scaffold"},
        )

        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        updated = adapter.task_runtime.get_task(task_id)
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
        assert adapter_result.get("failure_class") == "INCOMPLETE_MATERIALIZATION"
        assert adapter_result.get("modified_files") == []
        assert adapter_result.get("primary_llm", {}).get("error") == "role_model_not_configured"

    def test_text_patch_mode_requests_parseable_file_blocks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T"}, text_patch_mode=True)
        assert "当前运行时要求纯文本补丁" in msg
        assert "relative/path.ext" in msg
        assert "path/to/file.py" not in msg


# ---------------------------------------------------------------------------
# Existing workspace evidence
# ---------------------------------------------------------------------------


