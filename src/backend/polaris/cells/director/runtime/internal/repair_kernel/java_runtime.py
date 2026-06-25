"""Runtime-owned Java repair execution flows."""

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
from .java_syntax import JAVA_ACCESSOR_ALIAS_SOURCE_TOOL, build_java_accessor_alias_plan
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate


@dataclass(frozen=True)
class JavaAccessorAliasPlanning:
    """Internal planning result for Java accessor alias repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else JAVA_ACCESSOR_ALIAS_SOURCE_TOOL


@dataclass(frozen=True)
class JavaAccessorAliasRun:
    """Internal execution result for Java accessor alias repairs."""

    planning: JavaAccessorAliasPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_java_accessor_alias_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaAccessorAliasPlanning:
    """Plan Java accessor alias repairs inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = build_java_accessor_alias_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return JavaAccessorAliasPlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return JavaAccessorAliasPlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def run_java_accessor_alias_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaAccessorAliasRun:
    """Run Java accessor alias repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_java_accessor_alias_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return JavaAccessorAliasRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Java accessor alias repair plan.",
        )
    if planning.composition is None:
        return JavaAccessorAliasRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Java accessor alias repair composition was not produced.",
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
        return JavaAccessorAliasRun(
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
    return JavaAccessorAliasRun(
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
    "JavaAccessorAliasPlanning",
    "JavaAccessorAliasRun",
    "plan_java_accessor_alias_repair",
    "run_java_accessor_alias_repair",
]
