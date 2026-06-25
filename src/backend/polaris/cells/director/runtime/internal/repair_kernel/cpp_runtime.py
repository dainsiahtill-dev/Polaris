"""Runtime-owned C++ repair execution flows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from .composer import PatchComposer
from .contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairExecutionResult,
    RepairPlan,
)
from .cpp_syntax import (
    CPP_INCLUDE_PATH_SOURCE_TOOL,
    CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL,
    CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL,
    CPP_STANDARD_INCLUDE_SOURCE_TOOL,
    CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL,
    build_cpp_include_path_plan,
    build_cpp_missing_private_members_plan,
    build_cpp_placeholder_declaration_plan,
    build_cpp_standard_include_plan,
    build_cpp_struct_getter_field_access_plan,
)
from .diagnostics import normalize_artifact_quality_errors
from .executor import EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate


@dataclass(frozen=True)
class CppIncludePathPlanning:
    """Internal planning result for C++ include path repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else CPP_INCLUDE_PATH_SOURCE_TOOL


@dataclass(frozen=True)
class CppIncludePathRun:
    """Internal execution result for C++ include path repairs."""

    planning: CppIncludePathPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CppStandardIncludePlanning:
    """Internal planning result for C++ standard include repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else CPP_STANDARD_INCLUDE_SOURCE_TOOL


@dataclass(frozen=True)
class CppStandardIncludeRun:
    """Internal execution result for C++ standard include repairs."""

    planning: CppStandardIncludePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CppMissingPrivateMembersPlanning:
    """Internal planning result for C++ missing private member repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else CPP_MISSING_PRIVATE_MEMBERS_SOURCE_TOOL


@dataclass(frozen=True)
class CppMissingPrivateMembersRun:
    """Internal execution result for C++ missing private member repairs."""

    planning: CppMissingPrivateMembersPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CppPlaceholderDeclarationPlanning:
    """Internal planning result for C++ placeholder declaration repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else CPP_PLACEHOLDER_DECLARATION_SOURCE_TOOL


@dataclass(frozen=True)
class CppPlaceholderDeclarationRun:
    """Internal execution result for C++ placeholder declaration repairs."""

    planning: CppPlaceholderDeclarationPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class CppStructGetterFieldAccessPlanning:
    """Internal planning result for C++ struct getter field-access repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else CPP_STRUCT_GETTER_FIELD_ACCESS_SOURCE_TOOL


@dataclass(frozen=True)
class CppStructGetterFieldAccessRun:
    """Internal execution result for C++ struct getter field-access repairs."""

    planning: CppStructGetterFieldAccessPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_cpp_include_path_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppIncludePathPlanning:
    """Plan C++ include path repairs inside the runtime kernel."""

    planning = _plan_cpp_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=build_cpp_include_path_plan,
    )
    return CppIncludePathPlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def plan_cpp_placeholder_declaration_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppPlaceholderDeclarationPlanning:
    """Plan C++ placeholder declaration repairs inside the runtime kernel."""

    planning = _plan_cpp_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=build_cpp_placeholder_declaration_plan,
    )
    return CppPlaceholderDeclarationPlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def plan_cpp_missing_private_members_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppMissingPrivateMembersPlanning:
    """Plan C++ missing private member repairs inside the runtime kernel."""

    planning = _plan_cpp_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=build_cpp_missing_private_members_plan,
    )
    return CppMissingPrivateMembersPlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def plan_cpp_standard_include_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppStandardIncludePlanning:
    """Plan C++ standard include repairs inside the runtime kernel."""

    planning = _plan_cpp_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=build_cpp_standard_include_plan,
    )
    return CppStandardIncludePlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def plan_cpp_struct_getter_field_access_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppStructGetterFieldAccessPlanning:
    """Plan C++ struct getter field-access repairs inside the runtime kernel."""

    planning = _plan_cpp_repair(
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=build_cpp_struct_getter_field_access_plan,
    )
    return CppStructGetterFieldAccessPlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
    )


def _plan_cpp_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    planner: Callable[..., RepairPlan | None],
) -> CppIncludePathPlanning:
    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = planner(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return CppIncludePathPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return CppIncludePathPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def run_cpp_include_path_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppIncludePathRun:
    """Run C++ include path repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_cpp_include_path_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return cast(
        CppIncludePathRun,
        _run_cpp_repair(
            workspace=workspace,
            normalized_base=normalized_base,
            planning=planning,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            not_planned_message="No matching C++ include path repair plan.",
            composition_missing_message="C++ include path repair composition was not produced.",
            run_cls=CppIncludePathRun,
        ),
    )


def run_cpp_standard_include_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppStandardIncludeRun:
    """Run C++ standard include repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_cpp_standard_include_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return cast(
        CppStandardIncludeRun,
        _run_cpp_repair(
            workspace=workspace,
            normalized_base=normalized_base,
            planning=planning,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            not_planned_message="No matching C++ standard include repair plan.",
            composition_missing_message="C++ standard include repair composition was not produced.",
            run_cls=CppStandardIncludeRun,
        ),
    )


def run_cpp_placeholder_declaration_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppPlaceholderDeclarationRun:
    """Run C++ placeholder declaration repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_cpp_placeholder_declaration_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return cast(
        CppPlaceholderDeclarationRun,
        _run_cpp_repair(
            workspace=workspace,
            normalized_base=normalized_base,
            planning=planning,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            not_planned_message="No matching C++ placeholder declaration repair plan.",
            composition_missing_message="C++ placeholder declaration repair composition was not produced.",
            run_cls=CppPlaceholderDeclarationRun,
        ),
    )


def run_cpp_missing_private_members_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppMissingPrivateMembersRun:
    """Run C++ missing private member repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_cpp_missing_private_members_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return cast(
        CppMissingPrivateMembersRun,
        _run_cpp_repair(
            workspace=workspace,
            normalized_base=normalized_base,
            planning=planning,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            not_planned_message="No matching C++ missing private member repair plan.",
            composition_missing_message="C++ missing private member repair composition was not produced.",
            run_cls=CppMissingPrivateMembersRun,
        ),
    )


def run_cpp_struct_getter_field_access_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> CppStructGetterFieldAccessRun:
    """Run C++ struct getter field-access repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_cpp_struct_getter_field_access_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return cast(
        CppStructGetterFieldAccessRun,
        _run_cpp_repair(
            workspace=workspace,
            normalized_base=normalized_base,
            planning=planning,
            writer=writer,
            editor=editor,
            allowed_paths=allowed_paths,
            not_planned_message="No matching C++ struct getter field-access repair plan.",
            composition_missing_message="C++ struct getter field-access repair composition was not produced.",
            run_cls=CppStructGetterFieldAccessRun,
        ),
    )


def _run_cpp_repair(
    *,
    workspace: str | Path,
    normalized_base: Mapping[str, str],
    planning: Any,
    writer: WriteFileFn,
    editor: EditFileFn | None,
    allowed_paths: Sequence[str] | None,
    not_planned_message: str,
    composition_missing_message: str,
    run_cls: Any,
) -> Any:
    if planning.plan is None:
        return run_cls(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message=not_planned_message,
        )
    if planning.composition is None:
        return run_cls(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message=composition_missing_message,
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
        return run_cls(
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
    return run_cls(
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
        normalized_path: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized_path := _normalize_repair_path(str(path or "")))
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


__all__ = [
    "CppIncludePathPlanning",
    "CppIncludePathRun",
    "CppMissingPrivateMembersPlanning",
    "CppMissingPrivateMembersRun",
    "CppPlaceholderDeclarationPlanning",
    "CppPlaceholderDeclarationRun",
    "CppStandardIncludePlanning",
    "CppStandardIncludeRun",
    "CppStructGetterFieldAccessPlanning",
    "CppStructGetterFieldAccessRun",
    "plan_cpp_include_path_repair",
    "plan_cpp_missing_private_members_repair",
    "plan_cpp_placeholder_declaration_repair",
    "plan_cpp_standard_include_repair",
    "plan_cpp_struct_getter_field_access_repair",
    "run_cpp_include_path_repair",
    "run_cpp_missing_private_members_repair",
    "run_cpp_placeholder_declaration_repair",
    "run_cpp_standard_include_repair",
    "run_cpp_struct_getter_field_access_repair",
]
