"""Executable Rust repair runtime bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .composer import PatchComposer
from .contracts import CompositionResult, RepairAdvisorNote, RepairDiagnostic, RepairExecutionResult, RepairPlan
from .diagnostics import normalize_artifact_quality_errors
from .executor import EditFileFn, TransactionalRepairExecutor, WriteFileFn
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate
from .rust_syntax import RUST_DEPENDENCY_SOURCE_TOOL, build_rust_dependency_plan


@dataclass(frozen=True)
class RustDependencyPlanning:
    """Planning result for Rust dependency repair."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class RustDependencyRun:
    """Execution result for Rust dependency repair."""

    planning: RustDependencyPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_rust_dependency_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDependencyPlanning:
    """Plan Rust dependency repair through typed diagnostics."""

    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    plan = build_rust_dependency_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    composition = PatchComposer().compose(base_files, plan.operations) if plan is not None else None
    return RustDependencyPlanning(
        source_tool=RUST_DEPENDENCY_SOURCE_TOOL,
        diagnostics=diagnostics,
        plan=plan,
        composition=composition,
        advisor_notes=tuple(advisor_notes or ()),
    )


def run_rust_dependency_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> RustDependencyRun:
    """Run Rust dependency repair through Plan→Compose→Policy→Execute."""

    normalized_base = _normalize_base_files(base_files)
    planning = plan_rust_dependency_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return RustDependencyRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching Rust dependency repair plan.",
        )
    if planning.composition is None:
        return RustDependencyRun(
            planning=planning,
            ok=False,
            error_code="repair_composition_missing",
            error_message="Rust dependency repair composition was not produced.",
        )

    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(_normalize_repair_path(str(path or "")) for path in (allowed_paths or normalized_base))
    )
    plan_decision = policy.evaluate_plan(planning.plan, policy_context)
    composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return RustDependencyRun(
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
    return RustDependencyRun(
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
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
