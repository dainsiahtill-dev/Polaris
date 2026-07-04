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

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.chief_engineer.blueprint.public import BlueprintPersistence
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.director.runtime.public.repair_kernel_contracts import (
    build_substantive_node_test_script as _build_substantive_node_test_script,
    is_overstrict_node_test_script_contract as _is_overstrict_node_test_script_contract,
    remove_patch_residue_lines as _remove_patch_residue_lines,
)
from polaris.cells.qa.audit_verdict.public import QaFailureClassV1
from polaris.cells.roles.adapters.internal.director import execute_method as execute_method_module
from polaris.cells.roles.adapters.internal.director.adapter import (
    DirectorAdapter,
    _build_director_actual_sibling_exports_payload,
    _build_director_blueprint_handoff_lines,
    _build_director_workspace_interface_lines,
    _director_actual_interface_injection_enabled,
    _inject_director_actual_sibling_exports,
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
    _extract_task_target_path_candidates,
    _finalize_claimed_execution,
    _materialization_task_boundary_triage_summary,
    _no_write_materialization_retry_needed,
    _no_write_materialization_retry_tool_definitions,
    _pin_file_schema_to_declared_targets,
    _resolve_claim_external_task_id,
    _run_empty_write_content_materialization_retry,
    _task_requires_fresh_materialization,
    _task_runtime_finalization_failed_result,
    execute_director_task,
)
from polaris.cells.roles.adapters.internal.director.execute_method_repair_bridge import (
    run_patch_residue_cleanup,
    run_python_runtime_smoke,
    run_python_static_smoke,
)
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _extract_task_interface_contract,
    _go_runtime_smoke_repair_target_files,
    _materialization_interface_discrepancy_evidence,
    _materialization_interface_discrepancy_retry_authorized,
    _quality_repair_edit_file_tool_definition,
    _quality_repair_write_file_tool_definition,
)
from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.adapters.public import service as roles_adapters_public_service
from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
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

    result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task={},
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            advisor_notes=tuple(advisor_notes),
        )
    )
    return [dict(item) for item in result.tool_results]


def _make_adapter(tmp_path: Any, task_board: Any = None, task_runtime: Any = None) -> DirectorAdapter:
    """Create a DirectorAdapter with mocked heavy dependencies."""
    if task_board is None and task_runtime is None:
        adapter = DirectorAdapter(workspace=str(tmp_path))
    else:
        adapter = DirectorAdapter(workspace=str(tmp_path), task_board=task_board, task_runtime=task_runtime)
    return adapter


def test_prepare_role_dialogue_context_bounds_forced_write_retry_budget() -> None:
    context, timeout = _prepare_role_dialogue_context(
        {
            "_transaction_kernel_forced_tool_choice": {
                "type": "function",
                "function": {"name": "write_file"},
            },
            "director_no_write_materialization_retry": {
                "write_only_declared_targets": {"target_files": ["package.json"]}
            },
        },
        timeout_seconds=660.0,
        stage_label="no_write_materialization_retry",
    )

    assert timeout == 120.0
    assert context["llm_call_timeout_ceiling_seconds"] == 120.0
    assert context["request_timeout_ceiling_seconds"] == 120.0
    assert context["llm_max_tokens"] == 7000
    assert context["director_role_call_timeout_budget"]["stage_label"] == "no_write_materialization_retry"
    assert context["director_forced_write_output_budget"]["max_tokens"] == 7000


def test_prepare_role_dialogue_context_caps_existing_large_forced_write_budget() -> None:
    context, timeout = _prepare_role_dialogue_context(
        {
            "max_tokens": 128_000,
            "max_output_tokens": 65_536,
            "director_no_write_materialization_retry": {
                "write_only_declared_targets": {"target_files": ["package.json"]}
            },
        },
        timeout_seconds=660.0,
        stage_label="no_write_materialization_retry",
    )

    assert timeout == 120.0
    assert context["llm_max_tokens"] == 7000
    assert context["max_tokens"] == 128_000
    budget = context["director_forced_write_output_budget"]
    assert budget["max_tokens"] == 7000
    assert budget["ceiling_tokens"] == 7000
    assert budget["previous_budget_values"] == {
        "max_output_tokens": 65_536,
        "max_tokens": 128_000,
    }


def test_extract_task_interface_contract_accepts_ce_module_interface_contract_alias() -> None:
    contract = {
        "exports": {"src/models/weather.py": ["WeatherSnapshot"]},
        "consumes": {"src/engine/forecast.py": ["WeatherSnapshot"]},
    }

    assert _extract_task_interface_contract({"module_interface_contract": contract}) == contract
    assert _extract_task_interface_contract({"metadata": {"module_interface_contract": contract}}) == contract


def test_materialization_interface_discrepancy_uses_module_interface_contract_for_director_route() -> None:
    evidence = _materialization_interface_discrepancy_evidence(
        task={
            "id": "TASK-2",
            "metadata": {
                "module_interface_contract": {
                    "exports": {"src/models/weather.py": ["WeatherSnapshot"]},
                    "consumes": {"src/engine/forecast.py": ["WeatherSnapshot"]},
                }
            },
        },
        plan_probe={
            "status": "coverage_matched_but_unplannable",
            "covered_unplannable_source_tools": ["deterministic_python_missing_export_repair"],
            "covered_unplannable_diagnostics": [
                {"path": "src/engine/forecast.py", "message": "module has no exported member"}
            ],
        },
        repair_target_files=["src/engine/forecast.py"],
        artifact_quality_errors=["module has no exported member"],
    )

    assert evidence["task_interface_contract_present"] is True
    assert evidence["recommended_owner"] == "director"
    assert evidence["recommended_route"] == "director_retry_with_interface_discrepancy_context"
    assert evidence["director_retry_allowed"] is True
    assert evidence["llm_fallback_blocked"] is False
    assert evidence["interface_delta"]["contract_present"] is True
    assert evidence["triage_summary"]["reason"] == "director_local_retry_with_interface_delta"
    assert "exports" in evidence["task_interface_contract_keys"]


def test_materialization_interface_discrepancy_retry_authorization_accepts_standard_and_legacy_keys() -> None:
    evidence = {
        "reason": "coverage_matched_but_unplannable",
        "recommended_owner": "director",
        "recommended_route": "director_retry_with_interface_discrepancy_context",
    }

    assert _materialization_interface_discrepancy_retry_authorized(
        context={
            "director_interface_discrepancy_retry": {
                "authorized": True,
                "interface_discrepancy_evidence": evidence,
            }
        },
        evidence=evidence,
    )
    assert _materialization_interface_discrepancy_retry_authorized(
        context={
            "task_boundary_interface_discrepancy_retry": {
                "authorized": True,
                "interface_discrepancy_evidence": evidence,
            }
        },
        evidence=evidence,
    )


def test_materialization_quality_repair_prompt_includes_interface_discrepancy_context_json() -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _build_materialization_quality_repair_message,
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair TypeScript verification exports.",
        artifact_quality_errors=[
            "Artifact quality scan failed: unresolved import symbol 'runVerification' "
            "from '../src/verify.js' in tests/verify.test.ts (sibling module does not define it)"
        ],
        changed_files=["src/verify.ts", "tests/verify.test.ts"],
        repair_target_files=["tests/verify.test.ts"],
        interface_discrepancy_evidence={
            "schema_version": "director.interface_discrepancy_receipt.v1",
            "recommended_owner": "director",
            "recommended_route": "director_retry_with_interface_discrepancy_context",
            "director_retry_allowed": True,
            "llm_fallback_blocked": False,
            "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
            "interface_delta": {
                "schema_version": "director.interface_delta.v1",
                "requested_symbols": ["runVerification"],
                "actual_public_symbols_by_path": {"src/verify.ts": ["verify"]},
            },
            "triage_summary": {
                "schema_version": "director.interface_discrepancy_triage.v1",
                "reason": "director_local_retry_with_interface_delta",
            },
            "diagnostics": [{"code": "unresolved_import_symbol", "path": "tests/verify.test.ts"}],
        },
    )

    assert "INTERFACE DISCREPANCY CONTEXT JSON" in message
    assert '"interface_delta"' in message
    assert '"triage_summary"' in message
    assert "runVerification" in message
    assert "director_local_retry_with_interface_delta" in message


def test_materialization_task_boundary_triage_summary_preserves_director_retry_evidence() -> None:
    source_evidence = {
        "schema_version": "director.interface_discrepancy_receipt.v1",
        "task_id": "TASK-2",
        "reason": "coverage_matched_but_unplannable",
        "recommended_owner": "director",
        "recommended_route": "director_retry_with_interface_discrepancy_context",
        "director_retry_allowed": True,
        "llm_fallback_blocked": False,
        "interface_delta": {
            "schema_version": "director.interface_delta.v1",
            "requested_symbols": ["WeatherKind"],
            "actual_public_symbols_by_path": {"src/models/weather.py": ["WeatherReport"]},
        },
        "triage_summary": {
            "schema_version": "director.interface_discrepancy_triage.v1",
            "reason": "director_local_retry_with_interface_delta",
        },
        "metadata": {"interface_delta_available": True},
    }
    summary = _materialization_task_boundary_triage_summary(
        {
            "task_id": "TASK-2",
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "covered_unplannable_source_tools": ["deterministic_python_missing_export_repair"],
                "covered_unplannable_diagnostic_count": 1,
            },
            "task_boundary_interface_discrepancy_retry_authorized": True,
            "interface_discrepancy_evidence": source_evidence,
        },
        repair_attempt=2,
        artifact_quality_errors=["module has no exported member WeatherKind"],
    )

    assert summary["director_retry_allowed"] is True
    assert summary["llm_fallback_blocked"] is False
    assert summary["task_boundary_interface_discrepancy_retry_authorized"] is True
    receipt = summary["interface_discrepancy_evidence"]
    assert receipt["director_retry_allowed"] is True
    assert receipt["llm_fallback_blocked"] is False
    assert receipt["interface_delta"]["requested_symbols"] == ["WeatherKind"]
    assert receipt["triage_summary"]["reason"] == "director_local_retry_with_interface_delta"
    assert receipt["metadata"]["repair_attempt"] == 2


def test_director_actual_interface_injection_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", raising=False)
    assert _director_actual_interface_injection_enabled() is True

    monkeypatch.setenv("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", "0")
    assert _director_actual_interface_injection_enabled() is False


def test_director_actual_sibling_exports_promoted_to_context_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    def fake_snapshot(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        return SimpleNamespace(
            physical_exports={
                "src/models/stall.ts": [
                    SimpleNamespace(name="Stall", symbol_kind="class", signature="class Stall"),
                    SimpleNamespace(
                        name="createStall",
                        symbol_kind="function",
                        signature="function createStall(): Stall",
                    ),
                ]
            }
        )

    monkeypatch.setattr(
        "polaris.kernelone.quality.cross_artifact_interfaces.build_symbol_index_snapshot",
        fake_snapshot,
    )

    payload = _build_director_actual_sibling_exports_payload(str(tmp_path))
    assert payload["schema_version"] == "polaris.actual_sibling_exports.evidence.v1"
    assert payload["source"] == "roles.adapters.director.workspace_symbol_index"
    assert payload["modules"][0]["path"] == "src/models/stall.ts"
    assert payload["modules"][0]["symbols"] == ["Stall", "createStall"]

    context: dict[str, Any] = {"metadata": {"task_id": "TASK-2"}}
    _inject_director_actual_sibling_exports(context, workspace=str(tmp_path))

    assert context["actual_sibling_exports"] == payload
    assert context["metadata"]["actual_sibling_exports"] == payload


def test_director_workspace_interface_lines_mark_actual_exports_authoritative(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "index.js").write_text("export function createIndex() { return {}; }\n", encoding="utf-8")

    def fake_snapshot(workspace: str) -> SimpleNamespace:
        assert workspace == str(tmp_path)
        return SimpleNamespace(
            physical_exports={
                "src/index.js": [
                    SimpleNamespace(
                        name="createIndex",
                        symbol_kind="function",
                        signature="function createIndex(): object",
                    )
                ]
            }
        )

    monkeypatch.setattr(
        "polaris.kernelone.quality.cross_artifact_interfaces.build_symbol_index_snapshot",
        fake_snapshot,
    )

    text = "\n".join(_build_director_workspace_interface_lines(str(tmp_path)))

    assert "TEST/CONFIG/DOC TASK HARD RULE" in text
    assert "planned_exports/tentative_exports are advisory" in text
    assert "src/index.js: createIndex" in text


def test_director_blueprint_handoff_projects_module_interface_contract(tmp_path: Any) -> None:
    BlueprintPersistence(str(tmp_path)).save(
        "bp-interface",
        {
            "blueprint_id": "bp-interface",
            "task_id": "task-1",
            "target_files": ["src/models/stall.ts", "src/main.ts"],
            "module_interface_contract": {
                "schema_version": "chief_engineer.module_interface_contract.v1",
                "modules": [
                    {
                        "path": "src/models/stall.ts",
                        "role": "domain_model",
                        "planned_public_symbols": ["Stall", "StallState"],
                    },
                    {
                        "path": "src/main.ts",
                        "role": "entrypoint",
                        "planned_public_symbols": ["main"],
                    },
                ],
                "rules": [
                    "Every symbol imported from a sibling target module must be defined by that module in the same task."
                ],
            },
        },
    )

    lines = _build_director_blueprint_handoff_lines(str(tmp_path), "bp-interface")
    text = "\n".join(lines)

    assert "- module_interface_contract: authority=handoff_guidance_not_scope_authority" in text
    assert (
        "src/models/stall.ts [domain_model]: tentative_exports Stall, StallState "
        "(authority=handoff_guidance_not_scope_authority)"
    ) in text
    assert "interface rule: Every symbol imported from a sibling target module" in text


def _run_runtime_director_repair(
    tmp_path: Any,
    *,
    source_tool: str,
    artifact_quality_errors: list[str],
    relative_paths: tuple[str, ...],
    task_id: str = "task-1",
    use_editor: bool = True,
) -> list[dict[str, Any]]:
    workspace = Path(tmp_path)
    base_files = {
        relative_path: (workspace / relative_path).read_text(encoding="utf-8") for relative_path in relative_paths
    }
    return run_runtime_repair_with_director_tools(
        _make_adapter(tmp_path),
        workspace_path=workspace,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=relative_paths,
        use_editor=use_editor,
    )


def _write_substantive_node_test_script(tmp_path: Any) -> None:
    script_path = tmp_path / "scripts" / "test.mjs"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "import assert from 'node:assert/strict';\n"
        "assert.equal(typeof process.version, 'string');\n"
        "console.log('node smoke test passed');\n",
        encoding="utf-8",
    )


def _assert_execute_method_attr_missing(name: str) -> None:
    with pytest.raises(AttributeError, match=name):
        getattr(execute_method_module, name)


def test_execute_method_legacy_repair_helper_surface_is_removed() -> None:
    assert not hasattr(execute_method_module, "get_legacy_execute_method_repair_helper")
    assert not hasattr(execute_method_module, "__getattr__")

    _assert_execute_method_attr_missing("_apply_deterministic_patch_residue_cleanup")
    _assert_execute_method_attr_missing("_apply_deterministic_not_a_registered_helper")


@pytest.mark.parametrize(
    "helper_name",
    [
        "_apply_deterministic_javascript_test_missing_target_repair",
        "_apply_deterministic_python_package_shadow_bridge_repair",
        "_apply_deterministic_python_runtime_smoke",
        "_apply_deterministic_python_static_smoke",
        "_apply_deterministic_python_unittest_runtime_failure_repair",
        "_apply_deterministic_unresolved_import_symbol_repair",
    ],
)
def test_execute_method_repair_bridge_blocks_migrated_materialization_helpers(helper_name: str) -> None:
    _assert_execute_method_attr_missing(helper_name)


def test_execute_method_repair_bridge_rust_helper_fails_closed() -> None:
    _assert_execute_method_attr_missing("_apply_deterministic_rust_crate_import_repair")
    _assert_execute_method_attr_missing("repair_rust_crate_imports")


def test_execute_method_repair_bridge_has_no_allowlisted_legacy_helpers() -> None:
    _assert_execute_method_attr_missing("_apply_deterministic_scaffold_marker_cleanup")


@pytest.mark.asyncio
async def test_execute_director_task_propagates_selected_task_identity_to_role_runtime_context(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = {"id": "selected-task-2", "subject": "Implement selected task"}
    captured: dict[str, Any] = {}

    class FakeStateTracker:
        def build_taskboard_observation_snapshot(self, task_runtime: Any) -> dict[str, Any]:
            del task_runtime
            return {}

        def collect_workspace_code_files(self) -> list[str]:
            return []

        def mark_rework_round_started(self, task_id: str, get_task: Any, update_task: Any) -> None:
            del task_id, get_task, update_task

    class FakeExecution:
        def resolve_llm_call_timeout_seconds(self, context: dict[str, Any]) -> float:
            del context
            return 1.0

    class FakeAdapter:
        workspace = str(tmp_path)
        task_runtime = object()
        _state_tracker = FakeStateTracker()
        _execution = FakeExecution()

        def _get_task(self, task_id: str) -> dict[str, Any] | None:
            return task if task_id == "requested-task" else None

        def _materialize_runtime_task(self, task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
            del task_id, input_data
            return task

        def _select_pending_board_task(self) -> dict[str, Any] | None:
            return None

        def _update_task_progress(self, task_id: str, status: str) -> None:
            captured.setdefault("progress", []).append((task_id, status))

        def _update_board_task(self, task_id: str, updates: dict[str, Any]) -> None:
            del task_id, updates

        def _resolve_execution_backend_request(
            self,
            *,
            task_id: str,
            task: dict[str, Any],
            input_data: dict[str, Any],
            context: dict[str, Any],
        ) -> dict[str, Any]:
            captured["backend_context"] = dict(context)
            return {"task_id": task_id, "task": task, "input_data": input_data}

        def _persist_execution_backend_metadata(self, task_id: str, request: dict[str, Any]) -> None:
            captured["persisted"] = {"task_id": task_id, "request": request}

        def _get_sequential_config(self, context: dict[str, Any]) -> None:
            del context
            return None

    async def fake_claim_task_with_retry(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        del args, kwargs
        return (
            task,
            "selected-task-2",
            "task_id_lookup",
            True,
            {},
            [],
            {"session": {"session_id": "lease-selected-task-2"}},
        )

    async def fake_standard_flow(
        adapter: Any,
        task_arg: dict[str, Any],
        target_task_id: str,
        run_id: str,
        context: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del adapter, task_arg, args, kwargs
        captured["flow"] = {
            "target_task_id": target_task_id,
            "run_id": run_id,
            "context": dict(context),
        }
        return {"success": True, "task_id": target_task_id}

    monkeypatch.setattr(execute_method_module, "_claim_task_with_retry", fake_claim_task_with_retry)
    monkeypatch.setattr(execute_method_module, "_execute_standard_llm_flow", fake_standard_flow)

    result = await execute_director_task(
        FakeAdapter(),
        "requested-task",
        {"task_id": "requested-task"},
        {"run_id": "director-run-1"},
    )

    assert result["success"] is True
    flow_context = captured["flow"]["context"]
    assert flow_context["task_id"] == "selected-task-2"
    assert flow_context["target_task_id"] == "selected-task-2"
    assert flow_context["pm_task_id"] == "requested-task"
    assert flow_context["task_runtime_session_id"] == "lease-selected-task-2"
    assert flow_context["task_runtime_guard"] is True
    assert flow_context["metadata"]["task_id"] == "selected-task-2"
    assert flow_context["metadata"]["target_task_id"] == "selected-task-2"
    assert flow_context["metadata"]["pm_task_id"] == "requested-task"
    assert flow_context["metadata"]["task_runtime_session_id"] == "lease-selected-task-2"
    assert captured["backend_context"]["task_id"] == "selected-task-2"


@pytest.mark.asyncio
async def test_execute_director_task_does_not_call_llm_when_exact_claim_conflicts(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = {"id": "1", "subject": "Implement exact PM task"}
    captured: dict[str, Any] = {}

    class FakeStateTracker:
        def build_taskboard_observation_snapshot(self, task_runtime: Any) -> dict[str, Any]:
            del task_runtime
            return {"pending": 1}

        def collect_workspace_code_files(self) -> list[str]:
            return []

    class FakeExecution:
        def resolve_llm_call_timeout_seconds(self, context: dict[str, Any]) -> float:
            del context
            return 1.0

    class FakeAdapter:
        workspace = str(tmp_path)
        role_id = "director"
        task_runtime = object()
        _state_tracker = FakeStateTracker()
        _execution = FakeExecution()

        def _get_task(self, task_id: str) -> dict[str, Any] | None:
            return task if task_id == "TASK-1" else None

        def _materialize_runtime_task(self, task_id: str, input_data: dict[str, Any]) -> dict[str, Any]:
            del task_id, input_data
            raise AssertionError("exact handoff task already exists and should not materialize")

        def _select_pending_board_task(self) -> dict[str, Any] | None:
            raise AssertionError("exact handoff claim must not fall back to another ready task")

        def _update_task_progress(self, task_id: str, status: str) -> None:
            captured.setdefault("progress", []).append((task_id, status))

    async def fake_claim_task_with_retry(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        del args, kwargs
        return (
            task,
            "1",
            "task_id_lookup",
            False,
            {"pending": 1},
            [{"task_id": "1", "claimed": False, "reason": "lease_conflict"}],
            {"success": False, "reason": "lease_conflict", "task": task},
        )

    async def fake_handle_claim_required(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        captured["claim_required_args"] = args
        return {"success": False, "error_code": "director.task_claim_required"}

    async def unexpected_standard_flow(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise AssertionError("LLM flow must not run without a TaskBoard claim")

    monkeypatch.setattr(execute_method_module, "_claim_task_with_retry", fake_claim_task_with_retry)
    monkeypatch.setattr(execute_method_module, "_handle_claim_required", fake_handle_claim_required)
    monkeypatch.setattr(execute_method_module, "_execute_standard_llm_flow", unexpected_standard_flow)

    result = await execute_director_task(
        FakeAdapter(),
        "task-0-director",
        {
            "metadata": {
                "task_id": "TASK-1",
                "pm_task_id": "TASK-1",
                "chief_engineer_handoff_id": "ce-TASK-1",
            }
        },
        {"run_id": "director-run-conflict"},
    )

    assert result["success"] is False
    assert result["error_code"] == "director.task_claim_required"
    assert captured["claim_required_args"][2] == "director-run-conflict"


def test_deterministic_materialization_repair_cleans_scaffold_marker_from_reported_source(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        'console.log("Hello from Polaris TypeScript scaffold.");\n',
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: deterministic scaffold marker 'Polaris TypeScript scaffold' in src/main.ts"
    ]

    results, summary = run_materialization_quality_repair_schedule(
        _make_adapter(tmp_path),
        task={"metadata": {"target_files": ["src/main.ts"]}},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    assert summary["source_tools"] == ["deterministic_scaffold_marker_quality_cleanup"]
    repaired = (tmp_path / "src" / "main.ts").read_text(encoding="utf-8")
    assert "Polaris TypeScript scaffold" not in repaired
    assert "TypeScript application" in repaired


def test_deterministic_materialization_repair_cleans_scaffold_marker_from_go_source(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        "package main\n\n// output reflects real state rather than a static placeholder.\nfunc main() {}\n",
        encoding="utf-8",
    )
    errors = [
        "Director output quality gate failed: generic/placeholder content detected: "
        "main.go:(?<![.:'\"-])\\bplaceholder\\b(?!\\s*[=:])(?![-'\"])"
    ]

    results, summary = run_materialization_quality_repair_schedule(
        _make_adapter(tmp_path),
        task={"metadata": {"target_files": ["main.go"]}},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    assert summary["source_tools"] == ["deterministic_scaffold_marker_quality_cleanup"]
    repaired = (tmp_path / "main.go").read_text(encoding="utf-8")
    assert "placeholder" not in repaired.lower()
    assert "sample-check" in repaired


def test_deterministic_materialization_repair_routes_typescript_missing_export(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "import { GardenSimulator } from './product';\nnew GardenSimulator().report();\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "product.ts").write_text(
        "export function gardenReport(): string {\n  return 'ok';\n}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(1,10): error TS2305: Module '\"./product\"' has no exported member 'GardenSimulator'."
    ]

    results, summary = run_materialization_quality_repair_schedule(
        _make_adapter(tmp_path),
        task={"metadata": {"target_files": ["src/main.ts", "src/product.ts"]}},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results == []
    assert summary["source_tools"] == []
    assert summary["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"
    assert (
        "deterministic_typescript_missing_export_repair"
        in summary["plan_probe_preaudit"]["covered_unplannable_source_tools"]
    )
    repaired = (tmp_path / "src" / "product.ts").read_text(encoding="utf-8")
    assert "export class GardenSimulator" not in repaired


def test_deterministic_typescript_nullable_canvas_context_repair_adds_guard(tmp_path: Any) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "const canvas = document.querySelector('canvas') as HTMLCanvasElement;\n"
        "const ctx = canvas.getContext('2d');\n"
        "ctx.fillStyle = '#fff';\n"
        "function draw(target: CanvasRenderingContext2D) {}\n"
        "draw(ctx);\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/index.ts(3,1): error TS18047: 'ctx' is possibly 'null'.\n"
        "src/index.ts(5,6): error TS2345: Argument of type 'CanvasRenderingContext2D | null' "
        "is not assignable to parameter of type 'CanvasRenderingContext2D'."
    ]

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_typescript_nullable_canvas_context_repair",
        artifact_quality_errors=errors,
        relative_paths=("src/index.ts",),
    )

    assert results
    assert results[0]["tool"] == "edit_file"
    assert results[0]["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
    repaired = (tmp_path / "src" / "index.ts").read_text(encoding="utf-8")
    assert "const ctx = canvas.getContext('2d')!;" in repaired
    assert "if (!ctx) {" in repaired
    assert 'throw new Error("Canvas 2D context unavailable");' in repaired
    assert repaired.index("if (!ctx) {") < repaired.index("ctx.fillStyle")


def test_deterministic_typescript_nullable_canvas_context_repair_handles_existing_guard(
    tmp_path: Any,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "const canvas = document.querySelector('canvas') as HTMLCanvasElement;\n"
        'const ctx = canvas.getContext("2d");\n'
        "if (!ctx) {\n"
        "  console.error('missing context');\n"
        "  throw new Error('missing context');\n"
        "}\n"
        "function animate(): void {\n"
        "  ctx.fillRect(0, 0, 1, 1);\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/index.ts(8,3): error TS18047: 'ctx' is possibly 'null'."
    ]

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_typescript_nullable_canvas_context_repair",
        artifact_quality_errors=errors,
        relative_paths=("src/index.ts",),
    )

    assert results
    assert results[0]["result"]["source_tool"] == "deterministic_typescript_nullable_canvas_context_repair"
    repaired = (tmp_path / "src" / "index.ts").read_text(encoding="utf-8")
    assert 'const ctx = canvas.getContext("2d")!;' in repaired
    assert repaired.count("if (!ctx) {") == 1


def test_deterministic_typescript_nullable_dom_handle_repair_adds_guard(tmp_path: Any) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "renderer.ts").write_text(
        "const container = document.getElementById('app');\n"
        "const canvas = container.querySelector('canvas');\n"
        "container.appendChild(canvas);\n"
        "canvas.setAttribute('width', '100');\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/renderer.ts(3,1): error TS18047: 'container' is possibly 'null'.\n"
        "src/renderer.ts(4,1): error TS18047: 'canvas' is possibly 'null'."
    ]

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_typescript_nullable_canvas_context_repair",
        artifact_quality_errors=errors,
        relative_paths=("src/renderer.ts",),
    )

    assert results
    repaired = (tmp_path / "src" / "renderer.ts").read_text(encoding="utf-8")
    assert "if (!container) {" in repaired
    assert 'throw new Error("DOM element unavailable: container");' in repaired
    assert "if (!canvas) {" in repaired
    assert 'throw new Error("DOM element unavailable: canvas");' in repaired
    assert repaired.index("if (!container) {") < repaired.index("const canvas")
    assert repaired.index("if (!canvas) {") < repaired.index("container.appendChild")


def test_deterministic_typescript_nullable_dom_handle_repair_narrows_existing_guard(
    tmp_path: Any,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "renderer.ts").write_text(
        "const canvas = document.getElementById(\n"
        '  "sim-canvas",\n'
        ") as HTMLCanvasElement | null;\n"
        "if (!canvas) {\n"
        '  throw new Error("missing canvas");\n'
        "}\n"
        "function resize(): void {\n"
        "  canvas.width = 100;\n"
        "}\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/renderer.ts(8,3): error TS18047: 'canvas' is possibly 'null'."
    ]

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_typescript_nullable_canvas_context_repair",
        artifact_quality_errors=errors,
        relative_paths=("src/renderer.ts",),
    )

    assert results
    repaired = (tmp_path / "src" / "renderer.ts").read_text(encoding="utf-8")
    assert "as HTMLCanvasElement;" in repaired
    assert "HTMLCanvasElement | null" not in repaired
    assert repaired.count("if (!canvas) {") == 1


def test_deterministic_typescript_duplicate_object_property_repair_removes_reported_line(
    tmp_path: Any,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "flower.ts").write_text(
        "enum Phase { Quarter = 'quarter', Full = 'full' }\n"
        "const adjacent = {\n"
        "  [Phase.Quarter]: [Phase.Full],\n"
        "  [Phase.Full]: [Phase.Quarter],\n"
        "  [Phase.Quarter]: [Phase.Quarter],\n"
        "};\n",
        encoding="utf-8",
    )
    errors = ["src/flower.ts(5,3): error TS1117: An object literal cannot have multiple properties with the same name."]

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_typescript_duplicate_object_property_repair",
        artifact_quality_errors=errors,
        relative_paths=("src/flower.ts",),
    )

    assert results
    assert results[0]["tool"] == "edit_file"
    assert results[0]["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
    repaired = (tmp_path / "src" / "flower.ts").read_text(encoding="utf-8")
    assert repaired.count("[Phase.Quarter]:") == 1
    assert "[Phase.Full]: [Phase.Quarter]" in repaired


def test_deterministic_materialization_repair_routes_vitest_globals(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "index.test.ts").write_text(
        "describe('garden', () => {\n  it('runs', () => {\n    expect(true).toBe(true);\n  });\n});\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "typescript-application",
                "scripts": {"build": "tsc", "test": "npm run build"},
                "devDependencies": {"typescript": "^5.6.0"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "tests/index.test.ts(1,1): error TS2582: Cannot find name 'describe'.\n"
        "tests/index.test.ts(2,3): error TS2582: Cannot find name 'it'.\n"
        "tests/index.test.ts(3,5): error TS2304: Cannot find name 'expect'."
    ]

    results, summary = run_materialization_quality_repair_schedule(
        _make_adapter(tmp_path),
        task={"target_files": ["tests/index.test.ts"]},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results
    assert "deterministic_typescript_vitest_globals_repair" in summary["source_tools"]
    repaired_test = (tests_dir / "index.test.ts").read_text(encoding="utf-8")
    assert "import { describe, expect, it } from 'vitest';" in repaired_test
    repaired_package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert repaired_package["scripts"]["test"] == "vitest run"
    assert repaired_package["devDependencies"]["vitest"] == "^2.1.8"


@pytest.mark.asyncio
async def test_phase_quality_repair_loop_continues_after_deterministic_progress_when_llm_empty(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(tmp_path)
    error_rounds = [
        [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/firefly.ts(1,1): error TS2339: Property 'illumination' does not exist on type 'Moon'."
        ],
        [
            "Artifact quality scan failed: TypeScript project typecheck failed: "
            "src/firefly.ts(1,1): error TS2339: Property 'brightness' does not exist on type 'Moon'."
        ],
        [],
    ]
    deterministic_inputs: list[list[str]] = []

    def fake_collect_materialization_quality_errors(*args: Any, **kwargs: Any) -> list[str]:
        return error_rounds.pop(0) if error_rounds else []

    def fake_deterministic_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from polaris.cells.director.runtime.public.service import build_director_repair_kernel_summary

        artifact_quality_errors = list(kwargs.get("artifact_quality_errors") or [])
        deterministic_inputs.append(artifact_quality_errors)
        if not artifact_quality_errors:
            return [], {"stage": "deterministic_quality_repair", "success": False}
        tool_results = [
            {
                "tool_name": "write_file",
                "tool": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/moon.ts",
                },
            }
        ]
        return tool_results, {
            "stage": "deterministic_quality_repair",
            "success": True,
            "repair_kernel": build_director_repair_kernel_summary(
                stage="materialization_quality_repairs",
                tool_results=tool_results,
                artifact_quality_errors=artifact_quality_errors,
            ),
        }

    async def fake_llm_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], {"stage": "llm_quality_repair", "success": False}

    monkeypatch.setattr(
        execute_method_module, "_collect_materialization_quality_errors", fake_collect_materialization_quality_errors
    )
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        execute_method_module,
        "run_python_static_smoke",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        execute_method_module,
        "run_python_runtime_smoke",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        execute_method_module, "_filter_satisfied_declared_target_missing_errors", lambda errors, workspace: errors
    )
    monkeypatch.setattr(execute_method_module, "_missing_declared_target_files", lambda task, workspace: [])
    monkeypatch.setattr(
        execute_method_module,
        "run_declared_target_contract_repairs",
        lambda *args, **kwargs: ([], {"stage": "deterministic_contract_repair", "success": False}),
    )
    monkeypatch.setattr(
        execute_method_module, "_run_materialization_quality_public_boundary", fake_deterministic_quality_repair
    )
    monkeypatch.setattr(execute_method_module, "_run_materialization_quality_repair_retry", fake_llm_quality_repair)
    monkeypatch.setattr(
        execute_method_module,
        "_collect_workspace_code_diff",
        lambda *args, **kwargs: ({}, [], ["src/moon.ts"], ["src/firefly.ts", "src/moon.ts"]),
    )

    quality_repair_attempts: list[dict[str, Any]] = []

    state, residual_errors, _summary, _write_evidence = await execute_method_module._phase_quality_repair_loop(
        adapter,
        adapter_workspace=str(tmp_path),
        baseline_files={},
        context={},
        llm_call_timeout=1.0,
        message="repair TypeScript project",
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=None,
        run_id="run-1",
        target_task_id="task-1",
        task={"metadata": {"target_files": ["src/firefly.ts", "src/moon.ts"]}},
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=["src/firefly.ts"],
            tool_results=[],
        ),
    )

    assert residual_errors == []
    assert len(deterministic_inputs) == 2
    assert "illumination" in deterministic_inputs[0][0]
    assert "brightness" in deterministic_inputs[1][0]
    assert len(state.tool_results) == 2
    assert quality_repair_attempts[0]["revalidated"] is True
    assert quality_repair_attempts[0]["success"] is False
    assert quality_repair_attempts[0]["residual_error_count"] == 1
    first_receipt = quality_repair_attempts[0]["repair_kernel"]["receipts"][0]
    assert first_receipt["revalidation_evidence"]["errors_before"] == 1
    assert first_receipt["revalidation_evidence"]["errors_after"] == 1
    assert first_receipt["revalidation_evidence"]["exit_code"] == 1
    assert quality_repair_attempts[-1]["revalidated"] is True
    assert quality_repair_attempts[-1]["success"] is True
    assert quality_repair_attempts[-1]["residual_error_count"] == 0
    final_receipt = quality_repair_attempts[-1]["repair_kernel"]["receipts"][0]
    assert final_receipt["revalidation_evidence"]["errors_before"] == 1
    assert final_receipt["revalidation_evidence"]["errors_after"] == 0
    assert final_receipt["revalidation_evidence"]["exit_code"] == 0
    assert final_receipt["net_error_reduction"] == 1


@pytest.mark.asyncio
async def test_phase_quality_repair_loop_stops_on_plan_probe_task_boundary_triage(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(tmp_path)
    artifact_errors = [
        "Artifact quality scan failed: TypeScript project typecheck failed: "
        "src/main.ts(10,12): error TS2339: Property 'stallId' does not exist on type 'Stall'."
    ]
    deterministic_inputs: list[list[str]] = []

    def fake_collect_materialization_quality_errors(*args: Any, **kwargs: Any) -> list[str]:
        return list(artifact_errors)

    def fake_deterministic_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        deterministic_inputs.append(list(kwargs.get("artifact_quality_errors") or []))
        return [], {
            "stage": "materialization_quality_repairs",
            "success": False,
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "plannable_source_tools": [],
                "covered_unplannable_source_tools": ["deterministic_typescript_member_alias_repair"],
                "covered_unplannable_diagnostic_count": 1,
                "coverage_gap_count": 0,
            },
        }

    async def fail_if_llm_retry_called(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        raise AssertionError("covered_unplannable task-boundary diagnostics must not enter LLM retry grind")

    monkeypatch.setattr(
        execute_method_module, "_collect_materialization_quality_errors", fake_collect_materialization_quality_errors
    )
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "run_python_static_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "run_python_runtime_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        execute_method_module, "_filter_satisfied_declared_target_missing_errors", lambda errors, workspace: errors
    )
    monkeypatch.setattr(execute_method_module, "_missing_declared_target_files", lambda task, workspace: [])
    monkeypatch.setattr(
        execute_method_module,
        "run_declared_target_contract_repairs",
        lambda *args, **kwargs: ([], {"stage": "deterministic_contract_repair", "success": False}),
    )
    monkeypatch.setattr(
        execute_method_module, "_run_materialization_quality_public_boundary", fake_deterministic_quality_repair
    )
    monkeypatch.setattr(execute_method_module, "_run_materialization_quality_repair_retry", fail_if_llm_retry_called)

    quality_repair_attempts: list[dict[str, Any]] = []

    state, residual_errors, summary, write_evidence = await execute_method_module._phase_quality_repair_loop(
        adapter,
        adapter_workspace=str(tmp_path),
        baseline_files={},
        context={},
        llm_call_timeout=1.0,
        message="repair TypeScript project",
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=None,
        run_id="run-1",
        target_task_id="task-1",
        task={"metadata": {"target_files": ["src/main.ts", "src/models.ts"]}},
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=["src/main.ts", "src/models.ts"],
            tool_results=[],
        ),
    )

    assert residual_errors == artifact_errors
    assert deterministic_inputs == [artifact_errors]
    assert state.tool_results == []
    assert write_evidence is False
    assert summary is not None
    assert summary["stage"] == "runtime_plan_probe_unplannable"
    assert summary["llm_fallback_blocked"] is True
    assert summary["success_reason"] == "task_boundary_interface_discrepancy_required"
    assert summary["interface_discrepancy_evidence"]["schema_version"] == "director.interface_discrepancy_receipt.v1"
    assert (
        summary["interface_discrepancy_evidence"]["source"]
        == "roles.adapters.execute_method.materialization_quality_loop"
    )
    assert summary["interface_discrepancy_evidence"]["plan_probe_status"] == "coverage_matched_but_unplannable"
    assert quality_repair_attempts == [summary]


def test_plan_probe_task_boundary_triage_is_cross_language() -> None:
    for source_tool in (
        "deterministic_go_missing_symbol_repair",
        "deterministic_rust_wrong_crate_path_repair",
        "deterministic_cpp_include_path_repair",
        "deterministic_typescript_member_alias_repair",
    ):
        assert execute_method_module._materialization_plan_probe_requires_task_boundary_triage(
            {
                "plan_probe_preaudit": {
                    "status": "coverage_matched_but_unplannable",
                    "plannable_source_tools": [],
                    "covered_unplannable_source_tools": [source_tool],
                    "covered_unplannable_diagnostic_count": 1,
                    "coverage_gap_count": 0,
                }
            }
        )

    assert not execute_method_module._materialization_plan_probe_requires_task_boundary_triage(
        {
            "plan_probe_preaudit": {
                "status": "covered_plannable",
                "plannable_source_tools": ["deterministic_go_missing_symbol_repair"],
                "covered_unplannable_source_tools": [],
                "covered_unplannable_diagnostic_count": 0,
            }
        }
    )
    assert not execute_method_module._materialization_plan_probe_requires_task_boundary_triage(
        {
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "plannable_source_tools": ["deterministic_go_missing_symbol_repair"],
                "covered_unplannable_source_tools": ["deterministic_rust_wrong_crate_path_repair"],
                "covered_unplannable_diagnostic_count": 1,
            }
        }
    )


def test_go_bare_import_string_repair_uses_director_runtime_kernel(
    tmp_path: Any,
) -> None:
    from polaris.cells.director.runtime.public import RepairAdvisoryV1

    target = tmp_path / "cmd" / "app" / "main.go"
    target.parent.mkdir(parents=True)
    target.write_text('package main\n\n"fmt"\n\nfunc main() {}\n', encoding="utf-8")

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    adapter = FakeAdapter()
    results = _run_go_materialization_quality_schedule(
        adapter,
        task_id="task-go",
        advisor_notes=(
            RepairAdvisoryV1(
                advisor_source="resident_agi",
                message="Go bare imports are a recurring bench pattern.",
                confidence=0.6,
            ),
        ),
    )

    assert 'import "fmt"' in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_go_bare_import_string_repair"
    assert result["file"] == "cmd/app/main.go"
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert result["repair_kernel"]["planning_preflight"]["planned"] is True
    assert result["repair_kernel"]["planning_preflight"]["source_tool"] == "deterministic_go_bare_import_string_repair"
    assert result["repair_kernel"]["planning"]["advisor_notes"][0]["advisor_source"] == "resident_agi"
    assert result["repair_kernel"]["planning"]["advisor_notes"][0]["authoritative"] is False
    assert adapter.progress[-1] == ("task-go", "executing", "cmd/app/main.go")


def test_runtime_bridge_planning_preflight_blocks_unknown_source_tool(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.runtime_repair_tool_adapter import (
        run_runtime_repair_with_director_tools,
    )

    class FakeAdapter:
        workspace = str(tmp_path)
        _execution = SimpleNamespace(_message_bus=None)

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            raise AssertionError("progress must not update when planning preflight fails")

    def executor_factory(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("executor must not be created when planning preflight fails")

    results = run_runtime_repair_with_director_tools(
        FakeAdapter(),
        workspace_path=tmp_path,
        task_id="task-unknown-repair",
        source_tool="deterministic_future_language_repair",
        executor_factory=executor_factory,
        base_files={"src/main.future": "broken\n"},
        artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    result = results[0]["result"]
    assert result["error_code"] == "unsupported_repair_source_tool"
    assert result["repair_kernel"]["execution_skipped"] is True
    assert result["repair_kernel"]["execution_skip_reason"] == "planning_preflight_failed"
    assert result["repair_kernel"]["planning_preflight"]["planned"] is False


def test_cpp_post_include_path_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    header = tmp_path / "src" / "models" / "postcard.hpp"
    target = tmp_path / "src" / "engine" / "generator.cpp"
    header.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    header.write_text("#pragma once\n", encoding="utf-8")
    target.write_text('#include "src/models/postcard.hpp"\n#include <string>\n', encoding="utf-8")

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp",
    )

    relative_path = "src/engine/generator.cpp"
    assert writes == [("write_file", "task-cpp", relative_path)]
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_include_path_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-cpp", "executing", relative_path)


def test_cpp_post_standard_include_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    target = tmp_path / "src" / "models" / "seed.hpp"
    target.parent.mkdir(parents=True)
    target.write_text("#pragma once\nnamespace demo { std::uint32_t seed(); }\n", encoding="utf-8")

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-standard",
    )

    relative_path = "src/models/seed.hpp"
    assert writes == [("write_file", "task-cpp-standard", relative_path)]
    assert "#include <cstdint>" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_standard_include_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-cpp-standard", "executing", relative_path)


def test_cpp_post_missing_private_members_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    target = tmp_path / "src" / "models" / "poem.hpp"
    target.parent.mkdir(parents=True)
    target.write_text(
        "#pragma once\n"
        "#include <string>\n"
        "namespace demo {\n"
        "class Poem {\n"
        "public:\n"
        "    const std::string& title() const noexcept { return title_; }\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-private-members",
    )

    relative_path = "src/models/poem.hpp"
    assert writes == [("write_file", "task-cpp-private-members", relative_path)]
    assert "private:\n    std::string title_;" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_missing_private_members_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-cpp-private-members", "executing", relative_path)


def test_cpp_post_placeholder_declaration_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    target = tmp_path / "src" / "engine" / "generator.hpp"
    target.parent.mkdir(parents=True)
    target.write_text(
        "#pragma once\n"
        "namespace demo {\n"
        "class Generator {\n"
        "public:\n"
        "    std::render_return_type /* placeholder */ render_html() const = delete;\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-placeholder",
    )

    relative_path = "src/engine/generator.hpp"
    assert writes == [("write_file", "task-cpp-placeholder", relative_path)]
    assert "std::render_return_type" not in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_placeholder_declaration_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-cpp-placeholder", "executing", relative_path)


def test_cpp_post_struct_getter_field_access_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    header = tmp_path / "src" / "models" / "postcard.hpp"
    target = tmp_path / "src" / "main.cpp"
    header.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(
        "#pragma once\nnamespace demo {\nstruct Postcard {\n    int poem;\n};\n}\n",
        encoding="utf-8",
    )
    target.write_text(
        '#include "models/postcard.hpp"\nint main() {\n    demo::Postcard card{};\n    return card.get_poem();\n}\n',
        encoding="utf-8",
    )

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-struct-getter",
    )

    relative_path = "src/main.cpp"
    assert writes == [("write_file", "task-cpp-struct-getter", relative_path)]
    assert "card.poem" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_struct_getter_field_access_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-cpp-struct-getter", "executing", relative_path)


def test_java_post_accessor_alias_repair_uses_director_runtime_kernel(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    target = tmp_path / "src" / "main" / "java" / "demo" / "RhythmMonster.java"
    target.parent.mkdir(parents=True)
    target.write_text(
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    writes: list[tuple[str, str, str]] = []

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            writes.append((tool_name, task_id, file_path))
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge._run_java_post_repairs(adapter, tmp_path, task_id="task-java")

    relative_path = "src/main/java/demo/RhythmMonster.java"
    assert writes == [("write_file", "task-java", relative_path)]
    assert "public int temperament()" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_java_post_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress[-1] == ("task-java", "executing", relative_path)


def test_rust_post_repairs_run_remaining_rules_through_runtime_bridge(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    src = tmp_path / "src" / "lib.rs"
    src.parent.mkdir(parents=True)
    src.write_text("pub struct Demo;\n", encoding="utf-8")
    raw_error = "error[E0609]: no field `name` on type `Demo`\nerror[E0432]: unresolved import `demo::external`"

    def fail_if_adapter_aggregate_called(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("Rust post repairs must not run legacy aggregate repair")

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.artifact_quality_errors = [raw_error]

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            return None

    called: list[tuple[str, bool]] = []

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...] = (),
        use_editor: bool,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        assert isinstance(adapter, FakeAdapter)
        assert workspace_path == tmp_path.resolve()
        assert task_id == "task-rust-runtime"
        assert base_files["src/lib.rs"] == "pub struct Demo;\n"
        assert raw_error.strip() in artifact_quality_errors
        called.append((source_tool, use_editor))
        return []

    monkeypatch.setattr(post_execution_repair_bridge, "run_runtime_repair_with_director_tools", fake_runtime_bridge)

    results = post_execution_repair_bridge._run_rust_post_repairs(FakeAdapter(), tmp_path, task_id="task-rust-runtime")

    assert results == []
    assert called[-2:] == [
        ("deterministic_rust_missing_fields_repair", True),
        ("deterministic_rust_lib_root_facade_repair", True),
    ]


def test_post_execution_advisory_overlay_flows_into_runtime_receipt(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.director.runtime.public.contracts import DirectorRepairPostExecutionScheduleRunResultV1
    from polaris.cells.roles.adapters.internal.director import post_execution_repair_bridge

    target = tmp_path / "src" / "main" / "java" / "demo" / "RhythmMonster.java"
    target.parent.mkdir(parents=True)
    target.write_text(
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    class FakeDirectorToolExecutor:
        def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
            self.workspace = workspace
            self.message_bus = message_bus
            self.worker_id = worker_id

        def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
            file_path = payload["file"]
            content = payload["content"]
            (tmp_path / file_path).write_text(content, encoding="utf-8")
            return {
                "ok": True,
                "file": file_path,
                "bytes_written": len(content.encode("utf-8")),
                "operation": "modify",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            return None

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> DirectorRepairPostExecutionScheduleRunResultV1:
        assert "java.post_execution" in runner_step_ids
        assert max_rounds == 3
        step = post_execution_repair_bridge.DirectorRepairPostExecutionStepV1(
            step_id="java.post_execution",
            language="java",
            phase="post_materialization",
            priority=1,
            source_tool="deterministic_java_post_repair",
        )
        return DirectorRepairPostExecutionScheduleRunResultV1(
            schema_version="director.repair_post_execution_schedule_run_result.v1",
            source="director.runtime.repair_kernel.scheduler",
            ordered_steps=(step,),
            tool_results=tuple(runner(step)),
            receipt_projections=(),
            summary={
                "schedule_kind": "post_execution",
                "max_rounds": max_rounds,
                "rounds_run": 1,
                "receipt_projection_count": 0,
            },
            max_rounds=max_rounds,
            rounds_run=1,
            convergence_status="converged",
            stopped_reason="test_java_advisory",
        )

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FakeDirectorToolExecutor)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_director_post_execution_repair_schedule_result",
        fake_schedule_result,
    )

    tool_results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-java-advisory",
        resident_agi_repair_advisory_overlay={
            "status": "ready",
            "eligible_for_director_injection": True,
            "advisory_only": True,
            "advisor_notes": [
                {
                    "advisor_source": "resident_agi",
                    "message": "Accessor aliases are a recurring Java bench pattern.",
                    "confidence": 0.7,
                    "suggested_rules": [
                        {
                            "pattern": "getTemperament",
                            "fix_template": "temperament",
                            "confidence": 0.7,
                            "evidence": ["javac cannot find symbol temperament()"],
                        }
                    ],
                    "metadata": {"evidence_ref": "runtime/contexts/advisory"},
                }
            ],
        },
    )

    assert summary is not None
    assert len(tool_results) == 1
    receipt_notes = tool_results[0]["result"]["repair_kernel"]["advisor_notes"]
    assert receipt_notes[0]["advisor_source"] == "resident_agi"
    assert receipt_notes[0]["authoritative"] is False
    assert receipt_notes[0]["suggested_rules"][0]["pattern"] == "getTemperament"
    assert summary["repair_kernel"]["agi_advisory"]["active"] is True
    assert summary["scheduler_bridge"]["resident_agi_advisory_note_count"] == 1
    assert summary["scheduler_bridge"]["resident_agi_suggested_rule_count"] == 1


def test_quality_repair_revalidation_marks_nested_post_execution_kernel() -> None:
    diagnostic = {
        "diagnostic_id": "diag-main",
        "source": "typescript",
        "code": "typescript_ts1005",
        "message": "',' expected",
        "severity": "error",
        "path": "src/main.ts",
    }
    receipt = {
        "receipt_id": "receipt-main",
        "plan_id": "plan-main",
        "rule_id": "typescript.object_literal_missing_comma",
        "source_tool": "deterministic_typescript_return_object_semicolon_repair",
        "status": "pending_revalidation",
        "mode": "commit",
        "authoritative": False,
        "files_changed": ["src/main.ts"],
        "operation_ids": ["op-main"],
        "diagnostics": [diagnostic],
        "before_hashes": {},
        "after_hashes": {},
        "metadata": {"requires_revalidation": True},
    }
    nested_receipt = {
        **receipt,
        "receipt_id": "receipt-post",
        "plan_id": "plan-post",
        "rule_id": "rust.unlinked_crate_dependency",
        "source_tool": "deterministic_rust_dependency_repair",
        "files_changed": ["Cargo.toml"],
    }
    summary: dict[str, Any] = {
        "repair_kernel": {
            "mode": "commit",
            "receipts": [dict(receipt)],
            "coverage_report": {"total_diagnostics": 1},
        },
        "post_execution_repair_kernel": {
            "repair_kernel": {
                "mode": "commit",
                "receipts": [dict(nested_receipt)],
                "coverage_report": {"total_diagnostics": 1},
            }
        },
        "repair_attempts": [
            {
                "stage": "quality_retry",
                "repair_kernel": {
                    "mode": "commit",
                    "receipts": [dict(receipt)],
                    "coverage_report": {"total_diagnostics": 1},
                },
            }
        ],
    }

    execute_method_module._mark_quality_repair_summary_revalidated(summary, [])

    top_receipt = summary["repair_kernel"]["receipts"][0]
    post_receipt = summary["post_execution_repair_kernel"]["repair_kernel"]["receipts"][0]
    attempt_receipt = summary["repair_attempts"][0]["repair_kernel"]["receipts"][0]
    assert summary["revalidated"] is True
    assert summary["success"] is True
    assert top_receipt["revalidation_evidence"]["errors_after"] == 0
    assert post_receipt["revalidation_evidence"]["errors_after"] == 0
    assert attempt_receipt["revalidation_evidence"]["errors_after"] == 0
    assert post_receipt["status"] == "applied"
    assert post_receipt["authoritative"] is True
    assert summary["post_execution_repair_kernel"]["repair_kernel"]["receipts_with_revalidation"] == 1


def test_rust_dependency_repair_runs_through_director_runtime_bridge(tmp_path: Any) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.rs").write_text(
        "use serde::Serialize;\nfn main() { let _ = serde_json::json!({}); }\n",
        encoding="utf-8",
    )

    results = _run_runtime_director_repair(
        tmp_path,
        source_tool="deterministic_rust_dependency_repair",
        task_id="task-rust-deps",
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
        relative_paths=("Cargo.toml", "src/main.rs"),
        use_editor=False,
    )
    repaired = (tmp_path / "Cargo.toml").read_text(encoding="utf-8")

    assert len(results) == 1
    assert results[0]["tool"] == "write_file"
    assert results[0]["result"]["source_tool"] == "deterministic_rust_dependency_repair"
    assert results[0]["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
    assert results[0]["result"]["repair_kernel"]["status"] == "applied"
    assert results[0]["result"]["repair_kernel"]["metadata"]["requires_revalidation"] is True
    assert 'serde = { version = "1.0", features = ["derive"] }' in repaired
    assert 'serde_json = "1.0"' in repaired


def test_materialization_quality_summary_projects_dark_launch_cutover_blocker() -> None:
    from polaris.cells.director.runtime.public import DirectorRepairMaterializationQualityStepV1
    from polaris.cells.roles.adapters.internal.director import (
        materialization_quality_callback_ports,
        materialization_quality_evidence_ports,
    )

    tool_results = [
        {
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_dependency_repair",
                "file": "Cargo.toml",
                "before_hash": "before",
                "after_hash": "after",
            },
        }
    ]

    summary = materialization_quality_evidence_ports._annotate_materialization_quality_summary(
        step_summaries={},
        tool_results=tool_results,
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
        coverage_preaudit=materialization_quality_callback_ports._project_coverage_preaudit(
            ["error[E0432]: unresolved import `serde`"]
        ),
        ordered_steps=(
            DirectorRepairMaterializationQualityStepV1(
                step_id="materialization.rust_compiler",
                language="rust",
                phase="dependency_resolution",
                priority=0,
                source_tool="deterministic_rust_dependency_repair",
            ),
        ),
    )
    shadow = summary["dark_launch_comparison"]

    assert shadow["comparison_mode"] == "receipt_projection_self_check"
    assert summary["coverage_preaudit"]["total_diagnostics"] == 1
    assert summary["materialization_quality_runtime_ports"]["coverage_preaudit_uncovered_diagnostic_count"] == 0
    assert shadow["cutover_ready"] is False
    assert "independent_shadow_required" in shadow["cutover_blockers"]
    assert shadow["independent_shadow_required"] is True
    assert shadow["independent_shadow_satisfied"] is False
    assert shadow["writes_allowed"] is False
    assert summary["materialization_quality_runtime_ports"]["dark_launch_cutover_ready"] is False
    assert (
        "independent_shadow_required"
        in summary["materialization_quality_runtime_ports"]["dark_launch_cutover_blockers"]
    )


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


def test_phase_pre_materialization_quality_records_post_execution_kernel_summary(
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
    state, _evidence, _can_accept, _write_evidence, summary = execute_method_module._phase_pre_materialization_quality(
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
    assert receipt["revalidation_evidence"]["metadata"]["max_rounds"] == 3
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
    assert scheduler_bridge["configured_max_rounds"] == 3
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


def test_phase_pre_materialization_quality_passes_artifact_quality_convergence_verifier(
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
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, resident_agi_repair_advisory_overlay
        captured["bridge"] = {
            "task_id": task_id,
            "convergence_verifier": convergence_verifier,
        }
        return [], None

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_factory)
    monkeypatch.setattr(execute_method_module, "run_post_execution_language_repairs", fake_post_execution_repairs)

    absolute_inside = tmp_path / "lib" / "model.ts"
    state, _evidence, _can_accept, _write_evidence, _summary = execute_method_module._phase_pre_materialization_quality(
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


def test_phase_pre_materialization_quality_passes_verifier_to_materialization_bridge(
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
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del adapter
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
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, task_id, resident_agi_repair_advisory_overlay, convergence_verifier
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

    execute_method_module._phase_pre_materialization_quality(
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


def test_phase_pre_materialization_quality_omits_verifier_when_factory_fails(
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
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        del adapter, task_id, resident_agi_repair_advisory_overlay
        captured["convergence_verifier"] = convergence_verifier
        return [], None

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", failing_factory)
    monkeypatch.setattr(execute_method_module, "run_post_execution_language_repairs", fake_post_execution_repairs)

    execute_method_module._phase_pre_materialization_quality(
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


def _assert_retry_text_fallback_is_non_authoritative(
    *,
    adapter: Any,
    task_id: str,
    result: dict[str, Any],
    summary_key: str,
) -> dict[str, Any]:
    """Assert retry text/file-block output did not bypass native tool execution."""
    assert result["success"] is False
    assert result["error_code"] == "incomplete_materialization"
    assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"

    updated = adapter.task_board.get_task(task_id)
    assert updated is not None
    raw_metadata = updated.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}

    assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
    assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
    assert adapter_result.get("failure_class") == "INCOMPLETE_MATERIALIZATION"
    assert adapter_result.get("new_files") == []
    assert adapter_result.get("modified_files") == []
    retry_summary = adapter_result.get(summary_key)
    assert isinstance(retry_summary, dict)
    assert retry_summary.get("attempted") is True
    assert ("patch_apply", 0) in retry_summary.get("write_args", [])
    return adapter_result


def test_target_candidates_include_explicit_scope_directories_with_target_files() -> None:
    task = {
        "target_files": ["package.json", "README.md"],
        "scope_paths": ["package.json", "README.md", "src", "tests"],
    }

    assert _extract_task_target_path_candidates(task) == ["package.json", "README.md", "src", "tests"]


@pytest.mark.asyncio
async def test_execute_retries_blank_write_content_with_materialize_prompt(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_board.create(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
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
        task_id=str(task.id),
        input_data={"task_id": str(task.id)},
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
        task_id=str(task.id),
        result=result,
        summary_key="empty_write_content_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_no_write_probe_with_write_only_materialize_prompt(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_board.create(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"], "phase": "implementation"},
    )
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
        task_id=str(task.id),
        input_data={"task_id": str(task.id)},
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
        task_id=str(task.id),
        result=result,
        summary_key="no_write_materialization_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_multi_file_no_write_with_mutation_tools_only(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_board.create(
        subject="Create application modules",
        description="Create src/app.py and src/utils.py with a runnable entry point.",
        metadata={
            "target_files": ["src/app.py", "src/utils.py"],
            "scope_paths": ["src/app.py", "src/utils.py"],
            "phase": "implementation",
        },
    )
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
        task_id=str(task.id),
        input_data={"task_id": str(task.id)},
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
    assert edit_def["function"]["parameters"]["properties"]["file"]["enum"] == ["src/app.py", "src/utils.py"]
    assert seen_contexts[1]["director_no_write_materialization_retry"]["multi_file_declared_targets"] == {
        "required_write_tools": ["edit_file", "write_file"],
        "target_files": ["src/app.py", "src/utils.py"],
    }
    _assert_retry_text_fallback_is_non_authoritative(
        adapter=adapter,
        task_id=str(task.id),
        result=result,
        summary_key="no_write_materialization_retry",
    )


@pytest.mark.asyncio
async def test_execute_retries_read_only_materialization_with_forced_write(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    task = adapter.task_board.create(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
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
        task_id=str(task.id),
        input_data={"task_id": str(task.id)},
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
        task_id=str(task.id),
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


def _source_tools_from_tool_results(tool_results: Any) -> list[str]:
    source_tools: list[str] = []
    if not isinstance(tool_results, list):
        return source_tools
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        raw_result = item.get("result")
        if not isinstance(raw_result, dict):
            continue
        source_tools.append(str(raw_result.get("source_tool") or ""))
    return source_tools


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


class TestBuildDirectorMessage:
    """_build_director_message constructs prompt text deterministically."""

    def test_includes_subject(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "Fix login", "description": "Bug in auth"})
        assert "任务: Fix login" in msg
        assert "文本文件块格式" in msg

    def test_sanitizes_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": "# Header\n\nBody line"})
        assert "描述:" in msg

    def test_empty_description_omitted(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message({"subject": "T", "description": ""})
        # The line "描述: " with empty content should still appear because implementation
        # does not filter it out; we just assert no crash.
        assert "任务: T" in msg

    def test_uses_real_scope_instead_of_placeholder_path(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Scaffold app",
                "metadata": {
                    "goal": "Create a Vite app",
                    "scope": "package.json, src/main.tsx",
                    "steps": ["Create package manifest"],
                    "acceptance": ["npm test passes"],
                },
            }
        )
        assert "范围: package.json, src/main.tsx" in msg
        assert "- Create package manifest" in msg
        assert "- npm test passes" in msg
        assert "path/to/file.py" not in msg

    def test_includes_pm_contract_paths_checklist_and_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Three.js client scene",
                "description": "Implement the client3d task",
                "metadata": {
                    "goal": "Add the missing client3d capability",
                    "scope_paths": ["src/client/three-scene.ts"],
                    "target_files": ["src/client/three-scene.ts"],
                    "execution_checklist": ["Modify the existing Three.js scene file"],
                    "acceptance_criteria": ["Run `npm run build` passes"],
                },
            }
        )

        assert "范围: src/client/three-scene.ts" in msg
        assert "目标文件: src/client/three-scene.ts" in msg
        assert "- Modify the existing Three.js scene file" in msg
        assert "- Run `npm run build` passes" in msg

    def test_includes_explicit_verification_commands_from_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement Go module",
                "metadata": {
                    "target_files": ["go.mod", "main.go", "models/capsule.go"],
                    "execution_checklist": ["Run `go test ./...` after writing code"],
                    "acceptance_criteria": ["`go run .` returns success", "`go test ./...` passes"],
                },
            }
        )

        assert "Verification commands / 验证命令:" in msg
        assert "- go test ./..." in msg
        assert "- go run ." in msg

    def test_includes_runtime_context_verification_commands_from_acceptance(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context = {
            "target_files": ("go.mod", "main.go"),
            "execution_checklist": "Run `go test ./...` after writing code",
            "acceptance_criteria": "`go test ./...` passes",
        }
        msg = adapter._build_director_message(
            {"subject": "Implement Go module"},
            context=context,
        )

        commands = DirectorAdapter._ensure_director_verification_commands(message=msg, context=context)

        assert "目标文件: go.mod, main.go" in msg
        assert "Verification commands / 验证命令:" in msg
        assert "- go test ./..." in msg
        assert commands == ["go test ./..."]

    def test_includes_language_specific_director_identity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        context: dict[str, Any] = {
            "target_files": ["go.mod", "main.go"],
            "metadata": {"project_type": "service"},
        }
        msg = adapter._build_director_message(
            {
                "subject": "Implement Go module",
                "description": "Use context cancellation and table-driven tests",
            },
            context=context,
        )

        assert "Director language/task identity / 语言专项身份:" in msg
        assert "精通 Go" in msg
        assert "Primary language: Go (Golang)" in msg
        assert "=== Go (Golang) Language Best Practices ===" in msg
        assert "软件工程师" not in str(context["metadata"]["director_language_identity"])

    def test_multi_target_message_requires_all_target_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement model files",
                "metadata": {
                    "scope_paths": ["src/models"],
                    "target_files": [
                        "src/models/flower.ts",
                        "src/models/moon.ts",
                        "src/models/firefly.ts",
                    ],
                },
            }
        )

        assert "目标文件: src/models/flower.ts, src/models/moon.ts, src/models/firefly.ts" in msg
        assert "目标文件覆盖硬门禁" in msg
        assert "每个目标文件分别发出 write/edit 工具调用" in msg
        assert "不得只写第一个 sibling 文件后结束" in msg

    def test_includes_ce_blueprint_and_factory_context(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement firefly garden simulator",
                "metadata": {
                    "goal": "Create the L1-01 simulator artifacts",
                    "scope_paths": ["src/engine/SimulationEngine.ts"],
                    "target_files": ["src/engine/SimulationEngine.ts"],
                    "execution_checklist": ["Write the simulation engine"],
                    "acceptance_criteria": ["npm run build passes"],
                },
            },
            context={
                "blueprint_id": "bp-L1-01-4",
                "construction_step": {
                    "target_file": "src/engine/SimulationEngine.ts",
                    "signatures": ["class SimulationEngine", "runSimulation()"],
                    "verify": "npm run build",
                },
                "metadata": {
                    "factory_bench_project_id": "L1-01",
                    "factory_bench_title": "发光昆虫花园模拟器",
                },
            },
        )

        assert "PM Task Contract / 任务合同:" in msg
        assert "Acceptance criteria / 验收标准:" in msg
        assert "Chief Engineer Blueprint / CE 蓝图交接:" in msg
        assert "- blueprint_id: bp-L1-01-4" in msg
        assert "- construction target: src/engine/SimulationEngine.ts" in msg
        assert "- construction signatures: class SimulationEngine; runSimulation()" in msg
        assert "- construction verify: npm run build" in msg
        assert "- factory bench project: L1-01 - 发光昆虫花园模拟器" in msg

    def test_includes_persisted_ce_blueprint_contract(self, tmp_path: Any) -> None:
        blueprint_id = "bp-L1-01-contract"
        BlueprintPersistence(str(tmp_path)).save(
            blueprint_id,
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": blueprint_id,
                "task_id": "TASK-1",
                "target_files": [
                    "src/engine/SimulationEngine.ts",
                    "src/engine/Renderer.ts",
                    "src/engine/Clock.ts",
                    "src/models/Firefly.ts",
                    "src/models/Garden.ts",
                    "src/index.ts",
                    "src/main.ts",
                    "src/web.ts",
                    "tests/behavior.test.ts",
                ],
                "scope_paths": ["src/engine/SimulationEngine.ts", "tests/behavior.test.ts"],
                "pm_contract_hash": "pm-contract-hash",
                "execution_profile_hash": "execution-profile-hash",
                "acceptance_criteria": ["npm run build passes"],
                "execution_checklist": ["Write the simulation engine"],
                "recommendations": ["Run build", "Run smoke test"],
                "contract_completeness": {
                    "handoff_ready": True,
                    "missing_fields": [],
                    "semantic_blockers": [],
                    "semantic_alignment": {
                        "expected_terms": ["firefly", "garden", "simulation"],
                        "planning_text_matches": ["firefly", "garden", "simulation"],
                        "target_file_matches": [],
                        "advisory": ["semantic_alignment.target_files: matched 0/2 required domain terms"],
                        "blockers": [],
                    },
                },
                "llm_blueprint": {
                    "schema_version": "chief_engineer.llm_blueprint_overlay.v1",
                    "source": "chief_engineer.llm_output",
                    "authoritative": False,
                    "authority": "advisory_only",
                    "implementation_phases": [
                        "Split simulation state from rendering",
                        "Add deterministic tick samples",
                    ],
                    "module_boundaries": ["SimulationEngine owns rules", "Canvas adapter owns drawing"],
                    "verification_steps": ["npm run build", "npm test"],
                    "scope_for_apply_advisory": ["src/engine/SimulationEngine.ts"],
                    "risk_flags": ["browser bootstrap can drift from compiled output"],
                },
            },
        )
        adapter = _make_adapter(tmp_path)

        msg = adapter._build_director_message(
            {
                "subject": "Implement firefly garden simulator",
                "metadata": {
                    "blueprint_id": blueprint_id,
                    "goal": "Create the simulator artifacts",
                    "target_files": ["src/engine/SimulationEngine.ts"],
                    "execution_checklist": ["Write the simulation engine"],
                    "acceptance_criteria": ["npm run build passes"],
                },
            }
        )

        assert "- blueprint_id: bp-L1-01-contract" in msg
        assert "- handoff_ready: yes" in msg
        assert "- blueprint target_files: src/engine/SimulationEngine.ts" in msg
        assert "tests/behavior.test.ts" in msg
        assert "- blueprint required test targets: tests/behavior.test.ts" in msg
        assert "- blueprint acceptance: npm run build passes" in msg
        assert "- blueprint execution_checklist: Write the simulation engine" in msg
        assert "- blueprint expected_terms: firefly, garden, simulation" in msg
        assert "- ce_llm_blueprint: consumed (advisory_only)" in msg
        assert "- ce plan phases: Split simulation state from rendering, Add deterministic tick samples" in msg
        assert "- ce module boundaries: SimulationEngine owns rules, Canvas adapter owns drawing" in msg
        assert "- ce verification: npm run build, npm test" in msg
        assert "- ce scope advisory: src/engine/SimulationEngine.ts" in msg
        assert "- ce risks: browser bootstrap can drift from compiled output" in msg

    def test_promote_task_contract_preserves_claimed_write_boundary_from_ce_blueprint(self, tmp_path: Any) -> None:
        blueprint_id = "bp-task-1-with-tests"
        BlueprintPersistence(str(tmp_path)).save(
            blueprint_id,
            {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": blueprint_id,
                "task_id": "TASK-1",
                "target_files": ["src/index.ts", "tests/behavior.test.ts"],
                "scope_paths": ["src/index.ts", "tests/behavior.test.ts"],
                "acceptance_criteria": ["npm run test passes"],
                "execution_checklist": ["Implement source and behavior tests"],
            },
        )
        task = {
            "id": 1,
            "metadata": {
                "blueprint_id": blueprint_id,
                "target_files": ["src/index.ts"],
                "scope_paths": ["src/index.ts"],
            },
        }
        context: dict[str, Any] = {}

        DirectorAdapter._promote_task_contract_to_runtime_context(
            task=task,
            context=context,
            workspace=str(tmp_path),
        )

        metadata = context["metadata"]
        assert context["target_files"] == ["src/index.ts"]
        assert context["scope_paths"] == ["src/index.ts"]
        assert metadata["target_files"] == ["src/index.ts"]
        assert metadata["scope_paths"] == ["src/index.ts"]
        task_metadata = task["metadata"]
        assert isinstance(task_metadata, dict)
        assert task["target_files"] == ["src/index.ts"]
        assert task["scope_paths"] == ["src/index.ts"]
        assert task_metadata["target_files"] == ["src/index.ts"]
        assert task_metadata["scope_paths"] == ["src/index.ts"]
        assert execute_method_module._declared_write_retry_target_files(task) == ["src/index.ts"]
        assert metadata["ce_blueprint"]["blueprint_id"] == blueprint_id

    def test_message_requires_unittest_and_contract_scoped_python_tests(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Implement calculator tests",
                "metadata": {
                    "scope_paths": ["calculator.py", "tests/test_calculator.py"],
                    "target_files": ["calculator.py", "tests/test_calculator.py"],
                    "execution_checklist": ["Add calculator regression tests"],
                    "acceptance_criteria": ["2+3*4 returns 14", "10/0 is rejected"],
                },
            }
        )

        assert "标准库 unittest" in msg
        assert "python -m unittest discover -s tests -p 'test_*.py' -v" in msg
        assert "至少发现并运行 1 个测试" in msg
        assert "不得新增合同外功能断言" in msg
        assert "未声明第三方测试依赖" in msg

    def test_includes_qa_rework_evidence(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_director_message(
            {
                "subject": "Fix QA findings",
                "metadata": {
                    "qa_rework_reason": "placeholder_content_detected",
                    "qa_rework_evidence": [
                        "src/backend/fashiongen_worker.py:\\bplaceholder\\b",
                        "src/main/providers.ts:\\bplaceholder\\b",
                    ],
                },
            }
        )

        assert "QA 返工要求" in msg
        assert "placeholder_content_detected" in msg
        assert "src/backend/fashiongen_worker.py" in msg
        assert "src/main/providers.ts" in msg


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


class TestDirectorFailureClosure:
    """Runtime failures must fail the claimed task instead of leaving it running."""

    def test_finalize_claimed_execution_reports_terminal_transition_failure(self) -> None:
        class _Runtime:
            def complete_execution(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                del args, kwargs
                raise RuntimeError("Cannot transition task from 'failed' to 'completed'")

        adapter = SimpleNamespace(task_runtime=_Runtime())

        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id="task-1",
            session_id="session-1",
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
    async def test_execute_fails_claimed_task_on_unhandled_runtime_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="实现核心模块",
            description="创建文件",
            metadata={"scope": "src/core.ts", "steps": ["写入核心文件"]},
        )

        async def _boom_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise RuntimeError("director kernel exploded")

        adapter._invoke_role_dialogue_with_timeout = _boom_call  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-fail-closed"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director.runtime.exception"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "failed"

    @pytest.mark.asyncio
    async def test_execute_rejects_workspace_diff_without_write_tool_receipt(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Repair failing TypeScript test",
            description="Apply the smallest code change and verify npm test behavior.",
            metadata={
                "scope": "src/types/domain.ts",
                "steps": ["Update the domain type contract"],
                "acceptance": ["The TypeScript test failure is repaired"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        updated = adapter.task_board.get_task(str(task.id))
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
        task = adapter.task_board.create(
            subject="Implement browser networking client",
            description="Update the declared network client target file.",
            metadata={
                "target_files": ["src/client/network-client.ts"],
                "scope_paths": ["src"],
                "steps": ["Implement src/client/network-client.ts"],
                "acceptance": ["src/client/network-client.ts is changed"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-off-target-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialized_out_of_scope"
        assert result["failure_class"] == QaFailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("new_files") == []
        assert adapter_result.get("modified_files") == []

    @pytest.mark.asyncio
    async def test_execute_keeps_failed_no_write_separate_from_sibling_diff(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement garden simulator module",
            description="Update only the declared garden module.",
            metadata={
                "target_files": ["src/garden.ts"],
                "scope_paths": ["src/garden.ts"],
                "steps": ["Implement src/garden.ts"],
                "acceptance": ["src/garden.ts is changed"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-sibling-diff"},
        )

        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == QaFailureClassV1.INCOMPLETE_MATERIALIZATION.value
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_no_materialized_changes"
        assert adapter_result.get("materialization_error_code") == "incomplete_materialization"
        assert adapter_result.get("failure_class") == QaFailureClassV1.INCOMPLETE_MATERIALIZATION.value
        assert adapter_result.get("out_of_scope_files") == ["scripts/verify.js"]

    def test_no_materialized_changes_ignores_sibling_diff_after_failed_write_tool(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
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
            target_task_id=str(task.id),
            task={"target_files": ["package.json"], "scope_paths": ["package.json"]},
            task_claim_session_id="",
            workspace_name=tmp_path.name,
            write_tool_evidence=False,
            state=state,
        )

        assert result is not None
        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == QaFailureClassV1.INCOMPLETE_MATERIALIZATION.value

    def test_no_materialized_changes_preserves_primary_tool_dispatch_failure(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            MaterializationState,
            _phase_no_materialized_changes,
        )

        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Bootstrap package manifest",
            description="Create only package.json.",
            metadata={"target_files": ["package.json"], "scope_paths": ["package.json"]},
        )
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
            target_task_id=str(task.id),
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

    @pytest.mark.asyncio
    async def test_execute_fails_when_changed_test_file_keeps_placeholder_arithmetic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = adapter.task_board.create(
            subject="Replace placeholder Card3D unit tests",
            description="Remove trivial arithmetic placeholder tests and replace them with domain assertions.",
            metadata={
                "target_files": ["tests/unit/card-rules.test.ts"],
                "steps": ["Replace or remove existing trivial arithmetic placeholder tests"],
                "acceptance": ["No trivial arithmetic placeholder tests remain"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-artifact-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_quality_failed"
        assert any("tests/unit/card-rules.test.ts" in item for item in result["artifact_quality_errors"])
        updated = adapter.task_board.get_task(str(task.id))
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
        task = adapter.task_board.create(
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        task = adapter.task_board.create(
            subject="Create package manifest",
            description="Create a package.json with a runnable local test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test runs a local package manifest check"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        task = adapter.task_board.create(
            subject="Create package manifest",
            description="Create a package.json with a syntactically valid npm test script.",
            metadata={
                "target_files": ["package.json"],
                "scope_paths": ["package.json"],
                "steps": ["Create package manifest"],
                "acceptance": ["npm test parses and exits 0"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
    ) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Create task model summary",
            description="Create a task model summary function with valid TypeScript syntax.",
            metadata={
                "target_files": ["src/models/task.ts"],
                "scope_paths": ["src/models/task.ts"],
                "steps": ["Create task model"],
                "acceptance": ["src/models/task.ts typechecks"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
    ) -> None:
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
        task = adapter.task_board.create(
            subject="Define tenant model",
            description="Create the tenant model with runtime imports declared in package.json.",
            metadata={
                "target_files": ["src/models/tenant.model.ts"],
                "scope_paths": ["src/models/tenant.model.ts", "package.json"],
                "steps": ["Create tenant model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-undeclared-import-deterministic-repair"},
        )

        package_text = (tmp_path / "package.json").read_text(encoding="utf-8")
        tenant_text = (tmp_path / "src" / "models" / "tenant.model.ts").read_text(encoding="utf-8")
        source_tools = _source_tools_from_tool_results(result["tool_results"])
        assert result["success"] is True
        assert stage_labels == ["first_call"]
        assert '"typeorm":' not in package_text
        assert "from 'typeorm'" not in tenant_text
        assert "@Entity" not in tenant_text
        assert "tasks: unknown[] = [];" in tenant_text
        assert "deterministic_typeorm_model_normalization_repair" in source_tools
        assert "src/models/tenant.model.ts" in result["changed_files"]

    @pytest.mark.asyncio
    async def test_execute_declares_mongoose_runtime_dependency_for_audit_log_model(
        self,
        tmp_path: Any,
    ) -> None:
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
        task = adapter.task_board.create(
            subject="Tenant Context & Audit Log Middleware",
            description="Implement immutable audit log model with tenant context.",
            metadata={
                "target_files": ["src/models/auditlog.ts"],
                "scope_paths": ["src/models/auditlog.ts", "package.json"],
                "steps": ["Create audit log model"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
    ) -> None:
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
        task = adapter.task_board.create(
            subject="Immutable Audit Logging Implementation",
            description="Create a TypeScript audit log service with stable event IDs and structured logging.",
            metadata={
                "target_files": ["src/services/auditlog.ts"],
                "scope_paths": ["src/services/auditlog.ts", "package.json"],
                "steps": ["Create the audit log service"],
                "acceptance": ["No undeclared runtime imports remain"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
    ) -> None:
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
        task = adapter.task_board.create(
            subject="Tenant Context Middleware",
            description="Create request-scoped tenant context middleware for an Express service.",
            metadata={
                "target_files": ["src/middleware/auth.ts"],
                "scope_paths": ["src/middleware/auth.ts", "package.json"],
                "steps": ["Create tenant middleware"],
                "acceptance": ["TypeScript exports remain reachable and Node builtin typings are declared"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
    ) -> None:
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
        task = adapter.task_board.create(
            subject="Task Definition Model",
            description="Create zod-backed task definition model.",
            metadata={
                "target_files": ["src/models/task_definition.ts"],
                "scope_paths": ["src/models/task_definition.ts", "package.json"],
                "steps": ["Create task definition schema and model"],
                "acceptance": ["TypeScript typecheck accepts schema and class exports"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        task = adapter.task_board.create(
            subject="Define tenant and task model files",
            description="Create explicit tenant.model.ts and task.model.ts model files.",
            metadata={
                "target_files": ["src/models/tenant.model.ts", "src/models/task.model.ts"],
                "scope_paths": ["src/models"],
                "steps": ["Create tenant and task model files"],
                "acceptance": ["Both declared target model files exist"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        task = adapter.task_board.create(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["No generic unrelated implementation remains"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-semantic-quality"},
        )

        assert result["success"] is False
        assert result["error_code"] == "director_materialization_semantic_quality_failed"
        assert "no project-domain signal" in result["semantic_quality_error"]
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("materialization_error") == "director_materialization_semantic_quality_failed"

    @pytest.mark.asyncio
    async def test_execute_repairs_semantic_quality_failure_before_final_fail(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        target = tmp_path / "src" / "fish" / "arena.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        task = adapter.task_board.create(
            subject="Implement fish predator prey multiplayer arena",
            description="Build fish arena movement and predator prey scoring for the online game.",
            metadata={
                "target_files": ["src/fish/arena.ts"],
                "scope_paths": ["src/fish/arena.ts"],
                "steps": ["Implement fish arena gameplay"],
                "acceptance": ["Arena code contains fish domain behavior"],
            },
        )
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-semantic-quality-repair"},
        )

        assert result["success"] is True
        assert stages.count("quality_repair") == 1
        assert "fish arena predator prey" in target.read_text(encoding="utf-8")
        assert repair_contexts[0]["director_quality_repair"]["artifact_quality_errors"]
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert len(adapter_result.get("semantic_quality_repair_attempts") or []) == 1

    @pytest.mark.asyncio
    async def test_execute_fails_autofix_declared_scope_without_real_materialization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _unexpected_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("direct runtime provider bypass must not be called")

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _unexpected_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-scaffold"},
        )

        target = tmp_path / "src" / "renderer" / "game-view.tsx"
        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        assert target.exists() is False
        updated = adapter.task_board.get_task(str(task.id))
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
    async def test_execute_accepts_existing_scope_after_read_only_mutation_guard(self, tmp_path: Any) -> None:
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
        task = adapter.task_board.create(
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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-read-only"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        updated = adapter.task_board.get_task(str(task.id))
        assert updated is not None
        assert str(updated.get("status") or "").lower() == "completed"
        raw_metadata = updated.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_adapter_result = metadata.get("adapter_result")
        adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
        assert adapter_result.get("existing_contract_evidence", {}).get("ok") is True
        assert adapter_result.get("primary_llm", {}).get("error", "").startswith("TransactionKernel execution failed")
        assert adapter_result.get("direct_fallback", {}).get("skipped_reason") == "runtime_provider_bypass_removed"

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_read_write_batch_violation(self, tmp_path: Any) -> None:
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
        task = adapter.task_board.create(
            subject="Extend multiplayer session persistence",
            description="Implement multiplayer session persistence.",
            metadata={
                "phase": "core",
                "scope_paths": ["src/server/session-store.ts"],
                "target_files": ["src/server/session-store.ts"],
                "acceptance": ["src/server/session-store.ts exposes persistence methods"],
            },
        )

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
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-batch-violation"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"
        assert result["existing_contract_evidence"]["ok"] is True

    @pytest.mark.asyncio
    async def test_execute_accepts_existing_scope_after_successful_no_diff_response(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "server" / "app.ts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("export const serverReady = true;\n", encoding="utf-8")
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
            subject="Extend Node.js backend entrypoint",
            description="Implement Node.js backend entrypoint.",
            metadata={
                "phase": "implementation",
                "scope_paths": ["src/server/app.ts"],
                "target_files": ["src/server/app.ts"],
            },
        )

        async def _successful_no_diff_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "Verified existing backend entrypoint.", "success": True}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _successful_no_diff_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scope-after-successful-no-diff"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "verified_existing_workspace_scope"

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
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration tests",
            description="Verify multiplayer card integration tests according to acceptance criteria.",
            metadata={
                "phase": "verify",
                "scope_paths": ["tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["No placeholder tests remain"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("existing verification scope preflight should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-verification-scope-preflight"},
        )

        assert result["success"] is True
        assert result["materialization_mode"] == "preflight_verified_existing_workspace_scope"
        raw_evidence = result.get("existing_contract_evidence")
        evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
        assert evidence.get("ok") is True

    @pytest.mark.asyncio
    async def test_execute_repairs_overstrict_node_test_contract_before_llm(self, tmp_path: Any) -> None:
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
        task = adapter.task_board.create(
            subject="Strengthen multiplayer card integration test runner",
            description="Replace the brittle scripts/test.mjs validation-contract gate with substantive test checks.",
            metadata={
                "phase": "verify",
                "scope_paths": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "target_files": ["scripts/test.mjs", "tests/integration/multiplayer-flow.test.ts"],
                "acceptance": ["npm run test verifies the Card3D behavior test suite"],
            },
        )

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("deterministic test script repair should finish before LLM dialogue")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-node-test-script-contract-repair"},
        )

        rewritten = script.read_text(encoding="utf-8")
        assert result["success"] is True
        assert result["materialization_mode"] == "write_tool_and_workspace_diff"
        assert result["tools_executed"] >= 1
        assert result["changed_files"] == ["scripts/test.mjs"]
        assert rewritten == _build_substantive_node_test_script()
        assert "missing validation contract" not in rewritten
        assert "test file lacks executable check contract" in rewritten

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

    def test_deterministic_patch_residue_cleanup_removes_declared_marker(self, tmp_path: Any) -> None:
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
    async def test_execute_completes_scaffold_marker_cleanup_without_llm_call(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "server" / "app.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            'export const tags = ["runtime", "audit-seed"];\nexport const title = "server planning scenario 0";\n',
            encoding="utf-8",
        )
        adapter = _make_adapter(tmp_path)
        task = adapter.task_board.create(
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

        async def _unexpected_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            raise AssertionError("cleanup task should complete without invoking Gemma")

        adapter._invoke_role_dialogue_with_timeout = _unexpected_dialogue  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
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
        combat = adapter.task_board.create(
            subject="Audit turn based combat system scope",
            description="Materialize combat scope.",
            metadata={
                "external_task_id": "PM-AUTO-COMBAT",
                "source_task_id": "PM-AUTO-COMBAT",
                "target_files": ["src/combat/combat-system.ts"],
            },
        )

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
        updated = adapter.task_board.get_task(str(combat.id))
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
        sibling = adapter.task_board.create(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
                "scope_paths": ["worker_3.py"],
            },
        )

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

        assert result["task_id"] != str(sibling.id)
        materialized = adapter.task_runtime.get_task("D4-SAT-2")
        assert materialized is not None
        assert materialized["metadata"]["external_task_id"] == "D4-SAT-2"
        sibling_after = adapter.task_board.get_task(str(sibling.id))
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
        adapter.task_board.create(
            subject="Create independent saturation Python file 3",
            description="Create worker_3.py.",
            metadata={
                "external_task_id": "D4-SAT-3",
                "source_task_id": "D4-SAT-3",
                "target_files": ["worker_3.py"],
            },
        )
        adapter.task_board.create(
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
        task = adapter.task_board.create(
            subject="Implement interactive game renderer",
            description="Quality gate repair task generated because the PM contract omitted renderer scope.",
            metadata={
                "scope_paths": ["src/renderer/game-view.tsx"],
                "target_files": ["src/renderer/game-view.tsx"],
                "autofix": True,
                "autofix_reason": "game_pm_domain_coverage",
            },
        )

        async def _empty_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "role_model_not_configured"}

        async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

        adapter._invoke_role_dialogue_with_timeout = _empty_dialogue  # type: ignore[method-assign]
        adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

        result = await adapter.execute(
            task_id=str(task.id),
            input_data={"task_id": str(task.id)},
            context={"run_id": "run-director-existing-scaffold"},
        )

        assert result["success"] is False
        assert result["error_code"] == "incomplete_materialization"
        assert result["failure_class"] == "INCOMPLETE_MATERIALIZATION"
        updated = adapter.task_board.get_task(str(task.id))
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


class TestExistingWorkspaceTaskEvidence:
    """Director can verify already-materialized task scope without fresh diffs."""

    def test_declared_scope_present(self) -> None:
        task = {
            "scope": [
                "package.json",
                "src/types",
                "src/spec",
                "src/services",
                "src/store",
            ]
        }
        current_files = {
            "package.json": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert evidence["reason"] == "declared_scope_present"
        assert "src/spec" in evidence["existing_paths"]

    def test_missing_or_weak_scope_is_not_enough(self) -> None:
        task = {"scope": ["src/workbench", "src/library", "src/layouts", "src/components"]}
        current_files = {"src/components/StudioShell.tsx": "1"}

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"

    def test_high_coverage_scope_with_missing_declared_targets_is_not_enough(self) -> None:
        task = {
            "target_files": [
                "go.mod",
                "models/entity.go",
                "engine/service.go",
                "main.go",
                "main_test.go",
                "README.md",
            ]
        }
        current_files = {
            "go.mod": "1",
            "models/entity.go": "1",
            "engine/service.go": "1",
            "main.go": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_incomplete"
        assert evidence["coverage"] > 0.5
        assert evidence["missing_paths"] == ["main_test.go", "README.md"]

    def test_no_scope_paths_is_not_evidence(self) -> None:
        evidence = _build_existing_workspace_task_evidence(
            task={"goal": "Implement a UI"},
            current_files={"src/App.tsx": "1"},
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "no_declared_scope_paths"

    def test_glob_scope_paths_match_workspace_files(self) -> None:
        task = {
            "metadata": {
                "scope": [
                    "src/**/*.test.ts",
                    "src/**/*.test.tsx",
                    "README.md",
                    "tests",
                ]
            }
        }
        current_files = {
            "src/spec/generationSpec.test.ts": "1",
            "src/App.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "src/**/*.test.ts" in evidence["existing_paths"]
        assert "README.md" in evidence["existing_paths"]

    def test_existing_scope_rejects_placeholder_tests_when_workspace_is_available(self, tmp_path: Any) -> None:
        test_file = tmp_path / "tests" / "unit" / "card-rules.test.ts"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(
            "\n".join(f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(4)) + "\n",
            encoding="utf-8",
        )
        task = {
            "target_files": ["tests/unit/card-rules.test.ts"],
            "scope_paths": ["tests"],
        }
        current_files = {"tests/unit/card-rules.test.ts": "1"}

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_full=str(tmp_path),
        )

        assert evidence["ok"] is False
        assert evidence["reason"] == "declared_scope_quality_failed"
        assert any("trivial arithmetic placeholder" in item for item in evidence["artifact_quality_errors"])

    def test_materialized_orchestration_scope_markers_are_evidence(self) -> None:
        task = {
            "subject": (
                "Execute PM tasks strictly in order:\n"
                "- Project Foundation [scope: package.json, tsconfig.json, vite.config.ts, tailwind.config.js]\n"
                "- Domain Layer [scope: src/types, src/spec, src/services, src/store]\n"
                "- Delivery Verification [scope: tests, src/**/*.test.tsx, README.md]"
            )
        }
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "vite.config.ts": "1",
            "tailwind.config.js": "1",
            "src/types/domain.ts": "1",
            "src/spec/generationSpec.ts": "1",
            "src/services/mockGenerationService.ts": "1",
            "src/store/useStudioStore.ts": "1",
            "src/App.test.tsx": "1",
            "tests/routes/WorkbenchRoute.test.tsx": "1",
            "README.md": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src/**/*.test.tsx" in evidence["existing_paths"]
        assert evidence["reason"] == "declared_scope_present"

    def test_scope_label_prefixes_do_not_pollute_path_candidates(self) -> None:
        task = {"metadata": {"scope": "Root configuration files: package.json, tsconfig.json, postcss.config.js"}}
        current_files = {
            "package.json": "1",
            "tsconfig.json": "1",
            "postcss.config.js": "1",
        }

        evidence = _build_existing_workspace_task_evidence(task=task, current_files=current_files)

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert all("Root configuration files" not in item for item in evidence["candidate_paths"])

    def test_workspace_basename_prefix_is_not_treated_as_nested_scope(self) -> None:
        task = {
            "metadata": {
                "scope": "fashion-gen-studio/package.json, fashion-gen-studio/src/, vite.config.ts",
            }
        }
        current_files = {
            "package.json": "1",
            "src/App.tsx": "1",
            "vite.config.ts": "1",
        }

        evidence = _build_existing_workspace_task_evidence(
            task=task,
            current_files=current_files,
            workspace_name="fashion-gen-studio",
        )

        assert evidence["ok"] is True
        assert "package.json" in evidence["existing_paths"]
        assert "src" in evidence["existing_paths"]
        assert "fashion-gen-studio/package.json" not in evidence["missing_paths"]

    def test_repair_tasks_require_fresh_materialization(self) -> None:
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Repair TypeScript failure",
                    "metadata": {"acceptance": ["npm test returns PASS"]},
                }
            )
            is True
        )
        assert _task_requires_fresh_materialization({"subject": "Create initial source files"}) is True
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Implement Card3D tests",
                    "phase": "verification",
                    "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "title": "补齐领域验收测试",
                    "goal": "移除旧的占位测试，创建覆盖卡牌、牌组、多人流程、同步与3D场景的测试",
                    "phase": "verify",
                    "target_files": [
                        "tests/unit/card-rules.test.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                    ],
                    "execution_checklist": ["删除已存在的 trivial 占位测试（如算术测试）"],
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Replace placeholder Card3D unit tests",
                    "description": "Remove trivial arithmetic placeholder tests.",
                }
            )
            is True
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "QA Placeholder Repair Verification",
                    "phase": "verification",
                    "metadata": {"qa_rework_verification_only": True},
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Frontend Test Failure Reproduction",
                    "description": "Fix npm test failure with the smallest target-project change after evidence is collected.",
                    "metadata": {
                        "phase": "requirements",
                        "steps": ["Run npm test", "Identify failing assertion"],
                        "acceptance": ["The failing Vitest case is identified"],
                    },
                }
            )
            is False
        )
        assert (
            _task_requires_fresh_materialization(
                {
                    "subject": "Requirements task reopened by QA",
                    "metadata": {
                        "phase": "requirements",
                        "qa_rework_requested": True,
                        "adapter_result": {
                            "qa_passed": False,
                            "qa_rework_reason": "placeholder_content_detected",
                        },
                    },
                }
            )
            is True
        )

    def test_transient_provider_errors_can_accept_existing_scope(self) -> None:
        task = {
            "subject": "Extend realtime gateway",
            "phase": "implementation",
            "target_files": ["src/server/realtime-gateway.ts"],
        }

        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "TransactionKernel execution failed: circuit_open:50s_remaining",
                },
            )
            is True
        )
        assert (
            _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={
                    "success": False,
                    "error": "429 Client Error: Too Many Requests for url",
                },
            )
            is True
        )

    def test_non_transient_no_write_still_requires_materialization(self) -> None:
        assert (
            _can_accept_existing_workspace_scope(
                task={
                    "subject": "Extend realtime gateway",
                    "phase": "implementation",
                    "target_files": ["src/server/realtime-gateway.ts"],
                },
                requires_fresh_materialization=True,
                write_tool_evidence=False,
                primary_llm_summary={"success": False, "error": "model returned no tool calls"},
            )
            is False
        )


class TestDeterministicPythonRuntimeSmokeLongRunningBoundary:
    """Runtime smoke must not penalize a long-running __main__ block.

    Live factory-bench L4-23 (2026-06-17, after the static-smoke
    fix): the model wrote ``gateway/server.py`` whose ``__main__``
    block launches an HTTP server with ``serve_forever()`` — the
    canonical pattern for a Python web gateway. The runtime smoke
    killed the process after 5s and reported it as a timeout
    failure, which requeued the parent task and broke
    integration_qa. The script itself is correct:
    ``python3 gateway/server.py`` really starts the server on
    127.0.0.1:8080 and waits for connections. The platform must
    not penalize a long-running, contract-compliant __main__
    block as a quality failure.

    Strategy: when the runtime smoke hits its timeout, check
    whether the process is still alive. If yes, the model wrote
    a process that intentionally runs forever (server/daemon) —
    kill it and do NOT add it to ``artifact_quality_errors``. If
    the process already exited (perhaps during the cleanup),
    the timeout itself is the failure — report it as before.
    The smoke's job is to surface CALL-TIME errors, not loop
    semantics; a long-running server is contract-compliant for
    web-gateway / daemon / game-loop L4-L8 briefs.
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

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-server-bb-1",
            all_affected_files=["server.py"],
            timeout_seconds=1.0,
        )

        # Long-running process: smoke should NOT flag it as a failure.
        assert errors == [], errors

    def test_clean_main_block_still_passes(self, tmp_path: Any) -> None:
        """A clean main that exits within timeout is still a pass."""
        adapter = _make_adapter(tmp_path)
        (tmp_path / "ok.py").write_text(
            "if __name__ == '__main__':\n    print('done')\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-ok-bb-1",
            all_affected_files=["ok.py"],
            timeout_seconds=1.0,
        )

        assert errors == [], errors

    def test_subprocess_timeout_expired_process_killed(self, tmp_path: Any) -> None:
        """Sanity: when a long-running process is killed, the
        subprocess handle must be cleaned up so no zombie lingers.
        This guards against the regression where the smoke leaves
        a leaked subprocess after returning no errors."""
        adapter = _make_adapter(tmp_path)
        (tmp_path / "server.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.5)\n",
            encoding="utf-8",
        )

        # Find one leftover python3 process owned by this test pid
        # BEFORE running. (None expected, but the assertion is that
        # the count does not GROW after running.)
        import os
        import subprocess

        my_pid = os.getpid()
        before = (
            subprocess.run(
                ["pgrep", "-P", str(my_pid), "python3"],
                capture_output=True,
                text=True,
                check=False,
            )
            .stdout.strip()
            .split()
        )
        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-zombie-bb-1",
            all_affected_files=["server.py"],
            timeout_seconds=0.5,
        )
        after = (
            subprocess.run(
                ["pgrep", "-P", str(my_pid), "python3"],
                capture_output=True,
                text=True,
                check=False,
            )
            .stdout.strip()
            .split()
        )
        assert errors == [], errors
        # No new zombie child of this test process
        assert len(after) <= len(before), (before, after)


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


class TestDeterministicPythonRuntimeSmoke:
    """Director surfaces runtime errors that py_compile misses.

    Live factory-bench L1-01 (2026-06-17, after symbol-coherence fix):
    qwen3.6-27b-int4 wrote calculator.py that imports cleanly and
    py_compiles, but its ``__main__`` block calls ``evaluate('1+2')``
    which raises ``ValueError`` at call time (the model's tokenizer
    stores ``value=float(text)`` for operator tokens — a model bug,
    but a real one the platform should surface). The post-write
    materialization quality gate currently relies on ``py_compile`` +
    ``scan_workspace_artifact_quality`` — neither catches call-time
    errors. A rigid ruler must run the code, not just parse it.

    Strategy: for each ``.py`` file with a ``__main__`` block, run it
    in a subprocess with a timeout. If exit code != 0 or the process
    is killed by the timeout, surface a runtime error so the
    materialization repair ladder can fix it (currently via the LLM
    repair path; the deterministic path is intentionally conservative
    because runtime fixes are project-specific).
    """

    def test_python_runtime_smoke_catches_call_time_error(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # A script with __main__ that crashes. py_compile will pass;
        # only running it surfaces the bug.
        (tmp_path / "calculator.py").write_text(
            "def evaluate(expr: str) -> float:\n"
            "    raise ValueError('broken tokenizer')\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print(evaluate('1+2'))\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-1",
            all_affected_files=["calculator.py"],
            timeout_seconds=10.0,
        )

        assert len(errors) == 1, errors
        assert "calculator.py" in errors[0]
        assert "ValueError" in errors[0] or "Traceback" in errors[0]

    def test_python_runtime_smoke_passes_clean_main(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "hello.py").write_text(
            "if __name__ == '__main__':\n    print('hello')\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-2",
            all_affected_files=["hello.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_sets_workspace_pythonpath_for_test_scripts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "guess_number.py").write_text(
            "def generate_target() -> int:\n    return 42\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_guess_number.py").write_text(
            "import guess_number\n\n"
            "def test_target() -> None:\n"
            "    assert guess_number.generate_target() == 42\n\n"
            "if __name__ == '__main__':\n"
            "    test_target()\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-test-import",
            all_affected_files=["tests/test_guess_number.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_runs_unittest_discover_for_touched_tests(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "weather.py").write_text("class Weather:\n    pass\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_weather.py").write_text(
            "import unittest\n"
            "from src.weather import Weather\n\n"
            "class WeatherTests(unittest.TestCase):\n"
            "    def test_weather_updates(self) -> None:\n"
            "        Weather().update(dt_s=1.0, is_day=True)\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-unittest-discover",
            all_affected_files=["tests/test_weather.py"],
            timeout_seconds=10.0,
        )

        assert len(errors) == 1, errors
        assert "python -m unittest discover" in errors[0]
        assert "tests/test_weather.py" in errors[0]
        assert "AttributeError" in errors[0]

    def test_python_runtime_smoke_skips_module_without_main(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Library file with no __main__ block — must not be executed
        # (calling it would hang on missing CLI args or do nothing
        # useful). Just skip.
        (tmp_path / "library.py").write_text(
            "def helper() -> int:\n    return 42\n",
            encoding="utf-8",
        )

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-3",
            all_affected_files=["library.py"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_skips_non_python_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        (tmp_path / "readme.md").write_text("# title\n", encoding="utf-8")
        (tmp_path / "config.toml").write_text("x = 1\n", encoding="utf-8")

        errors = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-4",
            all_affected_files=["readme.md", "config.toml"],
            timeout_seconds=10.0,
        )

        assert errors == [], errors

    def test_python_runtime_smoke_terminates_hung_process(self, tmp_path: Any) -> None:
        """The smoke test must kill the child process after the
        configured timeout so a hung ``__main__`` does not leak
        past the Director turn boundary. After the L4-23 fix-#3
        boundary change, a long-running script is NOT a quality
        failure; the smoke only ensures the child is reaped so
        the next smoke call in the same turn is not contaminated.
        We verify the reap by re-running the smoke on the same
        file and expecting it to return within a small multiple
        of the timeout (i.e. it did not block on a leftover
        process).
        """
        adapter = _make_adapter(tmp_path)
        (tmp_path / "hung.py").write_text(
            "import time\nif __name__ == '__main__':\n    while True:\n        time.sleep(0.1)\n",
            encoding="utf-8",
        )

        # Long-running: the smoke must NOT flag this as a quality
        # failure (the L4-23 fix-#3 boundary). It must, however,
        # kill the child so subsequent smokes in the same turn
        # are not blocked by a leaked process.
        import time

        first = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5",
            all_affected_files=["hung.py"],
            timeout_seconds=0.5,
        )
        assert first == [], first
        started = time.monotonic()
        second = run_python_runtime_smoke(
            adapter,
            task_id="task-py-runtime-5b",
            all_affected_files=["hung.py"],
            timeout_seconds=0.5,
        )
        elapsed = time.monotonic() - started
        assert second == [], second
        assert elapsed < 5.0, f"second smoke took {elapsed:.2f}s -- leaked process"


# ---------------------------------------------------------------------------
# Materialized metadata
# ---------------------------------------------------------------------------


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
        # Should not raise even with no task_board
        adapter._persist_execution_backend_metadata("", MagicMock())

    def test_calls_update_board_task(self, tmp_path: Any) -> None:
        mock_runtime = MagicMock()
        adapter = _make_adapter(tmp_path, task_runtime=mock_runtime)
        from polaris.cells.roles.adapters.internal.director_execution_backend import DirectorExecutionBackendRequest

        req = DirectorExecutionBackendRequest(execution_backend="code_edit")
        adapter._persist_execution_backend_metadata("t1", req)
        mock_runtime.update_task.assert_called_once()


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
            def __init__(self) -> None:
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
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

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
        return run_materialization_quality_repair_schedule(
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
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

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

    results, summary = run_materialization_quality_repair_schedule(
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
    results, summary = run_materialization_quality_repair_schedule(
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
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

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

    results, summary = run_materialization_quality_repair_schedule(
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


def test_typescript_unresolved_unused_import_repair_removes_import(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    main = src / "main.ts"
    main.write_text(
        'import { Garden } from "./engine/garden";\nconsole.log("ready");\n',
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": []},
        task_id="task-unused-import",
        artifact_quality_errors=[
            "Artifact quality scan failed: unresolved relative import './engine/garden' in src/main.ts"
        ],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_unused_import_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert main.read_text(encoding="utf-8") == 'console.log("ready");\n'


def test_npm_test_script_repair_handles_ts_source_require_module_not_found(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "models" / "firefly.ts").write_text("export class Firefly {}\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n', encoding="utf-8")
    package_path = tmp_path / "package.json"
    package_path.write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "tsc",
                    "test": "node -e \"require('./src/models/firefly')\"",
                },
                "devDependencies": {"typescript": "^5.0.0"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": []},
        task_id="task-npm-test",
        artifact_quality_errors=[
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "node -e \"require('./src/models/firefly')\"\nError: Cannot find module './src/models/firefly'"
        ],
    )

    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_npm_script_contract_repair"
        for item in results
        if isinstance(item, dict)
    )
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    assert payload["scripts"]["test"] == "npm run build"


def test_typescript_comma_expected_repair_fixes_object_literal_semicolons(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flower = model_dir / "flower.ts"
    flower.write_text(
        "\n".join(
            [
                "export enum FlowerType {",
                "  Moonflower = 'moonflower'",
                "}",
                "",
                "export interface FlowerState {",
                "  type: FlowerType;",
                "  wilted: boolean;",
                "}",
                "",
                "export class Flower implements FlowerState {",
                "  public type: FlowerType = FlowerType.Moonflower;",
                "  public wilted: boolean = false;",
                "",
                "  private calculateAttractiveness(): number {",
                "    const baseAttractiveness: Record<FlowerType, number> = {",
                "      [FlowerType.Moonflower]: 0.9;",
                "    };",
                "    return baseAttractiveness[this.type];",
                "  }",
                "",
                "  public getState(): FlowerState {",
                "    return {",
                "      type: this.type,",
                "      wilted: this.wilted;",
                "    };",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/models/flower.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "Artifact quality scan failed: syntax error in src/models/flower.ts: "
            "src/models/flower.ts(16,37): error TS1005: ',' expected."
        ],
    )

    repaired = flower.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_return_object_semicolon_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "[FlowerType.Moonflower]: 0.9," in repaired
    assert "wilted: this.wilted," in repaired
    assert "type: FlowerType;" in repaired
    assert "public type: FlowerType = FlowerType.Moonflower;" in repaired


def test_typescript_return_object_comma_repair_fixes_inline_missing_property_comma(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
        typescript_repairs as typescript_repairs_module,
    )
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    def _forbid_legacy_fallback(*args: object, **kwargs: object) -> str:
        raise AssertionError("typescript return-object repair must not use legacy direct-write fallback")

    monkeypatch.setattr(
        typescript_repairs_module,
        "_repair_typescript_return_object_semicolon_lines",
        _forbid_legacy_fallback,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flight = model_dir / "Flight.ts"
    flight.write_text(
        "\n".join(
            [
                "export interface FlightResult {",
                "  samples: unknown[];",
                "  range: number;",
                "  maxAltitude: number;",
                "  flightTime: number;",
                "  landed?: boolean;",
                "}",
                "",
                "export class Flight {",
                "  simulate(): FlightResult {",
                "    const samples: unknown[] = [];",
                "    const range = 10;",
                "    const maxAltitude = 2;",
                "    const flightTime = 3;",
                "    return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/models/Flight.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(15,55): error TS1005: ',' expected."
        ],
    )

    repaired = flight.read_text(encoding="utf-8")
    repair_results = [
        item
        for item in results
        if isinstance(item, dict)
        and (item.get("result") or {}).get("source_tool") == "deterministic_typescript_return_object_semicolon_repair"
    ]
    assert summary["attempted"] is True
    assert repair_results
    for item in repair_results:
        repair_kernel = dict((item.get("result") or {}).get("repair_kernel") or {})
        assert repair_kernel["owner_cell"] == "director.runtime"
        assert repair_kernel["authority_hash"]
        assert repair_kernel["projection_hash"]
        assert repair_kernel["metadata"]["requires_revalidation"] is True
    assert "flightTime, landed:" in repaired


def test_typescript_return_object_comma_repair_fixes_previous_line_missing_comma(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flight = model_dir / "Flight.ts"
    flight.write_text(
        "\n".join(
            [
                "export function summarizeFlight() {",
                "  const range = 10;",
                "  const maxAltitude = 2;",
                "  return {",
                "    range",
                "    maxAltitude: maxAltitude,",
                "  };",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/models/Flight.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected."
        ],
    )

    repaired = flight.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert results
    assert "    range," in repaired
    assert "    maxAltitude: maxAltitude," in repaired


def test_typescript_enum_member_separator_repair_fixes_enum_semicolon_only(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    moonphase = model_dir / "moonphase.ts"
    moonphase.write_text(
        "\n".join(
            [
                "export enum MoonPhase {",
                "  New,",
                "  Full,",
                "  WaningCrescent;",
                "}",
                "",
                "export interface MoonState {",
                "  phase: MoonPhase;",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/models/moonphase.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: "
            "src/models/moonphase.ts(4,18): error TS1357: "
            "An enum member name must be followed by a ',', '=', or '}'."
        ],
    )

    repaired = moonphase.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    enum_result = next(
        item
        for item in results
        if isinstance(item, dict)
        and (item.get("result") or {}).get("source_tool") == "deterministic_typescript_enum_member_separator_repair"
    )
    assert enum_result["tool"] == "edit_file"
    assert enum_result["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_enum_member_separator_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "  WaningCrescent," in repaired
    assert "  phase: MoonPhase;" in repaired


def test_typescript_missing_closing_brace_repair_fixes_ts1005_brace_expected(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    engine_dir = tmp_path / "src" / "engine"
    engine_dir.mkdir(parents=True)
    renderer = engine_dir / "renderer.ts"
    renderer.write_text(
        "\n".join(
            [
                "export function renderGarden(): string {",
                "  const firefly = { glow: 1 };",
                "  if (firefly.glow > 0) {",
                "    return 'firefly flower moon humidity';",
                "  }",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/engine/renderer.ts"]},
        task_id="task-1",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/engine/renderer.ts(1,3780): error TS1005: '}' expected."
        ],
    )

    repaired = renderer.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_missing_closing_brace_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert repaired.rstrip().endswith("}")
    assert repaired.count("{") == repaired.count("}")


def test_typescript_comma_expected_repair_accepts_plain_tsc_error_format(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    model_dir = tmp_path / "src" / "models"
    model_dir.mkdir(parents=True)
    flower = model_dir / "Flower.ts"
    flower.write_text(
        "\n".join(
            [
                "export interface FlowerState {",
                "  color: string;",
                "}",
                "",
                "export class Flower {",
                "  public state: FlowerState;",
                "  constructor(color: string) {",
                "    this.state = {",
                "      color;",
                "    };",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["src/models/Flower.ts"]},
        task_id="task-1",
        artifact_quality_errors=["src/models/Flower.ts(9,12): error TS1005: ',' expected."],
    )

    repaired = flower.read_text(encoding="utf-8")
    assert summary["attempted"] is True
    assert any(
        (item.get("result") or {}).get("source_tool") == "deterministic_typescript_return_object_semicolon_repair"
        for item in results
        if isinstance(item, dict)
    )
    assert "      color,\n" in repaired
    assert "      color;\n" not in repaired


def test_materialization_quality_errors_scan_declared_target_files(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "changed.ts").write_text("export const changed = 1;\n", encoding="utf-8")
    (src / "declared.ts").write_text(
        "export function broken() {\n  return {\n    value: 1;\n  };\n}\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(workspace=str(tmp_path))

    errors = _collect_materialization_quality_errors(
        adapter,
        task={"target_files": ["src/changed.ts", "src/declared.ts"]},
        all_affected_files=["src/changed.ts"],
        workspace_name=tmp_path.name,
    )

    assert any("src/declared.ts" in error for error in errors)


def test_materialization_quality_evidence_scanner_receives_task_id(tmp_path: Any, monkeypatch: Any) -> None:
    from polaris.cells.roles.adapters.internal.director import execute_method

    seen: dict[str, Any] = {}

    def _capture_evidence(workspace: str, *, relative_paths: list[str] | None = None, task_id: str = "") -> Any:
        seen["workspace"] = workspace
        seen["paths"] = list(relative_paths or [])
        seen["task_id"] = task_id
        return SimpleNamespace(errors=[], issues=[])

    monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality_evidence", _capture_evidence)
    adapter = SimpleNamespace(workspace=str(tmp_path))

    execute_method._collect_materialization_quality_errors(
        adapter,
        task={"task_id": "TASK-FALLBACK", "target_files": ["src/entry.ts"]},
        all_affected_files=["src/entry.ts"],
        workspace_name=tmp_path.name,
        context={"target_task_id": "TASK-RUNTIME"},
    )

    assert seen["workspace"] == str(tmp_path)
    assert seen["task_id"] == "TASK-RUNTIME"
    assert seen["paths"] == ["src/entry.ts"]


def test_materialization_quality_errors_keep_pinned_step_single_file_scope(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "pinned.ts").write_text("export const pinned = 1;\n", encoding="utf-8")
    (src / "other.ts").write_text(
        "export function broken() {\n  return {\n    value: 1;\n  };\n}\n",
        encoding="utf-8",
    )
    adapter = SimpleNamespace(workspace=str(tmp_path))

    errors = _collect_materialization_quality_errors(
        adapter,
        task={"target_files": ["src/pinned.ts", "src/other.ts"]},
        all_affected_files=["src/pinned.ts"],
        workspace_name=tmp_path.name,
        context={"construction_step": {"target_file": "src/pinned.ts"}},
    )

    assert not any("src/other.ts" in error for error in errors)


def test_explicit_quality_repair_prefers_failed_test_named_artifact(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
    )

    (tmp_path / "README.md").write_text("# Dream Note\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node --test tests/smoke.test.js"}}\n', encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "smoke.test.js").write_text("import test from 'node:test';\n", encoding="utf-8")
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm test):\n"
        "not ok 5 - README.md documents how to install and run the project\n"
        f"  location: '{tmp_path / 'tests' / 'smoke.test.js'}:83:1'\n"
        "  error: 'README must document npm install'\n"
        "  name: 'AssertionError'\n"
    )

    targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["package.json", "README.md", "tests/smoke.test.js"],
        workspace_full=str(tmp_path),
    )

    assert targets[0] == "README.md"


def test_javascript_test_repair_expands_barrel_import_to_owner_modules(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
    )

    src = tmp_path / "src"
    models = src / "models"
    tests = tmp_path / "tests"
    models.mkdir(parents=True)
    tests.mkdir()
    (src / "index.ts").write_text(
        "\n".join(
            [
                'export { Market } from "./models/Market";',
                'export type { InventoryItem } from "./models/Inventory";',
                'export type { ReputationEvent } from "./models/Reputation";',
                'export type { FairyProfile } from "./models/Fairy";',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for name in ("Market", "Inventory", "Reputation", "Fairy"):
        (models / f"{name}.ts").write_text(f"export class {name} {{}}\n", encoding="utf-8")
    (tests / "behavior.test.ts").write_text(
        'import { Market } from "../src/index";\n'
        'import { describe, expect, it } from "vitest";\n'
        'describe("Market", () => it("rejects overflow", () => expect(Market).toBeDefined()));\n',
        encoding="utf-8",
    )
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm test):\n"
        "FAIL  tests/behavior.test.ts > Market > rejects overflow at maxStalls (boundary)\n"
        "AssertionError: expected [Function] to throw error matching /MARKET_FULL/ "
        "but got '市集 Tiny 已达摊位上限 1'\n"
        f" ❯ tests/behavior.test.ts:151:66\n"
        f" ❯ {tmp_path / 'src' / 'models' / 'Fairy.ts'}:118:13\n"
    )

    targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=[
            "src/index.ts",
            "src/models/Market.ts",
            "src/models/Inventory.ts",
            "src/models/Reputation.ts",
            "src/models/Fairy.ts",
            "tests/behavior.test.ts",
        ],
        workspace_full=str(tmp_path),
    )

    assert targets[:4] == [
        "src/models/Market.ts",
        "src/models/Inventory.ts",
        "src/models/Reputation.ts",
        "src/models/Fairy.ts",
    ], targets
    assert targets.index("src/index.ts") > targets.index("src/models/Fairy.ts")


def test_step_verify_environment_prep_plan_comes_from_runtime_catalog(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _step_verify_environment_prep_plans,
    )

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^1.6.0"}}) + "\n",
        encoding="utf-8",
    )

    plans = _step_verify_environment_prep_plans("npm run test", workspace=str(tmp_path))

    assert len(plans) == 1
    assert plans[0]["schema_version"] == "director.environment_prep_plan.v1"
    assert plans[0]["ecosystem"] == "node"
    assert plans[0]["package_manager"] == "npm"
    assert plans[0]["policy"]["command_source"] == "director.runtime.environment_prep_catalog"
    assert plans[0]["policy"]["llm_generated_command_allowed"] is False


def test_step_verify_environment_prep_runs_when_node_modules_is_stale(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _step_verify_environment_prep_plans,
    )

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "vitest").mkdir()
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"build": "tsc -p tsconfig.json"},
                "devDependencies": {
                    "vitest": "^1.6.0",
                    "@types/node": "^22.10.0",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "stale-env",
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": {"vitest": "^1.6.0", "@types/node": "^22.10.0"}},
                    "node_modules/vitest": {"version": "1.6.0"},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    plans = _step_verify_environment_prep_plans("npm run build", workspace=str(tmp_path))

    assert len(plans) == 1
    assert plans[0]["policy"]["command_source"] == "director.runtime.environment_prep_catalog"


def test_step_verify_runs_environment_prep_before_verify(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import quality_gate as quality_gate_module

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^1.6.0"}}) + "\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, str]] = []

    def fake_environment_prep(verify: str, *, workspace: str) -> list[str]:
        calls.append((verify, workspace))
        (tmp_path / "node_modules").mkdir()
        return []

    def fake_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        assert args[0] == "npm run test"
        assert kwargs["cwd"] == str(tmp_path)
        assert (tmp_path / "node_modules").is_dir()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(quality_gate_module, "_run_step_verify_environment_prep", fake_environment_prep)
    monkeypatch.setattr(quality_gate_module.subprocess, "run", fake_run)

    errors = quality_gate_module._collect_step_verify_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        {"construction_step": {"verify": "npm run test"}},
    )

    assert errors == []
    assert calls == [("npm run test", str(tmp_path))]


def test_node_tap_multi_failure_quality_repair_preserves_batch(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    src = tmp_path / "src"
    src.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tmp_path / "package.json").write_text(
        '{"name":"wrong","scripts":{"test":"node tests/smoke.test.js"}}\n', encoding="utf-8"
    )
    (src / "index.js").write_text("console.log('wrong')\n", encoding="utf-8")
    (tests / "smoke.test.js").write_text("import test from 'node:test';\n", encoding="utf-8")
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm test):\n"
        "not ok 1 - package.json declares package name and module type\n"
        "  error: 'package name mismatch'\n"
        "not ok 2 - npm start launches src/index.js and prints product banner\n"
        "  error: 'banner missing'\n"
        "not ok 3 - JSON persistence is handled by src/index.js add/list commands\n"
        "  error: 'store file missing'\n"
        f"  location: '{tests / 'smoke.test.js'}:42:1'\n"
        "  name: 'AssertionError'\n"
        "# tests 3\n"
        "# pass 0\n"
        "# fail 3\n"
    )

    targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["package.json", "src/index.js", "tests/smoke.test.js"],
        workspace_full=str(tmp_path),
    )
    preserve_batch = _should_preserve_materialization_quality_repair_batch([error])
    selected = _select_materialization_quality_repair_target_batch(
        targets,
        repair_attempt=2,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert preserve_batch is True
    assert selected[:3] == ["package.json", "src/index.js", "tests/smoke.test.js"]


def test_python_runtime_smoke_traceback_quality_repair_preserves_batch(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
        _python_runtime_smoke_repair_target_files,
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    tests = tmp_path / "tests"
    src = tmp_path / "src"
    tests.mkdir()
    src.mkdir()
    (tests / "test_product.py").write_text(
        "import unittest\nclass ProductTest(unittest.TestCase):\n    pass\n",
        encoding="utf-8",
    )
    (src / "main.rs").write_text('fn main() { println!("palette"); }\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("cargo run\n", encoding="utf-8")
    error = (
        "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py' (returncode=1); tail:\n"
        f'  File "{tmp_path / "tests" / "test_product.py"}", line 188, in test_html_output_marker\n'
        '    self.assertRegex(src, r"println!\\\\s*\\\\(\\\\s*\\"Palette")\n'
        "AssertionError: Regex didn't match: '<\\\\s*html|<!DOCTYPE|\\\\.html' not found\n"
        "README.md must include cargo test reference\n"
    )

    runtime_targets = _python_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["README.md", "src/main.rs", "tests/test_product.py"],
        workspace_full=str(tmp_path),
    )
    explicit_targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["README.md", "src/main.rs", "tests/test_product.py"],
        workspace_full=str(tmp_path),
    )
    targets = [*runtime_targets, *[target for target in explicit_targets if target not in runtime_targets]]
    preserve_batch = _should_preserve_materialization_quality_repair_batch([error])
    selected = _select_materialization_quality_repair_target_batch(
        targets,
        repair_attempt=3,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert runtime_targets == ["src/main.rs", "tests/test_product.py"]
    assert "README.md" in explicit_targets
    assert preserve_batch is True
    assert selected == targets


def test_python_harness_behavior_failure_preserves_cross_language_source_batch() -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    error = (
        "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py' (returncode=1); tail:\n"
        "workspace validation command failed (python -m unittest discover -s tests -p test_*.py -v):\n"
        "FAIL: test_flavors_are_normalized_lowercase_and_deduped (test_product.ProductTest)\n"
        "AssertionError: 'sweet' not found in ['spicy', 'Sweet', 'salty', 'umami', 'xyz']\n"
    )
    targets = [
        "src/engine/mapper.rs",
        "src/engine/mod.rs",
        "src/engine/plating.rs",
        "src/main.rs",
        "tests/test_product.py",
    ]

    preserve_batch = _should_preserve_materialization_quality_repair_batch(
        [error],
        repair_target_candidates=targets,
    )
    selected = _select_materialization_quality_repair_target_batch(
        targets,
        repair_attempt=3,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert preserve_batch is True
    assert selected == targets


def test_python_harness_behavior_failure_without_cross_language_batch_stays_narrow() -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _should_preserve_materialization_quality_repair_batch,
    )

    error = (
        "Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py' (returncode=1); tail:\n"
        "AssertionError: expected rendered output marker\n"
    )

    assert (
        _should_preserve_materialization_quality_repair_batch(
            [error],
            repair_target_candidates=["src/engine/mapper.rs", "tests/test_product.py"],
        )
        is False
    )


def test_python_runtime_smoke_embedded_rust_compile_targets_workspace_sources(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _python_runtime_smoke_repair_target_files,
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    tests = tmp_path / "tests"
    src = tmp_path / "src"
    models = src / "models"
    engine = src / "engine"
    tests.mkdir()
    models.mkdir(parents=True)
    engine.mkdir()
    (tests / "test_product.py").write_text("import unittest\n", encoding="utf-8")
    (src / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (models / "flavor.rs").write_text("pub struct Flavor;\n", encoding="utf-8")
    (models / "palette.rs").write_text("pub struct Palette;\n", encoding="utf-8")
    (engine / "mod.rs").write_text("pub mod mapper;\n", encoding="utf-8")
    (engine / "mapper.rs").write_text("use crate::models::flavor::Taste;\n", encoding="utf-8")
    error = (
        "Artifact quality scan failed: workspace validation command failed "
        "(python -m unittest discover -s tests -p test_*.py -v); touched_tests=['tests/test_product.py']; tail:\n"
        "test_cargo_check (test_product.CargoBuildTests.test_cargo_check) ... skipped "
        "'cargo check did not succeed in this environment; stderr tail: "
        "> src/models/palette.rs:9:1\\n"
        "error[E0432]: unresolved import `crate::models::flavor::Taste`\\n"
        "Some errors have detailed explanations: E0432, E0609.\\n"
        "error: could not compile `example` (lib) due to 22 previous errors; 1 warning emitted\\n'\n"
        "FAIL: test_html_report_wired (test_product.ContentCoverageTests.test_html_report_wired)\n"
        "AssertionError: False is not true : main.rs (or engine module) must produce an HTML report.\n"
    )

    targets = _python_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["tests/test_product.py"],
        workspace_full=str(tmp_path),
    )
    preserve_batch = _should_preserve_materialization_quality_repair_batch(
        [error],
        repair_target_candidates=targets,
    )
    selected = _select_materialization_quality_repair_target_batch(
        targets,
        repair_attempt=3,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert targets[0] == "src/models/palette.rs"
    assert "src/models/flavor.rs" in targets
    assert "src/engine/mapper.rs" in targets
    assert "src/main.rs" in targets
    assert preserve_batch is True
    assert selected == targets


def test_javascript_runtime_smoke_traceback_targets_workspace_entrypoint(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _javascript_runtime_smoke_repair_target_files,
    )

    src = tmp_path / "src"
    src.mkdir()
    (src / "index.js").write_text("const galaxy = {};\ngalaxy.registerAlien();\n", encoding="utf-8")
    external_path = "/var/tmp/not-this-workspace/src/escape.js"
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "> interstellar-lost-and-found@1.0.0 start\n"
        "> node src/index.js\n\n"
        f"{tmp_path / 'src' / 'index.js'}:27\n"
        "  galaxy.registerAlien(\n"
        "         ^\n\n"
        "TypeError: galaxy.registerAlien is not a function\n"
        f"    at createDefaultStation ({tmp_path / 'src' / 'index.js'}:27:10)\n"
        f"    at ignored ({external_path}:4:2)\n"
        "Node.js v22.21.1"
    )

    targets = _javascript_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["package.json", "src/index.js"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["src/index.js"]


def test_python_module_entrypoint_traceback_prefers_executing_file(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
        _ordered_materialization_quality_repair_target_candidates,
        _python_runtime_smoke_repair_target_files,
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    src = tmp_path / "src"
    engine = src / "engine"
    engine.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "main.py").write_text(
        "from engine.forecast import build_forecast\nprint(build_forecast('happy'))\n",
        encoding="utf-8",
    )
    (engine / "__init__.py").write_text("", encoding="utf-8")
    (engine / "forecast.py").write_text("def build_forecast(mood):\n    return mood\n", encoding="utf-8")
    error = (
        "Artifact quality scan failed: workspace validation command failed "
        f"(/usr/bin/python -m src.main): Traceback (most recent call last):\n"
        '  File "<frozen runpy>", line 198, in _run_module_as_main\n'
        '  File "<frozen runpy>", line 88, in _run_code\n'
        f'  File "{src / "main.py"}", line 1, in <module>\n'
        "    from engine.forecast import build_forecast\n"
        "ModuleNotFoundError: No module named 'engine'"
    )
    changed_files = ["src/__init__.py", "src/main.py", "src/engine/__init__.py", "src/engine/forecast.py"]

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
    ordered = _ordered_materialization_quality_repair_target_candidates(
        missing_target_files=[],
        runtime_smoke_target_files=runtime_targets,
        semantic_quality_target_files=[],
        explicit_quality_target_files=explicit_targets,
        should_merge_missing_targets=False,
    )
    preserve_batch = _should_preserve_materialization_quality_repair_batch([error])
    selected = _select_materialization_quality_repair_target_batch(
        ordered,
        repair_attempt=2,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert runtime_targets[0] == "src/main.py"
    assert explicit_targets[0] == "src/main.py"
    assert "src/engine/__init__.py" in ordered
    assert ordered[0] == "src/main.py"
    assert preserve_batch is True
    assert selected[0] == "src/main.py"
    assert selected == ordered


def test_esmodule_commonjs_entrypoint_failure_preserves_repair_batch(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
        _select_materialization_quality_repair_target_batch,
        _should_preserve_materialization_quality_repair_batch,
    )

    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "package.json").write_text(
        '{"type":"module","scripts":{"start":"node src/index.js"}}\n', encoding="utf-8"
    )
    (src / "index.js").write_text('const Note = require("./models/Note");\n', encoding="utf-8")
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start):\n"
        f"file://{tmp_path}/src/index.js:3\n"
        'const Note = require("./models/Note");\n'
        "             ^\n"
        "ReferenceError: require is not defined in ES module scope, you can use import instead\n"
        "This file is being treated as an ES module because it has a '.js' file extension and "
        f'\'{tmp_path}/package.json\' contains "type": "module".\n'
    )

    targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["package.json", "src/index.js"],
        workspace_full=str(tmp_path),
    )
    preserve_batch = _should_preserve_materialization_quality_repair_batch([error])
    selected = _select_materialization_quality_repair_target_batch(
        targets,
        repair_attempt=2,
        preserve_batch_after_first_attempt=preserve_batch,
    )

    assert preserve_batch is True
    assert selected == ["package.json", "src/index.js"]


def test_placeholder_node_test_synthesis_default_off(monkeypatch, tmp_path: Any) -> None:
    """§8 regression: missing test files should not be masked by fabricated
    placeholder tests in production/director hot paths."""
    monkeypatch.delenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", raising=False)
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule as run_materialization_quality_repair_schedule,
    )

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    adapter = SimpleNamespace(
        workspace=str(tmp_path),
        _execution=SimpleNamespace(_message_bus=None),
        _update_task_progress=lambda *args, **kwargs: None,
    )

    errors = [
        "npm package manifest has test runner script but no test/spec files exist in package.json",
    ]

    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["package.json"]},
        task_id="task-1",
        artifact_quality_errors=errors,
    )

    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "tests" / "unit" / "workspace.test.ts").exists()

    # The opt-in env var is now permanently inert: setting it fabricates nothing.
    monkeypatch.setenv("KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS", "1")
    results, summary = run_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["package.json"]},
        task_id="task-1",
        artifact_quality_errors=errors,
    )
    assert results == []
    assert summary["attempted"] is False
    assert not (tmp_path / "tests" / "unit" / "workspace.test.ts").exists()


class TestDeclaredPathCaseInsensitiveMatching:
    """L2-09 PM-0001-2 regression: PM declared "readme.md", Director wrote
    "README.md"; the case-sensitive declared-path filter dropped the task's
    only real output and produced director_no_materialized_changes."""

    def test_filter_keeps_case_mismatched_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["README.md"],
            modified_files=[],
        )
        assert new_files == ["README.md"]
        assert modified_files == []

    def test_filter_keeps_exact_case_target(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, _ = _filter_diff_to_task_declared_paths(
            task={"target_files": ["src/App.tsx"]},
            new_files=["src/App.tsx", "src/unrelated.ts"],
            modified_files=[],
        )
        assert new_files == ["src/App.tsx"]

    def test_filter_still_excludes_unrelated_files(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={"target_files": ["readme.md"]},
            new_files=["game.js"],
            modified_files=["index.html"],
        )
        assert new_files == []
        assert modified_files == []

    def test_filter_allows_declared_scope_directory_outputs_with_target_files(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={
                "target_files": ["package.json", "README.md"],
                "scope_paths": ["package.json", "README.md", "src", "tests"],
            },
            new_files=["src/index.js", "tests/smoke.test.js", "outside.js"],
            modified_files=["README.md"],
        )

        assert new_files == ["src/index.js", "tests/smoke.test.js"]
        assert modified_files == ["README.md"]

    def test_filter_does_not_broaden_parent_scope_when_specific_target_exists(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _filter_diff_to_task_declared_paths,
        )

        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task={
                "target_files": ["src/client/network-client.ts"],
                "scope_paths": ["src"],
            },
            new_files=["src/server/moderation.ts", "src/client/network-client.ts"],
            modified_files=[],
        )

        assert new_files == ["src/client/network-client.ts"]
        assert modified_files == []

    def test_missing_declared_target_files_ignores_scope_directories(self, tmp_path: Any) -> None:
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _missing_declared_target_files,
        )

        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Run\n", encoding="utf-8")

        missing = _missing_declared_target_files(
            {
                "target_files": ["package.json", "README.md"],
                "scope_paths": ["package.json", "README.md", "src", "tests"],
            },
            str(tmp_path),
        )

        assert missing == []

    def test_out_of_scope_diff_reports_filtered_real_output(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_workspace_out_of_scope_diff,
        )

        diff = _collect_workspace_out_of_scope_diff(
            task={"scope": ["src/python"], "target_files": ["src/python/guess_number.py"]},
            baseline_files={},
            current_files={"guess_number.py": "fingerprint"},
        )

        assert diff["affected_files"] == ["guess_number.py"]
        assert diff["new_files"] == ["guess_number.py"]
        assert diff["modified_files"] == []

    def test_out_of_scope_diff_ignores_declared_output(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_workspace_out_of_scope_diff,
        )

        diff = _collect_workspace_out_of_scope_diff(
            task={"scope": ["src/python"], "target_files": ["src/python/guess_number.py"]},
            baseline_files={},
            current_files={"src/python/guess_number.py": "fingerprint"},
        )

        assert diff["affected_files"] == []
        assert diff["new_files"] == []
        assert diff["modified_files"] == []

    def test_directory_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _path_matches_declared_candidate,
        )

        assert _path_matches_declared_candidate("Docs/Guide.md", "docs")
        assert _path_matches_declared_candidate("src/app.PY", "src/app.py")
        assert not _path_matches_declared_candidate("other/file.md", "docs")

    def test_glob_candidate_matches_case_insensitively(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _glob_path_matches,
        )

        assert _glob_path_matches("README.md", "readme.*")
        assert _glob_path_matches("src/Views/Home.vue", "src/**/home.vue")


class TestAcceptanceVerifyExistsExemption:
    """L2-09 class: identical-rewrite / case-variant writes produce an empty
    diff; when the PM contract's own `verify <path> exists` machine checks all
    pass AND write receipts exist, the task is satisfied, not failed."""

    @staticmethod
    def _evaluate(task: dict, workspace: Any, write_tool_evidence: bool = True) -> tuple[bool, dict]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _evaluate_acceptance_verify_exists,
        )

        return _evaluate_acceptance_verify_exists(
            task=task,
            workspace_full=str(workspace),
            write_tool_evidence=write_tool_evidence,
        )

    def test_all_assertions_pass(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["包含运行说明", "verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["readme.md"], "missing": []}

    def test_missing_path_not_exempted(self, tmp_path) -> None:
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["missing"] == ["readme.md"]

    def test_no_machine_assertions_no_exemption(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["README.md 存在于工作区根"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 0

    def test_requires_write_tool_evidence(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
        satisfied, _ = self._evaluate(
            {"acceptance_criteria": ["verify ./readme.md exists"]},
            tmp_path,
            write_tool_evidence=False,
        )
        assert satisfied is False

    def test_nested_path_case_insensitive(self, tmp_path) -> None:
        (tmp_path / "Docs").mkdir()
        (tmp_path / "Docs" / "Guide.md").write_text("g\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify docs/guide.md exists"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence["passed"] == ["docs/guide.md"]

    def test_one_missing_among_many_blocks_exemption(self, tmp_path) -> None:
        (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["verify a.md exists", "verify b.md exists"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["passed"] == ["a.md"]
        assert evidence["missing"] == ["b.md"]

    def test_posix_test_file_assertion_passes(self, tmp_path) -> None:
        (tmp_path / "requirements.txt").write_text("# standard library only\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f requirements.txt"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["requirements.txt"], "missing": []}

    def test_posix_test_and_grep_assertion_passes(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("Run with python3 calculator.py\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f README.md && grep -q 'python3 calculator.py' README.md"]},
            tmp_path,
        )
        assert satisfied is True
        assert evidence == {"checked": 1, "passed": ["README.md"], "missing": []}

    def test_posix_grep_literal_miss_blocks_exemption(self, tmp_path) -> None:
        (tmp_path / "README.md").write_text("Run with python calculator.py\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ["test -f README.md && grep -q 'python3 calculator.py' README.md"]},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 1
        assert evidence["passed"] == ["README.md"]
        assert evidence["missing"] == ["README.md"]

    def test_arbitrary_shell_command_not_exempted(self, tmp_path) -> None:
        (tmp_path / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
        satisfied, evidence = self._evaluate(
            {"acceptance_criteria": ['python3 -c "import calculator; print(calculator.add(2,3))"']},
            tmp_path,
        )
        assert satisfied is False
        assert evidence["checked"] == 0


class TestQualityRepairMissingTargetContract:
    """Quality-repair target selection contract.

    Original L2-10 r3 regression: the repair turn rewrote src/main.js (already
    present) instead of creating the missing src/styles.css — the repair
    message itself seeded the wrong target by listing changed files as paths.

    Current write-scope contract: task ``target_files`` are the write
    authority. Quality repair may only select repair targets inside the
    current task write scope; out-of-scope paths (cross-task source files,
    downstream test files, root manifests owned by other tasks) are DEFERRED
    with structured evidence (``stage=task_boundary_repair_targets_deferred``
    plus a ``task_boundary_scope_filter`` record naming the deferred paths)
    instead of being authorized for writing.
    """

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
    async def test_quality_repair_uses_deterministic_typescript_semantic_repair_before_llm(self, tmp_path) -> None:
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
                raise AssertionError("deterministic semantic repair should run before LLM repair")

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

        assert len(tool_results) == 1
        repair_payload = tool_results[0]["result"]
        assert repair_payload["source_tool"] == "deterministic_typescript_missing_export_repair"
        assert repair_payload["file"] == "src/verify.ts"
        assert repair_payload["repair_kernel"]["owner_cell"] == "director.runtime"
        assert repair_payload["repair_kernel"]["status"] == "applied"
        assert summary["stage"] == "deterministic_materialization_quality_repair"
        assert summary["success_reason"] == "repair_actions_require_quality_gate_rerun"
        assert summary["write_tool_evidence"] is True
        repaired = (tmp_path / "src" / "verify.ts").read_text(encoding="utf-8")
        assert "runVerification" in repaired

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
        assert "_transaction_kernel_forced_tool_choice" not in adapter.repair_context
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
        context: dict[str, Any] = {}

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

    def test_step_verify_missing_downstream_file_is_deferred(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate as director_quality_gate
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "Could not find 'tests/product.test.js'\n"

        monkeypatch.setattr(director_quality_gate.subprocess, "run", lambda *_args, **_kwargs: _Proc())
        monkeypatch.setattr(director_quality_gate, "_first_failing_verify_clause", lambda *_args, **_kwargs: "")
        context: dict[str, Any] = {"construction_step": {"verify": "npm test"}}

        errors = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task={"target_files": ["src/engine/rules.js", "src/engine/runner.js"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert context["director_task_boundary_deferred_quality_errors"] == [
            {
                "schema_version": "director.task_boundary.deferred_quality_errors.v1",
                "reason": "missing_workspace_file_outside_current_task_target_files",
                "artifact_quality_errors": [
                    "step verify failed (exit 1): npm test :: failure excerpt: Could not find 'tests/product.test.js'"
                ],
                "target_files": ["tests/product.test.js"],
            }
        ]

    def test_step_verify_package_script_entrypoint_outside_task_scope_is_deferred(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate as director_quality_gate
        from polaris.cells.roles.adapters.internal.director.quality_gate import (
            _collect_step_verify_errors,
        )

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "script 'verify' references missing local entrypoint: ./scripts/verify-package.js\n"

        clause = (
            "Artifact quality scan failed: npm package manifest script 'verify' references missing local "
            "entrypoint './scripts/verify-package.js' in package.json"
        )
        monkeypatch.setattr(director_quality_gate.subprocess, "run", lambda *_args, **_kwargs: _Proc())
        monkeypatch.setattr(director_quality_gate, "_first_failing_verify_clause", lambda *_args, **_kwargs: clause)
        context: dict[str, Any] = {"construction_step": {"verify": "npm run verify"}}

        errors = _collect_step_verify_errors(
            SimpleNamespace(workspace=str(tmp_path)),
            context,
            task={"target_files": ["src/index.js"]},
            workspace_name=tmp_path.name,
        )

        assert errors == []
        assert context["director_task_boundary_deferred_quality_errors"] == [
            {
                "schema_version": "director.task_boundary.deferred_quality_errors.v1",
                "reason": "npm_script_entrypoint_outside_current_task_target_files",
                "artifact_quality_errors": [
                    "step verify failed (exit 1) | "
                    "Artifact quality scan failed: npm package manifest script 'verify' references missing local "
                    "entrypoint './scripts/verify-package.js' in package.json | "
                    "failure excerpt: script 'verify' references missing local entrypoint: "
                    "./scripts/verify-package.js | full: npm run verify"
                ],
                "target_files": ["scripts/verify-package.js"],
            }
        ]

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
        assert forced_names == ["edit_file", "write_file", "execute_command"]
        assert adapter.repair_context["metadata"]["tool_contract"]["required_tools"] == ["execute_command"]
        assert "_transaction_kernel_forced_tool_choice" not in adapter.repair_context
        assert "_transaction_kernel_force_exact_tools" not in adapter.repair_context
        assert adapter.repair_context["director_quality_repair"]["edit_preferred_target_files"] == ["models/exhibit.go"]
        assert "edit_preferred_single_target" in adapter.repair_message
        assert "Do not call read_file" not in adapter.repair_message
        assert "call read_file for this target first when required by tool policy" in adapter.repair_message
        assert "write_only_single_target" not in adapter.repair_message
        assert adapter._execution.allowed_tool_names == {"edit_file", "execute_command", "write_file"}
        assert adapter._execution.allow_patch_fallback is True
        assert summary["repair_target_files"] == ["models/exhibit.go"]

    @pytest.mark.asyncio
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
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del adapter, task, artifact_quality_errors
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
    async def test_go_scaffold_marker_quality_repair_runs_deterministic_before_llm(self, tmp_path) -> None:
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
    async def test_single_missing_target_repair_coerces_raw_content_to_write_file(self, tmp_path) -> None:
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
        assert summary["write_tool_evidence"] is True
        assert tool_results[0]["result"]["source_tool"] == "director_quality_repair_raw_single_target_write_file"
        assert (tmp_path / "services" / "product_service" / "app.py").read_text(encoding="utf-8") == (
            "print('service ready')"
        )

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

    def test_materialization_quality_repair_filters_prompt_errors_to_current_scope(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
            _filter_materialization_quality_errors_for_repair_targets,
        )

        errors = [
            "Artifact quality scan failed: declared target file missing 'src/engine/mapper.rs'",
            "Artifact quality scan failed: declared target file missing 'src/engine/palette_generator.rs'",
            "Artifact quality scan failed: declared target file missing 'src/engine/plating_rules.rs'",
        ]

        scoped_errors = _filter_materialization_quality_errors_for_repair_targets(
            errors,
            ["src/engine/mapper.rs"],
        )
        message = _build_materialization_quality_repair_message(
            original_message="Implement Rust engine modules.",
            artifact_quality_errors=scoped_errors,
            changed_files=["index.html", "src/engine/mod.rs"],
            missing_target_files=["src/engine/mapper.rs"],
        )

        assert "src/engine/mapper.rs" in message
        assert "src/engine/palette_generator.rs" not in message
        assert "src/engine/plating_rules.rs" not in message
        assert "SINGLE MISSING TARGET REPAIR" in message

    def test_existing_quality_repair_keeps_full_verifier_diagnostics_context(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
            _filter_materialization_quality_errors_for_repair_targets,
        )

        errors = [
            'src/models/Market.ts(162,27): error TS2322: Type "not_found" is not assignable to type "unknown_sku".',
            "src/models/Market.ts(244,34): error TS2552: Cannot find name '_updated'. Did you mean 'updated'?",
            "src/web.ts(52,11): error TS2322: Type 'Document' is not assignable to type '{ readonly body: { appendChild: (n: unknown) => void; }; }'.",
        ]
        scoped_errors = _filter_materialization_quality_errors_for_repair_targets(
            errors,
            ["src/models/Market.ts"],
        )

        message = _build_materialization_quality_repair_message(
            original_message="Implement a TypeScript market project.",
            artifact_quality_errors=scoped_errors,
            directive_artifact_quality_errors=errors,
            changed_files=["src/models/Market.ts", "src/web.ts"],
            repair_target_files=["src/models/Market.ts"],
        )

        assert "FULL VERIFIER DIAGNOSTICS" in message
        assert "src/web.ts(52,11)" in message
        assert "TS2552" in message
        assert "EXISTING FAILED TARGET FILES" in message

    def test_repair_message_names_missing_targets_and_hides_changed_paths(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )
        from polaris.cells.roles.kernel.public import (
            extract_target_files_from_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现 Markdown 预览器核心文件",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/styles.css'"],
            changed_files=["index.html", "package.json", "src/main.js"],
            missing_target_files=["src/styles.css"],
        )
        assert "MISSING TARGET FILES" in message
        assert "[director_quality_repair:write_only_single_target]" in message
        assert "src/styles.css" in message
        # Changed files appear only as a count — path-shaped tokens seed the
        # retry target extractor with wrong targets.
        assert "src/main.js" not in message
        assert "3 file(s) were already written" in message
        extracted = extract_target_files_from_message(message)
        assert "src/styles.css" in extracted
        assert "src/main.js" not in extracted

    def test_repair_message_existing_target_block_prevents_raw_code_target_fallback(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )
        from polaris.cells.roles.kernel.public import (
            extract_target_files_from_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message=(
                "[mode:materialize]\n"
                "Implement the engine modules.\n"
                "const opts = { json: true };\n"
                "if (arg.startsWith('--json=')) opts.json = arg.slice('--json='.length);"
            ),
            artifact_quality_errors=[
                "Artifact quality scan failed: JavaScript source src/engine/runner.js uses CommonJS runtime syntax",
                "Artifact quality scan failed: JavaScript source src/engine/rules.js uses CommonJS runtime syntax",
            ],
            changed_files=["src/engine/rules.js", "src/engine/runner.js"],
            repair_target_files=["src/engine/runner.js", "src/engine/rules.js"],
        )

        extracted = extract_target_files_from_message(message)

        assert extracted == ["src/engine/runner.js", "src/engine/rules.js"]
        assert "opts.json" not in extracted

    def test_repair_message_missing_targets_do_not_replay_declared_contract_targets(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )
        from polaris.cells.roles.kernel.public import (
            extract_target_files_from_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message=(
                "[mode:materialize]\n"
                "target_files: tests/test_planet.py, tests/test_weather.py, tests/test_simulation.py\n"
                "Create Python unittest coverage for the planet weather simulation."
            ),
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed "
                "(python -m unittest discover -s tests -p test_*.py -v); tail:\n"
                "ModuleNotFoundError: No module named 'planet'"
            ],
            changed_files=["tests/test_planet.py", "tests/test_weather.py", "tests/test_simulation.py"],
            missing_target_files=["src/planet.py", "src/weather.py", "src/simulation.py"],
        )

        extracted = extract_target_files_from_message(message)

        assert extracted == [
            "src/planet.py",
            "src/weather.py",
            "src/simulation.py",
        ]
        assert "target_files: tests/test_planet.py" not in message

    def test_single_missing_target_repair_forces_one_write_without_reads(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create app assets.",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/styles.css'"],
            changed_files=["index.html"],
            missing_target_files=["src/styles.css"],
        )

        assert "SINGLE MISSING TARGET REPAIR" in message
        assert "exactly one write_file" in message
        assert "src/styles.css" in message
        assert "Do not read" in message
        assert "Do not list" in message

    def test_unresolved_symbol_repair_targets_exporting_module(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create shared SDK.",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved import symbol 'HTTPClient' "
                "from 'common.http_client' in common/__init__.py "
                "(sibling module does not define it)"
            ],
            changed_files=["common/__init__.py", "common/http_client.py"],
            missing_target_files=[],
        )

        assert "CROSS-FILE SYMBOL REPAIR" in message
        assert "common.http_client" in message
        assert "HTTPClient" in message
        assert "common/__init__.py" in message
        assert "Do not edit the importing file" in message
        assert "make the exporting module define or export exactly the missing symbol" in message
        assert "Do not read files first" in message
        assert "Do not list directories" in message
        assert "If this repair prompt also names package or typecheck targets" in message

    def test_javascript_missing_named_export_gets_contract_repair_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create JavaScript CLI app.",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm test): "
                "import furnace, { loadData, saveData, refineDream, addNote } from '../src/index.js';\n"
                "SyntaxError: The requested module '../src/index.js' does not provide an export named 'addNote'"
            ],
            changed_files=["src/index.js", "tests/index.test.js"],
            repair_target_files=["src/index.js", "tests/index.test.js"],
        )

        assert "JAVASCRIPT NAMED EXPORT REPAIR" in message
        assert "../src/index.js" in message
        assert "'loadData'" in message
        assert "'saveData'" in message
        assert "'refineDream'" in message
        assert "'addNote'" in message
        assert "default binding(s): 'furnace'" in message
        assert "keep a valid default export" in message
        assert "Do not remove, weaken, or skip that import" in message
        assert "preserve its import contract" in message

    def test_javascript_module_system_error_gets_coherence_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create JavaScript CLI app.",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                "ReferenceError: require is not defined in ES module scope. "
                'package.json contains "type": "module".'
            ],
            changed_files=["package.json", "src/index.js"],
            repair_target_files=["package.json", "src/index.js"],
        )

        assert "JAVASCRIPT MODULE SYSTEM REPAIR" in message
        assert "one coherent module system" in message
        assert "module.exports" in message
        assert "package.json is an authorized failed repair target" in message
        assert "Prefer preserving the named ESM import/export contract" in message
        assert "npm test plus the entrypoint smoke can run" in message

    def test_javascript_module_system_repair_freezes_package_when_out_of_scope(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create JavaScript CLI app.",
            artifact_quality_errors=[
                "Artifact quality scan failed: workspace validation command failed (npm run start): "
                "ReferenceError: require is not defined in ES module scope. "
                'package.json contains "type": "module".'
            ],
            changed_files=["package.json", "src/index.js"],
            repair_target_files=["src/index.js"],
        )

        assert "JAVASCRIPT MODULE SYSTEM REPAIR" in message
        assert "treat its existing module declaration as fixed input" in message
        assert "do not write package.json" in message
        assert "rewrite only the authorized JavaScript source/test files" in message
        assert "export` declarations" in message

    def test_html5_canvas_entrypoint_repair_message_names_browser_bootstrap(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create an HTML5 Canvas TypeScript flight simulator.",
            artifact_quality_errors=[
                "Real run gate failed: Canvas entrypoint did not render non-empty pixels "
                "for index.html after browser load."
            ],
            changed_files=["index.html", "src/main.ts"],
            repair_target_files=["index.html", "src/web.ts"],
        )

        assert "HTML5 CANVAS ENTRYPOINT REPAIR" in message
        assert "paint visible pixels" in message
        assert "after the DOM/canvas exists" in message
        assert "Node-only CLI entrypoint" in message

    def test_unresolved_relative_import_repair_prompt_omits_parent_traversal_specifier(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create TypeScript simulation modules.",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import '../data/seeddata' "
                "in src/engine/gardenengine.ts"
            ],
            changed_files=["src/engine/gardenengine.ts"],
            missing_target_files=["src/data/seeddata.ts"],
        )

        assert "../data/seeddata" not in message
        assert "src/data/seeddata.ts" in message
        assert "src/engine/gardenengine.ts" in message
        assert "Raw relative specifier omitted for path safety" in message

    def test_typecheck_repair_prompt_omits_external_dependency_parent_paths(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create TypeScript simulation modules.",
            artifact_quality_errors=[
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "../../../../node_modules/@types/three/src/nodes/accessors/ReferenceNode.d.ts(31,26): "
                "error TS1139: Type parameter declaration expected."
            ],
            changed_files=["package.json", "src/engine/simulation.ts"],
            missing_target_files=[],
            repair_target_files=["package.json"],
        )

        assert "../" not in message
        assert "node_modules" not in message
        assert "external dependency diagnostic TS1139" in message
        assert "Path omitted for workspace safety" in message

    def test_repair_message_without_missing_block_when_none(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["some error"],
            changed_files=[],
            missing_target_files=[],
        )
        assert "MISSING TARGET FILES" not in message
        assert "0 file(s) were already written" in message

    def test_python_runtime_smoke_no_args_gets_cli_entrypoint_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现一个命令行交互式计算器，支持循环读取用户输入。",
            artifact_quality_errors=[
                "Artifact quality scan failed: python runtime smoke crashed for 'calculator.py' "
                "(returncode=1); tail:\nError: No expression provided"
            ],
            changed_files=["calculator.py"],
            missing_target_files=[],
            repair_target_files=["calculator.py"],
        )

        assert "PYTHON CLI ENTRYPOINT REPAIR" in message
        assert "python <script>" in message
        assert "no-argument path must not crash or exit non-zero" in message
        assert "interactive CLI/input loop" in message
        assert "Do not require positional argv" in message

    def test_semantic_quality_repair_allows_rewriting_failed_changed_artifact(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="输出 verification_report.md",
            artifact_quality_errors=[
                "Director output quality gate failed: no project-domain signal found in changed files; "
                "expected one of ['task-1', 'task-2', 'readme', 'verification_report', 'task-3']"
            ],
            changed_files=["verification_report.md"],
            missing_target_files=[],
        )

        assert "MISSING TARGET FILES" not in message
        assert "must NOT be rewritten" not in message
        assert "failed quality gates" in message
        assert "rewrite only the failing changed artifact" in message
        # The repair-specific changed-file line remains count-based; the
        # original task contract may still legitimately name the target path.
        assert "1 file(s) were already written and failed quality gates" in message


class TestSyntaxRepairDirective:
    """I3-r15: the narrow-edit-ONLY directive (added L2-11 r2 to break the
    whole-file rewrite slip) backfired on weak local models — qwen could not
    form edit_blocks at all (121x "missing blocks or start") and was also
    forbidden the write_file rewrite it CAN do, leaving no usable repair path.
    The directive must give the laborer an executable path."""

    def test_syntax_error_gives_executable_repair_path(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in typing.js: typing.js:9\n"
                "    endTime: null;\n                 ^\n\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["typing.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        # The trap is gone: write_file rewrite of the one line is offered as the
        # easiest reliable path, with edit_blocks as a copy-verbatim alternative.
        assert "Do NOT rewrite the whole file" not in message
        assert "write_file" in message
        assert "edit_blocks" in message
        # Still constrains the change to the one broken line.
        assert "byte-for-byte" in message

    def test_tool_receipt_contamination_is_sanitized_before_repair_prompt(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="Create simulation tests.",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in tests/simulation.test.ts: simulation.test.ts:1\n"
                "**write_file**: Error - {'ok': False, 'error': 'Destructive shrink rejected: "
                "this edit would replace tests/garden.test.ts'}"
            ],
            changed_files=["tests/simulation.test.ts"],
            missing_target_files=[],
            repair_target_files=["tests/simulation.test.ts"],
        )

        assert "tool execution receipt contamination in tests/simulation.test.ts" in message
        assert "Destructive shrink rejected" not in message
        assert "**write_file**: Error" not in message
        assert "tests/garden.test.ts" not in message

    def test_no_directive_without_syntax_errors(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["declared target file missing 'readme.md'"],
            changed_files=[],
            missing_target_files=["readme.md"],
        )
        assert "SYNTAX REPAIR DIRECTIVE" not in message

    def test_quality_repair_message_includes_existing_target_content(self, tmp_path) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        source = tmp_path / "src" / "index.ts"
        source.parent.mkdir(parents=True)
        source.write_text(
            "export interface Moon { phase: 'full'; }\nexport const createMoon = (): Moon => ({ phase: 'full' });\n",
            encoding="utf-8",
        )

        message = _build_materialization_quality_repair_message(
            original_message="Repair TypeScript project.",
            artifact_quality_errors=[
                "Artifact quality scan failed: TypeScript project typecheck failed: "
                "src/index.ts(3,18): error TS2693: 'Moon' only refers to a type, "
                "but is being used as a value here."
            ],
            changed_files=["src/index.ts"],
            repair_target_files=["src/index.ts"],
            workspace_full=str(tmp_path),
        )

        assert "CURRENT UTF-8 CONTENT OF REPAIR TARGETS" in message
        assert "--- src/index.ts ---" in message
        assert "export const createMoon" in message


class TestTruncatedFileDirective:
    """L2-11 r6: index.html was whole-file-rewritten three times and every
    copy was output-limit-truncated; only append converges."""

    def test_truncation_error_gets_append_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="构建打字测试器",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in index.html: "
                "truncated/incomplete HTML: missing </html> closing tag; 1 unclosed <script> tag(s)"
            ],
            changed_files=["index.html"],
            missing_target_files=[],
        )
        assert "TRUNCATED FILE DIRECTIVE" in message
        assert "append_to_file" in message
        assert "Do NOT rewrite" in message
        assert "SYNTAX REPAIR DIRECTIVE" not in message

    def test_plain_syntax_error_keeps_narrow_edit_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=[
                "Artifact quality scan failed: syntax error in app.js: app.js:9\n"
                "    gfm: true;\n^\nSyntaxError: Unexpected token ';'"
            ],
            changed_files=["app.js"],
            missing_target_files=[],
        )
        assert "SYNTAX REPAIR DIRECTIVE" in message
        assert "TRUNCATED FILE DIRECTIVE" not in message


class TestCrossFileCoherenceRepair:
    """C7-text W3 (#54 repair-mode cross-file coherence): an unresolved relative
    import means the importer points at a module that does not exist yet. The
    repair message reframes it as a coherence obligation (create the module,
    export what the importer uses) instead of a bare missing path. Floor-safe:
    absent unless an unresolved-import error is present."""

    def test_unresolved_import_gets_coherence_directive(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="实现 React 应用",
            artifact_quality_errors=[
                "Artifact quality scan failed: unresolved relative import './router' in src/main.tsx"
            ],
            changed_files=["src/main.tsx"],
            missing_target_files=["src/router.tsx"],
        )
        assert "CROSS-FILE COHERENCE REPAIR" in message
        assert "./router" in message
        assert "EXPORT" in message
        # Must not steer the model to rewrite the (existing) importing file.
        assert "Do not edit the importing file" in message

    def test_no_coherence_block_without_unresolved_imports(self) -> None:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="task",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'readme.md'"],
            changed_files=[],
            missing_target_files=["readme.md"],
        )
        assert "CROSS-FILE COHERENCE REPAIR" not in message

    def test_coherence_block_is_floor_inert(self) -> None:
        """Floor-safety lock: with no unresolved-import error the builder emits
        no trace of the coherence block (the L2 success path is unperturbed)."""
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _build_materialization_quality_repair_message,
        )

        message = _build_materialization_quality_repair_message(
            original_message="build app",
            artifact_quality_errors=["Artifact quality scan failed: syntax error in app.js: ..."],
            changed_files=["app.js"],
            missing_target_files=[],
        )
        assert "CROSS-FILE COHERENCE REPAIR" not in message
        assert "coherence" not in message.lower()


class TestCollectStepVerifyErrors:
    """写后即查（Fix-9, live I3-r11）: step verify 必须在执行轮内跑进修复梯,
    而不是等 exec→QA→bounce→exec 的市场往返(~30min/圈盲猜)。"""

    @staticmethod
    def _collect(context: Any, workspace: str) -> list[str]:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _collect_step_verify_errors,
        )

        return _collect_step_verify_errors(SimpleNamespace(workspace=workspace), context)

    def test_non_step_context_is_noop(self, tmp_path: Any) -> None:
        assert self._collect({}, str(tmp_path)) == []
        assert self._collect(None, str(tmp_path)) == []
        assert self._collect({"construction_step": {"target_file": "a.md"}}, str(tmp_path)) == []

    def test_passing_verify_returns_no_errors(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="game-canvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "test -f ./index.html && grep -q 'id=\"game-canvas\"' ./index.html"}}
        assert self._collect(context, str(tmp_path)) == []

    def test_failing_verify_yields_repairable_error(self, tmp_path: Any) -> None:
        (tmp_path / "index.html").write_text('<canvas id="gameCanvas"></canvas>', encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'id=\"game-canvas\"' ./index.html"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "game-canvas" in errors[0]

    def test_step_verify_preserves_tap_failure_block_before_summary(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        tap_output = """TAP version 13
# Subtest: Fairy rejects bad skill levels and empty identifiers
ok 1 - Fairy rejects bad skill levels and empty identifiers
# Subtest: Fairy mood starts cheerful and degrades with poor performance
not ok 2 - Fairy mood starts cheerful and degrades with poor performance
  ---
  failureType: 'testCodeFailure'
  error: |-
    Expected values to be strictly equal:

    'tired' !== 'neutral'
  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  stack: |-
    TestContext.<anonymous> (/workspace/src/models/Fairy.test.ts:57:10)
  ...
# Subtest: Fairy successRate handles zero shifts without dividing by zero
ok 3 - Fairy successRate handles zero shifts without dividing by zero
1..19
# tests 19
# pass 18
# fail 1
"""

        def _run(command: str, **kwargs: Any) -> Any:
            assert kwargs.get("encoding") == "utf-8"
            assert kwargs.get("errors") == "replace"
            return SimpleNamespace(returncode=1, stdout=tap_output, stderr="")

        monkeypatch.setattr(quality_gate.subprocess, "run", _run)
        monkeypatch.setattr(quality_gate, "_first_failing_verify_clause", lambda _verify, *, cwd: "")

        context = {"construction_step": {"verify": "grep -q ready ./index.html"}}
        errors = self._collect(context, str(tmp_path))

        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "Fairy mood starts cheerful and degrades with poor performance" in errors[0]
        assert "Expected values to be strictly equal" in errors[0]
        assert "'tired' !== 'neutral'" in errors[0]
        assert "# pass 18" not in errors[0].split("failure excerpt:", 1)[1]

    def test_unsafe_verify_rejected_before_shell_or_clause_diagnosis(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        calls: list[str] = []

        def _run(*_args: Any, **_kwargs: Any) -> Any:
            calls.append("subprocess")
            raise AssertionError("unsafe step verify must not reach subprocess.run")

        def _clause(_verify: str, *, cwd: str) -> str:
            calls.append(f"clause:{cwd}")
            raise AssertionError("unsafe step verify must not reach clause diagnosis")

        monkeypatch.setattr(quality_gate.subprocess, "run", _run)
        monkeypatch.setattr(quality_gate, "_first_failing_verify_clause", _clause)

        errors = self._collect({"construction_step": {"verify": "rm -rf ."}}, str(tmp_path))

        assert len(errors) == 1
        assert "step verify command rejected by safety policy" in errors[0]
        assert "blocked_command:rm" in errors[0]
        assert "'rm -rf .'" in errors[0]
        assert calls == []

    def test_unsafe_verify_rejected_before_target_mismatch(self, tmp_path: Any) -> None:
        context = {
            "construction_step": {
                "target_file": "src/rules/dancerule.ts",
                "verify": "rm -rf . && test -f ./src/rules/dance-rule.ts",
            }
        }

        errors = self._collect(context, str(tmp_path))

        assert len(errors) == 1
        assert "step verify command rejected by safety policy" in errors[0]
        assert "step verify target mismatch" not in errors[0]

    def test_legacy_safe_wc_verify_reaches_failure_diagnosis(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        seen: dict[str, str] = {}

        def _clause(command: str, *, cwd: str) -> str:
            seen["command"] = command
            seen["cwd"] = cwd
            return 'failing clause [2/2]: [ "$(wc -l < ./style.css)" -le 120 ]'

        monkeypatch.setattr(quality_gate, "_first_failing_verify_clause", _clause)

        errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))

        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "step verify command rejected by safety policy" not in errors[0]
        assert "failing clause [2/2]" in errors[0]
        assert seen == {"command": verify, "cwd": str(tmp_path)}

    def test_list_verify_joined(self, tmp_path: Any) -> None:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        context = {"construction_step": {"verify": ["test -f ./a.md", "grep -q x ./a.md"]}}
        assert self._collect(context, str(tmp_path)) == []

    def test_acceptance_go_verify_is_executed_from_task_payload(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        seen: dict[str, Any] = {}

        def _run(command: str, **kwargs: Any) -> Any:
            seen["command"] = command
            seen["cwd"] = kwargs.get("cwd")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(quality_gate.subprocess, "run", _run)

        context = {
            "metadata": {
                "task_payload": {
                    "target_files": ["go.mod", "main.go", "main_test.go"],
                    "acceptance_criteria": [
                        "`go test ./...` returns success",
                        "`python -m unittest discover -s tests -p 'test_*.py' -v` returns success",
                    ],
                }
            }
        }

        assert self._collect(context, str(tmp_path)) == []
        assert seen == {"command": "go test ./...", "cwd": str(tmp_path)}

    def test_verification_commands_go_verify_is_executed(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        seen: dict[str, Any] = {}

        def _run(command: str, **kwargs: Any) -> Any:
            seen["command"] = command
            seen["cwd"] = kwargs.get("cwd")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(quality_gate.subprocess, "run", _run)

        context = {
            "target_files": ["go.mod", "main.go"],
            "verification_commands": ["go test ./...", "go run ."],
        }

        assert self._collect(context, str(tmp_path)) == []
        assert seen == {"command": "go test ./...", "cwd": str(tmp_path)}

    def test_acceptance_go_verify_failure_enters_repair_errors(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import quality_gate

        def _run(command: str, **kwargs: Any) -> Any:
            return SimpleNamespace(
                returncode=1,
                stdout="FAIL\tmodule [build failed]\n",
                stderr="./main_test.go:3:6: multiple-value call in single-value context\n",
            )

        monkeypatch.setattr(quality_gate.subprocess, "run", _run)
        monkeypatch.setattr(quality_gate, "_first_failing_verify_clause", lambda _verify, *, cwd: "")

        context = {
            "metadata": {
                "task_payload": {
                    "target_files": ["go.mod", "main.go", "main_test.go"],
                    "steps": ["run `go test ./...` before completion"],
                    "acceptance_criteria": ["`go test ./...` returns success"],
                }
            }
        }

        errors = self._collect(context, str(tmp_path))

        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "go test ./..." in errors[0]
        assert "multiple-value call" in errors[0]

    def test_near_miss_verify_target_path_is_repairable_error(self, tmp_path: Any) -> None:
        target = tmp_path / "src" / "rules" / "dancerule.ts"
        target.parent.mkdir(parents=True)
        target.write_text("export interface DanceRule {}\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/rules/dancerule.ts",
                "verify": ("test -f ./src/rules/dance-rule.ts && grep -q 'DanceRule' ./src/rules/dance-rule.ts"),
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify target mismatch" in errors[0]
        assert "src/rules/dancerule.ts" in errors[0]
        assert "src/rules/dance-rule.ts" in errors[0]

    def test_verify_may_reference_test_file_for_source_target(self, tmp_path: Any) -> None:
        source = tmp_path / "src" / "main.ts"
        test_file = tmp_path / "tests" / "main.test.ts"
        source.parent.mkdir(parents=True)
        test_file.parent.mkdir(parents=True)
        source.write_text("export const answer = 42;\n", encoding="utf-8")
        test_file.write_text("import '../src/main';\n", encoding="utf-8")
        context = {
            "construction_step": {
                "target_file": "src/main.ts",
                "verify": "test -f ./tests/main.test.ts",
            }
        }
        assert self._collect(context, str(tmp_path)) == []

    def test_failure_names_first_failing_clause(self, tmp_path: Any) -> None:
        """Fix-10 (live I3-r12): S2 passed 7/8 clauses but teaching carried only
        the whole command + exit 1 — the model could not tell WHICH check failed."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        context = {
            "construction_step": {
                "verify": (
                    "test -f ./style.css && grep -q '#game' ./style.css && [ \"$(wc -l < ./style.css)\" -le 120 ]"
                )
            }
        }
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause [3/3]:" in errors[0]
        assert "wc -l" in errors[0].split("failing clause", 1)[1]

    def test_single_clause_failure_has_no_clause_suffix(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./missing.md"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_quoted_and_inside_pattern_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Splitting on ' && ' cuts through the quoted pattern; the sh -n guard
        must abandon diagnosis instead of naming a bogus clause."""
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        context = {"construction_step": {"verify": "grep -q 'a && b' ./a.txt && test -f ./a.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "step verify failed" in errors[0]
        assert "failing clause" not in errors[0]

    def test_state_carrying_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        """Adversarial review (live repro): a cd/VAR= clause passes sh -n but its
        successors re-run in a fresh shell against the wrong cwd/env — naming
        a wrong clause actively misleads the next attempt."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "app.js").write_text("bar\n", encoding="utf-8")
        for verify in (
            "cd src && test -f app.js && grep -q foo app.js",
            'X=1 && [ "$X" = 1 ] && test -f missing.txt',
            "export V=2 && test -f missing.txt",
        ):
            errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
            assert len(errors) == 1, verify
            assert "failing clause" not in errors[0], verify

    def test_top_level_or_chain_aborts_clause_diagnosis(self, tmp_path: Any) -> None:
        context = {"construction_step": {"verify": "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt"}}
        errors = self._collect(context, str(tmp_path))
        assert len(errors) == 1
        assert "failing clause" not in errors[0]

    def test_clause_detail_precedes_full_command_in_message(self, tmp_path: Any) -> None:
        """Teaching channels truncate (step card 240 chars) — the actionable
        clause must come before the potentially long full command."""
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        errors = self._collect({"construction_step": {"verify": verify}}, str(tmp_path))
        assert len(errors) == 1
        assert errors[0].index("failing clause") < errors[0].index("full:")


class TestSingleFileStepTarget:
    """对抗复核 C-fix: 钉靶步轮的质量门只裁决该步拥有的文件 — package.json 等
    其他文件的旧垃圾会要求被钉死的写工具做不到的修复, 反弹环永不收敛。"""

    @staticmethod
    def _target(source: Any) -> str:
        from polaris.cells.roles.adapters.internal.director.execute_method import (
            _single_file_step_target,
        )

        return _single_file_step_target(source)

    def test_clean_step_target_is_extracted(self) -> None:
        assert self._target({"construction_step": {"target_file": "./style.css"}}) == "style.css"

    def test_malformed_targets_are_refused(self) -> None:
        for target in ("src/*.js", "a.js, b.js", "/etc/passwd", "../x.js"):
            assert self._target({"construction_step": {"target_file": target}}) == "", target

    def test_non_step_sources_are_refused(self) -> None:
        assert self._target(None) == ""
        assert self._target({}) == ""
        assert self._target({"construction_step": {}}) == ""

    def test_quality_scan_is_scoped_to_step_target(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        context = {"construction_step": {"target_file": "style.css"}}
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "PM-1-S2"},
            all_affected_files=["style.css", "main.js", "package.json"],
            workspace_name="ws",
            context=context,
        )
        assert seen["paths"] == ["style.css"]

    def test_non_step_turn_keeps_full_scan_scope(self, tmp_path: Any, monkeypatch: Any) -> None:
        from polaris.cells.roles.adapters.internal.director import execute_method

        seen: dict[str, Any] = {}

        def _capture(workspace: str, relative_paths: list[str] | None = None) -> list[str]:
            seen["paths"] = list(relative_paths or [])
            return []

        monkeypatch.setattr(execute_method, "scan_workspace_artifact_quality", _capture)
        adapter = SimpleNamespace(workspace=str(tmp_path))
        execute_method._collect_materialization_quality_errors(
            adapter,
            task={"task_id": "T-1"},
            all_affected_files=["a.js", "b.js"],
            workspace_name="ws",
            context={"run_id": "r"},
        )
        assert set(seen["paths"]) >= {"a.js", "b.js"}


@pytest.mark.asyncio
async def test_phase_first_llm_call_retries_transient_provider_error(tmp_path: Any) -> None:
    adapter = _make_adapter(tmp_path)
    calls = 0

    async def _flaky_dialogue(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(
                "HTTPSConnectionPool(host='api.example.test', port=443): Max retries exceeded "
                "with url: /anthropic/v1/messages (Caused by SSLError(SSLEOFError()))"
            )
        return {
            "content": "",
            "success": True,
            "tool_results": [
                {
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/Main.java", "content": "class Main {}"},
                }
            ],
        }

    adapter._invoke_role_dialogue_with_timeout = _flaky_dialogue  # type: ignore[method-assign]

    state, summary = await execute_method_module._phase_first_llm_call(
        adapter,
        baseline_files={},
        context={},
        decision_signals=[],
        llm_call_timeout=5.0,
        message="create files",
        target_task_id="task-1",
        task={"target_files": ["src/Main.java"]},
        workspace_name=tmp_path.name,
        state=execute_method_module.MaterializationState(
            current_files={},
            new_files=[],
            modified_files=[],
            all_affected_files=[],
            tool_results=[],
        ),
    )

    assert calls == 2
    assert summary is not None
    assert summary["success"] is True
    assert state.tool_results[0]["tool_name"] == "write_file"


def _tool_properties(definition: dict[str, Any]) -> dict[str, Any]:
    function_payload = definition.get("function")
    assert isinstance(function_payload, dict)
    parameters = function_payload.get("parameters")
    assert isinstance(parameters, dict)
    properties = parameters.get("properties")
    assert isinstance(properties, dict)
    return properties


def test_no_write_retry_forced_tool_schemas_include_alias_expanded_arguments() -> None:
    definitions = _no_write_materialization_retry_tool_definitions(
        ["src/main.ts", "tests/behavior.test.ts"],
        strict_write_only=False,
    )
    by_name = {item["function"]["name"]: item for item in definitions}

    write_props = _tool_properties(by_name["write_file"])
    assert {"file", "path", "targetPath", "body", "newText"} <= set(write_props)
    assert write_props["file"]["enum"] == ["src/main.ts", "tests/behavior.test.ts"]
    assert write_props["path"]["enum"] == ["src/main.ts", "tests/behavior.test.ts"]
    assert write_props["targetPath"]["enum"] == ["src/main.ts", "tests/behavior.test.ts"]

    edit_props = _tool_properties(by_name["edit_file"])
    assert {"file", "path", "target_path", "oldText", "newText", "search", "replace"} <= set(edit_props)
    assert edit_props["target_path"]["enum"] == ["src/main.ts", "tests/behavior.test.ts"]


def test_strict_write_only_forced_schema_preserves_write_aliases() -> None:
    definitions = _no_write_materialization_retry_tool_definitions(
        ["src/main.ts"],
        strict_write_only=True,
    )

    assert len(definitions) == 1
    props = _tool_properties(definitions[0])
    assert {"file", "path", "targetFile", "body", "newCode"} <= set(props)
    assert props["file"]["enum"] == ["src/main.ts"]
    assert props["path"]["enum"] == ["src/main.ts"]
    assert props["targetFile"]["enum"] == ["src/main.ts"]


def test_quality_repair_write_and_edit_schemas_are_alias_expanded_but_edit_is_search_replace_only() -> None:
    write_props = _tool_properties(_quality_repair_write_file_tool_definition())
    assert {"file", "path", "targetPath", "body", "newText"} <= set(write_props)

    edit_props = _tool_properties(_quality_repair_edit_file_tool_definition())
    assert {"file", "path", "targetPath", "oldText", "newText", "search", "replace"} <= set(edit_props)
    assert "start_line" not in edit_props
    assert "end_line" not in edit_props
    assert "content" not in edit_props


def test_forced_schema_file_enum_pins_all_common_path_aliases() -> None:
    definition = _quality_repair_write_file_tool_definition()
    pinned = _pin_file_schema_to_declared_targets(definition, ["src/app.ts"])
    props = _tool_properties(pinned)

    for key in ("file", "path", "filepath", "file_path", "filename", "target_file", "targetPath"):
        assert props[key]["enum"] == ["src/app.ts"]
