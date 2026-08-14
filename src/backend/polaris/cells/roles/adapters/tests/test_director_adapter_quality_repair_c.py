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






class TestQualityRepairMissingTargetContractC:
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

