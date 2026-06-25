"""Public read services for the `director.runtime` cell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
)
from polaris.cells.director.runtime.internal.repair_kernel.composer import PatchComposer
from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairAdvisorNote, RepairReceipt
from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.executor import TransactionalRepairExecutor
from polaris.cells.director.runtime.internal.repair_kernel.legacy_bridge import (
    build_legacy_repair_kernel_summary as _build_legacy_repair_kernel_summary,
)
from polaris.cells.director.runtime.internal.repair_kernel.policy_gate import (
    RepairPolicyContext,
    RepairPolicyGate,
)
from polaris.cells.director.runtime.internal.repair_kernel.registry import (
    build_repair_coverage_report,
    repair_language_slots,
)
from polaris.cells.director.runtime.internal.repair_kernel.shadow import compare_legacy_and_kernel_repairs
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
    CompareDirectorRepairShadowRunV1,
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairCompositionIssueV1,
    DirectorRepairCompositionSummaryV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairDiagnosticCoverageV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairPatchSummaryV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanSummaryV1,
    DirectorRepairResultV1,
    DirectorRepairShadowComparisonResultV1,
    DirectorRepairStrategyCatalogResultV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
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


def query_director_repair_advisory_policy(
    query: QueryDirectorRepairAdvisoryPolicyV1 | None = None,
) -> DirectorRepairAdvisoryPolicyResultV1:
    """Return the read-only AGI repair advisory policy projection."""

    request = query or QueryDirectorRepairAdvisoryPolicyV1()
    allowed_fields = tuple(sorted(ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS)) if request.include_field_lists else ()
    forbidden_metadata = tuple(sorted(FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS)) if request.include_field_lists else ()
    forbidden_suggested = (
        tuple(sorted(FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS)) if request.include_field_lists else ()
    )
    return DirectorRepairAdvisoryPolicyResultV1(
        schema_version="director.repair_advisory_policy.v1",
        source="director.runtime.repair_kernel.advisory_policy",
        access="read_only",
        allowed_suggested_rule_fields=allowed_fields,
        forbidden_metadata_fields=forbidden_metadata,
        forbidden_suggested_rule_fields=forbidden_suggested,
        summary={
            "advisory_only": True,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "suggested_rules_allowed": True,
            "suggested_rules_required_fields": ["pattern", "fix_template"],
            "director_runtime_remains_authoritative": True,
        },
    )


def compare_director_repair_shadow_run(
    command: CompareDirectorRepairShadowRunV1,
) -> DirectorRepairShadowComparisonResultV1:
    """Compare legacy deterministic repairs against new-kernel shadow receipts without writes."""

    comparison = compare_legacy_and_kernel_repairs(
        legacy_tool_results=command.legacy_tool_results,
        kernel_receipts=tuple(_public_receipt_to_internal(receipt) for receipt in command.kernel_receipts),
    )
    payload = comparison.to_dict()
    return DirectorRepairShadowComparisonResultV1(
        schema_version="director.repair_shadow_comparison.v1",
        source="director.runtime.repair_kernel.shadow",
        access="read_only",
        matched=comparison.matched,
        legacy_source_tools=tuple(payload["legacy_source_tools"]),
        kernel_source_tools=tuple(payload["kernel_source_tools"]),
        legacy_paths=tuple(payload["legacy_paths"]),
        kernel_paths=tuple(payload["kernel_paths"]),
        missing_paths_in_kernel=tuple(payload["missing_paths_in_kernel"]),
        extra_paths_in_kernel=tuple(payload["extra_paths_in_kernel"]),
        missing_source_tools_in_kernel=tuple(payload["missing_source_tools_in_kernel"]),
        extra_source_tools_in_kernel=tuple(payload["extra_source_tools_in_kernel"]),
        metadata=payload["metadata"],
    )


def query_director_repair_coverage(query: QueryDirectorRepairCoverageV1) -> DirectorRepairCoverageReportV1:
    """Return read-only repair-rule coverage for raw artifact-quality errors."""

    diagnostics = normalize_artifact_quality_errors(list(query.artifact_quality_errors))
    report = build_repair_coverage_report(diagnostics)
    return DirectorRepairCoverageReportV1(
        schema_version="director.repair_coverage_report.v1",
        source="director.runtime.repair_kernel.registry",
        access="read_only",
        total_diagnostics=report.total_diagnostics,
        covered_diagnostic_count=report.covered_diagnostic_count,
        uncovered_diagnostic_count=report.uncovered_diagnostic_count,
        items=tuple(
            DirectorRepairDiagnosticCoverageV1(
                diagnostic=(coverage_payload := item.to_dict())["diagnostic"],
                known_rule_matched=item.known_rule_matched,
                matched_rule_ids=tuple(rule.rule_id for rule in item.matched_rules),
                matched_source_tools=tuple(rule.source_tool for rule in item.matched_rules),
                archetypes=tuple(sorted({rule.archetype.value for rule in item.matched_rules})),
                phases=tuple(sorted({rule.phase for rule in item.matched_rules})),
                languages=tuple(sorted({rule.language for rule in item.matched_rules})),
                diagnostic_archetype=str(coverage_payload["diagnostic_archetype"]),
                diagnostic_phase=str(coverage_payload["diagnostic_phase"]),
                diagnostic_language=str(coverage_payload["diagnostic_language"]),
                suggested_rule_family=str(coverage_payload["suggested_rule_family"]),
            )
            for item in report.items
        ),
    )


def query_director_repair_language_slots(
    query: QueryDirectorRepairLanguageSlotsV1 | None = None,
) -> DirectorRepairLanguageSlotsResultV1:
    """Return read-only future language extension slots for deterministic repairs."""

    request = query or QueryDirectorRepairLanguageSlotsV1()
    slots = repair_language_slots()
    items = (
        tuple(
            DirectorRepairLanguageSlotV1(
                language=slot.language,
                aliases=slot.aliases,
                file_extensions=slot.file_extensions,
                diagnostic_sources=slot.diagnostic_sources,
                preferred_archetypes=tuple(archetype.value for archetype in slot.preferred_archetypes),
                notes=slot.notes,
            )
            for slot in slots
        )
        if request.include_items
        else ()
    )
    archetypes = sorted({archetype.value for slot in slots for archetype in slot.preferred_archetypes})
    extensions = sorted({extension for slot in slots for extension in slot.file_extensions})
    return DirectorRepairLanguageSlotsResultV1(
        schema_version="director.repair_language_slots.v1",
        source="director.runtime.repair_kernel.registry",
        access="read_only",
        items=items,
        summary={
            "language_count": len(slots),
            "extension_count": len(extensions),
            "languages": [slot.language for slot in slots],
            "file_extensions": extensions,
            "preferred_archetypes": archetypes,
            "bench_driven_rule_addition_required": True,
        },
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
    internal_advisor_notes = _to_internal_advisor_notes(public_advisor_notes)
    if internal_advisor_notes:
        plan = replace(plan, advisor_notes=internal_advisor_notes)

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


def _public_receipt_to_internal(receipt: RepairReceiptV1) -> RepairReceipt:
    return RepairReceipt(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        rule_id=receipt.source_tool,
        source_tool=receipt.source_tool,
        status=receipt.status,
        mode=str(receipt.metadata.get("mode") or "commit"),
        authoritative=receipt.authoritative,
        files_changed=receipt.files_changed,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        round_number=receipt.round_number,
        metadata=receipt.metadata,
    )


def _to_internal_advisor_notes(advisor_notes: Sequence[RepairAdvisoryV1]) -> tuple[RepairAdvisorNote, ...]:
    return tuple(
        RepairAdvisorNote(
            source=note.advisor_source,
            message=note.message,
            confidence=note.confidence,
            suggested_rules=note.suggested_rules,
            metadata=note.metadata,
        )
        for note in advisor_notes
    )


def _to_public_repair_receipt(receipt: RepairReceipt) -> RepairReceiptV1:
    advisor_notes = tuple(
        RepairAdvisoryV1(
            advisor_source=note.source,
            message=note.message,
            confidence=note.confidence,
            suggested_rules=note.suggested_rules,
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
        round_number=receipt.round_number,
        errors_before=receipt.errors_before,
        errors_after=receipt.errors_after,
        net_error_reduction=receipt.net_error_reduction,
        revalidation_evidence=receipt.revalidation_evidence.to_dict()
        if receipt.revalidation_evidence is not None
        else {},
        advisor_notes=advisor_notes,
        metadata=receipt.metadata,
    )


__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "DeterministicRepairStrategy",
    "build_director_repair_kernel_summary",
    "compare_director_repair_shadow_run",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "plan_director_typescript_object_literal_comma_repair",
    "query_director_repair_advisory_policy",
    "query_director_repair_coverage",
    "query_director_repair_language_slots",
    "query_director_repair_strategy_catalog",
    "run_director_typescript_object_literal_comma_repair",
    "summarize_deterministic_repair_source_tools",
]
