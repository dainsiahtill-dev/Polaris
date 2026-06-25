"""Public read services for the `director.runtime` cell."""

from __future__ import annotations

import json
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
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    RepairAdvisorNote,
    RepairReceipt,
    sha256_text,
)
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
    default_repair_rule_registry,
    repair_language_slots,
)
from polaris.cells.director.runtime.internal.repair_kernel.schedule_catalog import post_execution_repair_schedule
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
    AttachDirectorRepairRevalidationEvidenceV1,
    CompareDirectorRepairShadowRunV1,
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairAdvisoryValidationResultV1,
    DirectorRepairCompositionIssueV1,
    DirectorRepairCompositionSummaryV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairDiagnosticCoverageV1,
    DirectorRepairKernelSummaryProjectionResultV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairPatchSummaryV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanSummaryV1,
    DirectorRepairPostExecutionScheduleResultV1,
    DirectorRepairPostExecutionStepV1,
    DirectorRepairResultV1,
    DirectorRepairRevalidationProjectionResultV1,
    DirectorRepairShadowComparisonResultV1,
    DirectorRepairStrategyCatalogResultV1,
    ProjectDirectorRepairKernelSummaryV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    RepairReceiptV1,
)

WriteFileFn = Callable[[str, str], Mapping[str, Any]]


def _stable_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _count_by_key(items: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _receipt_authority_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "receipt_id": receipt.get("receipt_id"),
        "plan_id": receipt.get("plan_id"),
        "rule_id": receipt.get("rule_id"),
        "source_tool": receipt.get("source_tool"),
        "status": receipt.get("status"),
        "mode": receipt.get("mode"),
        "authoritative": receipt.get("authoritative"),
        "files_changed": list(receipt.get("files_changed") or []),
        "operation_ids": list(receipt.get("operation_ids") or []),
        "diagnostics": list(receipt.get("diagnostics") or []),
        "before_hashes": dict(receipt.get("before_hashes") or {}),
        "after_hashes": dict(receipt.get("after_hashes") or {}),
        "round_number": receipt.get("round_number"),
        "errors_before": receipt.get("errors_before"),
        "errors_after": receipt.get("errors_after"),
        "net_error_reduction": receipt.get("net_error_reduction"),
        "revalidation_evidence": receipt.get("revalidation_evidence"),
        "metadata": dict(receipt.get("metadata") or {}),
    }


def _refresh_receipt_hashes(receipt: dict[str, Any]) -> None:
    authority_payload = _receipt_authority_payload(receipt)
    receipt["authority_hash"] = sha256_text(_stable_json(authority_payload))
    receipt["projection_hash"] = sha256_text(
        _stable_json({**authority_payload, "advisor_notes": list(receipt.get("advisor_notes") or [])})
    )


def _receipt_context_with_revalidation(
    repair_kernel: Mapping[str, Any], receipts: list[dict[str, Any]]
) -> dict[str, Any]:
    context = dict(repair_kernel.get("receipt_context") or {})
    context_receipts = [dict(item or {}) for item in context.get("receipts") or []]
    receipt_by_id = {str(receipt.get("receipt_id") or ""): receipt for receipt in receipts}
    for item in context_receipts:
        receipt = receipt_by_id.get(str(item.get("receipt_id") or ""))
        if not receipt:
            continue
        item["errors_before"] = receipt.get("errors_before")
        item["errors_after"] = receipt.get("errors_after")
        item["net_error_reduction"] = receipt.get("net_error_reduction")
        item["post_check_evidence"] = {
            "available": receipt.get("revalidation_evidence") is not None,
            "exit_code": dict(receipt.get("revalidation_evidence") or {}).get("exit_code"),
            "residual_diagnostic_ids": dict(receipt.get("revalidation_evidence") or {}).get(
                "residual_diagnostic_ids",
                [],
            ),
        }
    context["receipts"] = context_receipts
    context["post_check_evidence_available"] = any(
        receipt.get("revalidation_evidence") is not None for receipt in receipts
    )
    return context


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


def attach_director_repair_revalidation_evidence(
    summary: Mapping[str, Any] | None,
    *,
    residual_artifact_quality_errors: Sequence[str],
    command: Sequence[str] = ("materialization_quality_revalidation",),
    exit_code: int | None = None,
    round_number: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility helper for projecting post-check evidence onto receipts."""

    return project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary=dict(summary or {}),
            residual_artifact_quality_errors=tuple(str(item) for item in residual_artifact_quality_errors),
            command=tuple(str(item) for item in command),
            exit_code=exit_code,
            round_number=round_number,
            metadata=dict(metadata or {}),
        )
    ).summary


def project_director_repair_revalidation_evidence(
    command: AttachDirectorRepairRevalidationEvidenceV1,
) -> DirectorRepairRevalidationProjectionResultV1:
    """Project post-check evidence onto a repair-kernel summary.

    This is a projection helper for migrated legacy paths: the verifier has
    already run, and this function binds its residual diagnostics back to each
    authoritative receipt without performing writes or registering rules.
    """

    updated_summary = dict(command.summary)
    repair_kernel_source = updated_summary.get("repair_kernel")
    if isinstance(repair_kernel_source, dict):
        nested = True
        repair_kernel = dict(repair_kernel_source)
    else:
        nested = False
        repair_kernel = dict(updated_summary)
    receipts = [dict(receipt or {}) for receipt in repair_kernel.get("receipts") or []]
    if not receipts:
        return DirectorRepairRevalidationProjectionResultV1(
            schema_version="director.repair_revalidation_projection.v1",
            source="director.runtime.repair_kernel.revalidation_projection",
            access="read_only",
            summary=updated_summary,
        )

    diagnostics_after = [
        diagnostic.to_dict()
        for diagnostic in normalize_artifact_quality_errors(list(command.residual_artifact_quality_errors))
    ]
    after_ids = {str(diagnostic.get("diagnostic_id") or "") for diagnostic in diagnostics_after}
    errors_after = len(diagnostics_after)
    resolved_exit_code = int(command.exit_code) if command.exit_code is not None else (0 if errors_after == 0 else 1)
    evidence_metadata = {
        "source": "director.runtime.repair_kernel.revalidation_projection",
        "residual_error_count": errors_after,
        **dict(command.metadata),
    }
    for receipt in receipts:
        diagnostics_before = [dict(diagnostic or {}) for diagnostic in receipt.get("diagnostics") or []]
        before_ids = {str(diagnostic.get("diagnostic_id") or "") for diagnostic in diagnostics_before}
        errors_before = len(diagnostics_before)
        if errors_before == 0:
            errors_before = int(dict(repair_kernel.get("coverage_report") or {}).get("total_diagnostics") or 0)
        residual_ids = sorted(before_ids & after_ids)
        resolved_ids = sorted(before_ids - after_ids)
        evidence = {
            "command": [str(item) for item in command.command if str(item or "").strip()],
            "exit_code": resolved_exit_code,
            "round_number": command.round_number if command.round_number is not None else receipt.get("round_number"),
            "errors_before": errors_before,
            "errors_after": errors_after,
            "net_error_reduction": errors_before - errors_after,
            "resolved_diagnostic_ids": resolved_ids,
            "residual_diagnostic_ids": residual_ids,
            "diagnostics_before": diagnostics_before,
            "diagnostics_after": diagnostics_after,
            "raw_output_ref": None,
            "metadata": evidence_metadata,
        }
        receipt["revalidation_evidence"] = evidence
        receipt["errors_before"] = errors_before
        receipt["errors_after"] = errors_after
        receipt["net_error_reduction"] = errors_before - errors_after
        receipt["round_number"] = evidence["round_number"]
        if receipt.get("status") == "pending_revalidation":
            receipt["status"] = "applied"
        receipt["authoritative"] = receipt.get("mode") == "commit" and receipt.get("status") != "failed"
        receipt_metadata = dict(receipt.get("metadata") or {})
        receipt_metadata["requires_revalidation"] = False
        receipt["metadata"] = receipt_metadata
        _refresh_receipt_hashes(receipt)

    repair_kernel["receipts"] = receipts
    repair_kernel["receipt_context"] = _receipt_context_with_revalidation(repair_kernel, receipts)
    pending_revalidation_count = sum(1 for receipt in receipts if receipt.get("status") == "pending_revalidation")
    repair_kernel["authoritative"] = (
        repair_kernel.get("mode") == "commit" and bool(receipts) and pending_revalidation_count == 0
    )
    repair_kernel["requires_revalidation"] = pending_revalidation_count > 0
    repair_kernel["pending_revalidation_count"] = pending_revalidation_count
    repair_kernel["receipts_with_revalidation"] = sum(1 for receipt in receipts if receipt.get("revalidation_evidence"))
    repair_kernel["revalidation"] = {
        "command": [str(item) for item in command.command if str(item or "").strip()],
        "exit_code": resolved_exit_code,
        "errors_after": errors_after,
        "residual_diagnostic_count": errors_after,
        "post_check_evidence_attached": True,
    }
    if nested:
        updated_summary["repair_kernel"] = repair_kernel
    else:
        updated_summary = repair_kernel
    return DirectorRepairRevalidationProjectionResultV1(
        schema_version="director.repair_revalidation_projection.v1",
        source="director.runtime.repair_kernel.revalidation_projection",
        access="read_only",
        summary=updated_summary,
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


def validate_director_repair_advisory(
    query: QueryDirectorRepairAdvisoryValidationV1,
) -> DirectorRepairAdvisoryValidationResultV1:
    """Validate and normalize a non-authoritative AGI repair advisory payload."""

    try:
        advisory = RepairAdvisoryV1(
            advisor_source=query.advisor_source,
            message=query.message,
            confidence=query.confidence,
            suggested_rules=query.suggested_rules,
            metadata=query.metadata,
        )
    except (TypeError, ValueError) as exc:
        return DirectorRepairAdvisoryValidationResultV1(
            schema_version="director.repair_advisory_validation.v1",
            source="director.runtime.repair_kernel.advisory_policy",
            access="read_only",
            ok=False,
            errors=(str(exc),),
            summary={
                "advisory_only": True,
                "accepted_suggested_rule_count": 0,
                "director_runtime_remains_authoritative": True,
            },
        )
    normalized = advisory.to_dict()
    return DirectorRepairAdvisoryValidationResultV1(
        schema_version="director.repair_advisory_validation.v1",
        source="director.runtime.repair_kernel.advisory_policy",
        access="read_only",
        ok=True,
        normalized_advisory=normalized,
        summary={
            "advisory_only": True,
            "accepted_suggested_rule_count": len(normalized.get("suggested_rules", [])),
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
    readiness = _shadow_cutover_readiness(command=command, matched=comparison.matched)
    metadata = {
        **dict(payload["metadata"]),
        "cutover_readiness": {
            "hashes_matched": readiness["hashes_matched"],
            "revalidation_evidence_complete": readiness["revalidation_evidence_complete"],
            "independent_shadow_required": True,
        },
    }
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
        cutover_ready=readiness["cutover_ready"],
        cutover_blockers=tuple(readiness["cutover_blockers"]),
        metadata=metadata,
    )


def _shadow_cutover_readiness(command: CompareDirectorRepairShadowRunV1, *, matched: bool) -> dict[str, Any]:
    blockers: list[str] = []
    if not matched:
        blockers.append("scope_mismatch")
    legacy_hashes = _legacy_shadow_hashes(command.legacy_tool_results)
    kernel_hashes = _kernel_receipt_hashes(command.kernel_receipts)
    if not legacy_hashes or not kernel_hashes:
        blockers.append("missing_before_after_hash_evidence")
        hashes_matched = False
    else:
        hashes_matched = legacy_hashes == kernel_hashes
        if not hashes_matched:
            blockers.append("before_after_hash_mismatch")
    revalidation_complete = bool(command.kernel_receipts) and all(
        bool(receipt.revalidation_evidence) for receipt in command.kernel_receipts
    )
    if not revalidation_complete:
        blockers.append("missing_revalidation_evidence")
    return {
        "cutover_ready": not blockers,
        "cutover_blockers": sorted(set(blockers)),
        "hashes_matched": hashes_matched,
        "revalidation_evidence_complete": revalidation_complete,
    }


def _legacy_shadow_hashes(tool_results: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    hashes: dict[str, tuple[str, str]] = {}
    for item in tool_results or ():
        result = item.get("result")
        payload = result if isinstance(result, Mapping) else {}
        file_path = str(payload.get("file") or payload.get("path") or "").strip().replace("\\", "/")
        before_hash = str(payload.get("before_hash") or "").strip()
        after_hash = str(payload.get("after_hash") or "").strip()
        if file_path and before_hash and after_hash:
            hashes[file_path] = (before_hash, after_hash)
    return hashes


def _kernel_receipt_hashes(receipts: Sequence[RepairReceiptV1]) -> dict[str, tuple[str, str]]:
    hashes: dict[str, tuple[str, str]] = {}
    for receipt in receipts or ():
        before_hashes = dict(receipt.before_hashes or {})
        after_hashes = dict(receipt.after_hashes or {})
        for path in receipt.files_changed:
            normalized_path = str(path or "").strip().replace("\\", "/")
            before_hash = str(before_hashes.get(path) or before_hashes.get(normalized_path) or "").strip()
            after_hash = str(after_hashes.get(path) or after_hashes.get(normalized_path) or "").strip()
            if normalized_path and before_hash and after_hash:
                hashes[normalized_path] = (before_hash, after_hash)
    return hashes


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
        executable_runtime_plan_diagnostic_count=report.executable_runtime_plan_diagnostic_count,
        metadata_only_diagnostic_count=report.metadata_only_diagnostic_count,
        items=tuple(
            DirectorRepairDiagnosticCoverageV1(
                diagnostic=(coverage_payload := item.to_dict())["diagnostic"],
                known_rule_matched=item.known_rule_matched,
                executable_runtime_plan_matched=item.executable_runtime_plan_matched,
                metadata_only_match=item.metadata_only_match,
                matched_rule_ids=tuple(rule.rule_id for rule in item.matched_rules),
                matched_source_tools=tuple(rule.source_tool for rule in item.matched_rules),
                runtime_plan_rule_ids=tuple(rule.rule_id for rule in item.matched_rules if rule.runtime_plan_available),
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
    rule_languages = sorted({rule.language for rule in default_repair_rule_registry().rules()})
    reserved_only_languages = sorted({slot.language for slot in slots} - set(rule_languages))
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
            "authoritative_rule_languages": rule_languages,
            "authoritative_rule_language_count": len(rule_languages),
            "reserved_only_languages": reserved_only_languages,
            "reserved_only_language_count": len(reserved_only_languages),
            "bench_driven_rule_addition_required": True,
        },
    )


def query_director_repair_post_execution_schedule(
    query: QueryDirectorRepairPostExecutionScheduleV1 | None = None,
) -> DirectorRepairPostExecutionScheduleResultV1:
    """Return the runtime-owned post-execution deterministic repair schedule."""

    request = query or QueryDirectorRepairPostExecutionScheduleV1()
    internal_steps = post_execution_repair_schedule()
    ordered_steps = tuple(
        DirectorRepairPostExecutionStepV1(
            step_id=step.step_id,
            language=step.language,
            phase=step.phase,
            priority=step.priority,
            source_tool=step.source_tool,
            depends_on=step.depends_on,
        )
        for step in internal_steps
    )
    languages = sorted({step.language for step in ordered_steps})
    phases = sorted({step.phase for step in ordered_steps})
    priorities = sorted({step.priority for step in ordered_steps})
    return DirectorRepairPostExecutionScheduleResultV1(
        schema_version="director.repair_post_execution_schedule.v1",
        source="director.runtime.repair_kernel.scheduler",
        access="read_only",
        items=ordered_steps if request.include_items else (),
        summary={
            "step_count": len(ordered_steps),
            "languages": languages,
            "phases": phases,
            "priorities": priorities,
            "ordered_step_ids": [step.step_id for step in ordered_steps],
            "source_tools": [step.source_tool for step in ordered_steps],
            "target_scheduler": "director.runtime.repair_kernel.scheduler",
            "runner_binding_owner": "roles.adapters",
            "legacy_callback_bridge": True,
            "runtime_schedule_authoritative": True,
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

    result = project_director_repair_kernel_summary(
        ProjectDirectorRepairKernelSummaryV1(
            stage=stage,
            tool_results=tuple(tool_results or ()),
            artifact_quality_errors=tuple(artifact_quality_errors or ()),
            mode=mode,
        )
    )
    return dict(result.summary)


def project_director_repair_kernel_summary(
    command: ProjectDirectorRepairKernelSummaryV1,
) -> DirectorRepairKernelSummaryProjectionResultV1:
    """Project legacy write-tool results into the runtime repair kernel receipt shape."""

    summary = _build_legacy_repair_kernel_summary(
        stage=command.stage,
        tool_results=[dict(item) for item in command.tool_results],
        artifact_quality_errors=list(command.artifact_quality_errors),
        mode=command.mode,
    )
    return DirectorRepairKernelSummaryProjectionResultV1(
        schema_version="director.repair_kernel_summary_projection.v1",
        source="director.runtime.repair_kernel.legacy_bridge",
        access="read_only",
        summary=summary,
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
    "AttachDirectorRepairRevalidationEvidenceV1",
    "DeterministicRepairStrategy",
    "DirectorRepairKernelSummaryProjectionResultV1",
    "DirectorRepairPostExecutionScheduleResultV1",
    "DirectorRepairPostExecutionStepV1",
    "DirectorRepairRevalidationProjectionResultV1",
    "ProjectDirectorRepairKernelSummaryV1",
    "attach_director_repair_revalidation_evidence",
    "build_director_repair_kernel_summary",
    "compare_director_repair_shadow_run",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "plan_director_typescript_object_literal_comma_repair",
    "project_director_repair_kernel_summary",
    "project_director_repair_revalidation_evidence",
    "query_director_repair_advisory_policy",
    "query_director_repair_coverage",
    "query_director_repair_language_slots",
    "query_director_repair_post_execution_schedule",
    "query_director_repair_strategy_catalog",
    "run_director_typescript_object_literal_comma_repair",
    "summarize_deterministic_repair_source_tools",
    "validate_director_repair_advisory",
]
