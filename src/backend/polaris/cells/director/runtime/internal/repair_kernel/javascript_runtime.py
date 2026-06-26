"""Runtime-owned JavaScript/Node repair execution flows."""

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
from .javascript_syntax import (
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
    JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
    NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
    build_javascript_esm_commonjs_entrypoint_plan,
    build_javascript_missing_export_plan,
    build_javascript_missing_method_runtime_plan,
    build_javascript_test_missing_target_plan,
    build_node_test_script_contract_plan,
    build_npm_script_contract_plan,
)
from .policy_gate import PolicyDecision, RepairPolicyContext, RepairPolicyGate

PlanBuilderFn = Callable[..., RepairPlan | None]


@dataclass(frozen=True)
class JavaScriptRepairPlanning:
    """Internal planning result for JavaScript/Node repairs."""

    source_tool: str
    diagnostics: tuple[RepairDiagnostic, ...]
    plan: RepairPlan | None
    composition: CompositionResult | None
    advisor_notes: tuple[RepairAdvisorNote, ...] = ()


@dataclass(frozen=True)
class JavaScriptRepairRun:
    """Internal execution result for JavaScript/Node repairs."""

    planning: JavaScriptRepairPlanning
    ok: bool
    execution_result: RepairExecutionResult | None = None
    plan_decision: PolicyDecision | None = None
    composition_decision: PolicyDecision | None = None
    error_code: str | None = None
    error_message: str | None = None


def plan_npm_script_contract_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan package.json script contract repairs inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_npm_script_contract_plan,
    )


def plan_node_test_script_contract_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan generated Node test script contract repairs inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_node_test_script_contract_plan,
    )


def plan_javascript_test_missing_target_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan missing JavaScript smoke test targets inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_javascript_test_missing_target_plan,
    )


def plan_javascript_missing_export_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan conservative JavaScript named-export repairs inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_javascript_missing_export_plan,
    )


def plan_javascript_esm_commonjs_entrypoint_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan conservative CommonJS-to-ESM entrypoint repairs inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_javascript_esm_commonjs_entrypoint_plan,
    )


def plan_javascript_missing_method_runtime_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairPlanning:
    """Plan conservative JavaScript missing-method runtime repairs inside the runtime kernel."""

    return _plan_javascript_repair(
        source_tool=JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
        builder=build_javascript_missing_method_runtime_plan,
    )


def run_npm_script_contract_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run package.json script contract repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_npm_script_contract_repair,
        not_planned_message="No matching npm script contract repair plan.",
        composition_missing_message="NPM script contract repair composition was not produced.",
    )


def run_node_test_script_contract_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run generated Node test contract repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_node_test_script_contract_repair,
        not_planned_message="No matching Node test script contract repair plan.",
        composition_missing_message="Node test script contract repair composition was not produced.",
    )


def run_javascript_test_missing_target_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run missing JavaScript smoke test target repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_javascript_test_missing_target_repair,
        not_planned_message="No matching JavaScript missing test target repair plan.",
        composition_missing_message="JavaScript missing test target repair composition was not produced.",
    )


def run_javascript_missing_export_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run conservative JavaScript named-export repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_javascript_missing_export_repair,
        not_planned_message="No matching JavaScript missing export repair plan.",
        composition_missing_message="JavaScript missing export repair composition was not produced.",
    )


def run_javascript_esm_commonjs_entrypoint_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run conservative CommonJS-to-ESM entrypoint repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_javascript_esm_commonjs_entrypoint_repair,
        not_planned_message="No matching JavaScript ESM/CommonJS entrypoint repair plan.",
        composition_missing_message="JavaScript ESM/CommonJS entrypoint repair composition was not produced.",
    )


def run_javascript_missing_method_runtime_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisorNote] | None = None,
    mode: str = "commit",
) -> JavaScriptRepairRun:
    """Run conservative JavaScript missing-method runtime repair through Plan->Compose->Policy->Execute."""

    return _run_javascript_repair(
        workspace=workspace,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        writer=writer,
        editor=editor,
        allowed_paths=allowed_paths,
        advisor_notes=advisor_notes,
        mode=mode,
        planner=plan_javascript_missing_method_runtime_repair,
        not_planned_message="No matching JavaScript missing-method runtime repair plan.",
        composition_missing_message="JavaScript missing-method runtime repair composition was not produced.",
    )


def _plan_javascript_repair(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    builder: PlanBuilderFn,
) -> JavaScriptRepairPlanning:
    normalized_base = _normalize_base_files(base_files)
    diagnostics = tuple(normalize_artifact_quality_errors(list(artifact_quality_errors or ())))
    notes = tuple(advisor_notes or ())
    plan = builder(base_files=normalized_base, diagnostics=diagnostics, mode=mode)
    if plan is None:
        return JavaScriptRepairPlanning(
            source_tool=source_tool,
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            advisor_notes=notes,
        )
    if notes:
        plan = replace(plan, advisor_notes=notes)
    composition = PatchComposer().compose(normalized_base, plan.operations)
    return JavaScriptRepairPlanning(
        source_tool=plan.source_tool,
        diagnostics=tuple(plan.diagnostics),
        plan=plan,
        composition=composition,
        advisor_notes=notes,
    )


def _run_javascript_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    editor: EditFileFn | None,
    allowed_paths: Sequence[str] | None,
    advisor_notes: Sequence[RepairAdvisorNote] | None,
    mode: str,
    planner: Callable[..., JavaScriptRepairPlanning],
    not_planned_message: str,
    composition_missing_message: str,
) -> JavaScriptRepairRun:
    normalized_base = _normalize_base_files(base_files)
    planning = planner(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        mode=mode,
    )
    if planning.plan is None:
        return JavaScriptRepairRun(
            planning=planning,
            ok=False,
            error_code="repair_not_planned",
            error_message=not_planned_message,
        )
    if planning.composition is None:
        return JavaScriptRepairRun(
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
        return JavaScriptRepairRun(
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
    return JavaScriptRepairRun(
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
    "JavaScriptRepairPlanning",
    "JavaScriptRepairRun",
    "plan_javascript_esm_commonjs_entrypoint_repair",
    "plan_javascript_missing_export_repair",
    "plan_javascript_missing_method_runtime_repair",
    "plan_javascript_test_missing_target_repair",
    "plan_node_test_script_contract_repair",
    "plan_npm_script_contract_repair",
    "run_javascript_esm_commonjs_entrypoint_repair",
    "run_javascript_missing_export_repair",
    "run_javascript_missing_method_runtime_repair",
    "run_javascript_test_missing_target_repair",
    "run_node_test_script_contract_repair",
    "run_npm_script_contract_repair",
]
