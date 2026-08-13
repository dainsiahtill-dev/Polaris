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






class TestQualityRepairMissingTargetContractB:
    async def test_rust_line_suggestion_quality_error_runs_runtime_bridge_before_llm(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        src = tmp_path / "src" / "models"
        src.mkdir(parents=True)
        (src / "flavor.rs").write_text(
            "#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]\npub enum FlavorKind {\n    Salty,\n}\n",
            encoding="utf-8",
        )
        (src / "palette.rs").write_text(
            "use std::collections::BTreeMap;\n"
            "use super::flavor::FlavorKind;\n"
            "pub fn demo(kind: FlavorKind) {\n"
            "    let mut map: BTreeMap<FlavorKind, u8> = BTreeMap::new();\n"
            "    let _ = map.entry(kind).or_insert(0);\n"
            "}\n",
            encoding="utf-8",
        )

        calls: list[dict[str, Any]] = []

        def fake_runtime_bridge(
            adapter: Any,
            *,
            task: dict[str, Any],
            task_id: str,
            artifact_quality_errors: list[str],
            artifact_quality_issues: tuple[dict[str, Any], ...] = (),
            execution_attempt: Any = None,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del adapter, task, artifact_quality_errors, artifact_quality_issues
            assert execution_attempt is None
            calls.append({"task_id": task_id})
            return (
                [
                    {
                        "tool_name": "edit_file",
                        "tool": "edit_file",
                        "success": True,
                        "file": "src/models/flavor.rs",
                        "result": {
                            "success": True,
                            "file": "src/models/flavor.rs",
                            "source_tool": "deterministic_rust_line_suggestion_repair",
                            "repair_kernel": {"owner_cell": "director.runtime"},
                        },
                    }
                ],
                {
                    "source_tools": ["deterministic_rust_line_suggestion_repair"],
                    "repair_kernel": {"owner_cell": "director.runtime"},
                },
            )

        monkeypatch.setattr(quality_gate, "_run_materialization_quality_public_boundary", fake_runtime_bridge)

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *args, **kwargs: None)

            async def _invoke_role_dialogue_with_timeout(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                raise AssertionError("LLM repair should not run when runtime bridge produced a write receipt")

        rust_error = (
            "Artifact quality scan failed: workspace validation command failed (cargo check):\n"
            "error[E0599]: the method `or_insert` exists for enum "
            "`std::collections::btree_map::Entry<'_, FlavorKind, u8>`, but its trait bounds were not satisfied\n"
            "  --> src/models/palette.rs:5:29\n"
            "   |\n"
            "5 |     let _ = map.entry(kind).or_insert(0);\n"
            "   |                             ^^^^^^^^^ method cannot be called due to unsatisfied trait bounds\n"
            "  ::: src/models/flavor.rs:2:1\n"
            "   |\n"
            "2 | pub enum FlavorKind {\n"
            "   | ------------------- doesn't satisfy `FlavorKind: Ord`\n"
            "help: consider annotating `FlavorKind` with `#[derive(Eq, Ord, PartialEq, PartialOrd)]`\n"
            "  --> src/models/flavor.rs:2:1\n"
            "   |\n"
            "2 + #[derive(Eq, Ord, PartialEq, PartialOrd)]\n"
            "3 | pub enum FlavorKind {\n"
        )

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["src/models/flavor.rs", "src/models/palette.rs"]},
            target_task_id="task-rust-line-suggestion",
            run_id="run-rust-line-suggestion",
            context={},
            original_message="Repair Rust compiler diagnostics.",
            llm_call_timeout=10,
            artifact_quality_errors=[rust_error],
            changed_files=["src/models/flavor.rs", "src/models/palette.rs"],
        )

        assert calls == [{"task_id": "task-rust-line-suggestion"}]
        assert tool_results[0]["result"]["source_tool"] == "deterministic_rust_line_suggestion_repair"
        assert summary["stage"] == "deterministic_materialization_quality_repair"
        assert summary["write_tool_evidence"] is True
        assert summary["source_tools"] == ["deterministic_rust_line_suggestion_repair"]

    @pytest.mark.asyncio
    async def test_go_scaffold_marker_quality_repair_runs_deterministic_before_llm(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_all_test_deferred_projections(monkeypatch, tmp_path)
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        (tmp_path / "main.go").write_text(
            "package main\n\n// output reflects real state rather than a static placeholder.\nfunc main() {}\n",
            encoding="utf-8",
        )

        class _Execution:
            _message_bus = None

            @staticmethod
            def extract_kernel_tool_results(result: dict[str, Any]) -> list[dict[str, Any]]:
                del result
                return []

            @staticmethod
            async def execute_tools(
                content: str,
                target_task_id: str,
                update_task_progress: Any,
                **kwargs: Any,
            ) -> list[dict[str, Any]]:
                del content, target_task_id, update_task_progress, kwargs
                raise AssertionError("LLM fallback tools should not run when deterministic cleanup applies")

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
                raise AssertionError("LLM should not be invoked for deterministic scaffold marker cleanup")

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["main.go"]},
            target_task_id="factory-quality-gate:run-go-marker",
            run_id="run-go-marker",
            context={},
            original_message="Repair Go source quality marker.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Director output quality gate failed: generic/placeholder content detected: "
                "main.go:(?<![.:'\"-])\\bplaceholder\\b(?!\\s*[=:])(?![-'\"])"
            ],
            changed_files=["main.go"],
        )

        repaired = (tmp_path / "main.go").read_text(encoding="utf-8")
        assert tool_results
        assert summary["stage"] == "deterministic_materialization_quality_repair"
        assert "deterministic_scaffold_marker_quality_cleanup" in summary["source_tools"]
        assert "placeholder" not in repaired.lower()
        assert "sample-check" in repaired

    @pytest.mark.asyncio
    async def test_single_missing_target_raw_content_is_non_authoritative_and_does_not_write(self, tmp_path) -> None:
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
                return {"content": "```python\nprint('service ready')\n```", "success": True}

        (tmp_path / "services" / "product_service").mkdir(parents=True)

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["services/product_service/app.py"]},
            target_task_id="PM-0001-1",
            run_id="run-single-missing-raw-content",
            context={},
            original_message="Create the missing product service file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'services/product_service/app.py'"
            ],
            changed_files=[],
        )

        assert summary["tool_results"] == 1
        assert summary["write_tool_evidence"] is False
        assert tool_results[0]["success"] is False
        assert tool_results[0]["result"]["source_tool"] == "director_quality_repair_raw_single_target_body"
        assert tool_results[0]["result"]["error_code"] == "raw_single_target_body_not_authoritative"
        assert tool_results[0]["result"]["writes_allowed"] is False
        assert not (tmp_path / "services" / "product_service" / "app.py").exists()

    @pytest.mark.asyncio
    async def test_single_target_repair_refuses_raw_tool_receipt_content(self, tmp_path) -> None:
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
                return {
                    "content": (
                        "**write_file**: Error - {'ok': False, 'error': "
                        "'Destructive shrink rejected: this edit would replace tests/garden.test.ts'}"
                    ),
                    "success": True,
                }

        (tmp_path / "tests").mkdir(parents=True)

        tool_results, summary = await _run_materialization_quality_repair_retry(
            _Adapter(),
            task={"target_files": ["tests/simulation.test.ts"]},
            target_task_id="PM-0001-2",
            run_id="run-tool-receipt-raw-content",
            context={},
            original_message="Create the missing simulation test file.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'tests/simulation.test.ts'"
            ],
            changed_files=[],
        )

        assert tool_results == []
        assert summary["write_tool_evidence"] is False
        assert not (tmp_path / "tests" / "simulation.test.ts").exists()

    @pytest.mark.asyncio
    async def test_quality_repair_timeout_is_bounded_below_director_call_timeout(self, tmp_path) -> None:
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
                self.timeout_seconds = 0.0

            async def _invoke_role_dialogue_with_timeout(
                self,
                message: str,
                *,
                context: dict[str, Any],
                timeout_seconds: float,
                stage_label: str,
            ) -> dict[str, Any]:
                del message, context, stage_label
                self.timeout_seconds = timeout_seconds
                return {"content": ""}

        adapter = _Adapter()

        await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["package.json"]},
            target_task_id="PM-0001-1",
            run_id="run-quality-repair-timeout",
            context={},
            original_message="Repair package manifest.",
            llm_call_timeout=900,
            artifact_quality_errors=["Artifact quality scan failed: npm placeholder test script in package.json"],
            changed_files=["package.json"],
        )

        assert adapter.timeout_seconds == 180.0

    @pytest.mark.asyncio
    async def test_existing_python_runtime_smoke_failure_repair_forces_write_context(self, tmp_path) -> None:
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
        (tmp_path / "calculator.py").write_text("print('placeholder')\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["calculator.py"]},
            target_task_id="PM-0001-1",
            run_id="run-runtime-smoke-write-only",
            context={
                "director_interface_discrepancy_retry": {
                    "authorized": True,
                    "recommended_owner": "director",
                    "recommended_route": "director_retry_with_interface_discrepancy_context",
                    "reason": "coverage_matched_but_unplannable",
                }
            },
            original_message="Create a runnable Python CLI script.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'calculator.py' "
                "(returncode=2); tail:\nusage: calculator.py [-h] a {+,-,*,/} b"
            ],
            changed_files=["calculator.py"],
        )

        assert summary["missing_target_files"] == []
        assert summary["runtime_smoke_target_files"] == ["calculator.py"]
        assert summary["repair_target_files"] == ["calculator.py"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["calculator.py"]
        assert "_transaction_kernel_forced_tool_choice" not in adapter.repair_context
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "MISSING TARGET FILES" not in adapter.repair_message
        assert "calculator.py" in adapter.repair_message
        assert "edit only the existing failed target" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_python_runtime_smoke_repair_defers_cross_task_workspace_file(self, tmp_path) -> None:
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
        (tmp_path / "src" / "cli.py").write_text(
            "from src.parser import ExpressionParser\nprint(ExpressionParser)\n",
            encoding="utf-8",
        )
        (tmp_path / "README.md").write_text("placeholder\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["README.md"]},
            target_task_id="PM-0001-3",
            run_id="run-runtime-smoke-cross-task",
            context={
                "director_interface_discrepancy_retry": {
                    "authorized": True,
                    "recommended_owner": "director",
                    "recommended_route": "director_retry_with_interface_discrepancy_context",
                    "reason": "coverage_matched_but_unplannable",
                }
            },
            original_message="Complete the README for the CLI project.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'src/cli.py' "
                "(returncode=1); tail:\n"
                "Traceback (most recent call last):\n"
                '  File "/tmp/work/src/cli.py", line 1, in <module>\n'
                "ModuleNotFoundError: No module named 'src'"
            ],
            changed_files=["README.md"],
        )

        # src/cli.py belongs to another task: the runtime-smoke diagnostic is
        # still surfaced as evidence, but the repair target is deferred and no
        # repair LLM turn is spent, even with an interface-discrepancy retry
        # authorization in the caller context.
        assert summary["stage"] == "task_boundary_repair_targets_deferred"
        assert summary["success_reason"] == "repair_targets_outside_current_task_target_files"
        assert summary["missing_target_files"] == []
        assert summary["runtime_smoke_target_files"] == ["src/cli.py"]
        assert summary["repair_target_files"] == []
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["task_declared_write_targets"] == ["README.md"]
        assert scope_filter["out_of_scope_repair_target_files"] == ["src/cli.py"]
        assert adapter.repair_message == ""
        assert adapter.repair_context == {}
        assert adapter._execution.allowed_tool_names is None
        assert adapter._execution.allow_patch_fallback is None

    def test_python_runtime_smoke_test_failure_prefers_traceback_source_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "calculator.py").write_text("def tokenize(value):\n    return []\n", encoding="utf-8")
        (tmp_path / "test_calculator.py").write_text(
            "from calculator import tokenize\n\ndef test_empty():\n    assert tokenize('') == []\n",
            encoding="utf-8",
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'test_calculator.py' "
                "(returncode=1); tail:\n"
                '  File "/tmp/work/test_calculator.py", line 230, in test_tokenize_empty_raises\n'
                "    with self.assertRaises(SyntaxError):\n"
                '  File "calculator.py", line 34, in tokenize\n'
                "    raise SyntaxError('bad')\n"
                "AssertionError: SyntaxError not raised"
            ],
            changed_files=["test_calculator.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["calculator.py", "test_calculator.py"]

    def test_python_runtime_smoke_test_failure_infers_imported_workspace_module(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "calculator.py").write_text("def evaluate_expression(value):\n    return value\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_calculator.py").write_text(
            "import calculator\n\n"
            "def call_calculator(expression):\n"
            "    raise AssertionError('calculator module must expose parse_and_evaluate(), evaluate(), or calculate()')\n",
            encoding="utf-8",
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_calculator.py' "
                "(returncode=1); tail:\n"
                f'  File "{tmp_path / "tests" / "test_calculator.py"}", line 4, in call_calculator\n'
                "    raise AssertionError('calculator module must expose parse_and_evaluate(), evaluate(), or calculate()')\n"
                "AssertionError: calculator module must expose parse_and_evaluate(), evaluate(), or calculate()"
            ],
            changed_files=["README.md", "tests/test_calculator.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["calculator.py", "tests/test_calculator.py"]

    def test_python_runtime_smoke_cli_subcommand_failure_targets_existing_entrypoint(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        (tmp_path / "src" / "engine" / "mod.rs").write_text("pub fn run() {}\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_product.py").write_text("import unittest\n", encoding="utf-8")

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py' "
                "(returncode=1); tail:\n"
                "AssertionError: 2 != 0 : unknown subcommand: flavor"
            ],
            changed_files=["src/engine/mod.rs", "tests/test_product.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/main.rs", "tests/test_product.py"]

    def test_python_runtime_smoke_harness_targets_changed_non_python_sources(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
            _python_runtime_smoke_repair_target_files,
        )

        engine = tmp_path / "src" / "engine"
        engine.mkdir(parents=True)
        (engine / "mapper.rs").write_text('pub fn map() -> &\'static str { "ok" }\n', encoding="utf-8")
        (engine / "mod.rs").write_text("pub mod mapper;\npub mod plating;\n", encoding="utf-8")
        (engine / "plating.rs").write_text('pub fn plate() -> &\'static str { "basic" }\n', encoding="utf-8")
        (tmp_path / "src" / "main.rs").write_text('fn main() { println!("basic"); }\n', encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_product.py"
        test_file.write_text("def test_cli_output():\n    assert False\n", encoding="utf-8")
        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py' "
            "(returncode=1); tail:\n"
            "pytest tests/test_product.py\n"
            f'  File "{test_file}", line 2, in test_cli_output\n'
            "AssertionError: 'illegal rarity' not found in command output\n"
        )
        changed_files = [
            "src/engine/mapper.rs",
            "src/engine/mod.rs",
            "src/engine/plating.rs",
            "src/main.rs",
            "tests/test_product.py",
        ]

        runtime_targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=changed_files,
            workspace_full=str(tmp_path),
        )
        explicit_targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=changed_files,
            workspace_full=str(tmp_path),
        )

        assert runtime_targets == changed_files
        assert explicit_targets == changed_files

    def test_go_runtime_smoke_targets_existing_entrypoint(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _go_runtime_smoke_repair_target_files,
        )

        (tmp_path / "go.mod").write_text("module example\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
        (tmp_path / "engine").mkdir()
        (tmp_path / "engine" / "rules.go").write_text("package engine\n", encoding="utf-8")

        targets = _go_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go run .): "
                "stdout\nexpected runtime error\nexit status 1"
            ],
            changed_files=["engine/rules.go"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["main.go"]

    def test_go_compile_failure_targets_reported_source_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _go_runtime_smoke_repair_target_files,
        )

        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "gallery.go").write_text("package models\n", encoding="utf-8")
        (tmp_path / "models" / "capsule.go").write_text("package models\n", encoding="utf-8")

        targets = _go_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "# module/models\nmodels/gallery.go:13:14: undefined: Capsule"
            ],
            changed_files=["models/capsule.go"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["models/gallery.go"]

    def test_go_missing_field_compile_failure_also_targets_type_definition_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _go_runtime_smoke_repair_target_files,
        )

        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "gallery.go").write_text(
            "package models\n\nfunc use(e *Exhibit) string { return e.ID }\n",
            encoding="utf-8",
        )
        (tmp_path / "models" / "exhibit.go").write_text(
            "package models\n\ntype Exhibit struct { Name string }\n",
            encoding="utf-8",
        )

        targets = _go_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "# module/models\n"
                "models/gallery.go:3:40: e.ID undefined (type *Exhibit has no field or method ID)"
            ],
            changed_files=[],
            workspace_full=str(tmp_path),
        )

        assert targets == ["models/gallery.go", "models/exhibit.go"]

    def test_go_workspace_compile_repair_preserves_batch_after_first_attempt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _select_materialization_quality_repair_target_batch,
            _should_preserve_materialization_quality_repair_batch,
        )

        errors = [
            "Artifact quality scan failed: workspace validation command failed (go test ./...): "
            "# module/models\n"
            "models/exhibit.go:43:34: undefined: TimeSource\n"
            "models/gallery.go:49:28: e.ID undefined (type *Exhibit has no field or method ID)"
        ]
        targets = ["models/exhibit.go", "models/gallery.go", "models/capsule.go"]

        preserve = _should_preserve_materialization_quality_repair_batch(errors)
        selected = _select_materialization_quality_repair_target_batch(
            targets,
            repair_attempt=2,
            preserve_batch_after_first_attempt=preserve,
        )

        assert preserve is True
        assert selected == targets

    def test_go_runtime_target_is_prioritized_before_broad_missing_targets(self) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _ordered_materialization_quality_repair_target_candidates,
            _select_materialization_quality_repair_target_batch,
        )

        missing_targets = [
            "go.mod",
            "models/capsule.go",
            "models/exhibit.go",
            "models/gallery.go",
            "engine/museum.go",
            "engine/riddle.go",
            "engine/unlock.go",
            "main.go",
        ]

        ordered = _ordered_materialization_quality_repair_target_candidates(
            missing_target_files=missing_targets,
            runtime_smoke_target_files=["engine/unlock.go"],
            semantic_quality_target_files=[],
            explicit_quality_target_files=[],
            should_merge_missing_targets=True,
        )
        selected = _select_materialization_quality_repair_target_batch(ordered)

        assert ordered[0] == "engine/unlock.go"
        assert "engine/unlock.go" in selected

    def test_go_module_import_repair_runs_through_runtime_bridge(self, tmp_path) -> None:
        from types import SimpleNamespace

        class _Adapter:
            workspace = str(tmp_path)
            _execution = SimpleNamespace(_message_bus=None)

            @staticmethod
            def _update_task_progress(*args: Any, **kwargs: Any) -> None:
                del args, kwargs

        (tmp_path / "models").mkdir()
        (tmp_path / "engine").mkdir()
        (tmp_path / "go.mod").write_text("module example/app\n\ngo 1.21\n", encoding="utf-8")
        (tmp_path / "models" / "capsule.go").write_text("package models\n\ntype Capsule struct{}\n", encoding="utf-8")
        (tmp_path / "engine" / "unlock.go").write_text(
            'package engine\n\nimport "example-app/models"\n\nfunc Use() models.Capsule { return models.Capsule{} }\n',
            encoding="utf-8",
        )

        results = _run_go_materialization_quality_schedule(
            _Adapter(),
            task_id="factory-quality-gate:test",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "engine/unlock.go:3:8: package example-app/models is not in std"
            ],
        )

        repaired = (tmp_path / "engine" / "unlock.go").read_text(encoding="utf-8")
        source_tools = {
            str((item.get("result") or {}).get("source_tool") or item.get("source_tool") or "")
            for item in results
            if isinstance(item, dict)
        }
        assert 'import "example/app/models"' in repaired
        assert "deterministic_go_module_import_repair" in source_tools

    def test_go_unused_import_repair_runs_through_runtime_bridge(self, tmp_path) -> None:
        from types import SimpleNamespace

        class _Adapter:
            workspace = str(tmp_path)
            _execution = SimpleNamespace(_message_bus=None)

            @staticmethod
            def _update_task_progress(*args: Any, **kwargs: Any) -> None:
                del args, kwargs

        (tmp_path / "models").mkdir()
        (tmp_path / "engine").mkdir()
        (tmp_path / "go.mod").write_text("module example/app\n\ngo 1.21\n", encoding="utf-8")
        (tmp_path / "models" / "capsule.go").write_text("package models\n\ntype Capsule struct{}\n", encoding="utf-8")
        (tmp_path / "engine" / "riddle.go").write_text(
            "package engine\n"
            "\n"
            "import (\n"
            '    "errors"\n'
            '    "example/app/models"\n'
            ")\n"
            "\n"
            'func Riddle() error { return errors.New("sealed") }\n',
            encoding="utf-8",
        )

        results = _run_go_materialization_quality_schedule(
            _Adapter(),
            task_id="factory-quality-gate:test",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                'engine/riddle.go:5:5: "example/app/models" imported and not used'
            ],
        )

        repaired = (tmp_path / "engine" / "riddle.go").read_text(encoding="utf-8")
        source_tools = {
            str((item.get("result") or {}).get("source_tool") or item.get("source_tool") or "")
            for item in results
            if isinstance(item, dict)
        }
        assert '"example/app/models"' not in repaired
        assert '"errors"' in repaired
        assert "deterministic_go_unused_import_repair" in source_tools

    def test_go_error_string_helper_repair_runs_through_runtime_bridge(self, tmp_path) -> None:
        from types import SimpleNamespace

        class _Adapter:
            workspace = str(tmp_path)
            _execution = SimpleNamespace(_message_bus=None)

            @staticmethod
            def _update_task_progress(*args: Any, **kwargs: Any) -> None:
                del args, kwargs

        (tmp_path / "models").mkdir()
        (tmp_path / "go.mod").write_text("module example/app\n\ngo 1.21\n", encoding="utf-8")
        (tmp_path / "models" / "gallery.go").write_text(
            "package models\n"
            "\n"
            'import "errors"\n'
            "\n"
            "var (\n"
            '    ErrDuplicateCapsule = errString("capsule id already exists")\n'
            '    ErrUnknownCapsule   = errString("capsule id not found")\n'
            ")\n"
            "\n"
            'func Existing() error { return errors.New("x") }\n',
            encoding="utf-8",
        )

        results = _run_go_materialization_quality_schedule(
            _Adapter(),
            task_id="factory-quality-gate:test",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "models/gallery.go:6:27: undefined: errString"
            ],
        )

        repaired = (tmp_path / "models" / "gallery.go").read_text(encoding="utf-8")
        source_tools = {
            str((item.get("result") or {}).get("source_tool") or item.get("source_tool") or "")
            for item in results
            if isinstance(item, dict)
        }
        assert "type errString string" in repaired
        assert "func (e errString) Error() string { return string(e) }" in repaired
        assert repaired.index("type errString string") < repaired.index("var (")
        assert "deterministic_go_error_string_helper_repair" in source_tools

    def test_go_missing_member_repair_targets_package_qualified_definition(self, tmp_path) -> None:
        (tmp_path / "engine").mkdir()
        (tmp_path / "models").mkdir()
        (tmp_path / "engine" / "riddle.go").write_text(
            "package engine\n"
            "\n"
            'import "example.com/timecapsule/models"\n'
            "\n"
            "func needsCapsule(c models.Capsule) string {\n"
            "    return c.Validate()\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "models" / "capsule.go").write_text(
            "package models\n\ntype Capsule struct {\n    ID string\n}\n",
            encoding="utf-8",
        )
        errors = [
            "Artifact quality scan failed: workspace validation command failed (go test ./...): "
            "engine/riddle.go:6:14: c.Validate undefined "
            "(type models.Capsule has no field or method Validate)"
        ]

        targets = _go_runtime_smoke_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=["engine/riddle.go", "models/capsule.go"],
            workspace_full=str(tmp_path),
        )

        assert "engine/riddle.go" in targets
        assert "models/capsule.go" in targets

    def test_go_test_behavior_repair_targets_production_sources_not_tests(self, tmp_path) -> None:
        for directory in ("engine", "models"):
            (tmp_path / directory).mkdir()
        for rel in (
            "engine/museum.go",
            "engine/riddle.go",
            "engine/unlock.go",
            "main.go",
            "main_test.go",
            "models/capsule.go",
            "models/exhibit.go",
            "models/gallery.go",
        ):
            (tmp_path / rel).write_text("package main\n", encoding="utf-8")
        errors = [
            "Artifact quality scan failed: workspace validation command failed (go test ./...): "
            "--- FAIL: TestGallery_AddAndFindExhibits (0.00s)\n"
            "    main_test.go:91: expected ErrDuplicateExhibitID, got gallery is full\n"
            "--- FAIL: TestUnlocker_AttemptFlow (0.00s)\n"
            "    main_test.go:205: expected unlock success, got capsule is not ready to open\n"
            "--- FAIL: TestMainEntrypointOutput (0.00s)\n"
            "    main_test.go:256: unlock: capsule is not ready to open"
        ]

        targets = _go_runtime_smoke_repair_target_files(
            artifact_quality_errors=errors,
            changed_files=[
                "engine/museum.go",
                "engine/riddle.go",
                "engine/unlock.go",
                "main.go",
                "main_test.go",
                "models/capsule.go",
                "models/exhibit.go",
                "models/gallery.go",
            ],
            workspace_full=str(tmp_path),
        )

        assert "main_test.go" not in targets
        assert targets[:3] == ["models/gallery.go", "engine/unlock.go", "main.go"]
        assert "engine/riddle.go" in targets
        assert "models/capsule.go" in targets

    @pytest.mark.asyncio
    async def test_existing_go_runtime_smoke_failure_repair_forces_write_context(self, tmp_path) -> None:
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
        (tmp_path / "go.mod").write_text("module example\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["go.mod", "main.go"]},
            target_task_id="PM-0001-1",
            run_id="run-go-runtime-smoke-write-only",
            context={},
            original_message="Create a runnable Go CLI.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go run .): "
                "stdout\nexpected runtime error\nexit status 1"
            ],
            changed_files=["main.go"],
        )

        assert summary["runtime_smoke_target_files"] == ["main.go"]
        assert summary["repair_target_files"] == ["main.go"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["main.go"]
        assert "_transaction_kernel_forced_tool_choice" not in adapter.repair_context
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "CURRENT UTF-8 CONTENT OF REPAIR TARGETS" in adapter.repair_message
        assert "Before edit_file, call read_file" not in adapter.repair_message
        assert "main.go" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_existing_go_compile_failure_repair_forces_reported_file(self, tmp_path) -> None:
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
        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "gallery.go").write_text("package models\n", encoding="utf-8")
        (tmp_path / "models" / "capsule.go").write_text("package models\n", encoding="utf-8")

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["models/gallery.go", "models/capsule.go"]},
            target_task_id="PM-0001-1",
            run_id="run-go-compile-write-only",
            context={},
            original_message="Repair a Go package compile failure.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (go test ./...): "
                "# module/models\nmodels/gallery.go:13:14: undefined: Entity"
            ],
            changed_files=["models/capsule.go"],
        )

        assert summary["runtime_smoke_target_files"] == ["models/gallery.go"]
        assert summary["repair_target_files"] == ["models/gallery.go"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["models/gallery.go"]
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert "models/gallery.go" in adapter.repair_message

    def test_python_unittest_discover_result_lines_infer_imported_src_modules(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "planet.py").write_text("class Planet:\n    pass\n", encoding="utf-8")
        (src_dir / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_planet.py").write_text(
            "import unittest\n"
            "from src.planet import Planet\n\n"
            "class TestPlanet(unittest.TestCase):\n"
            "    def test_name_attribute(self) -> None:\n"
            "        Planet(name='Aurora', radius=6371.0, mass=5.972e24)\n",
            encoding="utf-8",
        )
        (tests_dir / "test_weather.py").write_text(
            "import unittest\n"
            "from src.planet import Planet\n"
            "from src.weather import Weather\n\n"
            "class TestWeather(unittest.TestCase):\n"
            "    def test_temperature_attribute(self) -> None:\n"
            "        Weather(planet=Planet(), temperature=288.15, humidity=0.5)\n",
            encoding="utf-8",
        )

        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "test_name_attribute (test_planet.TestPlanet.test_name_attribute) ... ERROR\n"
            "test_weather (unittest.loader._FailedTest.test_weather) ... ERROR\n"
            "TypeError: Planet.__init__() got an unexpected keyword argument 'radius'\n"
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=["tests/test_planet.py", "tests/test_weather.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/planet.py", "src/weather.py", "tests/test_planet.py", "tests/test_weather.py"]

    def test_python_unittest_missing_top_level_src_module_authorizes_shim(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "planet.py").write_text("class Planet:\n    pass\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_planet.py").write_text(
            "import os\n"
            "import sys\n"
            "import unittest\n\n"
            "PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
            "SRC_DIR = os.path.join(PROJECT_ROOT, 'src')\n"
            "sys.path.insert(0, SRC_DIR)\n\n"
            "from planet import Planet\n\n"
            "class TestPlanet(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        Planet()\n",
            encoding="utf-8",
        )

        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "test_planet (unittest.loader._FailedTest.test_planet) ... ERROR\n"
            "ImportError: Failed to import test module: test_planet\n"
            "Traceback (most recent call last):\n"
            f'  File "{tests_dir / "test_planet.py"}", line 10, in <module>\n'
            "    from planet import Planet  # noqa: E402\n"
            "ModuleNotFoundError: No module named 'planet'\n"
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=["tests/test_planet.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/planet.py", "tests/test_planet.py"]

    def test_python_unittest_src_imports_authorize_missing_source_modules(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_planet.py").write_text(
            "import unittest\n\n"
            "class TestPlanet(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        from src.planet import Planet\n"
            "        Planet()\n",
            encoding="utf-8",
        )
        (tests_dir / "test_weather.py").write_text(
            "import unittest\n\n"
            "class TestWeather(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        from src.weather import Weather\n"
            "        Weather()\n",
            encoding="utf-8",
        )
        (tests_dir / "test_simulation.py").write_text(
            "import unittest\n\n"
            "class TestSimulation(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        from src.simulation import Simulation\n"
            "        Simulation()\n",
            encoding="utf-8",
        )

        error = (
            "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_planet.py' "
            "(returncode=1); tail:\n"
            "test_planet_initialization_default (test_planet.TestPlanet.test_planet_initialization_default) ... ERROR\n"
            "test_weather_initialization (test_weather.TestWeather.test_weather_initialization) ... ERROR\n"
            "test_simulation_initialization (test_simulation.TestSimulation.test_simulation_initialization) ... ERROR\n"
            "Traceback (most recent call last):\n"
            f'  File "{tests_dir / "test_planet.py"}", line 5, in test_default\n'
            "    from src.planet import Planet\n"
            "ModuleNotFoundError: No module named 'src.planet'\n"
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=[
                "tests/test_planet.py",
                "tests/test_weather.py",
                "tests/test_simulation.py",
            ],
            workspace_full=str(tmp_path),
        )

        assert targets == [
            "src/planet.py",
            "src/weather.py",
            "src/simulation.py",
            "tests/test_planet.py",
            "tests/test_weather.py",
            "tests/test_simulation.py",
        ]

    def test_python_unittest_workspace_validation_authorizes_missing_src_modules(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        (tmp_path / "src").mkdir()
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_planet.py").write_text(
            "import unittest\n"
            "from src.planet import Planet\n\n"
            "class TestPlanet(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        Planet()\n",
            encoding="utf-8",
        )
        (tests_dir / "test_weather.py").write_text(
            "import unittest\n"
            "from src.weather import Weather, Cloud, Wind\n\n"
            "class TestWeather(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        Weather(); Cloud(); Wind()\n",
            encoding="utf-8",
        )
        (tests_dir / "test_simulation.py").write_text(
            "import unittest\n"
            "from src.simulation import Simulation\n\n"
            "class TestSimulation(unittest.TestCase):\n"
            "    def test_default(self) -> None:\n"
            "        Simulation()\n",
            encoding="utf-8",
        )

        error = (
            "Artifact quality scan failed: workspace validation command failed "
            "(python -m unittest discover -s tests -p test_*.py -v); tail:\n"
            "test_planet (unittest.loader._FailedTest.test_planet) ... ERROR\n"
            "test_simulation (unittest.loader._FailedTest.test_simulation) ... ERROR\n"
            "test_weather (unittest.loader._FailedTest.test_weather) ... ERROR\n"
            "ImportError: Failed to import test module: test_planet\n"
            "Traceback (most recent call last):\n"
            f'  File "{tests_dir / "test_planet.py"}", line 2, in <module>\n'
            "    from src.planet import Planet\n"
            "ModuleNotFoundError: No module named 'src.planet'\n"
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=[
                "tests/test_planet.py",
                "tests/test_weather.py",
                "tests/test_simulation.py",
            ],
            workspace_full=str(tmp_path),
        )

        assert targets == [
            "src/planet.py",
            "src/simulation.py",
            "src/weather.py",
            "tests/test_planet.py",
            "tests/test_simulation.py",
            "tests/test_weather.py",
        ]

    def test_python_runtime_smoke_source_failure_infers_imported_local_modules(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _python_runtime_smoke_repair_target_files,
        )

        core_dir = tmp_path / "src" / "core"
        engine_dir = tmp_path / "src" / "engine"
        models_dir = tmp_path / "src" / "models"
        core_dir.mkdir(parents=True)
        engine_dir.mkdir(parents=True)
        models_dir.mkdir(parents=True)
        (engine_dir / "renderer.py").write_text("class Renderer:\n    pass\n", encoding="utf-8")
        (models_dir / "planet.py").write_text("class Planet:\n    pass\n", encoding="utf-8")
        (models_dir / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        (core_dir / "simulation.py").write_text(
            "from src.engine.renderer import Renderer\n"
            "from src.models.planet import Planet\n"
            "from src.models.weather import Cloud, Weather, Wind\n\n"
            "def build() -> None:\n"
            "    Renderer(); Planet(); Weather(); Cloud(); Wind()\n",
            encoding="utf-8",
        )

        targets = _python_runtime_smoke_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'src/core/simulation.py' "
                "(returncode=1); tail:\n"
                "ImportError: cannot import name 'Cloud' from 'src.models.weather'"
            ],
            changed_files=["src/core/simulation.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == [
            "src/engine/renderer.py",
            "src/models/planet.py",
            "src/models/weather.py",
            "src/core/simulation.py",
        ]

    @pytest.mark.asyncio
    async def test_python_unittest_quality_repair_defers_cross_task_imported_source(self, tmp_path) -> None:
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
        source = tmp_path / "src" / "models" / "weather.py"
        source.parent.mkdir(parents=True)
        source.write_text("class Weather:\n    pass\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "test_weather.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import unittest\n"
            "from src.models.weather import Weather\n\n"
            "class WeatherTest(unittest.TestCase):\n"
            "    def setUp(self) -> None:\n"
            "        self.weather = Weather(cloud_cover=0.5)\n",
            encoding="utf-8",
        )
        quality_error = (
            "Artifact quality scan failed: workspace validation command failed "
            "(python -m unittest discover -s tests -p test_*.py -v):\n"
            "Traceback (most recent call last):\n"
            f'  File "{test_file}", line 6, in setUp\n'
            "    self.weather = Weather(cloud_cover=0.5)\n"
            "TypeError: Weather.__init__() got an unexpected keyword argument 'cloud_cover'\n"
        )

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["tests/test_weather.py"]},
            target_task_id="PM-0001-3",
            run_id="run-python-unittest-cross-task-source",
            context={},
            original_message="Create Python unittest coverage.",
            llm_call_timeout=10,
            artifact_quality_errors=[quality_error],
            changed_files=["tests/test_weather.py"],
        )

        # Diagnostic evidence still names both files, but only the task-owned
        # test file is authorized for writing; the cross-task imported source
        # module is deferred with scope evidence.
        assert summary["stage"] == "quality_repair"
        assert summary["explicit_quality_target_files"] == [
            "src/models/weather.py",
            "tests/test_weather.py",
        ]
        assert summary["repair_target_files"] == ["tests/test_weather.py"]
        scope_filter = summary["task_boundary_scope_filter"]
        assert scope_filter["deferred"] is True
        assert scope_filter["task_declared_write_targets"] == ["tests/test_weather.py"]
        assert scope_filter["out_of_scope_repair_target_files"] == ["src/models/weather.py"]
        assert adapter.repair_context["repair_target_files"] == ["tests/test_weather.py"]
        quality_repair_context = adapter.repair_context["director_quality_repair"]
        assert quality_repair_context["repair_target_files"] == ["tests/test_weather.py"]
        assert quality_repair_context["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == [
            "src/models/weather.py"
        ]
        assert "tests/test_weather.py" in adapter.repair_message
        assert "src/models/weather.py" not in adapter.repair_message
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True

    @pytest.mark.asyncio
    async def test_tap_failure_repair_targets_implementation_and_carries_structured_failure_context(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A real TAP assertion must become one same-task source edit request.

        Regression for r46: the verifier had one actionable assertion failure,
        but deterministic attempt evidence (without a write) suppressed the LLM
        repair.  This adapter-level boundary proves the surviving repair request
        identifies the imported implementation, preserves structured failure
        evidence, and exposes mutation-capable tools.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        monkeypatch.delenv("KERNELONE_DIRECTOR_REPAIR_FORCE_EXISTING_WRITE", raising=False)

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None

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

        source = tmp_path / "src" / "dream.js"
        source.parent.mkdir(parents=True)
        source.write_text(
            "export function extractDreamKeywords(text) { return text.split(/\\s+/); }\n",
            encoding="utf-8",
        )
        test_file = tmp_path / "tests" / "product.test.js"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import { extractDreamKeywords } from '../src/dream.js';\n",
            encoding="utf-8",
        )
        failure = (
            "not ok 2 - 正常路径：extractDreamKeywords 提取梦境关键词\n"
            "  ---\n"
            f"  location: '{test_file}:46:1'\n"
            "  failureType: 'testCodeFailure'\n"
            "  error: |-\n"
            "    assert.ok(keywords.includes('火焰'))\n"
            "  code: 'ERR_ASSERTION'\n"
            "  expected: true\n"
            "  actual: false\n"
            "  operator: '=='\n"
            "  ...\n"
        )

        adapter = _Adapter()
        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["src/dream.js"]},
            target_task_id="TASK-1-source-core",
            run_id="run-tap-semantic-repair",
            context={},
            original_message="Implement dream keyword extraction.",
            llm_call_timeout=10,
            artifact_quality_errors=[failure],
            changed_files=["src/dream.js", "tests/product.test.js"],
        )

        assert summary["explicit_quality_target_files"] == [
            "src/dream.js",
            "tests/product.test.js",
        ]
        assert summary["repair_target_files"] == ["src/dream.js"]
        assert adapter.repair_context["repair_target_files"] == ["src/dream.js"]
        failure_errors = adapter.repair_context["failed_gate_evidence"]["quality_errors"]
        workspace_errors = adapter.repair_context["workspace_quality_evidence"]["quality_errors"]
        assert len(failure_errors) == 1
        assert len(workspace_errors) == 1
        assert "火焰" in failure_errors[0]
        forced_tools = {
            item["function"]["name"] for item in adapter.repair_context["_transaction_kernel_forced_tool_definitions"]
        }
        assert forced_tools == {"edit_file"}
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "edit_file"},
        }
        assert adapter.repair_context["_transaction_kernel_force_exact_tools"] is True
        assert adapter.repair_context["metadata"]["tool_contract"]["required_tools"] == ["edit_file"]
        assert adapter.repair_context["metadata"]["tool_contract"]["mutation_required"] is True
        assert adapter._execution.allowed_tool_names == {"edit_file"}
        assert "CURRENT UTF-8 CONTENT" in adapter.repair_message
        assert "assert.ok(keywords.includes('火焰'))" in adapter.repair_message

    @pytest.mark.asyncio
    async def test_semantic_quality_single_changed_file_repair_forces_write_context(self, tmp_path) -> None:
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
        (tmp_path / "test_calculator.py").write_text(
            "from __future__ import annotations\n\n\ndef workspace_artifact_ready() -> bool:\n    return True\n",
            encoding="utf-8",
        )

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["test_calculator.py"]},
            target_task_id="PM-0001-3",
            run_id="run-semantic-quality-write-only",
            context={},
            original_message="Create meaningful calculator tests.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "Director output quality gate failed: no project-domain signal found in changed files; "
                "expected one of calculator, arithmetic, expression"
            ],
            changed_files=["test_calculator.py"],
        )

        assert summary["missing_target_files"] == []
        assert summary["semantic_quality_target_files"] == ["test_calculator.py"]
        assert summary["repair_target_files"] == ["test_calculator.py"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == [
            "test_calculator.py"
        ]
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "test_calculator.py" in adapter.repair_message

    def test_semantic_quality_repair_uses_explicit_error_path_when_multiple_files_changed(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        (tmp_path / "README.md").write_text("# Todo App\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_product.py").write_text("raise NotImplementedError\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Director output quality gate failed: generic/placeholder content detected: "
                "tests/test_product.py:\\bNotImplemented(?:Error|Exception)?\\b"
            ],
            changed_files=["README.md", "tests/test_product.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["tests/test_product.py"]

    def test_semantic_quality_repair_targets_exporter_for_typescript_symbol_errors(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (tmp_path / "package.json").write_text('{"scripts":{"test":"node dist/simulator.test.js"}}\n', encoding="utf-8")
        (src_dir / "index.ts").write_text("export const version = '0.1.0';\n", encoding="utf-8")
        (src_dir / "main.ts").write_text("import { Garden } from './index';\nvoid Garden;\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Artifact quality scan failed: npm package manifest script 'test' references missing local "
                "entrypoint 'dist/simulator.test.js' in package.json",
                "Artifact quality scan failed: unresolved import symbol 'Garden' from './index' in src/main.ts "
                "(sibling module does not define it)",
                "Artifact quality scan failed: TypeScript project typecheck failed: src/index.ts(12,8): "
                "error TS2305: Module '\"./main\"' has no exported member 'GardenSnapshot'.",
            ],
            changed_files=["package.json", "src/index.ts", "src/main.ts"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/index.ts", "src/main.ts", "package.json"]

    def test_unresolved_symbol_without_typecheck_triggers_exporter_repair(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _select_materialization_quality_repair_target_batch,
            _semantic_quality_repair_target_files,
            _should_preserve_materialization_quality_repair_batch,
        )

        src_dir = tmp_path / "src"
        tests_dir = tmp_path / "tests"
        src_dir.mkdir()
        tests_dir.mkdir()
        (src_dir / "verify.ts").write_text("export const verify = () => true;\n", encoding="utf-8")
        (tests_dir / "verify.test.ts").write_text(
            "import { runVerify } from '../src/verify';\nvoid runVerify;\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: unresolved import symbol 'runVerify' "
            "from '../src/verify' in tests/verify.test.ts (sibling module does not define it)"
        ]

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=quality_errors,
            changed_files=["README.md", "src/verify.ts", "tests/verify.test.ts", "package.json"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/verify.ts", "tests/verify.test.ts"]
        assert _should_preserve_materialization_quality_repair_batch(quality_errors) is True
        assert _select_materialization_quality_repair_target_batch(
            ["src/verify.ts", "tests/verify.test.ts"],
            repair_attempt=2,
            preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(quality_errors),
        ) == ["src/verify.ts", "tests/verify.test.ts"]

    def test_unresolved_symbol_with_typescript_extension_preserves_importer_and_exporter_batch(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _select_materialization_quality_repair_target_batch,
            _semantic_quality_repair_target_files,
            _should_preserve_materialization_quality_repair_batch,
        )

        src_dir = tmp_path / "src"
        tests_dir = tmp_path / "tests"
        src_dir.mkdir()
        tests_dir.mkdir()
        (src_dir / "verify.ts").write_text("export const verify = () => true;\n", encoding="utf-8")
        (tests_dir / "verify.test.ts").write_text(
            "import { runChecks } from '../src/verify.ts';\nvoid runChecks;\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: unresolved import symbol 'runChecks' from '../src/verify.ts' "
            "in tests/verify.test.ts (sibling module does not define it)"
        ]

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=quality_errors,
            changed_files=["README.md", "src/verify.ts", "tests/verify.test.ts", "package.json"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/verify.ts", "tests/verify.test.ts"]
        assert _select_materialization_quality_repair_target_batch(
            targets,
            repair_attempt=4,
            rotate_after_first_attempt=True,
            preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(quality_errors),
        ) == ["src/verify.ts", "tests/verify.test.ts"]

    def test_workspace_typecheck_repair_ignores_tsc_config_argument_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":true}}\n', encoding="utf-8")
        (src_dir / "index.ts").write_text(
            "export const report = (): MissingType => ({} as never);\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: workspace validation command failed (npm run build): "
            "> glow-bug-garden-simulator@1.0.0 build\n"
            "> tsc -p tsconfig.json\n\n"
            "src/index.ts(32,14): error TS2304: Cannot find name 'MoonPhase'.",
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/index.ts(32,14): error TS2304: Cannot find name 'MoonPhase'.",
        ]

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=quality_errors,
            changed_files=["package.json", "tsconfig.json", "src/index.ts"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/index.ts"]

    def test_semantic_quality_repair_targets_unknown_exporter_and_usage_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _select_materialization_quality_repair_target_batch,
            _semantic_quality_repair_target_files,
            _should_preserve_materialization_quality_repair_batch,
        )

        src_dir = tmp_path / "src"
        domain_dir = src_dir / "domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "humidity.ts").write_text(
            "export const adjustHumidity: unknown = undefined;\n",
            encoding="utf-8",
        )
        (src_dir / "main.ts").write_text(
            "import { adjustHumidity } from './domain/humidity';\n"
            "const humidity = adjustHumidity({ value: 0.5 }, 0.1);\n"
            "void humidity;\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/main.ts(2,18): error TS18046: 'adjustHumidity' is of type 'unknown'."
        ]

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=quality_errors,
            changed_files=["src/main.ts", "src/domain/humidity.ts"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/domain/humidity.ts", "src/main.ts"]
        assert _select_materialization_quality_repair_target_batch(
            targets,
            repair_attempt=2,
            rotate_after_first_attempt=True,
            preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(quality_errors),
        ) == ["src/domain/humidity.ts", "src/main.ts"]

    def test_semantic_quality_repair_targets_type_only_exporter_and_usage_file(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _select_materialization_quality_repair_target_batch,
            _semantic_quality_repair_target_files,
            _should_preserve_materialization_quality_repair_batch,
        )

        src_dir = tmp_path / "src"
        domain_dir = src_dir / "domain"
        domain_dir.mkdir(parents=True)
        (domain_dir / "moon.ts").write_text(
            "export type MoonPhase = 'new' | 'full';\nexport interface Moon { phase: MoonPhase; }\n",
            encoding="utf-8",
        )
        (src_dir / "index.ts").write_text(
            "import { Moon } from './domain/moon';\nconst moon = new Moon();\nvoid moon;\n",
            encoding="utf-8",
        )
        quality_errors = [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/index.ts(2,18): error TS2693: 'Moon' only refers to a type, but is being used as a value here."
        ]

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=quality_errors,
            changed_files=["src/index.ts", "src/domain/moon.ts"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/domain/moon.ts", "src/index.ts"]
        assert _select_materialization_quality_repair_target_batch(
            targets,
            repair_attempt=2,
            rotate_after_first_attempt=True,
            preserve_batch_after_first_attempt=_should_preserve_materialization_quality_repair_batch(quality_errors),
        ) == ["src/domain/moon.ts", "src/index.ts"]

    def test_explicit_quality_repair_targets_vitest_source_and_test_files(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        source = tmp_path / "src" / "index.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export function updateFirefly() {}\n", encoding="utf-8")
        test_file = tmp_path / "tests" / "index.test.ts"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import { updateFirefly } from '../src/index';\n"
            "import { describe, expect, it } from 'vitest';\n"
            "describe('updateFirefly', () => {\n"
            "  it('bounces', () => expect(updateFirefly()).toBe(true));\n"
            "});\n",
            encoding="utf-8",
        )

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[
                "FAIL tests/index.test.ts > Firefly Garden Simulator > updateFirefly > should bounce firefly off walls\n"
                "AssertionError: expected 3 to be less than 0\n"
                " ❯ tests/index.test.ts:80:26"
            ],
            changed_files=["src/index.ts", "tests/index.test.ts"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/index.ts", "tests/index.test.ts"]

    def test_explicit_quality_repair_maps_node_test_dist_stack_to_typescript_sources(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        models_dir = tmp_path / "src" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "Market.ts").write_text("export class Market {}\n", encoding="utf-8")
        (models_dir / "Inventory.ts").write_text("export class Inventory {}\n", encoding="utf-8")
        (models_dir / "Market.test.ts").write_text(
            "import { Market } from './Market';\n"
            "import { Inventory } from './Inventory';\n"
            "void Market;\n"
            "void Inventory;\n",
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: workspace validation command failed (npm test):\n"
            "not ok 7 - Inventory behavior\n"
            "  ---\n"
            "  failureType: 'testCodeFailure'\n"
            "  error: |-\n"
            "    Expected values to be strictly equal:\n"
            "    'c' !== 'b'\n"
            "  code: 'ERR_ASSERTION'\n"
            "  name: 'AssertionError'\n"
            "  stack: |-\n"
            f"    TestContext.<anonymous> ({tmp_path}/dist/models/Market.test.js:121:22)\n"
            "  ...\n"
        )

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=[
                "package.json",
                "tsconfig.json",
                "src/models/Inventory.ts",
                "src/models/Market.test.ts",
                "src/models/Market.ts",
            ],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/models/Inventory.ts", "src/models/Market.test.ts", "src/models/Market.ts"]

    def test_explicit_quality_repair_uses_changed_node_test_imports_when_output_tail_loses_stack(
        self, tmp_path
    ) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        models_dir = tmp_path / "src" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "Market.ts").write_text("export class Market {}\n", encoding="utf-8")
        (models_dir / "Inventory.ts").write_text("export class Inventory {}\n", encoding="utf-8")
        (models_dir / "Market.test.ts").write_text(
            "import { Market } from './Market';\n"
            "import { Inventory } from './Inventory';\n"
            "void Market;\n"
            "void Inventory;\n",
            encoding="utf-8",
        )
        error = "step verify failed (exit 1): npm run test ::\n1..13\n# tests 13\n# pass 10\n# fail 3\n"

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=[
                "package.json",
                "tsconfig.json",
                "src/models/Inventory.ts",
                "src/models/Market.test.ts",
                "src/models/Market.ts",
            ],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/models/Market.ts", "src/models/Inventory.ts", "src/models/Market.test.ts"]

    def test_explicit_quality_repair_targets_python_unittest_imported_source(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        source = tmp_path / "src" / "models" / "weather.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            "class Weather:\n    def __init__(self, condition: str) -> None:\n        self.condition = condition\n",
            encoding="utf-8",
        )
        test_file = tmp_path / "tests" / "test_weather.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text(
            "import unittest\n"
            "from src.models.weather import Weather\n\n"
            "class WeatherTest(unittest.TestCase):\n"
            "    def setUp(self) -> None:\n"
            "        self.weather = Weather(cloud_cover=0.5)\n",
            encoding="utf-8",
        )
        error = (
            "Artifact quality scan failed: workspace validation command failed "
            "(python -m unittest discover -s tests -p test_*.py -v):\n"
            "ERROR: test_summary (tests.test_weather.WeatherTest.test_summary)\n"
            "Traceback (most recent call last):\n"
            f'  File "{test_file}", line 6, in setUp\n'
            "    self.weather = Weather(cloud_cover=0.5)\n"
            "TypeError: Weather.__init__() got an unexpected keyword argument 'cloud_cover'\n"
        )

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[error],
            changed_files=["tests/test_weather.py"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/models/weather.py", "tests/test_weather.py"]

    def test_explicit_artifact_quality_repair_targets_prettier_syntax_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _explicit_artifact_quality_repair_target_files,
        )

        source = tmp_path / "src" / "engine" / "simulation.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const broken = 'unterminated\n", encoding="utf-8")
        (tmp_path / "index.html").write_text('<div id="app"></div>\n', encoding="utf-8")

        targets = _explicit_artifact_quality_repair_target_files(
            artifact_quality_errors=[
                "[error] src/engine/simulation.ts: SyntaxError: Unterminated string literal. (1:24)"
            ],
            changed_files=["src/engine/simulation.ts", "index.html"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/engine/simulation.ts"]

    @pytest.mark.asyncio
    async def test_quality_repair_forces_write_for_explicit_artifact_syntax_path(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            def __init__(self) -> None:
                self.allowed_tool_names: set[str] | None = None
                self.allow_patch_fallback: bool | None = None

            @staticmethod
            def extract_kernel_tool_results(result) -> list:
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

        source = tmp_path / "src" / "engine" / "simulation.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const broken = 'unterminated\n", encoding="utf-8")
        (tmp_path / "index.html").write_text('<div id="app"></div>\n', encoding="utf-8")

        adapter = _Adapter()
        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": ["src/engine/simulation.ts", "index.html"]},
            target_task_id="PM-0001-9",
            run_id="run-explicit-artifact-quality-write-only",
            context={},
            original_message="Implement the simulation engine and app shell.",
            llm_call_timeout=10,
            artifact_quality_errors=[
                "[error] src/engine/simulation.ts: SyntaxError: Unterminated string literal. (1:24)"
            ],
            changed_files=["src/engine/simulation.ts", "index.html"],
        )

        assert summary["explicit_quality_target_files"] == ["src/engine/simulation.ts"]
        assert summary["repair_target_files"] == ["src/engine/simulation.ts"]
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == [
            "src/engine/simulation.ts"
        ]
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert "EXISTING FAILED TARGET FILES" in adapter.repair_message
        assert "SINGLE FAILED TARGET REPAIR" in adapter.repair_message
        assert "src/engine/simulation.ts" in adapter.repair_message

    def test_semantic_quality_repair_accepts_new_catalog_languages(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _semantic_quality_repair_target_files,
        )

        source = tmp_path / "src" / "dragon_factory.go"
        source.parent.mkdir(parents=True)
        source.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")

        targets = _semantic_quality_repair_target_files(
            artifact_quality_errors=[
                "Director output quality gate failed: generic/placeholder content detected: src/dragon_factory.go:TODO"
            ],
            changed_files=["README.md", "src/dragon_factory.go"],
            workspace_full=str(tmp_path),
        )

        assert targets == ["src/dragon_factory.go"]

    @pytest.mark.asyncio
    async def test_multi_missing_target_repair_forces_write_tool_choice(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """General C7 L6 gap: when multiple declared target files are missing,
        the weak Director LLM (e.g. qwen3.6-27b-int4) is structurally prone
        to echo the long repair prompt back as its response with zero tool
        calls. Live factory-bench L6-32 (2026-06-17): all three repair
        attempts echoed the prompt verbatim and the loop broke after the hard
        cap. The single-missing path already forces tool_choice=write_file;
        the multi-missing path must enforce the same structural constraint
        so the model cannot sidestep the contract.
        """
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _run_materialization_quality_repair_retry,
        )

        # Deterministic repair must NOT mask this with synthesized content;
        # we are testing the LLM repair path contract, not the synthesizer.
        monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
        monkeypatch.delenv("KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS", raising=False)

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result) -> list:
                return []

            @staticmethod
            async def execute_tools(content, target_task_id, update_task_progress, **_) -> list:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *a, **k: None)

            def __init__(self) -> None:
                self.repair_context: dict[str, Any] = {}
                self.repair_message = ""
                self.invocations = 0

            async def _invoke_role_dialogue_with_timeout(self, message, *, context, timeout_seconds, stage_label):
                del timeout_seconds, stage_label
                self.invocations += 1
                self.repair_message = message
                self.repair_context = context
                return {"content": message}

        (tmp_path / "services" / "user_service").mkdir(parents=True)
        (tmp_path / "services" / "product_service").mkdir(parents=True)
        (tmp_path / "common").mkdir(parents=True)
        adapter = _Adapter()

        missing_targets = [
            "services/__init__.py",
            "services/user_service/__init__.py",
            "services/product_service/__init__.py",
            "common/__init__.py",
            "scripts/run_all.py",
        ]
        artifact_quality_errors = [
            f"Artifact quality scan failed: declared target file missing '{p}'" for p in missing_targets
        ]

        _, summary = await _run_materialization_quality_repair_retry(
            adapter,
            task={"target_files": missing_targets},
            target_task_id="PM-0001-1",
            run_id="run-multi-missing-forced-write",
            context={},
            original_message="Create Python microservice skeleton with 4 services.",
            llm_call_timeout=10,
            artifact_quality_errors=artifact_quality_errors,
            changed_files=["pyproject.toml"],
        )

        assert adapter.invocations == 1, summary
        assert adapter.repair_context["task_id"] == "PM-0001-1"
        assert adapter.repair_context["metadata"]["task_id"] == "PM-0001-1"
        assert adapter.repair_context["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }, adapter.repair_context
        forced_defs = adapter.repair_context["_transaction_kernel_forced_tool_definitions"]
        assert forced_defs and forced_defs[0]["function"]["name"] == "write_file"
        assert forced_defs[0]["function"]["parameters"]["required"] == [
            "file",
            "content",
        ]
        assert adapter.repair_context["_transaction_kernel_force_exact_tools"] is True
        assert "SINGLE MISSING TARGET REPAIR" not in adapter.repair_message
        assert "write_only_single_target" not in adapter.repair_context["director_quality_repair"]
        assert adapter.repair_context["director_quality_repair"]["repair_target_files"] == missing_targets
        for missing_target in missing_targets:
            assert missing_target in adapter.repair_message
        assert "MISSING TARGET FILES" in adapter.repair_message
        assert summary["missing_target_files"] == missing_targets
        assert summary["repair_target_files"] == missing_targets

    @pytest.mark.asyncio
    async def test_materialization_quality_repair_promotes_task_contract_context(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _run_materialization_quality_repair_retry,
        )

        class _Execution:
            @staticmethod
            def extract_kernel_tool_results(result) -> list:
                del result
                return []

            @staticmethod
            async def execute_tools(content, target_task_id, update_task_progress, **_) -> list:
                del content, target_task_id, update_task_progress
                return []

        class _Adapter:
            workspace = str(tmp_path)
            _execution = _Execution()
            _update_task_progress = staticmethod(lambda *a, **k: None)

            def __init__(self) -> None:
                self.promote_calls = 0
                self.repair_context: dict[str, Any] = {}

            def _promote_task_contract_to_runtime_context(
                self,
                *,
                task: dict[str, Any],
                context: dict[str, Any],
                workspace: str,
            ) -> None:
                self.promote_calls += 1
                assert workspace == str(tmp_path)
                task_metadata = task.get("metadata")
                assert isinstance(task_metadata, dict)
                metadata = dict(context.get("metadata") or {})
                for key in ("module_interface_contract", "delivery_plan_document"):
                    context[key] = task_metadata[key]
                    metadata[key] = task_metadata[key]
                context["metadata"] = metadata

            async def _invoke_role_dialogue_with_timeout(self, message, *, context, timeout_seconds, stage_label):
                del message, timeout_seconds, stage_label
                self.repair_context = context
                return {"content": ""}

        source = tmp_path / "src" / "engine" / "runner.js"
        source.parent.mkdir(parents=True)
        source.write_text("module.exports = { broken: true\n", encoding="utf-8")
        task: dict[str, Any] = {
            "target_files": ["src/engine/runner.js"],
            "metadata": {
                "module_interface_contract": {
                    "schema_version": "chief_engineer.module_interface_contract.v1",
                    "modules": [{"path": "src/engine/runner.js", "planned_public_symbols": ["runQueue"]}],
                },
                "delivery_plan_document": {
                    "schema_version": "polaris.delivery_plan_document.v1",
                    "capability_plan": ["core engine implements the queue rules"],
                    "behavior_plan": ["architecture plan keeps engine and entrypoint separate"],
                },
            },
        }
        adapter = _Adapter()

        await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id="TASK-1-source-core",
            run_id="run-quality-contract-context",
            context={},
            original_message="Implement the core engine/service modules.",
            llm_call_timeout=10,
            artifact_quality_errors=["[error] src/engine/runner.js: SyntaxError: Unexpected end of input. (1:29)"],
            changed_files=["src/engine/runner.js"],
        )

        assert adapter.promote_calls == 1
        assert adapter.repair_context["module_interface_contract"] == task["metadata"]["module_interface_contract"]
        assert adapter.repair_context["delivery_plan_document"] == task["metadata"]["delivery_plan_document"]
        assert (
            adapter.repair_context["metadata"]["module_interface_contract"]
            == task["metadata"]["module_interface_contract"]
        )

    def test_materialization_quality_repair_retry_after_first_attempt_stays_single_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _select_materialization_quality_repair_target_batch,
        )

        missing_targets = [
            "src/main.ts",
            "README.md",
            "tests/main.test.ts",
        ]

        assert _select_materialization_quality_repair_target_batch(missing_targets, repair_attempt=1) == missing_targets
        assert _select_materialization_quality_repair_target_batch(missing_targets, repair_attempt=2) == ["src/main.ts"]

    def test_materialization_quality_repair_preserves_coupled_batch_after_first_attempt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _select_materialization_quality_repair_target_batch,
        )

        repair_targets = [
            "package.json",
            "src/index.ts",
            "src/main.ts",
        ]

        assert (
            _select_materialization_quality_repair_target_batch(
                repair_targets,
                repair_attempt=3,
                rotate_after_first_attempt=True,
                preserve_batch_after_first_attempt=True,
            )
            == repair_targets
        )

    def test_materialization_quality_repair_retry_can_rotate_single_target_after_first_attempt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _select_materialization_quality_repair_target_batch,
        )

        missing_targets = [
            "src/main.ts",
            "README.md",
            "tests/main.test.ts",
        ]

        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=2,
            rotate_after_first_attempt=True,
        ) == ["README.md"]
        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=3,
            rotate_after_first_attempt=True,
        ) == ["tests/main.test.ts"]
        assert _select_materialization_quality_repair_target_batch(
            missing_targets,
            repair_attempt=4,
            rotate_after_first_attempt=True,
        ) == ["src/main.ts"]

