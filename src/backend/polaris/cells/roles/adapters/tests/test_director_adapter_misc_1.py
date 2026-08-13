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




def test_quality_repair_context_projects_structured_failure_and_workspace_evidence() -> None:
    failure_evidence = _build_materialization_quality_failure_evidence_context(
        artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/index.ts'"],
        missing_target_files=["src/index.ts"],
        repair_target_files=["src/index.ts"],
        changed_files=["package.json"],
        repair_attempt=1,
    )
    workspace_evidence = _build_materialization_quality_workspace_evidence_context(
        artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/index.ts'"],
        missing_target_files=["src/index.ts"],
        repair_target_files=["src/index.ts"],
        changed_files=["package.json"],
        repair_attempt=1,
    )

    assert looks_like_failed_gate_evidence_context_payload(failure_evidence)
    assert looks_like_workspace_quality_evidence_payload(workspace_evidence)
    assert failure_evidence["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert workspace_evidence["all_checks_passed"] is False


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


def test_prepare_role_dialogue_context_derives_stable_distinct_subinvocation_identity() -> None:
    original = {
        "metadata": {
            "task_runtime_session_id": "task-runtime-session-7",
            "runtime_execution": {
                "session_id": "task-runtime-session-7",
                "lease_id": "lease-7",
            },
        }
    }

    first, _ = _prepare_role_dialogue_context(
        original,
        timeout_seconds=60.0,
        stage_label="first_call",
    )
    replay, _ = _prepare_role_dialogue_context(
        original,
        timeout_seconds=60.0,
        stage_label="first_call",
    )
    repair, _ = _prepare_role_dialogue_context(
        original,
        timeout_seconds=60.0,
        stage_label="quality_repair",
    )

    first_metadata = first["metadata"]
    repair_metadata = repair["metadata"]
    first_runtime_metadata = DirectorAdapter._build_role_runtime_metadata(first, max_retries=0)
    repair_runtime_metadata = DirectorAdapter._build_role_runtime_metadata(repair, max_retries=0)
    assert first_metadata["turn_request_id"] == replay["metadata"]["turn_request_id"]
    assert first_metadata["turn_request_id"] != repair_metadata["turn_request_id"]
    assert first_runtime_metadata["turn_request_id"] == first_metadata["turn_request_id"]
    assert repair_runtime_metadata["turn_request_id"] == repair_metadata["turn_request_id"]
    assert first["turn_request_id"] == first_metadata["turn_request_id"]
    assert repair["turn_request_id"] == repair_metadata["turn_request_id"]
    for key in (
        "execution_attempt_id",
        "execution_id",
        "task_runtime_session_id",
    ):
        assert key not in first_metadata
        assert key not in repair_metadata
    assert "runtime_execution" not in first_metadata
    assert "runtime_execution" not in repair_metadata
    assert first_metadata["director_role_subinvocation"] == {
        "schema_version": "director.role_subinvocation.v1",
        "parent_execution_scope_kind": "task_runtime_session_id",
        "parent_execution_scope_id": "task-runtime-session-7",
        "stage_label": "first_call",
        "turn_request_id": first_metadata["turn_request_id"],
    }
    assert repair_metadata["director_role_subinvocation"]["stage_label"] == "quality_repair"
    assert original["metadata"]["task_runtime_session_id"] == "task-runtime-session-7"
    assert original["metadata"]["runtime_execution"]["session_id"] == "task-runtime-session-7"


def test_prepare_role_dialogue_context_rejects_conflicting_parent_execution_identity() -> None:
    with pytest.raises(RuntimeError, match="director_role_invocation_parent_identity_mismatch"):
        _prepare_role_dialogue_context(
            {
                "metadata": {
                    "execution_attempt_id": "attempt-a",
                    "task_runtime_session_id": "attempt-b",
                }
            },
            timeout_seconds=60.0,
            stage_label="quality_repair",
        )


def test_prepare_role_dialogue_context_reuses_original_parent_when_reprepared() -> None:
    original = {
        "metadata": {
            "execution_attempt_id": "task-attempt-11",
            "runtime_execution": {"lease_id": "lease-11"},
        }
    }

    first, _ = _prepare_role_dialogue_context(
        original,
        timeout_seconds=60.0,
        stage_label="first_call",
    )
    repeated_first, _ = _prepare_role_dialogue_context(
        first,
        timeout_seconds=60.0,
        stage_label="first_call",
    )
    sibling_repair, _ = _prepare_role_dialogue_context(
        first,
        timeout_seconds=60.0,
        stage_label="quality_repair",
    )
    direct_repair, _ = _prepare_role_dialogue_context(
        original,
        timeout_seconds=60.0,
        stage_label="quality_repair",
    )

    assert repeated_first["turn_request_id"] == first["turn_request_id"]
    assert sibling_repair["turn_request_id"] == direct_repair["turn_request_id"]
    assert sibling_repair["turn_request_id"] != first["turn_request_id"]
    assert sibling_repair["director_role_subinvocation"]["parent_execution_scope_id"] == "task-attempt-11"
    assert "runtime_execution" not in sibling_repair["metadata"]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda context: context["director_role_subinvocation"].update(
            {"parent_execution_scope_id": "different-parent"}
        ),
        lambda context: context["metadata"].update({"execution_attempt_id": "different-parent"}),
        lambda context: context["metadata"].update({"runtime_execution": {"session_id": "different-parent"}}),
    ),
)
def test_prepare_role_dialogue_context_rejects_conflicting_prior_subinvocation_evidence(
    mutate: Any,
) -> None:
    prepared, _ = _prepare_role_dialogue_context(
        {
            "metadata": {
                "execution_attempt_id": "task-attempt-12",
                "runtime_execution": {"lease_id": "lease-12"},
            }
        },
        timeout_seconds=60.0,
        stage_label="first_call",
    )
    mutate(prepared)

    with pytest.raises(RuntimeError, match="director_role_invocation_prior_evidence_mismatch"):
        _prepare_role_dialogue_context(
            prepared,
            timeout_seconds=60.0,
            stage_label="quality_repair",
        )


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


def test_prepare_role_dialogue_context_caps_primary_write_code_call_budget() -> None:
    context, timeout = _prepare_role_dialogue_context(
        {
            "target_files": [
                "go.mod",
                "models/capsule.go",
                "models/exhibit.go",
                "models/gallery.go",
                "main.go",
            ],
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

    assert timeout == 42.0
    assert context["llm_max_tokens"] == 7000
    budget = context["director_forced_write_output_budget"]
    assert budget["stage_label"] == "first_call"
    assert budget["max_tokens"] == 7000


def test_prepare_role_dialogue_context_does_not_cap_primary_review_call_budget() -> None:
    context, timeout = _prepare_role_dialogue_context(
        {
            "target_files": ["src/service.go"],
            "task_execution_profile": {
                "schema_version": "task.execution_profile.v1",
                "task_type": "review",
            },
        },
        timeout_seconds=42.0,
        stage_label="first_call",
    )

    assert timeout == 42.0
    assert "llm_max_tokens" not in context
    assert "director_forced_write_output_budget" not in context


def test_prepare_role_dialogue_context_timeout_override_is_not_shadowed_by_ceiling() -> None:
    context, timeout = _prepare_role_dialogue_context(
        {
            "llm_call_timeout_seconds": 420.0,
            "llm_call_timeout_ceiling_seconds": 660.0,
            "request_timeout_ceiling_seconds": 660.0,
        },
        timeout_seconds=660.0,
        stage_label="first_call",
    )

    assert timeout == 420.0
    assert context["llm_call_timeout_ceiling_seconds"] == 420.0
    assert context["request_timeout_ceiling_seconds"] == 420.0
    assert context["director_role_call_timeout_budget"]["timeout_seconds"] == 420.0


def test_prepare_role_dialogue_context_reclamps_at_provider_boundary() -> None:
    with patch(
        "polaris.cells.roles.adapters.internal.director.execution.time.time",
        return_value=100.0,
    ):
        context, timeout = _prepare_role_dialogue_context(
            {"factory_director_execution_deadline_epoch_seconds": 112.5},
            timeout_seconds=50.0,
            stage_label="first_call",
        )

    assert timeout == 12.5
    assert context["llm_call_timeout_ceiling_seconds"] == 12.5
    assert context["request_timeout_ceiling_seconds"] == 12.5
    assert context["timeout_ceiling_seconds"] == 12.5


def test_prepare_role_dialogue_context_rejects_expired_factory_deadline() -> None:
    with (
        patch(
            "polaris.cells.roles.adapters.internal.director.execution.time.time",
            return_value=113.0,
        ),
        pytest.raises(RuntimeError, match="factory_director_execution_deadline_exhausted"),
    ):
        _prepare_role_dialogue_context(
            {
                "metadata": {
                    "factory_director_execution_deadline_epoch_seconds": 112.5,
                }
            },
            timeout_seconds=50.0,
            stage_label="first_call",
        )


def test_prepare_role_dialogue_context_injects_current_task_write_boundary() -> None:
    context, _ = _prepare_role_dialogue_context(
        {
            "target_files": ["package.json", "src/models/Humidity.ts"],
            "project_declared_target_files": [
                "package.json",
                "src/models/Humidity.ts",
                "tests/simulation.test.ts",
            ],
        },
        timeout_seconds=300.0,
        stage_label="first_call",
    )

    boundary = context["current_task_write_boundary"]
    assert boundary["schema_version"] == "director.current_task_write_boundary.v1"
    assert boundary["current_target_files"] == ["package.json", "src/models/Humidity.ts"]
    assert boundary["project_declared_target_files_are_inventory_only"] is True
    assert boundary["project_files_absent_from_current_target_are_downstream_or_read_only"] == [
        "tests/simulation.test.ts"
    ]
    assert boundary["non_test_current_targets"] == ["package.json", "src/models/Humidity.ts"]
    assert boundary["test_current_targets"] == []
    assert "Do not embed tests/spec content into non-test source files" in " ".join(boundary["rules"])


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


def test_materialization_plan_probe_triage_allows_current_task_missing_target_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_runtime_boundary(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del args, kwargs
        return [], {
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "covered_unplannable_source_tools": ["deterministic_typescript_relative_import_case_repair"],
                "covered_unplannable_diagnostic_count": 1,
            }
        }

    monkeypatch.setattr(
        quality_gate_module,
        "run_materialization_quality_public_boundary",
        _fake_runtime_boundary,
    )

    _tool_results, summary = quality_gate_module._run_materialization_quality_public_boundary(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/main.ts"]},
        task_id="TASK-1",
        artifact_quality_errors=[
            "Artifact quality scan failed: declared target file missing 'src/main.ts'",
            "Artifact quality scan failed: unresolved relative import './main' in src/index.ts",
        ],
    )

    assert summary["task_boundary_director_continuation_allowed"] is True
    assert summary["task_boundary_continuation_reason"] == "current_task_missing_targets"
    assert summary["task_boundary_continuation_route"] == "director_retry_with_missing_target_context"
    assert summary["task_boundary_continuation_target_files"] == ["src/main.ts"]
    assert _materialization_plan_probe_requires_task_boundary_triage(summary) is False


def test_materialization_plan_probe_triage_stays_fail_closed_without_current_missing_targets() -> None:
    summary = {
        "plan_probe_preaudit": {
            "status": "coverage_matched_but_unplannable",
            "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
            "covered_unplannable_diagnostic_count": 1,
        }
    }

    assert _materialization_plan_probe_requires_task_boundary_triage(summary) is True


def test_director_actual_interface_injection_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", raising=False)
    assert _director_actual_interface_injection_enabled() is True

    monkeypatch.setenv("KERNELONE_DIRECTOR_INJECT_WORKSPACE_INTERFACE", "0")
    assert _director_actual_interface_injection_enabled() is False


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
    execution_attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(tmp_path),
        task_id=2,
        external_task_id="selected-task-2",
        session_id="lease-selected-task-2",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="director-run-1",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )

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
            {
                "session": {"session_id": "lease-selected-task-2"},
                "execution_attempt": execution_attempt.to_record(),
            },
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
    authority = flow_context["task_runtime_execution_attempt_authority"]
    assert authority.snapshot().identity == execution_attempt
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
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        'console.log("Hello from Polaris TypeScript scaffold.");\n',
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: deterministic scaffold marker 'Polaris TypeScript scaffold' in src/main.ts"
    ]

    results, summary = _run_test_materialization_quality_repair_schedule(
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
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        "package main\n\n// output reflects real state rather than a static placeholder.\nfunc main() {}\n",
        encoding="utf-8",
    )
    errors = [
        "Director output quality gate failed: generic/placeholder content detected: "
        "main.go:(?<![.:'\"-])\\bplaceholder\\b(?!\\s*[=:])(?![-'\"])"
    ]

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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


def test_quality_repair_progress_requires_mutation_and_net_diagnostic_reduction() -> None:
    before = ["src/main.ts(1,1): error TS2307: Cannot find module 'node:path'."]
    after = ["src/main.ts(8,2): error TS2304: Cannot find name 'relative'."]

    changed_signature = execute_method_module._quality_repair_progress_evidence(
        before_files={"src/main.ts": "old"},
        after_files={"src/main.ts": "new"},
        before_errors=before,
        after_errors=after,
        before_missing_count=0,
        after_missing_count=0,
        successful_write_paths=["src/main.ts"],
    )
    assert changed_signature["status"] == "stalled"
    assert changed_signature["workspace_mutation_evidenced"] is True
    assert changed_signature["net_error_reduction"] == 0
    assert changed_signature["introduced_diagnostic_signatures"]

    improved = execute_method_module._quality_repair_progress_evidence(
        before_files={"src/main.ts": "old"},
        after_files={"src/main.ts": "new"},
        before_errors=[*before, *after],
        after_errors=before,
        before_missing_count=0,
        after_missing_count=0,
        successful_write_paths=["src/main.ts"],
    )
    assert improved["status"] == "progress"
    assert improved["effective_progress"] is True
    assert improved["net_error_reduction"] == 1
    assert improved["introduced_diagnostic_signatures"] == []


@pytest.mark.asyncio
async def test_phase_quality_repair_loop_stops_after_two_worsening_director_edits(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _make_adapter(tmp_path)
    initial_error = "src/main.ts(1,1): error TS2307: Cannot find module 'node:path'."
    worsened_errors = [
        "src/main.ts(8,2): error TS2304: Cannot find name 'relative'.",
        "src/main.ts(12,4): error TS1005: ',' expected.",
    ]
    error_rounds = [[initial_error], list(worsened_errors), list(worsened_errors)]
    llm_calls = 0
    workspace_round = 0

    def fake_collect_materialization_quality_errors(*args: Any, **kwargs: Any) -> list[str]:
        return error_rounds.pop(0) if error_rounds else list(worsened_errors)

    async def fake_llm_quality_repair(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nonlocal llm_calls
        llm_calls += 1
        return [
            {
                "tool_name": "write_file",
                "tool": "write_file",
                "success": True,
                "result": {"ok": True, "file": "src/main.ts"},
            }
        ], {"stage": "llm_quality_repair", "success": False}

    def fake_workspace_diff(*args: Any, **kwargs: Any) -> tuple[dict[str, str], list[str], list[str], list[str]]:
        nonlocal workspace_round
        workspace_round += 1
        return {"src/main.ts": f"v{workspace_round}"}, [], ["src/main.ts"], ["src/main.ts"]

    monkeypatch.setattr(
        execute_method_module, "_collect_materialization_quality_errors", fake_collect_materialization_quality_errors
    )
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(execute_method_module, "run_python_static_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "run_python_runtime_smoke", lambda *args, **kwargs: ([], []))
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
        execute_method_module,
        "_run_materialization_quality_public_boundary",
        lambda *args, **kwargs: ([], {"stage": "deterministic_quality_repair", "success": False}),
    )
    monkeypatch.setattr(execute_method_module, "_run_materialization_quality_repair_retry", fake_llm_quality_repair)
    monkeypatch.setattr(execute_method_module, "_collect_workspace_code_diff", fake_workspace_diff)

    quality_repair_attempts: list[dict[str, Any]] = []
    _state, residual_errors, summary, _write_evidence = await execute_method_module._phase_quality_repair_loop(
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
        task={"metadata": {"target_files": ["src/main.ts"]}},
        workspace_name=tmp_path.name,
        write_tool_evidence=False,
        state=execute_method_module.MaterializationState(
            current_files={"src/main.ts": "v0"},
            new_files=[],
            modified_files=[],
            all_affected_files=["src/main.ts"],
            tool_results=[],
        ),
    )

    assert llm_calls == 2
    assert residual_errors == worsened_errors
    assert summary is quality_repair_attempts[-1]
    assert summary["convergence_status"] == "repair_stalled"
    assert summary["error_code"] == "director_quality_repair_stalled"
    assert summary["failure_class"] == "model_ceiling"
    assert summary["retry_scope"] == "same_director_task_only"
    assert summary["pm_ce_restart_allowed"] is False
    assert summary["stagnant_attempts"] == 2


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
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(
        execute_method_module,
        "run_python_static_smoke",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        execute_method_module,
        "run_python_runtime_smoke",
        lambda *args, **kwargs: ([], []),
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
    monkeypatch.setattr(execute_method_module, "_collect_step_verify_errors", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(execute_method_module, "run_python_static_smoke", lambda *args, **kwargs: [])
    monkeypatch.setattr(execute_method_module, "run_python_runtime_smoke", lambda *args, **kwargs: ([], []))
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
    assert adapter.progress == []


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

    results = run_runtime_repair_with_director_tools(
        FakeAdapter(),
        workspace_path=tmp_path,
        task_id="task-unknown-repair",
        source_tool="deterministic_future_language_repair",
        execution_attempt=TaskRuntimeExecutionAttemptIdentityV1(
            workspace=tmp_path.resolve().as_posix(),
            task_id=92,
            external_task_id="task-unknown-repair",
            session_id="session-unknown-repair",
            attempt=1,
            role_id="director",
            worker_id="director-test-worker",
            run_id="run-unknown-repair",
            lease_expires_at="2099-01-01T00:00:00Z",
        ),
        base_files={"src/main.future": "broken\n"},
        artifact_quality_errors=("future-lang compiler: unknown diagnostic",),
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    result = results[0]["result"]
    assert result["error_code"] == "unsupported_repair_source_tool"
    assert result["repair_kernel"]["execution_skipped"] is True
    assert result["repair_kernel"]["execution_skip_reason"] == "unsupported_repair_source_tool"
    assert result["repair_kernel"]["planning"]["planned"] is False


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp",
    )

    relative_path = "src/engine/generator.cpp"
    assert writes == []
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_include_path_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-standard",
    )

    relative_path = "src/models/seed.hpp"
    assert writes == []
    assert "#include <cstdint>" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_standard_include_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-private-members",
    )

    relative_path = "src/models/poem.hpp"
    assert writes == []
    assert "private:\n    std::string title_;" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_missing_private_members_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-placeholder",
    )

    relative_path = "src/engine/generator.hpp"
    assert writes == []
    assert "std::render_return_type" not in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_placeholder_declaration_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge.run_cpp_post_repairs_as_tool_results(
        tmp_path,
        adapter=adapter,
        task_id="task-cpp-struct-getter",
    )

    relative_path = "src/main.cpp"
    assert writes == []
    assert "card.poem" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_cpp_struct_getter_field_access_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)

    adapter = FakeAdapter()
    results = post_execution_repair_bridge._run_java_post_repairs(adapter, tmp_path, task_id="task-java")

    relative_path = "src/main/java/demo/RhythmMonster.java"
    assert writes == []
    assert "public int temperament()" in target.read_text(encoding="utf-8")
    assert len(results) == 1
    result = results[0]["result"]
    assert result["source_tool"] == "deterministic_java_post_repair"
    assert result["file"] == relative_path
    assert result["repair_kernel"]["owner_cell"] == "director.runtime"
    assert result["repair_kernel"]["status"] == "applied"
    assert adapter.progress == []


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
        assert max_rounds == 1
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

    _install_test_deferred_projection(monkeypatch, post_execution_repair_bridge, tmp_path)
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
    receipt_notes = tool_results[0]["result"]["repair_kernel"]["planning"]["advisor_notes"]
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


