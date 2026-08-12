"""TypeScript/JavaScript/Node run-handler wrappers for runtime_dispatch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ..contracts import (
    RepairAdvisorNote,
    RepairDiagnostic,
)
from ..executor import DeleteFileFn, EditFileFn, WriteFileFn
from ..javascript_runtime import (
    run_javascript_dom_global_runtime_guard_repair,
    run_javascript_esm_commonjs_entrypoint_repair,
    run_javascript_missing_export_repair,
    run_javascript_missing_method_runtime_repair,
    run_javascript_test_missing_target_repair,
    run_node_test_script_contract_repair,
    run_npm_script_contract_repair,
    run_typescript_local_js_import_repair,
)
from ..typescript_runtime import (
    plan_typescript_runtime_repair_for_source_tool,
    run_typescript_canvas_scale_return_type_repair,
    run_typescript_duplicate_object_property_repair,
    run_typescript_enum_member_separator_repair,
    run_typescript_missing_closing_brace_repair,
    run_typescript_nullable_canvas_context_repair,
    run_typescript_number_to_string_argument_repair,
    run_typescript_object_literal_comma_repair,
    run_typescript_readonly_assignment_repair,
    run_typescript_runtime_repair_for_source_tool,
)
from ._adapters import (
    _runtime_planning_from_typescript_runtime,
    _runtime_run_from_javascript,
    _runtime_run_from_typescript,
    _runtime_run_from_typescript_runtime,
)
from ._types import (
    RuntimePlannerFn,
    RuntimeRepairPlanning,
    RuntimeRepairRun,
    RuntimeRunnerFn,
    RuntimeTypedPlannerFn,
    RuntimeTypedRunnerFn,
)


def _run_typescript_object_literal_comma(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_object_literal_comma_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_nullable_canvas_context(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_nullable_canvas_context_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_duplicate_object_property(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_duplicate_object_property_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_enum_member_separator(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_enum_member_separator_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_missing_closing_brace(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_missing_closing_brace_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_number_to_string_argument(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_number_to_string_argument_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_readonly_assignment(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    del deleter
    run = run_typescript_readonly_assignment_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _run_typescript_canvas_scale_return_type(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_typescript_canvas_scale_return_type_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_typescript(run)


def _plan_typescript_runtime_source_tool(source_tool: str) -> RuntimePlannerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def planner(
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairPlanning:
        planning = plan_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_planning_from_typescript_runtime(planning)

    return planner


def _plan_typescript_runtime_source_tool_typed(source_tool: str) -> RuntimeTypedPlannerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def planner(
        base_files: Mapping[str, str],
        repair_diagnostics: Sequence[RepairDiagnostic],
        artifact_quality_errors: Sequence[str],
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairPlanning:
        planning = plan_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            repair_diagnostics=repair_diagnostics,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_planning_from_typescript_runtime(planning)

    return planner


def _run_typescript_runtime_source_tool(source_tool: str) -> RuntimeRunnerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def runner(
        workspace: str | Path,
        base_files: Mapping[str, str],
        artifact_quality_errors: Sequence[str],
        writer: WriteFileFn,
        editor: EditFileFn | None,
        deleter: DeleteFileFn | None,
        allowed_paths: Sequence[str] | None,
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairRun:
        del deleter
        run = run_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            workspace=workspace,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_run_from_typescript_runtime(run)

    return runner


def _run_typescript_runtime_source_tool_typed(source_tool: str) -> RuntimeTypedRunnerFn:
    normalized_source_tool = str(source_tool or "").strip()

    def runner(
        workspace: str | Path,
        base_files: Mapping[str, str],
        repair_diagnostics: Sequence[RepairDiagnostic],
        artifact_quality_errors: Sequence[str],
        writer: WriteFileFn,
        editor: EditFileFn | None,
        deleter: DeleteFileFn | None,
        allowed_paths: Sequence[str] | None,
        advisor_notes: Sequence[RepairAdvisorNote] | None,
        mode: str,
    ) -> RuntimeRepairRun:
        del deleter
        run = run_typescript_runtime_repair_for_source_tool(
            source_tool=normalized_source_tool,
            workspace=workspace,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            repair_diagnostics=repair_diagnostics,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            advisor_notes=advisor_notes,
            mode=mode,
        )
        return _runtime_run_from_typescript_runtime(run)

    return runner


def _run_npm_script_contract(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_npm_script_contract_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_node_test_script_contract(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_node_test_script_contract_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_typescript_local_js_import(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    del deleter
    run = run_typescript_local_js_import_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_test_missing_target(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_test_missing_target_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_missing_export(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_missing_export_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_esm_commonjs_entrypoint(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_esm_commonjs_entrypoint_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_dom_global_runtime_guard(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_dom_global_runtime_guard_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)


def _run_javascript_missing_method_runtime(
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
) -> RuntimeRepairRun:
    run = run_javascript_missing_method_runtime_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return _runtime_run_from_javascript(run)
