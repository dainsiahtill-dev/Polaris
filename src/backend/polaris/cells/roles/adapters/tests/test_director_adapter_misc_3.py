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




def test_typescript_unresolved_unused_import_repair_removes_import(tmp_path: Any) -> None:
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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
        assert repair_kernel["execution_deferred"] is True
        assert repair_kernel["execution_authority"] == "roles.kernel"
        assert repair_kernel["metadata"]["requires_revalidation"] is True
    assert "flightTime, landed:" in repaired


def test_typescript_return_object_comma_repair_fixes_previous_line_missing_comma(tmp_path: Any) -> None:
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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

    results, summary = _run_test_materialization_quality_repair_schedule(
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


def test_task_boundary_quality_scan_covers_all_owned_files_despite_pinned_step(tmp_path: Any) -> None:
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

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/pinned.ts", "src/other.ts"]},
        all_affected_files=["src/pinned.ts"],
        workspace_name=tmp_path.name,
        context={"construction_step": {"target_file": "src/pinned.ts"}},
        task_boundary=True,
    )

    assert any("src/other.ts" in error for error in errors)


def test_project_test_obligation_is_deferred_to_declared_downstream_owner(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    (tmp_path / "src" / "models").mkdir(parents=True)
    (tmp_path / "src" / "models" / "firefly.ts").write_text(
        "export const firefly = 'glow';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"}}\n',
        encoding="utf-8",
    )
    context: dict[str, Any] = {
        "project_declared_target_files": [
            "src/models/firefly.ts",
            "tests/simulation.test.ts",
        ]
    }

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/models/firefly.ts"]},
        all_affected_files=["src/models/firefly.ts"],
        workspace_name=tmp_path.name,
        context=context,
    )

    assert errors == []
    deferred = context["director_task_boundary_deferred_quality_errors"]
    project_record = next(record for record in deferred if record["reason"] == "project_test_targets_not_unlocked")
    assert project_record["target_files"] == ["tests/simulation.test.ts"]


def test_project_test_obligation_remains_blocking_without_declared_owner(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _collect_materialization_quality_errors,
    )

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"}}\n',
        encoding="utf-8",
    )

    errors = _collect_materialization_quality_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/index.ts"]},
        all_affected_files=["src/index.ts"],
        workspace_name=tmp_path.name,
        context={},
    )

    assert any("test runner script but no test/spec files exist" in error for error in errors)


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
) -> None:
    from polaris.cells.roles.adapters.internal.director import quality_gate as quality_gate_module

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^1.6.0"}}) + "\n",
        encoding="utf-8",
    )
    task_id = "task-step-prep"
    context = {
        "construction_step": {"verify": "npm run test"},
        **_test_execution_attempt_context(tmp_path, task_id),
    }
    errors, tool_results = quality_gate_module._collect_step_verify_errors(
        SimpleNamespace(workspace=str(tmp_path)),
        context,
        task_id=task_id,
    )

    assert errors == []
    requests = [item["result"]["deferred_request"] for item in tool_results]
    assert [request.purpose for request in requests] == [
        "00_environment_prep_000",
        "10_step_verify_000",
    ]
    assert requests[0].command.startswith("npm ")
    assert requests[1].command == "npm run test"


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


def test_javascript_runtime_smoke_traceback_maps_compiled_output_to_typescript_source(tmp_path: Any) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _javascript_runtime_smoke_repair_target_files,
        _python_runtime_smoke_repair_target_files,
    )

    src = tmp_path / "src"
    dist = tmp_path / "dist"
    src.mkdir()
    dist.mkdir()
    (src / "verify.ts").write_text("export function verify(): void {}\n", encoding="utf-8")
    (dist / "verify.js").write_text('"use strict";\n', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "rootDir": "src",
                    "outDir": "dist",
                }
            }
        ),
        encoding="utf-8",
    )
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "> npm run build && node dist/verify.js\n"
        "Error: ENOENT: no such file or directory, open 'engine/renderer.ts'\n"
        f"    at checkContentAny ({tmp_path / 'dist' / 'verify.js'}:87:52)\n"
        "Node.js v22.23.2"
    )

    targets = _javascript_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["src/verify.ts", "dist/verify.js"],
        workspace_full=str(tmp_path),
    )
    python_targets = _python_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["src/verify.ts", "dist/verify.js"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["src/verify.ts"]
    assert python_targets == []


def test_javascript_runtime_smoke_traceback_keeps_compiled_output_when_source_mapping_is_ambiguous(
    tmp_path: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _javascript_runtime_smoke_repair_target_files,
    )

    src = tmp_path / "src"
    dist = tmp_path / "dist"
    src.mkdir()
    dist.mkdir()
    (src / "verify.ts").write_text("export const verify = true;\n", encoding="utf-8")
    (src / "verify.tsx").write_text("export const Verify = () => null;\n", encoding="utf-8")
    (dist / "verify.js").write_text('"use strict";\n', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"rootDir": "src", "outDir": "dist"}}),
        encoding="utf-8",
    )
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm test): "
        "> npm run build && node dist/verify.js\n"
        "TypeError: verification failed\n"
        f"    at verify ({tmp_path / 'dist' / 'verify.js'}:1:1)\n"
        "Node.js v22.23.2"
    )

    assert _javascript_runtime_smoke_repair_target_files(
        artifact_quality_errors=[error],
        changed_files=["dist/verify.js"],
        workspace_full=str(tmp_path),
    ) == ["dist/verify.js"]


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

    results, summary = _run_test_materialization_quality_repair_schedule(
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
    results, summary = _run_test_materialization_quality_repair_schedule(
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
        scope_filter = diff.get("task_boundary_scope_filter") or diff
        assert isinstance(scope_filter, dict)
        assert scope_filter.get("deferred") is True

        scope_authority = scope_filter.get("scope_authority")
        assert isinstance(scope_authority, dict)
        assert scope_authority["authority"] == "kernelone.quality.scope_authority"
        assert scope_authority["deferred"] is True
        assert scope_authority["out_of_scope_repair_target_files"] == ["guess_number.py"]
        assert "src/python/guess_number.py" in scope_authority["task_declared_write_targets"]
        assert isinstance(scope_authority["ownership_handoff_requests"], list)

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


def test_materialization_typescript_runtime_repair_loads_html_entrypoint_without_diagnostic_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executable HTML repair must receive the physical entrypoint even when coverage path is absent."""

    from polaris.cells.roles.adapters.internal.director import materialization_quality_callback_ports as ports

    (tmp_path / "src").mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="./src/web.ts"></script>\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "web.ts").write_text("export {};\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def _capture_runtime_repair(_adapter: Any, **kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ports, "run_runtime_repair_with_director_tools", _capture_runtime_repair)

    ports._run_materialization_typescript_runtime_repair(
        SimpleNamespace(workspace=str(tmp_path)),
        task={"target_files": ["src/web.ts"]},
        task_id="TASK-2",
        artifact_quality_errors=[
            "Artifact quality scan failed: HTML module script references TypeScript source "
            "'./src/web.ts' in index.html; static entrypoints must load JavaScript"
        ],
        source_tool="deterministic_html_typescript_module_script_repair",
    )

    assert "index.html" in captured["base_files"]
    assert captured["base_files"]["index.html"] == ('<script type="module" src="./src/web.ts"></script>\n')


def test_runtime_dependency_repair_authorizes_missing_tsconfig_creation(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director import materialization_quality_callback_ports as ports

    (tmp_path / "package.json").write_text(
        '{"name":"demo","dependencies":{"zod":"^3.23.8"}}\n',
        encoding="utf-8",
    )
    errors = [
        "Artifact quality scan failed: TypeScript node builtin import 'node:test' "
        "requires '@types/node' in tests/verify.test.ts"
    ]

    base_files = ports._collect_materialization_runtime_base_files(
        tmp_path,
        artifact_quality_errors=errors,
        source_tool="deterministic_runtime_dependency_repair",
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"),
        collect_unmatched_diagnostic_paths=True,
        task={"target_files": ["src/model.ts"], "scope_paths": ["package.json"]},
    )

    assert base_files["package.json"]
    assert "tsconfig.json" not in base_files
    assert "tsconfig.json" in ports._materialization_runtime_allowed_paths(
        base_files,
        source_tool="deterministic_runtime_dependency_repair",
    )


def test_materialization_html_entrypoint_schedule_plans_physical_edit_without_llm(tmp_path: Path) -> None:
    """The r35 HTML residual must close through runtime repair before any LLM fallback."""

    (tmp_path / "src").mkdir()
    (tmp_path / "index.html").write_text(
        '<script type="module" src="./src/web.ts"></script>\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "web.ts").write_text("export {};\n", encoding="utf-8")
    task_id = "TASK-2"
    result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=SimpleNamespace(workspace=str(tmp_path)),
            task={"target_files": ["src/web.ts"]},
            task_id=task_id,
            artifact_quality_errors=(
                "Artifact quality scan failed: HTML module script references TypeScript source "
                "'./src/web.ts' in index.html; static entrypoints must load JavaScript",
            ),
            execution_attempt=_test_execution_attempt(tmp_path, task_id),
        )
    )

    projected = _project_deferred_repair_results_for_test(
        tmp_path,
        [dict(item) for item in result.tool_results],
    )

    assert any(item.get("success") and item.get("result", {}).get("file") == "index.html" for item in projected)
    assert "./dist/web.js" in (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "./src/web.ts" not in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_html_module_script_quality_error_identifies_existing_entrypoint_fallback_target(tmp_path: Path) -> None:
    """If deterministic commit cannot close, the LLM fallback must still be scoped to index.html."""

    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _explicit_artifact_quality_repair_target_files,
    )

    (tmp_path / "index.html").write_text(
        '<script type="module" src="./src/web.ts"></script>\n',
        encoding="utf-8",
    )
    targets = _explicit_artifact_quality_repair_target_files(
        artifact_quality_errors=[
            "Artifact quality scan failed: HTML module script references TypeScript source "
            "'./src/web.ts' in index.html; static entrypoints must load JavaScript"
        ],
        changed_files=["index.html"],
        workspace_full=str(tmp_path),
    )

    assert targets == ["index.html"]


@pytest.mark.asyncio
async def test_existing_html_quality_fallback_requires_edit_not_read_or_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A covered repair fallback may not become another read-only/command-only round."""

    from polaris.cells.roles.adapters.internal.director import quality_gate

    class _Execution:
        @staticmethod
        def extract_kernel_tool_results(_result: dict[str, Any]) -> list[dict[str, Any]]:
            return []

        @staticmethod
        async def execute_tools(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

    class _Adapter:
        workspace = str(tmp_path)
        _execution = _Execution()
        _update_task_progress = staticmethod(lambda *_args, **_kwargs: None)

        def __init__(self) -> None:
            self.repair_context: dict[str, Any] = {}

        async def _invoke_role_dialogue_with_timeout(
            self,
            _message: str,
            *,
            context: dict[str, Any],
            timeout_seconds: float,
            stage_label: str,
        ) -> dict[str, Any]:
            del timeout_seconds, stage_label
            self.repair_context = context
            return {"content": ""}

    (tmp_path / "index.html").write_text(
        '<script type="module" src="./src/web.ts"></script>\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        quality_gate, "_run_materialization_quality_public_boundary", lambda *_args, **_kwargs: ([], {})
    )
    adapter = _Adapter()

    await quality_gate._run_materialization_quality_repair_retry(
        adapter,
        task={"target_files": ["index.html"]},
        target_task_id="TASK-2",
        run_id="run-r35-regression",
        context={},
        original_message="Repair the browser entrypoint.",
        llm_call_timeout=30.0,
        artifact_quality_errors=[
            "Artifact quality scan failed: HTML module script references TypeScript source "
            "'./src/web.ts' in index.html; static entrypoints must load JavaScript"
        ],
        changed_files=["index.html"],
    )

    repair = adapter.repair_context["director_quality_repair"]
    assert repair["repair_target_files"] == ["index.html"]
    forced_names = [
        item["function"]["name"] for item in adapter.repair_context["_transaction_kernel_forced_tool_definitions"]
    ]
    assert "read_file" not in forced_names
    assert set(forced_names) == {"edit_file", "write_file", "execute_command"}
    assert adapter.repair_context["metadata"]["tool_contract"]["required_tools"] == ["edit_file"]


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
    # R127: path enums are only qualification-safe on write_file. edit_file must
    # stay registry-faithful without scoped path enums.
    assert "enum" not in edit_props["target_path"]
    assert "enum" not in edit_props["file"]


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


def test_quality_repair_write_and_edit_schemas_match_tool_spec_registry() -> None:
    """Quality-repair forced tools must stay registry-faithful.

    R126: mutating edit_file description/params or inventing execute_command
    schemas caused FinalProviderAttemptQualificationError
    tool_registry_function_contract_drift and blocked quality-repair turns.
    """
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    write_def = _quality_repair_write_file_tool_definition()
    edit_def = _quality_repair_edit_file_tool_definition()
    execute_def = _quality_repair_execute_command_tool_definition()
    write_props = _tool_properties(write_def)
    edit_props = _tool_properties(edit_def)
    assert {"file", "path", "targetPath", "body", "newText"} <= set(write_props)
    assert {"file", "path", "targetPath", "oldText", "newText", "search", "replace"} <= set(edit_props)
    # Full registry surface (line-range + search-replace) must remain available.
    assert "start_line" in edit_props
    assert "end_line" in edit_props

    for tool_name, actual in (
        ("write_file", write_def),
        ("edit_file", edit_def),
        ("execute_command", execute_def),
    ):
        expected = ToolSpecRegistry.get_llm_schema(
            tool_name,
            include_arg_aliases=True,
            deterministic=True,
        )
        assert expected is not None
        assert actual == expected


def test_forced_schema_file_enum_pins_all_common_path_aliases() -> None:
    definition = _quality_repair_write_file_tool_definition()
    pinned = _pin_file_schema_to_declared_targets(definition, ["src/app.ts"])
    props = _tool_properties(pinned)

    for key in ("file", "path", "filepath", "file_path", "filename", "target_file", "targetPath"):
        assert props[key]["enum"] == ["src/app.ts"]
