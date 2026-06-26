"""Runtime-owned Python repair execution flows."""

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
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .python_syntax import (
    PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
    PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
    PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
    PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
    PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
    build_python_package_child_reexport_plan,
    build_python_package_shadow_bridge_plan,
    build_python_unittest_missing_target_plan,
    build_python_unittest_runtime_failure_plan,
    build_python_unresolved_import_symbol_plan,
)

PlanBuilderFn = Callable[..., RepairPlan | None]


@dataclass(frozen=True)
class PythonRepairPlanning:
    """Internal planning result for Python repairs."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class PythonRepairRun:
    """Internal execution result for Python repairs."""

    planning: PythonRepairPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_python_unittest_missing_target_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairPlanning:
    """Plan missing Python unittest target repairs inside the runtime kernel."""

    return _plan_python_repair(
        source_tool=PYTHON_UNITTEST_MISSING_TARGET_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_python_unittest_missing_target_plan,
    )


def plan_python_unittest_runtime_failure_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairPlanning:
    """Plan generated unittest runtime-failure repairs inside the runtime kernel."""

    return _plan_python_repair(
        source_tool=PYTHON_UNITTEST_RUNTIME_FAILURE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_python_unittest_runtime_failure_plan,
    )


def plan_python_package_child_reexport_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairPlanning:
    """Plan package child-module re-export repairs inside the runtime kernel."""

    return _plan_python_repair(
        source_tool=PYTHON_PACKAGE_CHILD_REEXPORT_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_python_package_child_reexport_plan,
    )


def plan_python_package_shadow_bridge_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairPlanning:
    """Plan package/module shadow bridge repairs inside the runtime kernel."""

    return _plan_python_repair(
        source_tool=PYTHON_PACKAGE_SHADOW_BRIDGE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_python_package_shadow_bridge_plan,
    )


def plan_python_unresolved_import_symbol_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairPlanning:
    """Plan unresolved import-symbol repairs inside the runtime kernel."""

    return _plan_python_repair(
        source_tool=PYTHON_UNRESOLVED_IMPORT_SYMBOL_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_python_unresolved_import_symbol_plan,
    )


def _plan_python_repair(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    builder: PlanBuilderFn,
) -> PythonRepairPlanning:
    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = builder(base_files=normalized_base, diagnostics=diagnostics, mode=mode)
    if plan is None:
        return PythonRepairPlanning(
            source_tool=source_tool,
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return PythonRepairPlanning(
        source_tool=plan.source_tool,
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def run_python_unittest_missing_target_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairRun:
    """Run missing Python unittest target repair through Plan->Compose->Policy->Execute."""

    return _run_python_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_python_unittest_missing_target_repair,
        not_planned_message="No matching Python unittest missing target repair plan.",
        composition_missing_message="Python unittest missing target repair composition was not produced.",
    )


def run_python_unittest_runtime_failure_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairRun:
    """Run generated unittest runtime-failure repair through Plan->Compose->Policy->Execute."""

    return _run_python_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_python_unittest_runtime_failure_repair,
        not_planned_message="No matching Python unittest runtime-failure repair plan.",
        composition_missing_message="Python unittest runtime-failure repair composition was not produced.",
    )


def run_python_package_child_reexport_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairRun:
    """Run package child-module re-export repair through Plan->Compose->Policy->Execute."""

    return _run_python_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_python_package_child_reexport_repair,
        not_planned_message="No matching Python package child re-export repair plan.",
        composition_missing_message="Python package child re-export repair composition was not produced.",
    )


def run_python_package_shadow_bridge_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairRun:
    """Run package/module shadow bridge repair through Plan->Compose->Policy->Execute."""

    return _run_python_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_python_package_shadow_bridge_repair,
        not_planned_message="No matching Python package shadow bridge repair plan.",
        composition_missing_message="Python package shadow bridge repair composition was not produced.",
    )


def run_python_unresolved_import_symbol_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PythonRepairRun:
    """Run unresolved import-symbol repair through Plan->Compose->Policy->Execute."""

    return _run_python_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_python_unresolved_import_symbol_repair,
        not_planned_message="No matching Python unresolved import-symbol repair plan.",
        composition_missing_message="Python unresolved import-symbol repair composition was not produced.",
    )


def _run_python_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    planner: Callable[..., PythonRepairPlanning],
    not_planned_message: str,
    composition_missing_message: str,
) -> PythonRepairRun:
    normalized_base = _normalize_base_files(base_files)
    planning = planner(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return PythonRepairRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message=not_planned_message,
        )
    if planning.composition is None:
        return PythonRepairRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message=composition_missing_message,
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return PythonRepairRun(
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
    return PythonRepairRun(
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
        normalized: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized := _normalize_repair_path(str(path or "")))
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


__all__ = [
    "PythonRepairPlanning",
    "PythonRepairRun",
    "plan_python_package_child_reexport_repair",
    "plan_python_package_shadow_bridge_repair",
    "plan_python_unittest_missing_target_repair",
    "plan_python_unittest_runtime_failure_repair",
    "plan_python_unresolved_import_symbol_repair",
    "run_python_package_child_reexport_repair",
    "run_python_package_shadow_bridge_repair",
    "run_python_unittest_missing_target_repair",
    "run_python_unittest_runtime_failure_repair",
    "run_python_unresolved_import_symbol_repair",
]
