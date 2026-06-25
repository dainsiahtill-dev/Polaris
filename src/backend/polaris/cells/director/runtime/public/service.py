"""Public read services for the `director.runtime` cell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.composer import PatchComposer
from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairReceipt
from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.executor import TransactionalRepairExecutor
from polaris.cells.director.runtime.internal.repair_kernel.legacy_bridge import (
    build_legacy_repair_kernel_summary as _build_legacy_repair_kernel_summary,
)
from polaris.cells.director.runtime.internal.repair_kernel.policy_gate import (
    RepairPolicyContext,
    RepairPolicyGate,
)
from polaris.cells.director.runtime.internal.repair_kernel.strategy_catalog import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    DeterministicRepairStrategy,
    describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax import (
    TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
    build_typescript_object_literal_comma_plan,
)
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairCompositionIssueV1,
    DirectorRepairCompositionSummaryV1,
    DirectorRepairPatchSummaryV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanSummaryV1,
    DirectorRepairResultV1,
    DirectorRepairStrategyCatalogResultV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    RepairReceiptV1,
)

WriteFileFn = Callable[[str, str], Mapping[str, Any]]


def _count_by_key(items: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def query_director_repair_strategy_catalog(
    query: QueryDirectorRepairStrategyCatalogV1 | None = None,
) -> DirectorRepairStrategyCatalogResultV1:
    """Return the read-only Director deterministic repair strategy catalog."""

    request = query or QueryDirectorRepairStrategyCatalogV1()
    all_items = deterministic_repair_strategy_catalog()
    visible_items = all_items[: request.max_items] if request.include_items else []
    summary: dict[str, Any] = {
        "total": len(all_items),
        "returned": len(visible_items),
        "by_language": _count_by_key(all_items, "language"),
        "by_phase": _count_by_key(all_items, "phase"),
        "by_concern": _count_by_key(all_items, "concern"),
        "by_risk": _count_by_key(all_items, "risk_level"),
    }
    return DirectorRepairStrategyCatalogResultV1(
        schema_version="director.deterministic_repair_strategy_catalog.v1",
        source="director.runtime.repair_kernel.strategy_catalog",
        access="read_only",
        agi_execution_authority=False,
        director_tool_execution_required=True,
        items=tuple(visible_items),
        summary=summary,
    )


def build_director_repair_kernel_summary(
    *,
    stage: str,
    tool_results: Sequence[dict[str, Any]],
    artifact_quality_errors: list[str] | None = None,
    mode: str = "commit",
) -> dict[str, Any]:
    """Build a public repair-kernel summary for legacy Director repair flows."""

    return _build_legacy_repair_kernel_summary(
        stage=stage,
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        mode=mode,
    )


def plan_director_typescript_object_literal_comma_repair(
    *,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    advisor_notes: Sequence[RepairAdvisoryV1] | None = None,
    mode: str = "commit",
) -> DirectorRepairPlanningResultV1:
    """Plan TS1005 object-literal comma repairs through the public runtime surface."""

    normalized_base = {
        str(path or "").strip().replace("\\", "/"): str(content or "")
        for path, content in dict(base_files or {}).items()
        if str(path or "").strip()
    }
    diagnostics = normalize_artifact_quality_errors(list(artifact_quality_errors or ()))
    public_advisor_notes = tuple(advisor_notes or ())
    plan = build_typescript_object_literal_comma_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    if plan is None:
        return DirectorRepairPlanningResultV1(
            ok=False,
            planned=False,
            source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
            diagnostic_count=len(diagnostics),
            advisor_notes=public_advisor_notes,
        )

    composition = PatchComposer().compose(normalized_base, plan.operations)
    return DirectorRepairPlanningResultV1(
        ok=composition.ok,
        planned=True,
        source_tool=plan.source_tool,
        diagnostic_count=len(plan.diagnostics),
        plan_summary=DirectorRepairPlanSummaryV1(
            plan_id=plan.plan_id,
            rule_id=plan.rule_id,
            source_tool=plan.source_tool,
            mode=plan.mode,
            risk_level=plan.risk_level,
            diagnostic_count=len(plan.diagnostics),
            operation_count=len(plan.operations),
            advisor_note_count=len(public_advisor_notes),
        ),
        composition_summary=DirectorRepairCompositionSummaryV1(
            ok=composition.ok,
            patches=tuple(
                DirectorRepairPatchSummaryV1(
                    path=patch.path,
                    content_after=patch.content_after,
                    before_hash=patch.before_hash,
                    after_hash=patch.after_hash,
                    changed=patch.before_hash != patch.after_hash,
                    operation_ids=patch.operation_ids,
                )
                for patch in composition.patches
            ),
            issues=tuple(
                DirectorRepairCompositionIssueV1(
                    code=issue.code,
                    message=issue.message,
                    path=issue.path,
                    operation_ids=issue.operation_ids,
                )
                for issue in composition.issues
            ),
        ),
        advisor_notes=public_advisor_notes,
    )


def run_director_typescript_object_literal_comma_repair(
    *,
    workspace: str | Path,
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str],
    writer: WriteFileFn,
    allowed_paths: Sequence[str] | None = None,
    advisor_notes: Sequence[RepairAdvisoryV1] | None = None,
    mode: str = "commit",
) -> DirectorRepairResultV1:
    """Run TS1005 object-literal comma repair through Plan→Compose→Policy→Execute→Receipt."""

    normalized_base = _normalize_base_files(base_files)
    diagnostics = normalize_artifact_quality_errors(list(artifact_quality_errors or ()))
    public_advisor_notes = tuple(advisor_notes or ())
    plan = build_typescript_object_literal_comma_plan(
        base_files=normalized_base,
        diagnostics=diagnostics,
        mode=mode,
    )
    planning_result = plan_director_typescript_object_literal_comma_repair(
        base_files=normalized_base,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=public_advisor_notes,
        mode=mode,
    )
    if plan is None:
        return DirectorRepairResultV1(
            ok=False,
            error_code="repair_not_planned",
            error_message="No matching TypeScript object-literal comma repair plan.",
            metadata={"planning": planning_result.to_dict()},
        )

    composer = PatchComposer()
    composition = composer.compose(normalized_base, plan.operations)
    policy = RepairPolicyGate()
    policy_context = RepairPolicyContext(
        allowed_paths=tuple(
            str(path or "").strip().replace("\\", "/") for path in (allowed_paths or normalized_base.keys())
        )
    )
    plan_decision = policy.evaluate_plan(plan, policy_context)
    composition_decision = policy.evaluate_composition(plan, composition)
    if not plan_decision.allowed or not composition_decision.allowed:
        return DirectorRepairResultV1(
            ok=False,
            error_code="repair_policy_denied",
            error_message="Director Runtime repair policy denied the plan or composition.",
            metadata={
                "planning": planning_result.to_dict(),
                "plan_policy": plan_decision.to_dict(),
                "composition_policy": composition_decision.to_dict(),
            },
        )

    execution_result = TransactionalRepairExecutor().execute(
        workspace=Path(str(workspace)).resolve(),
        plan=plan,
        composition=composition,
        writer=writer,
    )
    receipt = _to_public_repair_receipt(execution_result.receipt)
    return DirectorRepairResultV1(
        ok=execution_result.ok,
        receipts=(receipt,),
        error_code=None if execution_result.ok else "repair_execution_failed",
        error_message=None if execution_result.ok else execution_result.error,
        metadata={
            "planning": planning_result.to_dict(),
            "plan_policy": plan_decision.to_dict(),
            "composition_policy": composition_decision.to_dict(),
            "execution_error": execution_result.error,
            "rolled_back": execution_result.rolled_back,
        },
    )


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        str(path or "").strip().replace("\\", "/"): str(content or "")
        for path, content in dict(base_files or {}).items()
        if str(path or "").strip()
    }


def _to_public_repair_receipt(receipt: RepairReceipt) -> RepairReceiptV1:
    advisor_notes = tuple(
        RepairAdvisoryV1(
            advisor_source=note.source,
            message=note.message,
            confidence=note.confidence,
            metadata=note.metadata,
        )
        for note in receipt.advisor_notes
    )
    return RepairReceiptV1(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        source_tool=receipt.source_tool,
        status=receipt.status,
        authoritative=receipt.authoritative,
        files_changed=receipt.files_changed,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        advisor_notes=advisor_notes,
        metadata=receipt.metadata,
    )


__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "DeterministicRepairStrategy",
    "build_director_repair_kernel_summary",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "plan_director_typescript_object_literal_comma_repair",
    "query_director_repair_strategy_catalog",
    "run_director_typescript_object_literal_comma_repair",
    "summarize_deterministic_repair_source_tools",
]
