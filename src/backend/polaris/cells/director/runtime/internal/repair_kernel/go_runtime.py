"""Runtime-owned Go repair execution flows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .composer import PatchComposer
from .contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .go_syntax import (
    GO_BARE_IMPORT_STRING_SOURCE_TOOL,
    GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
    GO_DEDUP_SOURCE_TOOL,
    GO_ERROR_STRING_HELPER_SOURCE_TOOL,
    GO_MISSING_STDLIB_IMPORT_SOURCE_TOOL,
    GO_MODULE_IMPORT_SOURCE_TOOL,
    GO_NESTED_IMPORT_SOURCE_TOOL,
    GO_SUBPATH_IMPORT_SOURCE_TOOL,
    GO_UNDEFINED_SELECTOR_SOURCE_TOOL,
    GO_UNUSED_IMPORT_SOURCE_TOOL,
    build_go_bare_import_string_plan,
    build_go_bare_local_import_plan,
    build_go_dedup_plan,
    build_go_error_string_helper_plan,
    build_go_missing_stdlib_import_plan,
    build_go_module_import_plan,
    build_go_nested_import_plan,
    build_go_subpath_import_plan,
    build_go_undefined_selector_plan,
    build_go_unused_import_plan,
)
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate


@dataclass(frozen=True)
class GoBareImportStringPlanning:
    """Internal planning result for Go bare import string repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    source_tool_hint: str = GO_BARE_IMPORT_STRING_SOURCE_TOOL

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else self.source_tool_hint


@dataclass(frozen=True)
class GoBareImportStringRun:
    """Internal execution result for Go bare import string repairs."""

    planning: GoBareImportStringPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_go_bare_import_string_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan Go bare import string repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_go_bare_import_string_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return GoBareImportStringPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return GoBareImportStringPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def plan_go_nested_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan nested Go import keyword repairs inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_NESTED_IMPORT_SOURCE_TOOL,
        planner=build_go_nested_import_plan,
    )


def plan_go_module_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan Go module import prefix repairs inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_MODULE_IMPORT_SOURCE_TOOL,
        planner=build_go_module_import_plan,
    )


def plan_go_dedup_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan conservative generated Go intra-file dedup repairs."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_DEDUP_SOURCE_TOOL,
        planner=build_go_dedup_plan,
    )


def plan_go_bare_local_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan Go bare local import prefix repairs inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_BARE_LOCAL_IMPORT_SOURCE_TOOL,
        planner=build_go_bare_local_import_plan,
    )


def plan_go_subpath_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan hallucinated Go import subpath repairs inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_SUBPATH_IMPORT_SOURCE_TOOL,
        planner=build_go_subpath_import_plan,
    )


def plan_go_unused_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan Go unused import removal inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_UNUSED_IMPORT_SOURCE_TOOL,
        planner=build_go_unused_import_plan,
    )


def plan_go_error_string_helper_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan missing Go error-string helper declarations inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_ERROR_STRING_HELPER_SOURCE_TOOL,
        planner=build_go_error_string_helper_plan,
    )


def plan_go_missing_stdlib_import_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan missing Go stdlib import repairs inside the runtime kernel."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_MISSING_STDLIB_IMPORT_SOURCE_TOOL,
        planner=build_go_missing_stdlib_import_plan,
    )


def plan_go_undefined_selector_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringPlanning:
    """Plan undefined Go selector remaps onto existing workspace bindings."""

    return _plan_go_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
        source_tool=GO_UNDEFINED_SELECTOR_SOURCE_TOOL,
        planner=build_go_undefined_selector_plan,
    )


def run_go_bare_import_string_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run Go bare import string repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_go_bare_import_string_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Go bare import string repair plan.",
        )
    if planning.composition is None:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Go bare import string repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            plan_decision=plan_decision,
            composition_decision=composition_decision,
            error_code="repair_policy_denied",
            error_message="Director Runtime repair policy denied the plan or composition.",
        )

    execution_result = TransactionalRepairExecutor().execute(
        workspace=Path(str(workspace)).resolve(),
        plan=planning.plan,
        composition=planning.composition,
        writer=writer,
        editor=editor,
    )
    return GoBareImportStringRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_go_nested_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run nested Go import keyword repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_nested_import_repair,
        missing_plan_message="No matching Go nested import keyword repair plan.",
        missing_composition_message="Go nested import keyword repair composition was not produced.",
    )


def run_go_bare_local_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run Go bare local import prefix repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_bare_local_import_repair,
        missing_plan_message="No matching Go bare local import repair plan.",
        missing_composition_message="Go bare local import repair composition was not produced.",
    )


def run_go_module_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run Go module import prefix repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_module_import_repair,
        missing_plan_message="No matching Go module import repair plan.",
        missing_composition_message="Go module import repair composition was not produced.",
    )


def run_go_dedup_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run conservative generated Go intra-file dedup through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_dedup_repair,
        missing_plan_message="No matching safe Go dedup repair plan.",
        missing_composition_message="Go dedup repair composition was not produced.",
    )


def run_go_subpath_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run hallucinated Go import subpath repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_subpath_import_repair,
        missing_plan_message="No matching Go import subpath repair plan.",
        missing_composition_message="Go import subpath repair composition was not produced.",
    )


def run_go_unused_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run Go unused import repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_unused_import_repair,
        missing_plan_message="No matching Go unused import repair plan.",
        missing_composition_message="Go unused import repair composition was not produced.",
    )


def run_go_error_string_helper_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run missing Go error-string helper repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_error_string_helper_repair,
        missing_plan_message="No matching Go error-string helper repair plan.",
        missing_composition_message="Go error-string helper repair composition was not produced.",
    )


def run_go_missing_stdlib_import_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run missing Go stdlib import repair through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_missing_stdlib_import_repair,
        missing_plan_message="No matching Go missing stdlib import repair plan.",
        missing_composition_message="Go missing stdlib import repair composition was not produced.",
    )


def run_go_undefined_selector_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GoBareImportStringRun:
    """Run undefined Go selector remaps through Plan→Compose→Policy→Execute."""

    return _run_go_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_go_undefined_selector_repair,
        missing_plan_message="No matching Go undefined selector repair plan.",
        missing_composition_message="Go undefined selector repair composition was not produced.",
    )


def _plan_go_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    source_tool: str,
    planner: Callable[..., RepairPlan | None],
) -> GoBareImportStringPlanning:
    normalized_base = _normalize_base_files(base_files)
    diagnostics = _diagnostics_for_go_repair(
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
    )
    notes = tuple(advisor_notes or ())
    plan = planner(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return GoBareImportStringPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
            source_tool_hint=source_tool,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return GoBareImportStringPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
        source_tool_hint=source_tool,
    )


def _run_go_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    planner: Callable[..., GoBareImportStringPlanning],
    missing_plan_message: str,
    missing_composition_message: str,
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
) -> GoBareImportStringRun:
    normalized_base = _normalize_base_files(base_files)
    if repair_diagnostics:
        planning = planner(
            base_files=normalized_base,
            artifact_quality_errors=artifact_quality_errors,
            repair_diagnostics=repair_diagnostics,
            advisor_notes=advisor_notes,
            mode=mode,
        )
    else:
        planning = planner(
            base_files=normalized_base,
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            mode=mode,
        )
    if planning.plan is None:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message=missing_plan_message,
        )
    if planning.composition is None:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message=missing_composition_message,
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            _normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return GoBareImportStringRun(
            planning=planning,
            ok=False,
            plan_decision=plan_decision,
            composition_decision=composition_decision,
            error_code="repair_policy_denied",
            error_message="Director Runtime repair policy denied the plan or composition.",
        )

    execution_result = TransactionalRepairExecutor().execute(
        workspace=Path(str(workspace)).resolve(),
        plan=planning.plan,
        composition=planning.composition,
        writer=writer,
        editor=editor,
    )
    return GoBareImportStringRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        _normalize_repair_path(str(path or "")): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(str(path or ""))
    }


def _diagnostics_for_go_repair(
    *,
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None,
) -> tuple[RepairDiagnostic, ...]:
    if repair_diagnostics:
        return tuple(repair_diagnostics)
    return tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


__all__ = [
    "GoBareImportStringPlanning",
    "GoBareImportStringRun",
    "plan_go_bare_import_string_repair",
    "plan_go_bare_local_import_repair",
    "plan_go_dedup_repair",
    "plan_go_error_string_helper_repair",
    "plan_go_module_import_repair",
    "plan_go_nested_import_repair",
    "plan_go_subpath_import_repair",
    "plan_go_unused_import_repair",
    "run_go_bare_import_string_repair",
    "run_go_bare_local_import_repair",
    "run_go_dedup_repair",
    "run_go_error_string_helper_repair",
    "run_go_module_import_repair",
    "run_go_nested_import_repair",
    "run_go_subpath_import_repair",
    "run_go_unused_import_repair",
]
