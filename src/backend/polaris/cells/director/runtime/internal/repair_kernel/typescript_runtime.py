"""Runtime-owned TypeScript repair execution flows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from .typescript_syntax import (
    TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
    TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
    TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    build_typescript_duplicate_object_property_plan,
    build_typescript_enum_member_separator_plan,
    build_typescript_nullable_canvas_context_plan,
    build_typescript_object_literal_comma_plan,
)


@dataclass(frozen=True)
class TypeScriptObjectLiteralCommaPlanning:
    """Internal planning result for TS1005 object-literal comma repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL


@dataclass(frozen=True)
class TypeScriptObjectLiteralCommaRun:
    """Internal execution result for TS1005 object-literal comma repairs."""

    planning: TypeScriptObjectLiteralCommaPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TypeScriptNullableCanvasContextPlanning:
    """Internal planning result for nullable DOM/canvas TypeScript repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL


@dataclass(frozen=True)
class TypeScriptNullableCanvasContextRun:
    """Internal execution result for nullable DOM/canvas TypeScript repairs."""

    planning: TypeScriptNullableCanvasContextPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TypeScriptDuplicateObjectPropertyPlanning:
    """Internal planning result for TS1117 duplicate object property repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL


@dataclass(frozen=True)
class TypeScriptDuplicateObjectPropertyRun:
    """Internal execution result for TS1117 duplicate object property repairs."""

    planning: TypeScriptDuplicateObjectPropertyPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class TypeScriptEnumMemberSeparatorPlanning:
    """Internal planning result for TS1357 enum member separator repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL


@dataclass(frozen=True)
class TypeScriptEnumMemberSeparatorRun:
    """Internal execution result for TS1357 enum member separator repairs."""

    planning: TypeScriptEnumMemberSeparatorPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_typescript_object_literal_comma_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptObjectLiteralCommaPlanning:
    """Plan TS1005 object-literal comma repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_typescript_object_literal_comma_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return TypeScriptObjectLiteralCommaPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return TypeScriptObjectLiteralCommaPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def plan_typescript_duplicate_object_property_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptDuplicateObjectPropertyPlanning:
    """Plan TS1117 duplicate object property repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_typescript_duplicate_object_property_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return TypeScriptDuplicateObjectPropertyPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return TypeScriptDuplicateObjectPropertyPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def plan_typescript_enum_member_separator_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptEnumMemberSeparatorPlanning:
    """Plan TS1357 enum member separator repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_typescript_enum_member_separator_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return TypeScriptEnumMemberSeparatorPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return TypeScriptEnumMemberSeparatorPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def plan_typescript_nullable_canvas_context_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptNullableCanvasContextPlanning:
    """Plan nullable DOM/canvas TypeScript repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_typescript_nullable_canvas_context_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return TypeScriptNullableCanvasContextPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return TypeScriptNullableCanvasContextPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def run_typescript_object_literal_comma_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptObjectLiteralCommaRun:
    """Run TS1005 repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_typescript_object_literal_comma_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return TypeScriptObjectLiteralCommaRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching TypeScript object-literal comma repair plan.",
        )
    if planning.composition is None:
        return TypeScriptObjectLiteralCommaRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="TypeScript object-literal comma repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            str(path or "").strip().replace("\\", "/") for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return TypeScriptObjectLiteralCommaRun(
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
    return TypeScriptObjectLiteralCommaRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_typescript_duplicate_object_property_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptDuplicateObjectPropertyRun:
    """Run TS1117 duplicate object property repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_typescript_duplicate_object_property_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return TypeScriptDuplicateObjectPropertyRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching TypeScript duplicate object property repair plan.",
        )
    if planning.composition is None:
        return TypeScriptDuplicateObjectPropertyRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="TypeScript duplicate object property repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            str(path or "").strip().replace("\\", "/") for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return TypeScriptDuplicateObjectPropertyRun(
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
    return TypeScriptDuplicateObjectPropertyRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_typescript_enum_member_separator_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptEnumMemberSeparatorRun:
    """Run TS1357 enum member separator repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_typescript_enum_member_separator_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return TypeScriptEnumMemberSeparatorRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching TypeScript enum member separator repair plan.",
        )
    if planning.composition is None:
        return TypeScriptEnumMemberSeparatorRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="TypeScript enum member separator repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            str(path or "").strip().replace("\\", "/") for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return TypeScriptEnumMemberSeparatorRun(
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
    return TypeScriptEnumMemberSeparatorRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_typescript_nullable_canvas_context_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> TypeScriptNullableCanvasContextRun:
    """Run nullable DOM/canvas TypeScript repair through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_typescript_nullable_canvas_context_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return TypeScriptNullableCanvasContextRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching TypeScript nullable canvas/context repair plan.",
        )
    if planning.composition is None:
        return TypeScriptNullableCanvasContextRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="TypeScript nullable canvas/context repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            str(path or "").strip().replace("\\", "/") for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return TypeScriptNullableCanvasContextRun(
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
    return TypeScriptNullableCanvasContextRun(
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


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


__all__ = [
    "TypeScriptDuplicateObjectPropertyPlanning",
    "TypeScriptDuplicateObjectPropertyRun",
    "TypeScriptEnumMemberSeparatorPlanning",
    "TypeScriptEnumMemberSeparatorRun",
    "TypeScriptNullableCanvasContextPlanning",
    "TypeScriptNullableCanvasContextRun",
    "TypeScriptObjectLiteralCommaPlanning",
    "TypeScriptObjectLiteralCommaRun",
    "plan_typescript_duplicate_object_property_repair",
    "plan_typescript_enum_member_separator_repair",
    "plan_typescript_nullable_canvas_context_repair",
    "plan_typescript_object_literal_comma_repair",
    "run_typescript_duplicate_object_property_repair",
    "run_typescript_enum_member_separator_repair",
    "run_typescript_nullable_canvas_context_repair",
    "run_typescript_object_literal_comma_repair",
]
