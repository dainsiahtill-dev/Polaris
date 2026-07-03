"""Runtime-owned generic hygiene repair execution flows."""

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
from .executor import DeleteFileFn, EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .generic_hygiene_syntax import PATCH_RESIDUE_CLEANUP_SOURCE_TOOL, build_generic_hygiene_plan
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate


@dataclass(frozen=True)
class GenericHygienePlanning:
    """Internal planning result for generic hygiene/contract/dependency repairs."""

    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()
    source_tool_hint: str = PATCH_RESIDUE_CLEANUP_SOURCE_TOOL

    @property
    def source_tool(self) -> str:
        return self.plan.source_tool if self.plan is not None else self.source_tool_hint


@dataclass(frozen=True)
class GenericHygieneRun:
    """Internal execution result for generic hygiene/contract/dependency repairs."""

    planning: GenericHygienePlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


class PatchResidueCleanupPlanning(GenericHygienePlanning):
    """Compatibility alias for the migrated patch-residue cleanup planner."""


class PatchResidueCleanupRun(GenericHygieneRun):
    """Compatibility alias for the migrated patch-residue cleanup runner."""


def plan_generic_hygiene_repair(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GenericHygienePlanning:
    """Plan generic repair source tools inside the runtime kernel."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = _diagnostics_for_generic_hygiene(
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
    )
    notes = tuple(advisor_notes or ())
    source_tool_hint = str(source_tool or "").strip()
    plan = build_generic_hygiene_plan(
        source_tool=source_tool_hint,
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return GenericHygienePlanning(
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
            source_tool_hint=source_tool_hint,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return GenericHygienePlanning(
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
        source_tool_hint=source_tool_hint,
    )


def plan_patch_residue_cleanup_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PatchResidueCleanupPlanning:
    """Plan generic patch-residue cleanup inside the runtime kernel."""

    planning = plan_generic_hygiene_repair(
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    return PatchResidueCleanupPlanning(
        diagnostics=planning.diagnostics,
        plan=planning.plan,
        composition=planning.composition,
        advisor_notes=planning.advisor_notes,
        source_tool_hint=planning.source_tool_hint,
    )


def run_generic_hygiene_repair(
    *,
    source_tool: str,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> GenericHygieneRun:
    """Run generic repairs through Plan->Compose->Policy->Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_generic_hygiene_repair(
        source_tool=source_tool,
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        repair_diagnostics=repair_diagnostics,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return GenericHygieneRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message=f"No matching runtime plan for source_tool={planning.source_tool!r}.",
        )
    if planning.composition is None:
        return GenericHygieneRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message=f"Runtime composition was not produced for source_tool={planning.source_tool!r}.",
        )

    policy = RepairPolicyGate()
    allowed = allowed_paths if allowed_paths is not None else tuple(normalized_base)
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in allowed)
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return GenericHygieneRun(
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
        deleter=deleter,
    )
    return GenericHygieneRun(
        planning=planning,
        ok=execution_result.ok,
        execution_result=execution_result,
        plan_decision=plan_decision,
        composition_decision=composition_decision,
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
    )


def run_patch_residue_cleanup_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None = None,
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> PatchResidueCleanupRun:
    """Run generic patch-residue cleanup through Plan->Compose->Policy->Execute."""

    run = run_generic_hygiene_repair(
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
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
    return PatchResidueCleanupRun(
        planning=run.planning,
        ok=run.ok,
        execution_result=run.execution_result,
        plan_decision=run.plan_decision,
        composition_decision=run.composition_decision,
        error_code=run.error_code,
        error_message=run.error_message,
    )


def _diagnostics_for_generic_hygiene(
    *,
    artifact_quality_errors: Sequence[str],
    repair_diagnostics: Sequence[RepairDiagnostic] | None,
) -> tuple[RepairDiagnostic, ...]:
    if repair_diagnostics is not None:
        return tuple(repair_diagnostics)
    return tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))


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
    "GenericHygienePlanning",
    "GenericHygieneRun",
    "PatchResidueCleanupPlanning",
    "PatchResidueCleanupRun",
    "plan_generic_hygiene_repair",
    "plan_patch_residue_cleanup_repair",
    "run_generic_hygiene_repair",
    "run_patch_residue_cleanup_repair",
]
