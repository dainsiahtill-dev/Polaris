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






class TestQualityRepairMissingTargetContractA:
    def test_missing_declared_targets_derived_from_workspace(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.js").write_text("x\n", encoding="utf-8")
        (tmp_path / "index.html").write_text("<html></html>\n", encoding="utf-8")
        task = {"target_files": ["index.html", "src/main.js", "src/styles.css", "package.json"]}
        missing = _missing_declared_target_files(task, str(tmp_path))
        assert missing == ["src/styles.css", "package.json"]

    def test_missing_targets_case_insensitive(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_declared_target_files,
        )

        (tmp_path / "README.md").write_text("r\n", encoding="utf-8")
        task = {"target_files": ["readme.md"]}
        assert _missing_declared_target_files(task, str(tmp_path)) == []

    def test_satisfied_declared_target_missing_errors_are_filtered(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_satisfied_declared_target_missing_errors,
        )

        (tmp_path / "records.json").write_text("[]\n", encoding="utf-8")

        errors = _filter_satisfied_declared_target_missing_errors(
            [
                "Artifact quality scan failed: declared target file missing 'records.json'",
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx",
            ],
            str(tmp_path),
        )

        assert errors == ["Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"]

    @pytest.mark.asyncio
    async def test_quality_repair_retry_defers_out_of_scope_unresolved_relative_import(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                self.repair_message = message
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.tsx").write_text(
            "import { router } from './router';\n",
            encoding="utf-8",
        )
        task = {"target_files": ["src/main.tsx"]}
        adapter = _Adapter()

        tool_results, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create the React app shell.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx",
            ],
            changed_files=["src/main.tsx"],
        )

        # src/router.tsx is owned by another task: the unresolved-import
        # target is deferred with scope evidence, never authorized for writes.
        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success"] is False
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["missing_target_files"] == []
        assert summary["repair_target_files"] == []
        assert summary["llm_fallback_blocked"] is True
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["reason"] == "missing_workspace_file_outside_current_task_target_files"
        assert scope_filter["task_declared_write_targets"] == ["src/main.tsx"]
        assert scope_filter["out_of_scope_repair_target_files"] == ["src/router.tsx"]
        assert adapter.repair_message == ""
        assert not (tmp_path / "src" / "router.tsx").exists()

    @pytest.mark.asyncio
    async def test_quality_repair_does_not_invent_missing_typescript_export(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        llm_calls: list[dict[str, Any]] = []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                llm_calls.append({"message": message, "context": context})
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "verify.ts").write_text("export const verify = () => true;\n", encoding="utf-8")
        (tmp_path / "tests" / "verify.test.ts").write_text(
            "import { runVerification } from '../src/verify.js';\nvoid runVerification;\n",
            encoding="utf-8",
        )
        quality_error = (
            "Artifact quality scan failed: unresolved import symbol 'runVerification' "
            "from '../src/verify.js' in tests/verify.test.ts (sibling module does not define it)"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/verify.ts", "tests/verify.test.ts"]},
            target_task_id="TASK-2",
            run_id="run-ts-semantic-repair",
            context={},
            original_message="Repair TypeScript verification exports.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["src/verify.ts", "tests/verify.test.ts"],
        )

        assert tool_results == []
        assert llm_calls == []
        assert summary["stage"] == "runtime_plan_probe_unplannable"
        assert summary["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert summary["interface_discrepancy_evidence"]["covered_unplannable"] is True
        assert summary["write_tool_evidence"] is False
        repaired = (tmp_path / "src" / "verify.ts").read_text(encoding="utf-8")
        assert repaired == "export const verify = () => true;\n"

    @pytest.mark.asyncio
    async def test_quality_repair_interface_discrepancy_retry_requires_final_request_evidence(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path,
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.internal.director.quality_gate.has_materialization_quality_runtime_repair_coverage",
            lambda errors: False,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "verify.ts").write_text("export const verify = () => true;\n", encoding="utf-8")
        (tmp_path / "tests" / "verify.test.ts").write_text(
            "import { runVerification } from '../src/verify.js';\nvoid runVerification;\n",
            encoding="utf-8",
        )
        task = {
            "id": "TASK-2",
            "target_files": ["src/verify.ts", "tests/verify.test.ts"],
            "metadata": {
                "module_interface_contract": {
                    "modules": [
                        {
                            "path": "src/verify.ts",
                            "actual_public_symbols": ["verify"],
                            "planned_public_symbols": ["verify"],
                        },
                        {
                            "path": "tests/verify.test.ts",
                            "consumed_symbols": ["runVerification"],
                        },
                    ]
                }
            },
        }
        quality_error = (
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/verify.ts(1,1): error TS9999: custom interface discrepancy retry failure"
        )
        interface_discrepancy_evidence = {
            "schema_version": "director.interface_discrepancy_receipt.v1",
            "reason": "coverage_matched_but_unplannable",
            "recommended_owner": "director",
            "recommended_route": "director_retry_with_interface_discrepancy_context",
            "director_retry_allowed": True,
            "llm_fallback_blocked": False,
            "interface_delta": {
                "schema_version": "director.interface_delta.v1",
                "contract_present": True,
                "requested_symbols": ["runVerification"],
            },
            "triage_summary": {
                "schema_version": "director.interface_discrepancy_triage.v1",
                "reason": "director_local_retry_with_interface_delta",
            },
        }
        retry_context = {
            "director_interface_discrepancy_retry": {
                "authorized": True,
                "recommended_owner": "director",
                "recommended_route": "director_retry_with_interface_discrepancy_context",
                "reason": "coverage_matched_but_unplannable",
                "interface_discrepancy_evidence": interface_discrepancy_evidence,
            }
        }

        adapter = _Adapter()
        await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id="TASK-2",
            run_id="run-interface-discrepancy-retry",
            context=retry_context,
            original_message="Repair TypeScript verification exports.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["src/verify.ts", "tests/verify.test.ts"],
        )

        assert "interface_discrepancy_context" in adapter.repair_context["required_evidence"]
        assert (
            adapter.repair_context["director_interface_discrepancy_retry"]["recommended_route"]
            == "director_retry_with_interface_discrepancy_context"
        )
        evidence = adapter.repair_context["director_interface_discrepancy_retry"]["interface_discrepancy_evidence"]
        assert evidence["director_retry_allowed"] is True
        assert evidence["interface_delta"]["contract_present"] is True
        assert evidence["triage_summary"]["reason"] == "director_local_retry_with_interface_delta"

    @pytest.mark.asyncio
    async def test_quality_repair_skips_llm_when_factory_deadline_is_too_close(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("quality repair must not start an LLM call past the factory deadline")

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("export const ok = true;\n", encoding="utf-8")

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/main.ts"]},
            target_task_id="TASK-DEADLINE",
            run_id="run-deadline",
            context={
                "metadata": {
                    "factory_run_deadline_epoch_seconds": 1,
                    "factory_run_deadline_safety_seconds": 27,
                }
            },
            original_message="Repair TypeScript project.",
            llm_call_timeout=180,
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run build): "
                "src/main.ts(1,1): error TS2304: Cannot find name 'missingSymbol'."
            ],
            changed_files=["src/main.ts"],
        )

        assert tool_results == []
        assert summary["error_code"] == "quality_repair_deadline_insufficient"
        assert summary["deadline_decision"]["can_start"] is False
        assert summary["deadline_decision"]["reason"] == "factory_deadline_insufficient"

    @pytest.mark.asyncio
    async def test_quality_repair_honors_director_wave_deadline_when_run_deadline_is_absent(
        self,
        tmp_path: Path,
    ) -> None:
        """Late local repair must preserve Director settlement/verifier reserve."""
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("quality repair must not outlive the Director wave")

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.ts").write_text("export const ok = true;\n", encoding="utf-8")

        with patch(
            "polaris.cells.roles.adapters.internal.director.quality_gate.time.time",
            return_value=100.0,
        ):
            tool_results, summary = await _run_materialization_quality_repair_retry(
                _Adapter(),
                task={"target_files": ["src/main.ts"]},
                target_task_id="TASK-DIRECTOR-DEADLINE",
                run_id="run-director-deadline",
                context={
                    "metadata": {
                        "factory_director_execution_deadline_epoch_seconds": 120.0,
                    }
                },
                original_message="Repair TypeScript project.",
                llm_call_timeout=180,
                artifact_quality_errors=[
                    "Artifact quality scan failed: workspace validation command failed (npm run build): "
                    "src/main.ts(1,1): error TS2304: Cannot find name 'missingSymbol'."
                ],
                changed_files=["src/main.ts"],
            )

        assert tool_results == []
        assert summary["error_code"] == "quality_repair_deadline_insufficient"
        assert summary["deadline_decision"]["can_start"] is False
        assert summary["deadline_decision"]["reason"] == "factory_deadline_insufficient"
        assert summary["deadline_decision"]["deadline_source"] == ("factory_director_execution_deadline_epoch_seconds")

    def test_repair_targets_css_import_exact_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.tsx").write_text(
            "import './styles/global.css';\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/app.tsx"]},
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './styles/global.css' in src/app.tsx"],
        )

        assert missing == ["src/styles/global.css"]

    def test_repair_targets_missing_workspace_file_from_python_unittest_error(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        tests = tmp_path / "tests"
        tests.mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
        (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Mini Planet\n", encoding="utf-8")
        test_file = tests / "test_scaffold.py"
        test_file.write_text(
            "import unittest\n\n"
            "class ScaffoldTest(unittest.TestCase):\n"
            "    def test_requirements(self) -> None:\n"
            "        self.assertTrue(False, 'requirements.txt must exist')\n",
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_scaffold.py' "
            "(returncode=1); tail:\n"
            f'  File "{test_file}", line 28, in test_requirements_txt_exists\n'
            "AssertionError: False is not true : requirements.txt must exist at "
            f"{tmp_path / 'requirements.txt'}\n"
            "ERROR: Could not open requirements file: [Errno 2] No such file or directory: "
            f"'{tmp_path / 'requirements.txt'}'\n"
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/__init__.py", "main.py", "README.md"]},
            str(tmp_path),
            [error],
        )

        assert missing == ["requirements.txt"]

    def test_repair_targets_missing_java_source_file_from_unittest_error(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        java_root = tmp_path / "src" / "main" / "java" / "polaris" / "factory" / "domain"
        java_root.mkdir(parents=True)
        (java_root / "RhythmMonster.java").write_text("package polaris.factory.domain;\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_product.py"
        test_file.parent.mkdir()
        test_file.write_text("import unittest\n", encoding="utf-8")
        error = (
            "Artifact quality scan failed: workspace validation command failed "
            "(python -m unittest discover -s tests -p test_*.py -v); tail:\n"
            f'  File "{test_file}", line 79, in test_source_files_present\n'
            "AssertionError: False is not true : missing or empty: "
            "src/main/java/polaris/factory/domain/Season.java\n"
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/main/java/polaris/factory/domain/RhythmMonster.java"]},
            str(tmp_path),
            [error],
        )

        assert missing == ["src/main/java/polaris/factory/domain/Season.java"]

    def test_repair_targets_workspace_file_named_by_assertion_requirement(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        tests = tmp_path / "tests"
        tests.mkdir()
        test_file = tests / "test_planet.py"
        test_file.write_text(
            "import unittest\n\n"
            "class PlanetTest(unittest.TestCase):\n"
            "    def test_requirements_txt_non_empty(self) -> None:\n"
            "        self.fail('requirements.txt must declare at least one dependency.')\n",
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            f'  File "{test_file}", line 39, in test_requirements_txt_non_empty\n'
            "AssertionError: '' is not true : requirements.txt must declare at least one dependency.\n"
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
        (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Mini Planet\n", encoding="utf-8")
        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/__init__.py", "main.py", "README.md"]},
            str(tmp_path),
            [error],
        )

        assert missing == ["requirements.txt"]

    def test_repair_targets_existing_workspace_file_named_by_content_assertion(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "requirements.txt").write_text("# standard library only\n", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
        (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Mini Planet\n", encoding="utf-8")
        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "AssertionError: 'pygame' not found in '# standard library only\\n' : "
            "requirements.txt must declare pygame\n"
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/__init__.py", "main.py", "README.md"]},
            str(tmp_path),
            [error],
        )

        assert missing == ["requirements.txt"]

    @pytest.mark.asyncio
    async def test_quality_repair_defers_out_of_scope_missing_workspace_file_and_runtime_smoke_targets(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
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
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_context = context
                self.repair_message = message
                return {"content": ""}

        adapter = _Adapter()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
        (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Mini Planet\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_scaffold.py"
        test_file.parent.mkdir()
        test_file.write_text(
            "import unittest\n\n"
            "class ScaffoldTest(unittest.TestCase):\n"
            "    def test_requirements_txt_exists(self) -> None:\n"
            "        raise AssertionError('requirements.txt must exist')\n",
            encoding="utf-8",
        )
        quality_error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_scaffold.py' "
            "(returncode=1); tail:\n"
            f'  File "{test_file}", line 28, in test_requirements_txt_exists\n'
            "AssertionError: False is not true : requirements.txt must exist at "
            f"{tmp_path / 'requirements.txt'}\n"
            "ERROR: Could not open requirements file: [Errno 2] No such file or directory: "
            f"'{tmp_path / 'requirements.txt'}'\n"
        )

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["src/__init__.py", "main.py", "README.md"]},
            target_task_id="PM-0001-1",
            run_id="run-python-missing-requirements",
            context={},
            original_message="Create Python scaffold.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["README.md", "main.py", "src/__init__.py", "tests/test_scaffold.py"],
        )

        # requirements.txt and tests/test_scaffold.py are owned by other
        # tasks: both are deferred and no repair LLM turn is spent.
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success"] is False
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["missing_target_files"] == ["requirements.txt"]
        assert summary["runtime_smoke_target_files"] == ["tests/test_scaffold.py"]
        assert summary["repair_target_files"] == []
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["out_of_scope_repair_target_files"] == [
            "requirements.txt",
            "tests/test_scaffold.py",
        ]
        assert adapter.repair_message == ""
        assert adapter.repair_context == {}
        assert adapter._execution.allowed_tool_names is None
        assert adapter._execution.allow_patch_fallback is None
        assert not (tmp_path / "requirements.txt").exists()

    @pytest.mark.asyncio
    async def test_quality_repair_routes_compiled_typescript_runtime_failure_to_owning_source(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del context, timeout_seconds, stage_label
                self.repair_message = message
                return {"content": ""}

        src = tmp_path / "src"
        dist = tmp_path / "dist"
        src.mkdir()
        dist.mkdir()
        (src / "verify.ts").write_text("export function verify(): void {}\n", encoding="utf-8")
        (dist / "verify.js").write_text('"use strict";\n', encoding="utf-8")
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"compilerOptions": {"rootDir": "src", "outDir": "dist"}}),
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "> npm run build && node dist/verify.js\n"
            "Error: ENOENT: no such file or directory, open 'engine/renderer.ts'\n"
            f"    at checkContentAny ({dist / 'verify.js'}:87:52)\n"
            "Node.js v22.23.2"
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["src/verify.ts", "tests/verify.test.ts", "README.md"]},
            target_task_id="TASK-3",
            run_id="director-task-3",
            context={},
            original_message="Repair the verification asset.",
            llm_call_timeout=10,
            artifact_quality_errors=[error],
            changed_files=["src/verify.ts", "dist/verify.js"],
        )

        assert summary["stage"] != "task_boundary_repair_targets_deferred"
        assert summary["runtime_smoke_target_files"] == ["src/verify.ts"]
        assert "src/verify.ts" in adapter.repair_message
        assert "dist/verify.js" not in summary["repair_target_files"]

    @pytest.mark.asyncio
    async def test_quality_repair_defers_single_missing_requirements_out_of_scope(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "__init__.py").write_text('"""package."""\n', encoding="utf-8")
        (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Mini Planet\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_scaffold.py"
        test_file.parent.mkdir()
        test_file.write_text("import unittest\n", encoding="utf-8")
        quality_error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_scaffold.py' "
            "(returncode=1); tail:\n"
            f'  File "{test_file}", line 29, in test_requirements_file_exists\n'
            "AssertionError: False is not true : requirements.txt must exist at "
            f"{tmp_path / 'requirements.txt'}\n"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/__init__.py", "main.py", "README.md"]},
            target_task_id="PM-0001-1",
            run_id="run-python-deterministic-requirements",
            context={},
            original_message="Create Python scaffold.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["README.md", "main.py", "src/__init__.py", "tests/test_scaffold.py"],
            repair_attempt=2,
        )

        # requirements.txt is not a declared write target of this task: the
        # deterministic requirements write is deferred, not executed.
        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["missing_target_files"] == ["requirements.txt"]
        assert summary["repair_target_files"] == []
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["out_of_scope_repair_target_files"] == ["requirements.txt"]
        assert not (tmp_path / "requirements.txt").exists()

    @pytest.mark.asyncio
    async def test_quality_repair_defers_required_requirement_write_out_of_scope(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": ""}

        (tmp_path / "requirements.txt").write_text("# standard library only\n", encoding="utf-8")
        quality_error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "AssertionError: 'pygame' not found in '# standard library only\\n' : "
            "requirements.txt must declare pygame\n"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/__init__.py", "main.py", "README.md"]},
            target_task_id="PM-0001-1",
            run_id="run-python-deterministic-requirement-contract",
            context={},
            original_message="Create Python scaffold.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["requirements.txt", "tests/test_planet.py"],
            repair_attempt=2,
        )

        # The requirements contract belongs to another task: the deterministic
        # requirement write is deferred and the cross-task file is untouched.
        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["out_of_scope_repair_target_files"] == ["requirements.txt"]
        assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == "# standard library only\n"

    @pytest.mark.asyncio
    async def test_quality_repair_defers_out_of_scope_requirement_before_llm_exception_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise RuntimeError(
                    "single_batch_contract_violation: mutation write target drift; "
                    "write targets out-of-scope=['requirements.txt']"
                )

        quality_error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "AssertionError: 0 not greater than or equal to 1 : "
            "requirements.txt must declare at least one dependency\n"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/__init__.py", "main.py", "README.md"]},
            target_task_id="PM-0001-1",
            run_id="run-python-exception-fallback",
            context={},
            original_message="Create Python scaffold.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["tests/test_planet.py"],
            repair_attempt=3,
        )

        # Deferral happens before any LLM repair turn: the raising dialogue
        # stub is never reached and no deterministic fallback write occurs.
        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        assert summary["write_tool_evidence"] is False
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["out_of_scope_repair_target_files"] == ["requirements.txt"]
        assert not (tmp_path / "requirements.txt").exists()

    def test_repair_targets_missing_python_module_alias_from_unittest_error(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_weather.py"
        test_file.parent.mkdir()
        test_file.write_text("from weather import Weather\n", encoding="utf-8")
        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_weather.py' "
            "(returncode=1); tail:\n"
            "Traceback (most recent call last):\n"
            f'  File "{test_file}", line 14, in <module>\n'
            "    from weather import Weather\n"
            "ModuleNotFoundError: No module named 'weather'\n"
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["tests/test_weather.py"]},
            str(tmp_path),
            [error],
        )

        assert missing == ["src/weather.py"]

    @pytest.mark.asyncio
    async def test_quality_repair_defers_python_module_alias_out_of_scope(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": ""}

        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_weather.py"
        test_file.parent.mkdir()
        test_file.write_text("from weather import Weather\n", encoding="utf-8")
        quality_error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_weather.py' "
            "(returncode=1); tail:\n"
            "Traceback (most recent call last):\n"
            f'  File "{test_file}", line 14, in <module>\n'
            "    from weather import Weather\n"
            "ModuleNotFoundError: No module named 'weather'\n"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["tests/test_weather.py"]},
            target_task_id="PM-0001-3",
            run_id="run-python-module-alias",
            context={},
            original_message="Create unittest coverage.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["tests/test_weather.py"],
            repair_attempt=2,
        )

        # src/weather.py is owned by another task: the deterministic module
        # alias write is deferred and the alias file is never materialized.
        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["missing_target_files"] == ["src/weather.py"]
        assert summary["repair_target_files"] == []
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["task_declared_write_targets"] == ["tests/test_weather.py"]
        assert scope_filter["out_of_scope_repair_target_files"] == ["src/weather.py"]
        assert not (tmp_path / "src" / "weather.py").exists()

    def test_repair_targets_unresolved_import_before_unrelated_declared_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src" / "data").mkdir(parents=True)
        (tmp_path / "src" / "data" / "seed.ts").write_text(
            "import { Flower } from './types';\nexport const flowers = [];\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {
                "target_files": [
                    "src/data/seed.ts",
                    "scripts/verify-rules.ts",
                ],
            },
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './types' in src/data/seed.ts"],
        )

        assert missing == ["src/data/types.ts", "scripts/verify-rules.ts"]

    def test_unresolved_import_repair_batches_all_missing_declared_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc","start":"node dist/index.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"outDir":"dist"}}\n', encoding="utf-8")
        (tmp_path / "src" / "models" / "flower.ts").write_text(
            "export class Flower {}\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "firefly.ts").write_text(
            "import { Moon } from './moon';\nexport class Firefly { moon?: Moon; }\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {
                "target_files": [
                    "package.json",
                    "tsconfig.json",
                    "src/models/flower.ts",
                    "src/models/firefly.ts",
                    "src/models/moon.ts",
                    "src/models/humidity.ts",
                    "src/engine/garden.ts",
                    "src/index.ts",
                ],
            },
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './moon' in src/models/firefly.ts"],
        )

        assert missing == [
            "src/models/moon.ts",
            "src/models/humidity.ts",
            "src/engine/garden.ts",
            "src/index.ts",
        ]

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_targets_package_json(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                self.repair_message = message
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"test": "jest"},
                    "devDependencies": {"jest": "^29.0.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create package manifest.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest has test runner script "
                "but no test/spec files exist in package.json",
            ],
            changed_files=["package.json"],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["repair_target_files"] == ["package.json"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["package.json"]
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "edit_file"},
        }
        assert adapter.repair_context["_transaction_kernel_force_exact_tools"] is True
        assert "NPM PACKAGE MANIFEST REPAIR" in adapter.repair_message

    def test_package_script_entrypoint_outside_task_scope_is_deferred(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate as director_quality_gate
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_materialization_quality_errors,
        )

        (tmp_path / "package.json").write_text(
            '{"scripts":{"start":"node src/index.js"}}\n',
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: npm package manifest script "
            "'start' references missing local entrypoint 'src/index.js'"
        )
        monkeypatch.setattr(
            director_quality_gate._em,
            "scan_workspace_artifact_quality",
            lambda *_args, **_kwargs: [error],
        )
        context: dict[str, Any] = {"project_declared_target_files": ["src/index.js"]}

        errors = _collect_materialization_quality_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            task={"target_files": ["package.json"]},
            all_affected_files=["package.json"],
            workspace_name=tmp_path.name,
            context=context,
        )

        assert errors == []
        records = context["director_task_boundary_deferred_quality_errors"]
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == "director.task_boundary.deferred_quality_errors.v1"
        assert record["reason"] == "npm_script_entrypoint_outside_current_task_target_files"
        assert record["artifact_quality_errors"] == [error]
        assert record["target_files"] == ["src/index.js"]
        assert record["artifact_quality_issues"][0]["code"] == "npm_manifest_invalid"
        assert record["artifact_quality_issues"][0]["metadata"]["entrypoint"] == "src/index.js"

    def test_unresolved_relative_import_outside_task_scope_is_deferred(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate as director_quality_gate
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_materialization_quality_errors,
        )

        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "rules.js").write_text(
            "import { createMeteor } from '../meteor.js';\nexport { createMeteor };\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "runner.js").write_text("export function run() {}\n", encoding="utf-8")
        error = "Artifact quality scan failed: unresolved relative import '../meteor.js' in src/engine/rules.js"
        monkeypatch.setattr(
            director_quality_gate._em,
            "scan_workspace_artifact_quality",
            lambda *_args, **_kwargs: [error],
        )
        context: dict[str, Any] = {}

        errors = _collect_materialization_quality_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            all_affected_files=["src/engine/rules.js", "src/engine/runner.js"],
            workspace_name=tmp_path.name,
            context=context,
        )

        assert errors == []
        records = context["director_task_boundary_deferred_quality_errors"]
        assert len(records) == 1
        record = records[0]
        assert record["schema_version"] == "director.task_boundary.deferred_quality_errors.v1"
        assert record["reason"] == "missing_workspace_file_outside_current_task_target_files"
        assert record["artifact_quality_errors"] == [error]
        assert record["target_files"] == ["src/meteor.js"]
        assert record["artifact_quality_issues"][0]["code"] == "unresolved_relative_import"
        assert record["artifact_quality_issues"][0]["metadata"]["importer_path"] == "src/engine/rules.js"

    def test_step_verify_failure_classification_waits_for_authoritative_receipt(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        task_id = "task-source-files"
        context: dict[str, Any] = {
            "construction_step": {"verify": "npm test"},
            **_test_execution_attempt_context(tmp_path, task_id),
        }

        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task_id=task_id,
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert len(tool_results) == 1
        request = tool_results[0]["result"]["deferred_request"]
        assert request.command == "npm test"
        assert request.execution_attempt.external_task_id == task_id

    def test_step_verify_normalizes_exact_private_row_id_to_bound_external_task_id(self, tmp_path: Any) -> None:
        """A claimed TaskRuntime row exposes both private and external ids.

        Director execution uses the private row id internally, while deferred
        directed effects are bound to the PM/CE external task id.  Only that
        exact identity pair may be normalized; otherwise the request must stay
        fail-closed.
        """
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        context: dict[str, Any] = {
            "construction_step": {"verify": "go test ./..."},
            **_test_execution_attempt_context(tmp_path, "TASK-1"),
        }

        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task_id="91",
            task={"target_files": ["go.mod", "main.go"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert len(tool_results) == 1
        request = tool_results[0]["result"]["deferred_request"]
        assert request.task_id == "TASK-1"
        assert request.execution_attempt.task_id == 91
        assert request.execution_attempt.external_task_id == "TASK-1"

    def test_step_verify_rejects_task_id_outside_bound_private_external_pair(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        context: dict[str, Any] = {
            "construction_step": {"verify": "go test ./..."},
            **_test_execution_attempt_context(tmp_path, "TASK-1"),
        }

        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task_id="unrelated-task",
            task={"target_files": ["go.mod", "main.go"]},
            workspace_name=tmp_path.name,
        )

        assert errors == [
            "step verify could not be admitted to directed-effect authority: deo_deferred_command_request_invalid"
        ]
        assert tool_results == []

    def test_step_verify_declared_downstream_test_owner_is_deferred_before_execution(
        self,
        tmp_path: Any,
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        task_id = "task-source-owner"
        context: dict[str, Any] = {
            "construction_step": {"verify": "npm run test"},
            "project_declared_target_files": [
                "src/index.ts",
                "tests/simulation.test.ts",
            ],
            **_test_execution_attempt_context(tmp_path, task_id),
        }

        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task_id=task_id,
            task={"target_files": ["src/index.ts"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert tool_results == []
        assert context["director_task_boundary_deferred_verification_obligations"] == [
            {
                "schema_version": "director.contract_step_verify_resolution.v1",
                "command": "",
                "disposition": "deferred",
                "reason": "project_test_targets_not_owned_by_current_task",
                "downstream_validation_targets": ["tests/simulation.test.ts"],
            }
        ]

    def test_step_verify_package_script_entrypoint_outside_task_scope_is_deferred(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        task_id = "task-entrypoint-owner"
        context: dict[str, Any] = {
            "construction_step": {"verify": "npm run verify"},
            **_test_execution_attempt_context(tmp_path, task_id),
        }

        errors, tool_results = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task_id=task_id,
            task={"target_files": ["src/index.js"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert len(tool_results) == 1
        assert tool_results[0]["result"]["deferred_request"].command == "npm run verify"

    @pytest.mark.asyncio
    async def test_package_script_entrypoint_retry_blocks_out_of_scope_target(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope package script entrypoint must not reach LLM repair")

        (tmp_path / "package.json").write_text(
            '{"scripts":{"start":"node src/index.js"}}\n',
            encoding="utf-8",
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["package.json"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create package manifest.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'src/index.js'",
            ],
            changed_files=["package.json"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["src/index.js"]

    @pytest.mark.asyncio
    async def test_workspace_validation_missing_module_retry_blocks_out_of_scope_target(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope workspace missing module must not reach LLM repair")

        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"node src/index.js"}}\n',
            encoding="utf-8",
        )
        missing_entrypoint = tmp_path / "src" / "index.js"

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            target_task_id="task-2",
            run_id="run-1",
            context={},
            original_message="Create source engine files.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run build): "
                f"Error: Cannot find module '{missing_entrypoint}'",
            ],
            changed_files=["package.json", "src/engine/rules.js"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        assert summary["task_boundary_scope_filter"]["reason"] == (
            "missing_workspace_file_outside_current_task_target_files"
        )
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["src/index.js"]

    @pytest.mark.asyncio
    async def test_node_test_missing_file_retry_blocks_out_of_scope_target(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope missing test target must not reach LLM repair")

        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"node --test tests/product.test.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "rules.js").write_text("export const rules = [];\n", encoding="utf-8")

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            target_task_id="task-2",
            run_id="run-1",
            context={},
            original_message="Create source engine files.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "step verify failed (exit 1): npm test :: failure excerpt: Could not find 'tests/product.test.js'",
            ],
            changed_files=["package.json", "src/engine/rules.js"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        assert summary["task_boundary_scope_filter"]["reason"] == (
            "missing_workspace_file_outside_current_task_target_files"
        )
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["tests/product.test.js"]

    @pytest.mark.asyncio
    async def test_node_test_missing_directory_with_reporter_retry_blocks_out_of_scope_target(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope missing test directory must not reach LLM repair")

        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"node --test tests/ --test-reporter=tap"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "rules.js").write_text("export const rules = [];\n", encoding="utf-8")

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            target_task_id="task-2",
            run_id="run-1",
            context={},
            original_message="Create source engine files.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "step verify failed (exit 1): npm test :: failure excerpt: "
                "Could not find 'tests/, --test-reporter=tap'",
            ],
            changed_files=["package.json", "src/engine/rules.js"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["repair_target_files"] == []
        assert summary["task_boundary_scope_filter"]["reason"] == (
            "missing_workspace_file_outside_current_task_target_files"
        )
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["tests"]

    @pytest.mark.asyncio
    async def test_package_manifest_semantic_quality_retry_blocks_out_of_scope_target(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope package semantic repair must not reach LLM repair")

        (tmp_path / "package.json").write_text(
            '{"type":"module","scripts":{"test":"node --test tests/product.test.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "runner.js").write_text("module.exports = { run() {} };\n", encoding="utf-8")

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            target_task_id="task-2",
            run_id="run-1",
            context={},
            original_message="Create source engine files.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest declares type=module "
                "but workspace JavaScript uses CommonJS runtime syntax in package.json",
            ],
            changed_files=["package.json", "src/engine/runner.js"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["repair_target_files"] == []
        assert summary["task_boundary_scope_filter"]["reason"] == (
            "quality_repair_targets_outside_current_task_target_files"
        )
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["package.json"]

    @pytest.mark.asyncio
    async def test_unresolved_symbol_exporter_out_of_scope_blocks_importer_repair(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                raise AssertionError("out-of-scope exporter owner must not fall through to importer LLM repair")

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            "export function engineScoreWish(wish) { return wish.length; }\n",
            encoding="utf-8",
        )
        (tmp_path / "tests" / "product.test.js").write_text(
            "import { scoreWish } from '../src/index.js';\nvoid scoreWish;\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: unresolved import symbol 'scoreWish' "
            "from '../src/index.js' in tests/product.test.js (sibling module does not define it)"
        ]

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"id": "TASK-2", "target_files": ["tests/product.test.js", "tests/test_product.py"]},
            target_task_id="TASK-2",
            run_id="run-1",
            context={},
            original_message="Create test files.",
            llm_call_timeout=1.0,
            artifact_quality_errors=quality_errors,
            changed_files=["src/index.js", "tests/product.test.js"],
        )

        assert tool_results == []
        assert summary["stage"] == "task_boundary_semantic_exporter_scope_conflict"
        assert summary["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert summary["semantic_quality_target_files"] == ["src/index.js", "tests/product.test.js"]
        assert summary["repair_target_files"] == ["src/index.js", "tests/product.test.js"]
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == ["src/index.js"]
        evidence = summary["interface_discrepancy_evidence"]
        assert evidence["reason"] == "semantic_exporter_owner_outside_current_task_scope"
        assert evidence["semantic_exporter_owner_targets"] == ["src/index.js"]
        assert evidence["task_declared_write_targets"] == ["tests/product.test.js", "tests/test_product.py"]
        assert evidence["director_retry_allowed"] is False

    def test_node_test_directory_quality_error_targets_package_json(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        (tmp_path / "package.json").write_text(
            '{"scripts":{"test":"node --test tests"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.js").write_text("export const ready = true;\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "smoke.test.js").write_text("export const testFile = true;\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script 'test' references test directory "
                "'tests' instead of concrete test files in package.json"
            ],
            changed_files=["package.json", "src/index.js", "tests/smoke.test.js"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["package.json"]

    def test_commonjs_type_module_quality_error_targets_package_json(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        (tmp_path / "package.json").write_text(
            '{"type":"module","scripts":{"start":"node src/index.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.js").write_text(
            'const Note = require("./models/Note");\n',
            encoding="utf-8",
        )

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest declares type=module but workspace "
                "JavaScript uses CommonJS runtime syntax in package.json"
            ],
            changed_files=["package.json", "src/index.js"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["package.json"]

    def test_type_module_commonjs_quality_error_prefers_offending_source_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        (tmp_path / "package.json").write_text(
            '{"type":"module","scripts":{"start":"node src/engine/runner.js"}}\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "runner.js").write_text(
            'const { runQueue } = require("./rules");\nmodule.exports = { runQueue };\n',
            encoding="utf-8",
        )

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: JavaScript source src/engine/runner.js uses CommonJS runtime syntax; "
                "npm package manifest declares type=module but workspace JavaScript uses CommonJS runtime syntax "
                "in package.json"
            ],
            changed_files=["package.json", "src/engine/runner.js"],
            workspace_full=str(tmp_path),
        )

        assert targets[:2] == ["src/engine/runner.js", "package.json"]

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_preserves_missing_targets(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_message = message
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/main.js", "test": "node dist/main.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json", "README.md", "src/main.ts", "index.html"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript project scaffold.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/main.js'",
                "Artifact quality scan failed: declared target file 'README.md' is missing",
                "Artifact quality scan failed: declared target file 'src/main.ts' is missing",
                "Artifact quality scan failed: declared target file 'index.html' is missing",
            ],
            changed_files=["package.json"],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["missing_target_files"] == ["README.md", "src/main.ts", "index.html"]
        assert summary["repair_target_files"] == ["README.md", "src/main.ts", "index.html", "package.json"]
        assert "write_only_single_target" not in adapter.repair_context["director_quality_repair"]
        assert "NPM PACKAGE MANIFEST REPAIR" in adapter.repair_message
        assert "MISSING TARGET FILES" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_package_manifest_missing_test_and_verify_scripts_defer_out_of_scope_entrypoint_targets(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_message = message
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc -p tsconfig.json",
                        "test": "node --import tsx --test tests/**/*.test.ts",
                        "verify": "node --import tsx scripts/verify.ts",
                    },
                    "devDependencies": {"tsx": "^4.7.2", "typescript": "^5.4.5"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript project scaffold.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'test' references missing local entrypoint 'tests/**/*.test.ts' in package.json",
                "Artifact quality scan failed: npm package manifest script "
                "'verify' references missing local entrypoint 'scripts/verify.ts' in package.json",
            ],
            changed_files=["package.json"],
        )

        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["semantic_quality_target_files"] == []
        assert summary["missing_target_files"] == []
        assert summary["repair_target_files"] == []
        assert summary["llm_fallback_blocked"] is True
        assert summary["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == [
            "tests/generated.test.ts",
            "scripts/verify.ts",
        ]
        assert adapter.repair_message == ""

    @pytest.mark.asyncio
    async def test_package_manifest_quality_error_targets_existing_path_when_changed_files_empty(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, timeout_seconds, stage_label
                return {"content": ""}

        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/index.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        _, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["package.json", "src/config.ts"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript project scaffold.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/index.js' in package.json"
            ],
            changed_files=[],
        )

        assert summary["semantic_quality_target_files"] == ["package.json"]
        assert summary["repair_target_files"] == ["package.json"]

    @pytest.mark.asyncio
    async def test_quality_repair_second_attempt_preserves_package_and_typescript_batch(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("const config = { humidity: 65.0; };\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "tsc", "start": "node dist/index.js"},
                    "devDependencies": {"typescript": "^5.3.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json", "src/index.ts"]},
            target_task_id="task-1",
            run_id="run-1",
            context={},
            original_message="Create TypeScript garden simulation.",
            llm_call_timeout=1.0,
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script "
                "'start' references missing local entrypoint 'dist/index.js' in package.json",
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/index.ts(16,21): error TS1005: ',' expected.",
            ],
            changed_files=["package.json", "src/index.ts"],
            repair_attempt=2,
        )

        assert summary["semantic_quality_target_files"] == ["package.json", "src/index.ts"]
        assert summary["repair_target_files"] == ["package.json", "src/index.ts"]
        assert "write_only_single_target" not in adapter.repair_context["director_quality_repair"]
        assert adapter.repair_context["delivery_mode"] == "materialize_changes"
        assert adapter.repair_context["metadata"]["delivery_mode"] == "materialize_changes"

    def test_repair_targets_existing_import_case_variant_is_not_missing(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _missing_materialization_quality_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Router.TSX").write_text(
            "export const router = {};\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "main.tsx").write_text(
            "import { router } from './router';\n",
            encoding="utf-8",
        )

        missing = _missing_materialization_quality_repair_target_files(
            {"target_files": ["src/main.tsx"]},
            str(tmp_path),
            ["Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"],
        )

        assert missing == []

    @pytest.mark.asyncio
    async def test_single_missing_target_repair_sets_forced_write_context(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **_: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        (tmp_path / "services" / "product_service").mkdir(parents=True)
        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["services/product_service/app.py"]},
            target_task_id="PM-0001-1",
            run_id="run-single-missing-write-only",
            context={},
            original_message="Create the missing product service file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'services/product_service/app.py'"
            ],
            changed_files=["services/product_service/__init__.py"],
        )

        assert summary["missing_target_files"] == ["services/product_service/app.py"]
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }
        assert (
            adapter.repair_context["_transaction_kernel_forced_tool_definitions"][0]["function"]["name"] == "write_file"
        )
        assert adapter.repair_context["_transaction_kernel_forced_tool_definitions"][0]["function"]["parameters"][
            "required"
        ] == ["file", "content"]
        assert adapter.repair_context["_transaction_kernel_force_exact_tools"] is True
        assert adapter.repair_context["director_quality_repair"]["write_only_single_target"] == {
            "tool": "write_file",
            "target_file": "services/product_service/app.py",
        }

    @pytest.mark.asyncio
    async def test_existing_compile_target_repair_prefers_edit_file_not_forced_write_only(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "exhibit.go").write_text(
            "package models\n\n"
            "type Exhibit struct {\n"
            "\tTitle string\n"
            "}\n\n"
            "func UseExhibit(e Exhibit) string {\n"
            "\treturn e.ID\n"
            "}\n",
            encoding="utf-8",
        )

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
                self.repair_message = ""
                self.repair_context: dict[str, Any] = {}

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del timeout_seconds, stage_label
                self.repair_message = message
                self.repair_context = context
                return {"content": "No native tool call", "success": True}

        adapter = _Adapter()

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["models/exhibit.go"]},
            target_task_id="factory-quality-gate:run-go-repair",
            run_id="run-go-existing-repair",
            context={},
            original_message="Repair Go compile diagnostics.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "# example/models\n"
                "models/exhibit.go:8:11: e.ID undefined (type Exhibit has no field or method ID)"
            ],
            changed_files=["models/exhibit.go"],
        )

        forced_defs = adapter.repair_context["_transaction_kernel_forced_tool_definitions"]
        forced_names = [item["function"]["name"] for item in forced_defs]
        assert forced_names == ["edit_file"]
        assert adapter.repair_context["metadata"]["tool_contract"]["required_tools"] == ["edit_file"]
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "edit_file"},
        }
        assert adapter.repair_context["_transaction_kernel_force_exact_tools"] is True
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["models/exhibit.go"]
        assert "edit_preferred_single_target" in adapter.repair_message
        assert "Do not call read_file" not in adapter.repair_message
        assert "call read_file for this target first when required by tool policy" in adapter.repair_message
        assert "write_only_single_target" not in adapter.repair_message
        assert adapter._execution.allowed_tool_names == {"edit_file"}
        assert adapter._execution.allow_patch_fallback is False
        assert summary["repair_target_files"] == ["models/exhibit.go"]
