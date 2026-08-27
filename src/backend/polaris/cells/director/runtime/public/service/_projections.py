"""Director runtime public service — _projections submodule."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    FILE_ABSENT_HASH,
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairPlan,
    RepairReceipt,
    RepairRevalidationEvidence,
    sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.environment import (
    environment_prep_plans_from_requirements,
    environment_refresh_requirements_from_receipts,
)
from polaris.cells.director.runtime.internal.repair_kernel.executor import (
    _can_apply_with_editor,
    _text_replace_operations_for_patch,
)
from polaris.cells.director.runtime.internal.repair_kernel.receipt_projection import (
    summarize_repair_revalidation_coverage,
)
from polaris.cells.director.runtime.internal.repair_kernel.receipts import attach_revalidation_evidence
from polaris.cells.director.runtime.internal.repair_kernel.registry import (
    build_repair_coverage_report,
)
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    RuntimeRepairPlanning,
    runtime_repair_binding_has_typed_planner,
)
from polaris.cells.director.runtime.internal.repair_kernel.shadow import compare_baseline_and_kernel_repairs
from polaris.cells.director.runtime.public.contracts import (
    AttachDirectorRepairRevalidationEvidenceV1,
    CompareDirectorRepairShadowRunV1,
    DirectorInterfaceDiscrepancyReceiptV1,
    DirectorRepairCompositionIssueV1,
    DirectorRepairCompositionSummaryV1,
    DirectorRepairConvergenceResultV1,
    DirectorRepairConvergenceRoundResultV1,
    DirectorRepairCutoverReadinessResultV1,
    DirectorRepairEffectPlanV1,
    DirectorRepairEffectToolNameV1,
    DirectorRepairEffectV1,
    DirectorRepairEnvironmentPrepPlanV1,
    DirectorRepairEnvironmentRefreshRequirementsResultV1,
    DirectorRepairEnvironmentRefreshRequirementV1,
    DirectorRepairPatchSummaryV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanProbeResultV1,
    DirectorRepairPlanSummaryV1,
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationProjectionResultV1,
    DirectorRepairRevalidationRequestV1,
    DirectorRepairShadowComparisonResultV1,
    DirectorRepairVerifierSnapshotInputV1,
    EvaluateDirectorRepairCutoverReadinessV1,
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairEnvironmentRefreshRequirementsV1,
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    RunDirectorRepairConvergenceCommandV1,
    RunDirectorTaskBoundaryQualityLoopCommandV1,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import DirectedEffectImmutableItemsV1

from ._core import (
    _ALLOWED_CONVERGENCE_VERIFIER_EVIDENCE_SOURCES,
    DeleteFileFn,
    DirectorRepairRevalidatorFn,
    EditFileFn,
    _ordered_unique,
    _PublicConvergenceVerifierError,
    _receipt_context_with_revalidation,
    _refresh_receipt_hashes,
)


def query_director_repair_environment_refresh_requirements(
    query: QueryDirectorRepairEnvironmentRefreshRequirementsV1,
) -> DirectorRepairEnvironmentRefreshRequirementsResultV1:
    """Return environment refresh requirements and plans derived from repair receipts."""

    requirements = environment_refresh_requirements_from_receipts(
        tuple(receipt.to_dict() for receipt in query.receipts),
        workspace=query.workspace or None,
    )
    previous_prep_receipts = _environment_prep_receipts_from_public_repair_receipts(query.receipts)
    plans = environment_prep_plans_from_requirements(
        requirements,
        workspace=query.workspace or None,
        previous_prep_receipts=previous_prep_receipts,
    )
    return DirectorRepairEnvironmentRefreshRequirementsResultV1(
        items=tuple(_to_public_environment_refresh_requirement(item) for item in requirements),
        plans=tuple(_to_public_environment_prep_plan(plan.to_dict()) for plan in plans),
    )


def attach_director_repair_revalidation_evidence(
    summary: Mapping[str, Any] | None,
    *,
    residual_artifact_quality_errors: Sequence[str],
    residual_artifact_quality_issues: Sequence[Mapping[str, Any]] = (),
    command: Sequence[str] = ("materialization_quality_revalidation",),
    exit_code: int | None = None,
    round_number: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project post-check evidence onto a repair summary and return the summary."""

    return dict(
        project_director_repair_revalidation_evidence(
            AttachDirectorRepairRevalidationEvidenceV1(
                summary=dict(summary or {}),
                residual_artifact_quality_errors=tuple(str(item) for item in residual_artifact_quality_errors),
                residual_artifact_quality_issues=tuple(dict(item) for item in residual_artifact_quality_issues),
                command=tuple(str(item) for item in command),
                exit_code=exit_code,
                round_number=round_number,
                metadata=dict(metadata or {}),
            )
        ).summary
    )


def project_director_repair_revalidation_evidence(
    command: AttachDirectorRepairRevalidationEvidenceV1,
) -> DirectorRepairRevalidationProjectionResultV1:
    """Project post-check evidence onto a repair-kernel summary.

    This is a projection helper for migrated deterministic paths: the verifier has
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
        for diagnostic in _repair_diagnostics_from_quality_inputs(
            command.residual_artifact_quality_errors,
            command.residual_artifact_quality_issues,
        )
    ]
    after_signature_index = _repair_diagnostic_signature_index(diagnostics_after)
    errors_after = len(diagnostics_after)
    resolved_exit_code = int(command.exit_code) if command.exit_code is not None else (0 if errors_after == 0 else 1)
    evidence_metadata_base = {
        "source": "director.runtime.repair_kernel.revalidation_projection",
        "residual_error_count": errors_after,
        "diagnostic_match_strategy": "stable_signature",
        "diagnostics_after_signatures": sorted(after_signature_index),
        **dict(command.metadata),
    }
    coverage_report_total_diagnostics = _optional_int(
        dict(repair_kernel.get("coverage_report") or {}).get("total_diagnostics")
    )
    for receipt in receipts:
        diagnostics_before = [dict(diagnostic or {}) for diagnostic in receipt.get("diagnostics") or []]
        before_signature_index = _repair_diagnostic_signature_index(diagnostics_before)
        errors_before = len(diagnostics_before)
        errors_before_source = "receipt_diagnostics"
        if errors_before == 0:
            explicit_errors_before = _optional_int(receipt.get("errors_before"))
            existing_evidence = receipt.get("revalidation_evidence")
            existing_evidence_dict = existing_evidence if isinstance(existing_evidence, dict) else {}
            if explicit_errors_before is None:
                explicit_errors_before = _optional_int(existing_evidence_dict.get("errors_before"))
                errors_before_source = "existing_revalidation_evidence"
            else:
                errors_before_source = "receipt_errors_before"
            if explicit_errors_before is None:
                errors_before = 0
                errors_before_source = "missing_receipt_diagnostics"
            else:
                errors_before = explicit_errors_before
        residual_signatures = sorted(set(before_signature_index) & set(after_signature_index))
        resolved_signatures = sorted(set(before_signature_index) - set(after_signature_index))
        residual_ids = _diagnostic_ids_for_signatures(before_signature_index, residual_signatures)
        resolved_ids = _diagnostic_ids_for_signatures(before_signature_index, resolved_signatures)
        evidence_metadata = {
            **evidence_metadata_base,
            "errors_before_source": errors_before_source,
            "coverage_report_total_diagnostics": coverage_report_total_diagnostics,
            "coverage_report_total_diagnostics_used_for_errors_before": False,
            "diagnostics_before_signatures": sorted(before_signature_index),
            "resolved_diagnostic_signatures": resolved_signatures,
            "residual_diagnostic_signatures": residual_signatures,
        }
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
        evidence["evidence_status"] = _repair_revalidation_evidence_status(evidence)
        receipt["revalidation_evidence"] = evidence
        receipt["evidence_status"] = evidence["evidence_status"]
        receipt["errors_before"] = errors_before
        receipt["errors_after"] = errors_after
        receipt["net_error_reduction"] = errors_before - errors_after
        receipt["verifier_command"] = _string_list(evidence.get("command"))
        receipt["verifier_exit_code"] = _optional_int(evidence.get("exit_code"))
        receipt["diagnostics_before"] = _mapping_list(evidence.get("diagnostics_before"))
        receipt["diagnostics_after"] = _mapping_list(evidence.get("diagnostics_after"))
        receipt["resolved_diagnostic_ids"] = _string_list(evidence.get("resolved_diagnostic_ids"))
        receipt["residual_diagnostic_ids"] = _string_list(evidence.get("residual_diagnostic_ids"))
        receipt["round_number"] = evidence["round_number"]
        revalidation_failed = _repair_revalidation_payload_failed(evidence)
        if receipt.get("status") == "pending_revalidation":
            receipt["status"] = "failed_revalidation" if revalidation_failed else "applied"
        elif revalidation_failed and receipt.get("status") == "applied":
            receipt["status"] = "failed_revalidation"
        receipt["authoritative"] = (
            receipt.get("mode") == "commit" and receipt.get("status") == "applied" and not revalidation_failed
        )
        receipt_metadata = dict(receipt.get("metadata") or {})
        receipt_metadata["requires_revalidation"] = False
        receipt["metadata"] = receipt_metadata
        _refresh_receipt_hashes(receipt)

    repair_kernel["receipts"] = receipts
    repair_kernel["receipt_context"] = _receipt_context_with_revalidation(repair_kernel, receipts)
    revalidation_coverage = summarize_repair_revalidation_coverage(receipts)
    pending_revalidation_count = int(revalidation_coverage["pending_revalidation_count"])
    repair_kernel["authoritative"] = (
        repair_kernel.get("mode") == "commit"
        and bool(receipts)
        and int(revalidation_coverage["receipts_missing_revalidation"]) == 0
        and int(revalidation_coverage["failed_revalidation_receipt_count"]) == 0
    )
    repair_kernel["requires_revalidation"] = bool(revalidation_coverage["requires_revalidation"])
    repair_kernel["pending_revalidation_count"] = pending_revalidation_count
    repair_kernel["receipts_with_revalidation"] = int(revalidation_coverage["receipts_with_revalidation"])
    repair_kernel["revalidation_coverage"] = revalidation_coverage
    repair_kernel["revalidation"] = {
        "command": [str(item) for item in command.command if str(item or "").strip()],
        "exit_code": resolved_exit_code,
        "errors_after": errors_after,
        "residual_diagnostic_count": errors_after,
        "diagnostics_after_signatures": sorted(after_signature_index),
        "post_check_evidence_attached": True,
        "coverage": revalidation_coverage,
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


def _repair_diagnostic_signature_index(diagnostics: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for diagnostic in diagnostics:
        diagnostic_dict = dict(diagnostic or {})
        indexed.setdefault(_repair_diagnostic_signature(diagnostic_dict), diagnostic_dict)
    return indexed


def _repair_diagnostic_signature(diagnostic: Mapping[str, Any]) -> str:
    payload = {
        "source": str(diagnostic.get("source") or "unknown").strip() or "unknown",
        "code": str(diagnostic.get("code") or "unknown").strip() or "unknown",
        "severity": str(diagnostic.get("severity") or "error").strip() or "error",
        "path": str(diagnostic.get("path") or "").replace("\\", "/").strip(),
        "line": diagnostic.get("line"),
        "column": diagnostic.get("column"),
        "message": " ".join(str(diagnostic.get("message") or "").split()),
    }
    return (
        f"diag_sig_{sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))[:24]}"
    )


def _diagnostic_ids_for_signatures(
    signature_index: Mapping[str, Mapping[str, Any]],
    signatures: Sequence[str],
) -> list[str]:
    diagnostic_ids: list[str] = []
    for signature in signatures:
        diagnostic_id = str(dict(signature_index.get(signature) or {}).get("diagnostic_id") or "").strip()
        if diagnostic_id:
            diagnostic_ids.append(diagnostic_id)
    return sorted(diagnostic_ids)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _repair_revalidation_payload_failed(evidence: Mapping[str, Any] | None) -> bool:
    payload = dict(evidence or {})
    if not payload:
        return False
    exit_code = _optional_int(payload.get("exit_code"))
    if exit_code is None or exit_code != 0:
        return True
    errors_after = _optional_int(payload.get("errors_after"))
    if errors_after is not None and errors_after > 0:
        return True
    if payload.get("residual_diagnostic_ids"):
        return True
    metadata = payload.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    return bool(metadata_dict.get("residual_diagnostic_signatures"))


def _repair_revalidation_evidence_status(evidence: Mapping[str, Any] | None) -> str:
    payload = dict(evidence or {})
    if not payload:
        return "missing_evidence"
    metadata = payload.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    failure_reason = str(metadata_dict.get("revalidation_failure_reason") or "").strip()
    if failure_reason in {
        "invalid_revalidation_evidence_type",
        "missing_revalidation_evidence",
        "missing_revalidation_exit_code",
        "revalidator_exception",
    }:
        return "missing_evidence"
    command = payload.get("command")
    has_command = isinstance(command, list | tuple) and any(str(item or "").strip() for item in command)
    exit_code = _optional_int(payload.get("exit_code"))
    if not has_command or exit_code is None:
        return "missing_evidence"
    if _repair_revalidation_payload_failed(payload):
        return "failed_evidence"
    return "resolved_evidence"


def _validate_public_convergence_verifier_evidence(
    verifier_input: DirectorRepairVerifierSnapshotInputV1,
    *,
    round_number: int,
) -> None:
    metadata = dict(verifier_input.metadata)
    evidence_source = str(metadata.get("evidence_source") or "").strip()
    raw_output_ref_verified = metadata.get("raw_output_ref_verified")
    blockers: list[str] = []

    if not verifier_input.command:
        blockers.append("missing_command")
    if verifier_input.exit_code is None:
        blockers.append("missing_exit_code")
    if not verifier_input.raw_output_ref:
        blockers.append("missing_raw_output_ref")
    if raw_output_ref_verified is not True:
        blockers.append("raw_output_ref_not_verified")
    if not evidence_source:
        blockers.append("missing_evidence_source")
    elif evidence_source not in _ALLOWED_CONVERGENCE_VERIFIER_EVIDENCE_SOURCES:
        blockers.append("unsupported_evidence_source")

    if not blockers:
        return

    raise _PublicConvergenceVerifierError(
        "Repair convergence verifier evidence failed public trust gate.",
        status="verifier_evidence_invalid",
        error_code="verifier_evidence_invalid",
        metadata={
            "verifier_failure_reason": "verifier_evidence_invalid",
            "evidence_blocker": blockers[0],
            "evidence_blockers": blockers,
            "round_number": round_number,
            "command_present": bool(verifier_input.command),
            "exit_code_present": verifier_input.exit_code is not None,
            "raw_output_ref_present": bool(verifier_input.raw_output_ref),
            "raw_output_ref_verified": raw_output_ref_verified,
            "evidence_source": evidence_source or None,
            "allowed_evidence_sources": sorted(_ALLOWED_CONVERGENCE_VERIFIER_EVIDENCE_SOURCES),
        },
    )


def compare_director_repair_shadow_run(
    command: CompareDirectorRepairShadowRunV1,
) -> DirectorRepairShadowComparisonResultV1:
    """Compare baseline deterministic repair effects against kernel receipts without writes."""

    comparison = compare_baseline_and_kernel_repairs(
        baseline_tool_results=command.baseline_tool_results,
        kernel_receipts=tuple(_public_receipt_to_internal(receipt) for receipt in command.kernel_receipts),
    )
    payload = comparison.to_dict()
    readiness = _shadow_cutover_readiness(command=command, matched=comparison.matched)
    metadata = {
        **dict(payload["metadata"]),
        "cutover_readiness": {
            "comparison_mode": command.comparison_mode,
            "hashes_matched": readiness["hashes_matched"],
            "revalidation_evidence_complete": readiness["revalidation_evidence_complete"],
            "revalidation_evidence_passed": readiness["revalidation_evidence_passed"],
            "authoritative_receipts": readiness["authoritative_receipts"],
            "revalidation_coverage": readiness["revalidation_coverage"],
            "independent_shadow_required": True,
            "independent_shadow_satisfied": readiness["independent_shadow_satisfied"],
        },
    }
    return DirectorRepairShadowComparisonResultV1(
        schema_version="director.repair_shadow_comparison.v1",
        source="director.runtime.repair_kernel.shadow",
        access="read_only",
        matched=comparison.matched,
        baseline_source_tools=tuple(payload["baseline_source_tools"]),
        kernel_source_tools=tuple(payload["kernel_source_tools"]),
        baseline_paths=tuple(payload["baseline_paths"]),
        kernel_paths=tuple(payload["kernel_paths"]),
        missing_paths_in_kernel=tuple(payload["missing_paths_in_kernel"]),
        extra_paths_in_kernel=tuple(payload["extra_paths_in_kernel"]),
        missing_source_tools_in_kernel=tuple(payload["missing_source_tools_in_kernel"]),
        extra_source_tools_in_kernel=tuple(payload["extra_source_tools_in_kernel"]),
        comparison_mode=command.comparison_mode,
        independent_shadow_required=True,
        independent_shadow_satisfied=readiness["independent_shadow_satisfied"],
        cutover_ready=readiness["cutover_ready"],
        cutover_blockers=tuple(readiness["cutover_blockers"]),
        metadata=metadata,
    )


def evaluate_director_repair_cutover_readiness(
    command: EvaluateDirectorRepairCutoverReadinessV1,
) -> DirectorRepairCutoverReadinessResultV1:
    """Evaluate repeated shadow comparisons before allowing migration cutover."""

    comparisons = tuple(command.comparisons or ())
    required_successful_runs = max(1, int(command.required_successful_runs or 0))
    successful = tuple(comparison for comparison in comparisons if _shadow_comparison_is_successful(comparison))
    blockers: list[str] = []
    if len(successful) < required_successful_runs:
        blockers.append("insufficient_successful_independent_shadow_runs")
    failed_indices = tuple(
        index for index, comparison in enumerate(comparisons) if not _shadow_comparison_is_successful(comparison)
    )
    if failed_indices:
        blockers.append("shadow_comparison_not_cutover_ready")
    scope_signatures = tuple(_shadow_comparison_scope_signature(comparison) for comparison in successful)
    if len(set(scope_signatures)) > 1:
        blockers.append("shadow_comparison_scope_drift")
    return DirectorRepairCutoverReadinessResultV1(
        schema_version="director.repair_cutover_readiness.v1",
        source="director.runtime.repair_kernel.cutover_gate",
        access="read_only",
        cutover_ready=not blockers,
        required_successful_runs=required_successful_runs,
        comparison_count=len(comparisons),
        successful_comparison_count=len(successful),
        cutover_blockers=tuple(sorted(set(blockers))),
        metadata={
            "comparison_modes": [comparison.comparison_mode for comparison in comparisons],
            "failed_comparison_indices": list(failed_indices),
            "successful_comparison_indices": [
                index for index, comparison in enumerate(comparisons) if _shadow_comparison_is_successful(comparison)
            ],
            "scope_signatures": [list(signature) for signature in scope_signatures],
            "independent_shadow_required": True,
            "multi_run_cutover_gate": True,
        },
    )


def _shadow_comparison_is_successful(comparison: DirectorRepairShadowComparisonResultV1) -> bool:
    return (
        comparison.cutover_ready
        and comparison.comparison_mode == "independent_shadow_run"
        and comparison.independent_shadow_satisfied
        and not comparison.cutover_blockers
    )


def _shadow_comparison_scope_signature(comparison: DirectorRepairShadowComparisonResultV1) -> tuple[str, ...]:
    return (
        "baseline_paths",
        *comparison.baseline_paths,
        "kernel_paths",
        *comparison.kernel_paths,
        "baseline_source_tools",
        *comparison.baseline_source_tools,
        "kernel_source_tools",
        *comparison.kernel_source_tools,
    )


def _shadow_cutover_readiness(command: CompareDirectorRepairShadowRunV1, *, matched: bool) -> dict[str, Any]:
    blockers: list[str] = []
    independent_shadow_satisfied = command.comparison_mode == "independent_shadow_run"
    if not independent_shadow_satisfied:
        blockers.append("independent_shadow_required")
    if not matched:
        blockers.append("scope_mismatch")
    baseline_hashes = _baseline_shadow_hashes(command.baseline_tool_results)
    kernel_hashes = _kernel_receipt_hashes(command.kernel_receipts)
    if not baseline_hashes or not kernel_hashes:
        blockers.append("missing_before_after_hash_evidence")
        hashes_matched = False
    else:
        hashes_matched = baseline_hashes == kernel_hashes
        if not hashes_matched:
            blockers.append("before_after_hash_mismatch")
    revalidation_complete = bool(command.kernel_receipts) and all(
        bool(receipt.revalidation_evidence) for receipt in command.kernel_receipts
    )
    revalidation_coverage = summarize_repair_revalidation_coverage(
        tuple(receipt.to_dict() for receipt in command.kernel_receipts)
    )
    revalidation_passed = (
        bool(command.kernel_receipts)
        and bool(revalidation_coverage["post_check_evidence_complete"])
        and int(revalidation_coverage["failed_revalidation_receipt_count"]) == 0
    )
    authoritative_receipts = bool(command.kernel_receipts) and all(
        receipt.authoritative and receipt.status == "applied" for receipt in command.kernel_receipts
    )
    if not revalidation_complete:
        blockers.append("missing_revalidation_evidence")
    if revalidation_complete and not revalidation_passed:
        blockers.append("failed_revalidation_evidence")
    if not authoritative_receipts:
        blockers.append("non_authoritative_kernel_receipt")
    return {
        "cutover_ready": not blockers,
        "cutover_blockers": sorted(set(blockers)),
        "hashes_matched": hashes_matched,
        "revalidation_evidence_complete": revalidation_complete,
        "revalidation_evidence_passed": revalidation_passed,
        "authoritative_receipts": authoritative_receipts,
        "independent_shadow_satisfied": independent_shadow_satisfied,
        "revalidation_coverage": revalidation_coverage,
    }


def _baseline_shadow_hashes(tool_results: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
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


def normalize_director_repair_diagnostics(artifact_quality_errors: Sequence[Any]) -> tuple[RepairDiagnosticV1, ...]:
    """Normalize raw or structured artifact-quality input into public repair diagnostics."""

    diagnostics = normalize_artifact_quality_errors(list(artifact_quality_errors or ()))
    return tuple(_to_public_repair_diagnostic(diagnostic) for diagnostic in diagnostics)


def normalize_director_repair_issue_diagnostics(
    artifact_quality_issues: Sequence[Mapping[str, Any]],
) -> tuple[RepairDiagnosticV1, ...]:
    """Normalize typed artifact-quality issues into public repair diagnostics.

    This consumes the JSON-ready ``ArtifactQualityIssue`` projection without
    importing KernelOne quality types into the Director runtime public surface.
    """

    diagnostics = normalize_artifact_quality_errors(list(artifact_quality_issues or ()))
    return tuple(_to_public_repair_diagnostic(diagnostic) for diagnostic in diagnostics)


def _repair_diagnostics_from_artifact_quality_issues(
    artifact_quality_issues: Sequence[Mapping[str, Any]],
) -> tuple[RepairDiagnostic, ...]:
    diagnostics: list[RepairDiagnostic] = []
    for public_diagnostic in normalize_director_repair_issue_diagnostics(artifact_quality_issues):
        diagnostics.append(_to_internal_repair_diagnostic(public_diagnostic))
    return tuple(diagnostics)


def _merge_repair_diagnostic_evidence(primary: RepairDiagnostic, additive: RepairDiagnostic) -> RepairDiagnostic:
    metadata = dict(additive.metadata)
    metadata.update(primary.metadata)
    return RepairDiagnostic(
        source=primary.source,
        code=primary.code,
        message=primary.message,
        severity=primary.severity,
        path=primary.path or additive.path,
        line=primary.line if primary.line is not None else additive.line,
        column=primary.column if primary.column is not None else additive.column,
        span_start=primary.span_start if primary.span_start is not None else additive.span_start,
        span_end=primary.span_end if primary.span_end is not None else additive.span_end,
        diagnostic_id=primary.diagnostic_id,
        raw=primary.raw or additive.raw,
        metadata=metadata,
    )


def _repair_diagnostic_raw_key(diagnostic: RepairDiagnostic) -> str:
    return str(diagnostic.raw or diagnostic.message or "").strip()


def _repair_diagnostic_structural_key(diagnostic: RepairDiagnostic) -> tuple[str, str, str, str, str] | None:
    code = str(diagnostic.code or "").strip()
    path = str(diagnostic.path or "").strip()
    if not code or not path:
        return None
    return (
        code,
        path,
        str(diagnostic.line or ""),
        str(diagnostic.column or ""),
        str(diagnostic.message or "").strip(),
    )


def _repair_diagnostics_from_quality_inputs(
    artifact_quality_errors: Sequence[str],
    artifact_quality_issues: Sequence[Mapping[str, Any]],
) -> tuple[RepairDiagnostic, ...]:
    """Combine legacy precise diagnostics with typed issue evidence.

    During WS4 migration, KernelOne typed artifact issues are an evidence
    projection while the Director runtime string normalizer still carries some
    language-specific diagnostic refinement. When both inputs describe the same
    raw finding, keep the legacy refined diagnostic and use typed issues only as
    additive evidence. Typed-only callers still work without legacy strings.
    """

    diagnostics = list(normalize_artifact_quality_errors(list(artifact_quality_errors)))
    index_by_raw = {
        raw_key: index for index, item in enumerate(diagnostics) if (raw_key := _repair_diagnostic_raw_key(item))
    }
    index_by_structural = {
        structural_key: index
        for index, item in enumerate(diagnostics)
        if (structural_key := _repair_diagnostic_structural_key(item)) is not None
    }
    for diagnostic in _repair_diagnostics_from_artifact_quality_issues(artifact_quality_issues):
        raw_key = _repair_diagnostic_raw_key(diagnostic)
        if raw_key and raw_key in index_by_raw:
            index = index_by_raw[raw_key]
            diagnostics[index] = _merge_repair_diagnostic_evidence(diagnostics[index], diagnostic)
            continue
        structural_key = _repair_diagnostic_structural_key(diagnostic)
        if structural_key is not None and structural_key in index_by_structural:
            index = index_by_structural[structural_key]
            diagnostics[index] = _merge_repair_diagnostic_evidence(diagnostics[index], diagnostic)
            continue
        diagnostics.append(diagnostic)
        if raw_key:
            index_by_raw[raw_key] = len(diagnostics) - 1
        if structural_key is not None:
            index_by_structural[structural_key] = len(diagnostics) - 1
    return tuple(diagnostics)


def _task_boundary_quality_metadata(
    command: RunDirectorTaskBoundaryQualityLoopCommandV1,
    *,
    plan_probe: DirectorRepairPlanProbeResultV1,
) -> dict[str, Any]:
    discrepancy_receipts = _interface_discrepancy_receipts_from_plan_probe(command, plan_probe)
    return {
        "public_entrypoint": "run_director_task_boundary_quality_loop",
        "owner_cell": "director.runtime",
        "quality_boundary": "ce_task",
        "qa_final_verdict_boundary": "final_project_gate",
        "task_boundary_validation_chain": [
            "coverage",
            "plan_probe",
            "convergence",
            "environment_prep",
            "revalidation",
            "receipt",
        ],
        "coverage_is_not_planning": True,
        "coverage_report_status": "has_gaps" if plan_probe.uncovered_diagnostics else "covered",
        "plan_probe_status": plan_probe.status,
        "plannable_source_tools": list(plan_probe.plannable_source_tools),
        "covered_unplannable_source_tools": list(plan_probe.covered_unplannable_source_tools),
        "covered_unplannable_diagnostic_count": len(plan_probe.covered_unplannable_diagnostics),
        "coverage_gap_count": len(plan_probe.uncovered_diagnostics),
        "interface_discrepancy_receipts": discrepancy_receipts,
        "interface_discrepancy_receipt_count": len(discrepancy_receipts),
        "task_interface_contract_present": bool(command.task_interface_contract),
        "task_interface_contract": dict(command.task_interface_contract),
        "topology_weighted_score_policy": {
            "status": "reserved",
            "requires": "SymbolIndexSnapshot dependency graph",
            "current_gate": "error_count_plus_coverage_plan_probe",
        },
        "transaction_isolation_policy": {
            "current_mode": "transactional_file_patch_with_hash_rollback",
            "reserved_modes": ["overlayfs_copy_on_write", "vfs_diff_log"],
            "overlayfs_not_implemented_in_public_runtime": True,
        },
        "context_slicing_policy": {
            "status": "reserved",
            "hot_context": "diagnostic_spans_and_interface_contract",
            "cold_context": "task_target_file_skeletons_read_only",
        },
        "command_metadata": dict(command.metadata),
    }


def _interface_discrepancy_receipts_from_plan_probe(
    command: RunDirectorTaskBoundaryQualityLoopCommandV1,
    plan_probe: DirectorRepairPlanProbeResultV1,
) -> list[dict[str, Any]]:
    diagnostics = [dict(item) for item in plan_probe.covered_unplannable_diagnostics]
    if not diagnostics:
        return []
    interface_delta = _interface_delta_from_task_boundary(command, diagnostics)
    triage_summary = _interface_discrepancy_triage_summary(
        command=command,
        plan_probe=plan_probe,
        interface_delta=interface_delta,
    )
    recommended_owner = str(triage_summary["recommended_owner"])
    recommended_route = (
        "pending_design_interface_contract"
        if recommended_owner == "chief_engineer"
        else "director_retry_with_interface_discrepancy_context"
    )
    return [
        DirectorInterfaceDiscrepancyReceiptV1(
            task_id=command.task_id,
            source="director.runtime.task_boundary_quality_loop",
            plan_probe_status=plan_probe.status,
            diagnostics=tuple(diagnostics),
            source_tools=tuple(plan_probe.covered_unplannable_source_tools),
            recommended_owner=recommended_owner,
            recommended_route=recommended_route,
            task_interface_contract_present=bool(command.task_interface_contract),
            llm_fallback_blocked=recommended_owner != "director",
            director_retry_allowed=recommended_owner == "director",
            interface_delta=interface_delta,
            triage_summary=triage_summary,
            metadata={
                "public_entrypoint": "run_director_task_boundary_quality_loop",
                "coverage_gap_count": len(plan_probe.uncovered_diagnostics),
                "interface_delta_available": bool(interface_delta),
            },
        ).to_dict()
    ]


def _interface_delta_from_task_boundary(
    command: RunDirectorTaskBoundaryQualityLoopCommandV1,
    diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    contract = dict(command.task_interface_contract)
    diagnostic_paths = _diagnostic_paths(diagnostics)
    requested_symbols = _diagnostic_symbols(diagnostics)
    actual_exports, planned_exports, consumed_symbols = _interface_contract_symbol_maps(contract)
    interface_conflicts = [dict(item) for item in contract.get("interface_conflicts", ()) if isinstance(item, Mapping)][
        :20
    ]
    return {
        "schema_version": "director.interface_delta.v1",
        "task_id": command.task_id,
        "contract_present": bool(contract),
        "contract_schema_version": str(contract.get("schema_version") or ""),
        "contract_keys": sorted(str(key) for key in contract),
        "diagnostic_paths": diagnostic_paths,
        "diagnostic_codes": _diagnostic_codes(diagnostics),
        "requested_symbols": requested_symbols,
        "actual_public_symbols_by_path": actual_exports,
        "planned_public_symbols_by_path": planned_exports,
        "consumed_symbols_by_path": consumed_symbols,
        "interface_conflicts": interface_conflicts,
        "interface_conflict_count": len(interface_conflicts),
        "actual_export_file_count": len(actual_exports),
        "planned_export_file_count": len(planned_exports),
        "diagnostic_count": len(diagnostics),
    }


def _interface_discrepancy_triage_summary(
    *,
    command: RunDirectorTaskBoundaryQualityLoopCommandV1,
    plan_probe: DirectorRepairPlanProbeResultV1,
    interface_delta: Mapping[str, Any],
) -> dict[str, Any]:
    contract_present = bool(command.task_interface_contract)
    has_contract_conflicts = bool(interface_delta.get("interface_conflicts"))
    recommended_owner = "director" if contract_present and not has_contract_conflicts else "chief_engineer"
    recommended_route = (
        "director_retry_with_interface_discrepancy_context"
        if recommended_owner == "director"
        else "pending_design_interface_contract"
    )
    return {
        "schema_version": "director.interface_discrepancy_triage.v1",
        "plan_probe_status": plan_probe.status,
        "recommended_owner": recommended_owner,
        "recommended_route": recommended_route,
        "contract_present": contract_present,
        "contract_conflict_count": int(interface_delta.get("interface_conflict_count") or 0),
        "director_retry_allowed": recommended_owner == "director",
        "llm_fallback_blocked": recommended_owner != "director",
        "macro_blueprint_regeneration_allowed": False,
        "triage_policy": "ce_contract_if_missing_or_conflicting_else_director_local_repair",
        "reason": (
            "task_interface_contract_conflict"
            if has_contract_conflicts
            else "task_interface_contract_missing"
            if not contract_present
            else "director_local_retry_with_interface_delta"
        ),
    }


def _interface_contract_symbol_maps(
    contract: Mapping[str, Any],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]]:
    actual_exports: dict[str, list[str]] = {}
    planned_exports: dict[str, list[str]] = {}
    consumed_symbols: dict[str, list[str]] = {}
    raw_modules = contract.get("modules")
    if isinstance(raw_modules, (list, tuple)):
        for raw_module in raw_modules:
            if not isinstance(raw_module, Mapping):
                continue
            path = str(raw_module.get("path") or "").strip()
            if not path:
                continue
            actual = _interface_string_list(raw_module.get("actual_public_symbols"))
            planned = _interface_string_list(raw_module.get("planned_public_symbols"))
            consumed = _interface_string_list(raw_module.get("consumed_symbols"))
            if actual:
                actual_exports[path] = actual
            if planned:
                planned_exports[path] = planned
            if consumed:
                consumed_symbols[path] = consumed
    for key, target in (
        ("exports", actual_exports),
        ("public_symbols", actual_exports),
        ("actual_public_symbols", actual_exports),
        ("planned_public_symbols", planned_exports),
        ("consumes", consumed_symbols),
        ("consumes_symbols", consumed_symbols),
        ("consumed_symbols", consumed_symbols),
    ):
        raw = contract.get(key)
        if isinstance(raw, Mapping):
            for raw_path, raw_symbols in raw.items():
                path = str(raw_path or "").strip()
                symbols = _interface_string_list(raw_symbols)
                if path and symbols:
                    target[path] = symbols
        elif raw:
            symbols = _interface_string_list(raw)
            if symbols:
                target.setdefault("<contract>", symbols)
    return actual_exports, planned_exports, consumed_symbols


def _diagnostic_paths(diagnostics: Sequence[Mapping[str, Any]]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        for key in ("path", "file", "file_path", "source_path", "target_path"):
            value = str(diagnostic.get(key) or "").strip()
            if value:
                paths.append(value)
    return list(_ordered_unique(paths))


def _diagnostic_codes(diagnostics: Sequence[Mapping[str, Any]]) -> list[str]:
    codes: list[str] = []
    for diagnostic in diagnostics:
        for key in ("code", "diagnostic_code", "error_code"):
            value = str(diagnostic.get(key) or "").strip()
            if value:
                codes.append(value)
    return list(_ordered_unique(codes))


_DIAGNOSTIC_SYMBOL_PATTERNS = (
    re.compile(r"has no exported member ['`\"](?P<symbol>[A-Za-z_$][\w$]*)['`\"]"),
    re.compile(r"no exported member ['`\"](?P<symbol>[A-Za-z_$][\w$]*)['`\"]"),
    re.compile(r"unresolved import symbol ['`\"](?P<symbol>[A-Za-z_$][\w$]*)['`\"]"),
    re.compile(r"undefined: (?P<symbol>[A-Za-z_][\w]*)"),
    re.compile(r"cannot find (?:name|symbol|type) ['`\"]?(?P<symbol>[A-Za-z_$][\w$]*)['`\"]?"),
    re.compile(r"no [`'\"](?P<symbol>[A-Za-z_$][\w$]*)[`'\"] in"),
)


def _diagnostic_symbols(diagnostics: Sequence[Mapping[str, Any]]) -> list[str]:
    symbols: list[str] = []
    for diagnostic in diagnostics:
        text = " ".join(str(diagnostic.get(key) or "") for key in ("message", "raw", "detail", "stderr", "diagnostic"))
        for pattern in _DIAGNOSTIC_SYMBOL_PATTERNS:
            for match in pattern.finditer(text):
                symbol = str(match.group("symbol") or "").strip()
                if symbol:
                    symbols.append(symbol)
    return list(_ordered_unique(symbols))


def _interface_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [str(key) for key in value if str(key or "").strip()]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if str(item or "").strip()]
    return []


def _to_public_convergence_result(
    command: RunDirectorRepairConvergenceCommandV1,
    internal_result: Any,
    *,
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
) -> DirectorRepairConvergenceResultV1:
    status = str(internal_result.status or "unknown").strip() or "unknown"
    ok = status in {"already_clean", "converged"}
    metadata = _public_convergence_metadata(
        command,
        internal_metadata=dict(internal_result.metadata),
        editor=editor,
        deleter=deleter,
    )
    return DirectorRepairConvergenceResultV1(
        ok=ok,
        converged=bool(internal_result.converged),
        status=status,
        final_diagnostics=tuple(_to_public_repair_diagnostic(item) for item in internal_result.final_diagnostics),
        receipts=tuple(_to_public_repair_receipt(item) for item in internal_result.receipts),
        rounds=tuple(_to_public_convergence_round(item) for item in internal_result.rounds),
        max_rounds=internal_result.max_rounds,
        metadata=metadata,
        error_code=None if ok else status,
        error_message=None if ok else f"Director repair convergence ended with status: {status}.",
    )


def _to_public_convergence_round(round_result: Any) -> DirectorRepairConvergenceRoundResultV1:
    evidence = round_result.revalidation_evidence.to_dict() if round_result.revalidation_evidence is not None else {}
    return DirectorRepairConvergenceRoundResultV1(
        round_number=round_result.round_number,
        status=round_result.status,
        schedule=round_result.schedule.to_dict(),
        diagnostics_before=tuple(_to_public_repair_diagnostic(item) for item in round_result.diagnostics_before),
        diagnostics_after=tuple(_to_public_repair_diagnostic(item) for item in round_result.diagnostics_after),
        receipts=tuple(_to_public_repair_receipt(item) for item in round_result.receipts),
        revalidation_evidence=evidence,
        metadata=round_result.metadata,
    )


def _failed_public_convergence_result(
    command: RunDirectorRepairConvergenceCommandV1,
    *,
    status: str,
    final_diagnostics: tuple[RepairDiagnostic, ...],
    error_code: str,
    error_message: str,
    metadata: Mapping[str, Any],
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
) -> DirectorRepairConvergenceResultV1:
    coverage_report = build_repair_coverage_report(final_diagnostics).to_dict()
    merged_metadata = _public_convergence_metadata(
        command,
        internal_metadata={
            "status": status,
            "converged": False,
            "unconverged": True,
            "coverage_report": coverage_report,
            "coverage_gap_count": coverage_report.get("coverage_gap_count", 0),
            "coverage_gaps": list(coverage_report.get("coverage_gaps") or []),
        },
        editor=editor,
        deleter=deleter,
        extra=metadata,
    )
    return DirectorRepairConvergenceResultV1(
        ok=False,
        converged=False,
        status=status,
        final_diagnostics=tuple(_to_public_repair_diagnostic(item) for item in final_diagnostics),
        max_rounds=command.max_rounds,
        metadata=merged_metadata,
        error_code=error_code,
        error_message=error_message,
    )


def _public_convergence_metadata(
    command: RunDirectorRepairConvergenceCommandV1,
    *,
    internal_metadata: Mapping[str, Any],
    editor: EditFileFn | None,
    deleter: DeleteFileFn | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    internal_payload = dict(internal_metadata or {})
    effect_boundary_labels = {
        "verifier": "adapter_supplied_verifier_callback_no_command_execution",
        "writer": "adapter_supplied_director_authorized_writer",
        "editor": "adapter_supplied_director_authorized_editor" if editor is not None else "editor_not_supplied",
        "deleter": "adapter_supplied_director_authorized_deleter" if deleter is not None else "deleter_not_supplied",
    }
    return {
        **internal_payload,
        "internal_convergence_metadata": internal_payload,
        "owner_cell": "director.runtime",
        "public_entrypoint": "run_director_repair_convergence",
        "preferred_internal_entrypoint": "run_runtime_repair_convergence",
        "effect_boundary": "adapter_supplied_verifier_callback_no_command_execution",
        "effect_boundary_labels": effect_boundary_labels,
        "callback_effect_boundary": effect_boundary_labels["verifier"],
        "writer_effect_boundary": effect_boundary_labels["writer"],
        "editor_effect_boundary": effect_boundary_labels["editor"],
        "deleter_effect_boundary": effect_boundary_labels["deleter"],
        "verifier_command_execution": "not_performed_by_public_runtime",
        "source_tools": list(command.source_tools),
        "command_metadata": dict(command.metadata),
        **dict(extra or {}),
    }


def _attach_native_revalidation_evidence(
    command: RunDirectorRepairCommandV1,
    receipt: RepairReceipt,
    revalidator: DirectorRepairRevalidatorFn,
) -> tuple[RepairReceipt, tuple[RepairDiagnostic, ...], str | None]:
    diagnostics_before = tuple(receipt.diagnostics or ())
    request = DirectorRepairRevalidationRequestV1(
        task_id=command.task_id,
        workspace=command.workspace,
        source_tool=command.source_tool,
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        files_changed=receipt.files_changed,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        diagnostics_before=tuple(diagnostic.to_dict() for diagnostic in diagnostics_before),
        metadata={
            "mode": receipt.mode,
            "status": receipt.status,
            "round_number": receipt.round_number,
        },
    )
    try:
        revalidation_input = revalidator(request)
    except Exception as exc:  # noqa: BLE001 - injected revalidator effect boundary must fail closed.
        return _attach_failed_native_revalidation(
            receipt,
            diagnostics_before,
            message=f"Repair revalidator failed: {type(exc).__name__}: {exc}",
            metadata={
                "revalidation_failure_reason": "revalidator_exception",
                "revalidator_error_type": type(exc).__name__,
                "revalidator_error": str(exc),
            },
        )

    if revalidation_input is None:
        return _attach_failed_native_revalidation(
            receipt,
            diagnostics_before,
            message="Repair revalidator returned no evidence.",
            metadata={"revalidation_failure_reason": "missing_revalidation_evidence"},
        )

    if not isinstance(revalidation_input, DirectorRepairRevalidationInputV1):
        return _attach_failed_native_revalidation(
            receipt,
            diagnostics_before,
            message="Repair revalidator returned invalid evidence type.",
            metadata={
                "revalidation_failure_reason": "invalid_revalidation_evidence_type",
                "revalidator_result_type": type(revalidation_input).__name__,
            },
        )

    diagnostics_after = _repair_diagnostics_from_quality_inputs(
        revalidation_input.residual_artifact_quality_errors,
        revalidation_input.residual_artifact_quality_issues,
    )
    if revalidation_input.exit_code is None:
        evidence = RepairRevalidationEvidence(
            command=revalidation_input.command,
            exit_code=1,
            diagnostics_before=diagnostics_before,
            diagnostics_after=diagnostics_after,
            errors_before_count=len(diagnostics_before),
            errors_after_count=len(diagnostics_after),
            round_number=receipt.round_number,
            raw_output_ref=revalidation_input.raw_output_ref,
            metadata={
                **dict(revalidation_input.metadata),
                "revalidation_failure_reason": "missing_revalidation_exit_code",
                "reported_exit_code": None,
            },
        )
        return (
            attach_revalidation_evidence(receipt, evidence),
            diagnostics_after,
            "Repair revalidation failed: missing verifier exit code.",
        )

    evidence = RepairRevalidationEvidence(
        command=revalidation_input.command,
        exit_code=revalidation_input.exit_code,
        diagnostics_before=diagnostics_before,
        diagnostics_after=diagnostics_after,
        errors_before_count=len(diagnostics_before),
        errors_after_count=len(diagnostics_after),
        round_number=receipt.round_number,
        raw_output_ref=revalidation_input.raw_output_ref,
        metadata=revalidation_input.metadata,
    )
    attached_receipt = attach_revalidation_evidence(receipt, evidence)
    if attached_receipt.status == "failed_revalidation":
        return attached_receipt, diagnostics_after, "Repair revalidation failed."
    return attached_receipt, diagnostics_after, None


def _attach_failed_native_revalidation(
    receipt: RepairReceipt,
    diagnostics_before: tuple[RepairDiagnostic, ...],
    *,
    message: str,
    metadata: Mapping[str, Any],
) -> tuple[RepairReceipt, tuple[RepairDiagnostic, ...], str]:
    diagnostics_after = diagnostics_before
    evidence = RepairRevalidationEvidence(
        command=(),
        exit_code=1,
        diagnostics_before=diagnostics_before,
        diagnostics_after=diagnostics_after,
        errors_before_count=len(diagnostics_before),
        errors_after_count=len(diagnostics_after),
        round_number=receipt.round_number,
        metadata=metadata,
    )
    return attach_revalidation_evidence(receipt, evidence), diagnostics_after, message


def _to_public_repair_planning_result(
    planning: RuntimeRepairPlanning,
    *,
    advisor_notes: Sequence[RepairAdvisoryV1] | None = None,
    public_advisor_notes: Sequence[RepairAdvisoryV1] | None = None,
    public_diagnostics: Sequence[RepairDiagnosticV1] | None = None,
) -> DirectorRepairPlanningResultV1:
    notes = tuple(public_advisor_notes if public_advisor_notes is not None else advisor_notes or ())
    if public_diagnostics is not None:
        diagnostics = tuple(public_diagnostics)
    else:
        diagnostics = tuple(_to_public_repair_diagnostic(item) for item in planning.diagnostics)
    if planning.plan is None:
        error_code = planning.error_code or "repair_not_planned"
        error_message = planning.error_message or (
            f"Registered repair source_tool={planning.source_tool!r} produced no effect plan."
        )
        return DirectorRepairPlanningResultV1(
            ok=False,
            planned=False,
            source_tool=planning.source_tool,
            diagnostic_count=len(diagnostics),
            diagnostics=diagnostics,
            advisor_notes=notes,
            error_code=error_code,
            error_message=error_message,
        )

    return DirectorRepairPlanningResultV1(
        ok=bool(planning.composition and planning.composition.ok),
        planned=True,
        source_tool=planning.plan.source_tool,
        diagnostic_count=len(diagnostics),
        diagnostics=diagnostics,
        plan_summary=_to_public_repair_plan_summary(planning.plan, advisor_note_count=len(notes)),
        composition_summary=_to_public_repair_composition_summary(planning.composition),
        effect_plan=_to_public_repair_effect_plan(planning.plan, planning.composition),
        advisor_notes=notes,
    )


def _repair_effect_call_id(*, plan_id: str, operation_id: str, ordinal: int, contingency_kind: str) -> str:
    identity = sha256_text(f"director-repair-effect-v1|{plan_id}|{operation_id}|{ordinal}|{contingency_kind}")
    return f"repair-effect-{identity[:24]}"


def _repair_effect_operation_id(*, plan_id: str, path: str, operation_ids: Sequence[str]) -> str:
    joined = "|".join(str(item) for item in operation_ids)
    return f"repair-patch-{sha256_text(f'{plan_id}|{path}|{joined}')[:24]}"


def _to_public_repair_effect_plan(
    plan: RepairPlan,
    composition: CompositionResult | None,
) -> DirectorRepairEffectPlanV1 | None:
    """Project one immutable forward/rollback plan before any Director tool runs."""

    if composition is None or not composition.ok:
        return None

    forward_effects: list[DirectorRepairEffectV1] = []
    rollback_effects: list[DirectorRepairEffectV1] = []
    for patch in composition.patches:
        if patch.before_hash == patch.after_hash and patch.exists_before == patch.exists_after:
            continue
        text_operations = _text_replace_operations_for_patch(plan.operations, patch.path)
        use_precise_editor = bool(
            patch.exists_before
            and patch.exists_after
            and len(text_operations) == 1
            # Empty-search insertions are not a safe ``edit_file`` contract.
            # The Director execution layer deliberately treats them as
            # recoverable no-ops (R195), so projecting one here would create a
            # covered/plannable repair that can never mutate.  Fall through to
            # the hash-bound whole-file write below for insertions.
            and str(text_operations[0].expected or "")
            and _can_apply_with_editor(patch.content_before, text_operations)
        )
        if use_precise_editor:
            current = patch.content_before
            for operation in text_operations:
                content_before_operation = current
                start = int(operation.span_start or 0)
                end = int(operation.span_end or 0)
                replacement = str(operation.replacement or "")
                content_after = content_before_operation[:start] + replacement + content_before_operation[end:]
                call_id = _repair_effect_call_id(
                    plan_id=plan.plan_id,
                    operation_id=operation.operation_id,
                    ordinal=len(forward_effects) + 1,
                    contingency_kind="forward",
                )
                forward_effects.append(
                    DirectorRepairEffectV1(
                        call_id=call_id,
                        operation_id=operation.operation_id,
                        tool_name="edit_file",
                        arguments=(
                            ("file", patch.path),
                            ("replace", replacement),
                            ("search", str(operation.expected or "")),
                        ),
                        contingency_kind="forward",
                        target_path=patch.path,
                        expected_before_hash=sha256_text(current),
                        expected_after_hash=sha256_text(content_after),
                        exists_before=True,
                        exists_after=True,
                    )
                )
                rollback_effects.append(
                    DirectorRepairEffectV1(
                        call_id=_repair_effect_call_id(
                            plan_id=plan.plan_id,
                            operation_id=operation.operation_id,
                            ordinal=len(forward_effects),
                            contingency_kind="rollback",
                        ),
                        operation_id=f"rollback-{operation.operation_id}",
                        tool_name="write_file",
                        arguments=(("content", content_before_operation), ("file", patch.path)),
                        contingency_kind="rollback",
                        activates_after_call_id=call_id,
                        target_path=patch.path,
                        expected_before_hash=sha256_text(content_after),
                        expected_after_hash=sha256_text(content_before_operation),
                        exists_before=True,
                        exists_after=True,
                    )
                )
                current = content_after
            continue

        operation_id = _repair_effect_operation_id(
            plan_id=plan.plan_id,
            path=patch.path,
            operation_ids=patch.operation_ids,
        )
        call_id = _repair_effect_call_id(
            plan_id=plan.plan_id,
            operation_id=operation_id,
            ordinal=len(forward_effects) + 1,
            contingency_kind="forward",
        )
        forward_arguments: DirectedEffectImmutableItemsV1
        forward_tool: DirectorRepairEffectToolNameV1
        if patch.exists_after:
            forward_tool = "write_file"
            forward_arguments = (("content", patch.content_after), ("file", patch.path))
        else:
            forward_tool = "delete_file"
            forward_arguments = (("file", patch.path),)
        forward_effects.append(
            DirectorRepairEffectV1(
                call_id=call_id,
                operation_id=operation_id,
                tool_name=forward_tool,
                arguments=forward_arguments,
                contingency_kind="forward",
                target_path=patch.path,
                expected_before_hash=(patch.before_hash if patch.exists_before else sha256_text(FILE_ABSENT_HASH)),
                expected_after_hash=(patch.after_hash if patch.exists_after else sha256_text(FILE_ABSENT_HASH)),
                exists_before=bool(patch.exists_before),
                exists_after=bool(patch.exists_after),
            )
        )
        rollback_arguments: DirectedEffectImmutableItemsV1
        rollback_tool: DirectorRepairEffectToolNameV1
        if patch.exists_before:
            rollback_tool = "write_file"
            rollback_arguments = (("content", patch.content_before), ("file", patch.path))
        else:
            rollback_tool = "delete_file"
            rollback_arguments = (("file", patch.path),)
        rollback_effects.append(
            DirectorRepairEffectV1(
                call_id=_repair_effect_call_id(
                    plan_id=plan.plan_id,
                    operation_id=operation_id,
                    ordinal=len(forward_effects),
                    contingency_kind="rollback",
                ),
                operation_id=f"rollback-{operation_id}",
                tool_name=rollback_tool,
                arguments=rollback_arguments,
                contingency_kind="rollback",
                activates_after_call_id=call_id,
                target_path=patch.path,
                expected_before_hash=(patch.after_hash if patch.exists_after else sha256_text(FILE_ABSENT_HASH)),
                expected_after_hash=(patch.before_hash if patch.exists_before else sha256_text(FILE_ABSENT_HASH)),
                exists_before=bool(patch.exists_after),
                exists_after=bool(patch.exists_before),
            )
        )

    return DirectorRepairEffectPlanV1(
        plan_id=plan.plan_id,
        source_tool=plan.source_tool,
        effects=tuple(forward_effects + rollback_effects),
        round_number=1,
    )


def _to_public_repair_plan_summary(
    plan: RepairPlan,
    *,
    advisor_note_count: int = 0,
) -> DirectorRepairPlanSummaryV1:
    return DirectorRepairPlanSummaryV1(
        plan_id=plan.plan_id,
        rule_id=plan.rule_id,
        source_tool=plan.source_tool,
        mode=plan.mode,
        risk_level=plan.risk_level,
        diagnostic_count=len(plan.diagnostics),
        operation_count=len(plan.operations),
        advisor_note_count=advisor_note_count,
    )


def _to_public_repair_composition_summary(
    composition: CompositionResult | None,
) -> DirectorRepairCompositionSummaryV1:
    if composition is None:
        return DirectorRepairCompositionSummaryV1(ok=False)
    return DirectorRepairCompositionSummaryV1(
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
    )


def _to_public_repair_diagnostic(diagnostic: RepairDiagnostic) -> RepairDiagnosticV1:
    metadata = dict(diagnostic.metadata)
    # RepairDiagnosticV1 intentionally keeps the compact public shape, so the
    # lossless fields required to round-trip a compiler/runtime diagnostic live
    # in metadata.  Dropping ``raw`` here forced cross-cell callers to choose
    # between reparsing display prose and forwarding generic gate wrappers;
    # both paths can hide an already-covered executable repair.
    for key, value in {
        "diagnostic_id": diagnostic.diagnostic_id,
        "raw": diagnostic.raw,
        "line": diagnostic.line,
        "column": diagnostic.column,
        "span_start": diagnostic.span_start,
        "span_end": diagnostic.span_end,
    }.items():
        metadata.setdefault(key, value)
    return RepairDiagnosticV1(
        source=diagnostic.source,
        code=diagnostic.code,
        message=diagnostic.message,
        path=diagnostic.path,
        severity=diagnostic.severity,
        metadata=metadata,
    )


def _to_internal_repair_diagnostic(diagnostic: RepairDiagnosticV1) -> RepairDiagnostic:
    metadata = dict(diagnostic.metadata)
    return RepairDiagnostic(
        source=diagnostic.source,
        code=diagnostic.code,
        message=diagnostic.message,
        severity=diagnostic.severity,
        path=diagnostic.path,
        line=_optional_int(metadata.get("line")),
        column=_optional_int(metadata.get("column")),
        span_start=_optional_int(metadata.get("span_start")),
        span_end=_optional_int(metadata.get("span_end")),
        diagnostic_id=str(metadata.get("diagnostic_id") or ""),
        raw=str(metadata.get("raw") or diagnostic.message),
        metadata=metadata,
    )


def _public_repair_diagnostics_from_command(
    command: PlanDirectorRepairCommandV1 | RunDirectorRepairCommandV1,
) -> tuple[RepairDiagnosticV1, ...]:
    """Project command diagnostics without forcing typed evidence through strings."""

    if command.diagnostics:
        return tuple(command.diagnostics)
    if command.artifact_quality_issues:
        return normalize_director_repair_issue_diagnostics(command.artifact_quality_issues)
    return tuple(
        _to_public_repair_diagnostic(diagnostic)
        for diagnostic in normalize_artifact_quality_errors(list(_artifact_quality_errors_from_command(command)))
    )


def _artifact_quality_errors_from_command(
    command: PlanDirectorRepairCommandV1 | RunDirectorRepairCommandV1,
) -> tuple[str, ...]:
    return tuple(str(item) for item in command.artifact_quality_errors if str(item or "").strip())


def _runtime_artifact_quality_errors_from_command(
    command: PlanDirectorRepairCommandV1 | RunDirectorRepairCommandV1,
    diagnostics: Sequence[RepairDiagnosticV1],
) -> tuple[str, ...]:
    if command.diagnostics and diagnostics and runtime_repair_binding_has_typed_planner(command.source_tool):
        return ()
    return _artifact_quality_errors_from_command(command)


def _public_receipt_to_internal(receipt: RepairReceiptV1) -> RepairReceipt:
    return RepairReceipt(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        rule_id=receipt.rule_id,
        source_tool=receipt.source_tool,
        status=receipt.status,
        mode=str(receipt.metadata.get("mode") or "commit"),
        authoritative=receipt.authoritative,
        files_changed=receipt.files_changed,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        round_number=receipt.round_number,
        revalidation_evidence=_public_revalidation_evidence_to_internal(receipt),
        metadata=receipt.metadata,
    )


def _public_revalidation_evidence_to_internal(receipt: RepairReceiptV1) -> RepairRevalidationEvidence | None:
    payload = dict(receipt.revalidation_evidence or {})
    if not payload and (
        receipt.verifier_command
        or receipt.verifier_exit_code is not None
        or receipt.diagnostics_before
        or receipt.diagnostics_after
        or receipt.resolved_diagnostic_ids
        or receipt.residual_diagnostic_ids
        or receipt.errors_before is not None
        or receipt.errors_after is not None
    ):
        payload = {
            "command": list(receipt.verifier_command),
            "exit_code": receipt.verifier_exit_code,
            "diagnostics_before": [dict(item) for item in receipt.diagnostics_before],
            "diagnostics_after": [dict(item) for item in receipt.diagnostics_after],
            "errors_before": receipt.errors_before,
            "errors_after": receipt.errors_after,
            "net_error_reduction": receipt.net_error_reduction,
            "resolved_diagnostic_ids": list(receipt.resolved_diagnostic_ids),
            "residual_diagnostic_ids": list(receipt.residual_diagnostic_ids),
            "round_number": receipt.round_number,
            "evidence_status": receipt.evidence_status,
            "metadata": {},
        }
    if not payload:
        return None
    command = payload.get("command")
    return RepairRevalidationEvidence(
        command=tuple(str(item) for item in command) if isinstance(command, list | tuple) else (),
        exit_code=_optional_int(payload.get("exit_code")),
        diagnostics_before=_public_revalidation_diagnostics_to_internal(payload.get("diagnostics_before")),
        diagnostics_after=_public_revalidation_diagnostics_to_internal(payload.get("diagnostics_after")),
        errors_before_count=_optional_int(payload.get("errors_before")),
        errors_after_count=_optional_int(payload.get("errors_after")),
        resolved_diagnostic_ids=tuple(str(item) for item in payload.get("resolved_diagnostic_ids") or ()),
        residual_diagnostic_ids=tuple(str(item) for item in payload.get("residual_diagnostic_ids") or ()),
        round_number=_optional_int(payload.get("round_number")),
        raw_output_ref=str(payload.get("raw_output_ref") or "").strip() or None,
        metadata=dict(payload.get("metadata") or {}),
    )


def _public_revalidation_diagnostics_to_internal(value: object) -> tuple[RepairDiagnostic, ...]:
    if not isinstance(value, list | tuple):
        return ()
    diagnostics: list[RepairDiagnostic] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        diagnostics.append(
            RepairDiagnostic(
                source=str(item.get("source") or "public_revalidation"),
                code=str(item.get("code") or "unknown"),
                message=str(item.get("message") or item.get("raw") or ""),
                severity=str(item.get("severity") or "error"),
                path=str(item.get("path")) if item.get("path") else None,
                raw=str(item.get("raw") or item.get("message") or ""),
                diagnostic_id=str(item.get("diagnostic_id") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    return tuple(diagnostics)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


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
    revalidation_evidence = receipt.revalidation_evidence.to_dict() if receipt.revalidation_evidence is not None else {}
    return RepairReceiptV1(
        receipt_id=receipt.receipt_id,
        plan_id=receipt.plan_id,
        source_tool=receipt.source_tool,
        status=receipt.status,
        authoritative=receipt.authoritative,
        rule_id=receipt.rule_id,
        files_changed=receipt.files_changed,
        before_hashes=receipt.before_hashes,
        after_hashes=receipt.after_hashes,
        round_number=receipt.round_number,
        evidence_status=receipt.evidence_status,
        errors_before=receipt.errors_before,
        errors_after=receipt.errors_after,
        net_error_reduction=receipt.net_error_reduction,
        authority_hash=receipt.authority_hash(),
        projection_hash=receipt.projection_hash(),
        revalidation_evidence=revalidation_evidence,
        verifier_command=tuple(str(item) for item in revalidation_evidence.get("command") or ()),
        verifier_exit_code=_optional_int(revalidation_evidence.get("exit_code")),
        diagnostics_before=tuple(
            dict(item) for item in revalidation_evidence.get("diagnostics_before") or () if isinstance(item, Mapping)
        ),
        diagnostics_after=tuple(
            dict(item) for item in revalidation_evidence.get("diagnostics_after") or () if isinstance(item, Mapping)
        ),
        resolved_diagnostic_ids=tuple(str(item) for item in revalidation_evidence.get("resolved_diagnostic_ids") or ()),
        residual_diagnostic_ids=tuple(str(item) for item in revalidation_evidence.get("residual_diagnostic_ids") or ()),
        advisor_notes=advisor_notes,
        metadata=receipt.metadata,
    )


def _to_public_environment_refresh_requirement(
    requirement: Mapping[str, Any],
) -> DirectorRepairEnvironmentRefreshRequirementV1:
    return DirectorRepairEnvironmentRefreshRequirementV1(
        ecosystem=str(requirement.get("ecosystem") or ""),
        package_manager=str(requirement.get("package_manager") or ""),
        manifest=str(requirement.get("manifest") or ""),
        lockfile=str(requirement.get("lockfile") or ""),
        command=tuple(str(item) for item in requirement.get("command") or ()),
        reason=str(requirement.get("reason") or "manifest_changed_before_revalidation"),
        receipt_id=str(requirement.get("receipt_id") or ""),
        manifest_after_hash=str(requirement.get("manifest_after_hash") or ""),
        lockfile_after_hash=str(requirement.get("lockfile_after_hash") or ""),
        freshness_key=str(requirement.get("freshness_key") or ""),
    )


def _to_public_environment_prep_plan(plan: Mapping[str, Any]) -> DirectorRepairEnvironmentPrepPlanV1:
    return DirectorRepairEnvironmentPrepPlanV1(
        plan_id=str(plan.get("plan_id") or ""),
        ecosystem=str(plan.get("ecosystem") or ""),
        package_manager=str(plan.get("package_manager") or ""),
        manifest=str(plan.get("manifest") or ""),
        lockfile=str(plan.get("lockfile") or ""),
        command=tuple(str(item) for item in plan.get("command") or ()),
        cwd=str(plan.get("cwd") or "."),
        timeout_seconds=int(plan.get("timeout_seconds") or 120),
        freshness_key=str(plan.get("freshness_key") or ""),
        source_receipt_id=str(plan.get("source_receipt_id") or ""),
        policy=dict(plan.get("policy") or {}),
        metadata=dict(plan.get("metadata") or {}),
        requirement=dict(plan.get("requirement") or {}),
    )


def _environment_prep_receipts_from_public_repair_receipts(
    receipts: Sequence[RepairReceiptV1],
) -> tuple[Mapping[str, Any], ...]:
    prep_receipts: list[Mapping[str, Any]] = []
    for receipt in receipts:
        evidence = dict(receipt.revalidation_evidence or {})
        metadata = dict(evidence.get("metadata") or {})
        raw_receipts = metadata.get("environment_prep_receipts") or ()
        if not isinstance(raw_receipts, Sequence) or isinstance(raw_receipts, str | bytes):
            continue
        prep_receipts.extend(dict(item) for item in raw_receipts if isinstance(item, Mapping))
    return tuple(prep_receipts)
