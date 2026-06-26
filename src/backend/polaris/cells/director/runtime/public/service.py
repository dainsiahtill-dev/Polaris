"""Public read services for the `director.runtime` cell."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    CompositionResult,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairPlan,
    RepairReceipt,
    RepairRevalidationEvidence,
    sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.diagnostics import normalize_artifact_quality_errors
from polaris.cells.director.runtime.internal.repair_kernel.legacy_bridge import (
    build_legacy_repair_kernel_summary as _build_legacy_repair_kernel_summary,
    summarize_repair_revalidation_coverage,
)
from polaris.cells.director.runtime.internal.repair_kernel.receipts import attach_revalidation_evidence
from polaris.cells.director.runtime.internal.repair_kernel.registry import (
    build_repair_coverage_report,
    default_repair_rule_registry,
    repair_language_slots,
)
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    RuntimeRepairPlanning,
    plan_runtime_repair,
    run_runtime_repair,
    run_runtime_repair_convergence,
    runtime_repair_bindings,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.schedule_catalog import (
    DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
    MaterializationQualityRepairScheduleStep,
    PostExecutionRepairScheduleStep,
    materialization_quality_repair_schedule,
    post_execution_repair_schedule,
    run_materialization_quality_repair_schedule_callbacks,
    run_post_execution_repair_schedule_callbacks,
)
from polaris.cells.director.runtime.internal.repair_kernel.scheduler import RepairVerifierSnapshot
from polaris.cells.director.runtime.internal.repair_kernel.shadow import compare_legacy_and_kernel_repairs
from polaris.cells.director.runtime.internal.repair_kernel.strategy_catalog import (
    deterministic_repair_strategy_catalog as _deterministic_repair_strategy_catalog,
)
from polaris.cells.director.runtime.public.contracts import (
    AttachDirectorRepairRevalidationEvidenceV1,
    CompareDirectorRepairShadowRunV1,
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairAdvisoryValidationResultV1,
    DirectorRepairCompositionIssueV1,
    DirectorRepairCompositionSummaryV1,
    DirectorRepairConvergenceResultV1,
    DirectorRepairConvergenceRoundResultV1,
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairDiagnosticCoverageV1,
    DirectorRepairKernelSummaryProjectionResultV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairMaterializationQualityScheduleResultV1,
    DirectorRepairMaterializationQualityScheduleRunResultV1,
    DirectorRepairMaterializationQualityStepV1,
    DirectorRepairPatchSummaryV1,
    DirectorRepairPlanningResultV1,
    DirectorRepairPlanSummaryV1,
    DirectorRepairPostExecutionScheduleResultV1,
    DirectorRepairPostExecutionScheduleRunResultV1,
    DirectorRepairPostExecutionStepV1,
    DirectorRepairResultV1,
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationProjectionResultV1,
    DirectorRepairRevalidationRequestV1,
    DirectorRepairShadowComparisonResultV1,
    DirectorRepairStrategyCatalogResultV1,
    DirectorRepairVerifierSnapshotInputV1,
    PlanDirectorRepairCommandV1,
    ProjectDirectorRepairKernelSummaryV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    RunDirectorRepairConvergenceCommandV1,
)

WriteFileFn = Callable[[str, str], Mapping[str, Any]]
EditFileFn = Callable[[Any], Mapping[str, Any]]
DeleteFileFn = Callable[[str], Mapping[str, Any]]
PostExecutionStepRunnerV1 = Callable[[DirectorRepairPostExecutionStepV1], Sequence[Mapping[str, Any]]]
MaterializationQualityStepRunnerV1 = Callable[[DirectorRepairMaterializationQualityStepV1], Sequence[Mapping[str, Any]]]
DirectorRepairRevalidatorFn = Callable[
    [DirectorRepairRevalidationRequestV1],
    DirectorRepairRevalidationInputV1 | None,
]
DirectorRepairConvergenceVerifierFn = Callable[
    [DirectorRepairConvergenceVerifierRequestV1],
    DirectorRepairVerifierSnapshotInputV1,
]

_ALLOWED_CONVERGENCE_VERIFIER_EVIDENCE_SOURCES = frozenset(
    {
        "adapter_convergence_verifier_factory",
    }
)
_DELETE_FILE_REQUIRES_POLICY_GATED_DELETER = "delete_file_requires_policy_gated_deleter"


class _PublicConvergenceVerifierError(RuntimeError):
    """Fail-closed wrapper for adapter-supplied convergence verifier callbacks."""

    def __init__(
        self,
        message: str,
        *,
        metadata: Mapping[str, Any],
        status: str = "verifier_callback_failed",
        error_code: str = "verifier_callback_failed",
    ) -> None:
        super().__init__(message)
        self.metadata = dict(metadata)
        self.status = status
        self.error_code = error_code


def _stable_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _count_by_key(items: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _repair_execution_error_code(error: Any) -> str | None:
    normalized = str(error or "").strip()
    if "requires policy-gated deleter" in normalized:
        return _DELETE_FILE_REQUIRES_POLICY_GATED_DELETER
    return None


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
        "evidence_status": receipt.get("evidence_status"),
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
            "evidence_status": receipt.get("evidence_status", "missing_evidence"),
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
    raw_items = _deterministic_repair_strategy_catalog()
    runtime_source_tools = set(runtime_repair_source_tools())
    all_items = [
        {
            **dict(item),
            "implementation_status": "executable_runtime"
            if str(item.get("source_tool") or "") in runtime_source_tools
            else "legacy_strategy_host",
            "execution_owner": "director.runtime"
            if str(item.get("source_tool") or "") in runtime_source_tools
            else "roles.adapters.legacy_strategy_host",
            "bench_driven_migration_required": str(item.get("source_tool") or "") not in runtime_source_tools,
        }
        for item in raw_items
    ]
    visible_items = all_items[: request.max_items] if request.include_items else []
    runtime_bindings = [dict(item) for item in runtime_repair_bindings()]
    legacy_strategy_host_source_tools = [
        str(item.get("source_tool") or "")
        for item in all_items
        if item["implementation_status"] == "legacy_strategy_host"
    ]
    summary: dict[str, Any] = {
        "total": len(all_items),
        "returned": len(visible_items),
        "by_language": _count_by_key(all_items, "language"),
        "by_phase": _count_by_key(all_items, "phase"),
        "by_concern": _count_by_key(all_items, "concern"),
        "by_risk": _count_by_key(all_items, "risk_level"),
        "implementation_status_counts": _count_by_key(all_items, "implementation_status"),
        "executable_runtime_binding_count": len(runtime_bindings),
        "executable_runtime_source_tools": sorted(runtime_source_tools),
        "executable_runtime_bindings": runtime_bindings,
        "executable_runtime_by_language": _count_by_key(runtime_bindings, "language"),
        "legacy_strategy_host_count": len(legacy_strategy_host_source_tools),
        "legacy_strategy_host_source_tools": legacy_strategy_host_source_tools,
        "legacy_strategy_host_owner": "roles.adapters.internal.director.deterministic_repairs",
        "migration_target_owner": "director.runtime.repair_kernel",
        "bench_driven_migration_required": bool(legacy_strategy_host_source_tools),
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
            summary=_repair_advisory_validation_summary(accepted_suggested_rule_count=0),
        )
    normalized = advisory.to_dict()
    return DirectorRepairAdvisoryValidationResultV1(
        schema_version="director.repair_advisory_validation.v1",
        source="director.runtime.repair_kernel.advisory_policy",
        access="read_only",
        ok=True,
        normalized_advisory=normalized,
        summary=_repair_advisory_validation_summary(
            accepted_suggested_rule_count=len(normalized.get("suggested_rules", [])),
        ),
    )


def _repair_advisory_validation_summary(*, accepted_suggested_rule_count: int) -> dict[str, Any]:
    return {
        "advisory_only": True,
        "accepted_suggested_rule_count": max(0, int(accepted_suggested_rule_count)),
        "director_runtime_remains_authoritative": True,
        "agi_execution_authority": False,
        "writes_allowed": False,
        "registration_allowed": False,
        "authoritative_receipts_allowed": False,
        "suggested_rules_are_advisory_only": True,
    }


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
        legacy_source_tools=tuple(payload["legacy_source_tools"]),
        kernel_source_tools=tuple(payload["kernel_source_tools"]),
        legacy_paths=tuple(payload["legacy_paths"]),
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


def _shadow_cutover_readiness(command: CompareDirectorRepairShadowRunV1, *, matched: bool) -> dict[str, Any]:
    blockers: list[str] = []
    independent_shadow_satisfied = command.comparison_mode == "independent_shadow_run"
    if not independent_shadow_satisfied:
        blockers.append("independent_shadow_required")
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
    coverage_gaps_by_id = {
        str(gap.get("diagnostic_id") or ""): dict(gap)
        for gap in report.coverage_gaps
        if str(gap.get("diagnostic_id") or "").strip()
    }
    return DirectorRepairCoverageReportV1(
        schema_version="director.repair_coverage_report.v1",
        source="director.runtime.repair_kernel.registry",
        access="read_only",
        total_diagnostics=report.total_diagnostics,
        covered_diagnostic_count=report.covered_diagnostic_count,
        uncovered_diagnostic_count=report.uncovered_diagnostic_count,
        executable_runtime_plan_diagnostic_count=report.executable_runtime_plan_diagnostic_count,
        metadata_only_diagnostic_count=report.metadata_only_diagnostic_count,
        items=tuple(_project_director_repair_diagnostic_coverage(item, coverage_gaps_by_id) for item in report.items),
    )


def _project_director_repair_diagnostic_coverage(
    item: Any,
    coverage_gaps_by_id: Mapping[str, Mapping[str, Any]],
) -> DirectorRepairDiagnosticCoverageV1:
    coverage_payload = item.to_dict()
    diagnostic = dict(coverage_payload["diagnostic"])
    gap_payload = (
        dict(coverage_gaps_by_id.get(str(diagnostic.get("diagnostic_id") or ""), {}))
        if not item.known_rule_matched
        else {}
    )
    return DirectorRepairDiagnosticCoverageV1(
        diagnostic=diagnostic,
        known_rule_matched=item.known_rule_matched,
        executable_runtime_plan_matched=item.executable_runtime_plan_matched,
        metadata_only_match=item.metadata_only_match,
        matched_rule_ids=tuple(rule.rule_id for rule in item.matched_rules),
        matched_source_tools=tuple(rule.source_tool for rule in item.matched_rules),
        runtime_plan_rule_ids=tuple(rule.rule_id for rule in item.matched_rules if rule.runtime_plan_available),
        archetypes=tuple(sorted({rule.archetype.value for rule in item.matched_rules})),
        phases=tuple(sorted({rule.phase for rule in item.matched_rules})),
        languages=tuple(sorted({rule.language for rule in item.matched_rules})),
        language=str(coverage_payload["language"]),
        diagnostic_archetype=str(coverage_payload["diagnostic_archetype"]),
        diagnostic_phase=str(coverage_payload["diagnostic_phase"]),
        diagnostic_language=str(coverage_payload["diagnostic_language"]),
        diagnostic_code=str(gap_payload.get("diagnostic_code") or diagnostic.get("code") or "unknown"),
        archetype_suggestion=str(coverage_payload["archetype_suggestion"]),
        phase_suggestion=str(coverage_payload["phase_suggestion"]),
        suggested_rule_family=str(coverage_payload["suggested_rule_family"]),
        reserved_slot_available=bool(coverage_payload.get("reserved_slot_available")),
        slot_status=str(coverage_payload.get("slot_status") or "reserved_slot_missing"),
        reserved_language_slot_matched=bool(gap_payload.get("reserved_language_slot_matched")),
        reserved_language_slot=dict(gap_payload.get("reserved_language_slot") or {}),
        reserved_repairer_module=str(gap_payload.get("reserved_repairer_module") or ""),
        reserved_slot_registration_policy=str(gap_payload.get("reserved_slot_registration_policy") or ""),
        recommended_next_owner=str(gap_payload.get("recommended_next_owner") or ""),
        recommended_route=str(coverage_payload.get("recommended_route") or gap_payload.get("recommended_route") or ""),
        handoff_recommendation=str(gap_payload.get("handoff_recommendation") or ""),
        llm_advisory_recommended=bool(gap_payload.get("llm_advisory_recommended")),
        agi_advisory_recommended=bool(gap_payload.get("agi_advisory_recommended")),
        authoritative_rule_registration_allowed=bool(gap_payload.get("authoritative_rule_registration_allowed")),
        recommended_registration_path=str(gap_payload.get("recommended_registration_path") or ""),
        coverage_status=str(coverage_payload.get("coverage_status") or "coverage_gap"),
    )


def query_director_repair_language_slots(
    query: QueryDirectorRepairLanguageSlotsV1 | None = None,
) -> DirectorRepairLanguageSlotsResultV1:
    """Return read-only future language extension slots for deterministic repairs."""

    request = query or QueryDirectorRepairLanguageSlotsV1()
    slots = repair_language_slots()
    slot_languages = {slot.language for slot in slots}
    rules = default_repair_rule_registry().rules()
    authoritative_source_tools_by_language: dict[str, list[str]] = {}
    for rule in rules:
        if rule.language not in slot_languages:
            continue
        authoritative_source_tools_by_language.setdefault(rule.language, []).append(rule.source_tool)
    runtime_source_tools_by_language: dict[str, list[str]] = {}
    for binding in runtime_repair_bindings():
        language = str(binding["language"])
        if language not in slot_languages:
            continue
        runtime_source_tools_by_language.setdefault(language, []).append(str(binding["source_tool"]))

    def _implementation_status(language: str) -> str:
        if runtime_source_tools_by_language.get(language):
            return "executable_runtime"
        if authoritative_source_tools_by_language.get(language):
            return "metadata_rule_registered"
        return "reserved_only"

    def _slot_next_action(language: str) -> str:
        status = _implementation_status(language)
        if status == "executable_runtime":
            return "extend_existing_runtime_rule_with_bench_evidence"
        if status == "metadata_rule_registered":
            return "promote_metadata_rule_to_executable_runtime_binding"
        return "add_bench_verified_rule_metadata_then_runtime_binding"

    items = (
        tuple(
            DirectorRepairLanguageSlotV1(
                language=slot.language,
                aliases=slot.aliases,
                file_extensions=slot.file_extensions,
                file_names=slot.file_names,
                diagnostic_sources=slot.diagnostic_sources,
                preferred_archetypes=tuple(archetype.value for archetype in slot.preferred_archetypes),
                repairer_module=slot.repairer_module,
                implementation_status=_implementation_status(slot.language),
                registration_policy=slot.registration_policy,
                authoritative_source_tools=tuple(sorted(authoritative_source_tools_by_language.get(slot.language, ()))),
                executable_runtime_source_tools=tuple(sorted(runtime_source_tools_by_language.get(slot.language, ()))),
                notes=slot.notes,
                slot_owner_cell="director.runtime",
                bench_evidence_required=True,
                rule_authoring_status=_implementation_status(slot.language),
                next_action=_slot_next_action(slot.language),
            )
            for slot in slots
        )
        if request.include_items
        else ()
    )
    archetypes = sorted({archetype.value for slot in slots for archetype in slot.preferred_archetypes})
    extensions = sorted({extension for slot in slots for extension in slot.file_extensions})
    file_names = sorted({file_name for slot in slots for file_name in slot.file_names})
    rule_languages = sorted(authoritative_source_tools_by_language)
    runtime_languages = sorted(runtime_source_tools_by_language)
    reserved_only_languages = sorted({slot.language for slot in slots} - set(rule_languages))
    implementation_status_by_language = {slot.language: _implementation_status(slot.language) for slot in slots}
    implementation_status_counts = _count_by_key(
        [{"implementation_status": status} for status in implementation_status_by_language.values()],
        "implementation_status",
    )
    repairer_modules = {slot.language: slot.repairer_module for slot in slots}
    next_actions_by_language = {slot.language: _slot_next_action(slot.language) for slot in slots}
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
            "file_names": file_names,
            "preferred_archetypes": archetypes,
            "authoritative_rule_languages": rule_languages,
            "authoritative_rule_language_count": len(rule_languages),
            "executable_runtime_languages": runtime_languages,
            "executable_runtime_language_count": len(runtime_languages),
            "reserved_only_languages": reserved_only_languages,
            "reserved_only_language_count": len(reserved_only_languages),
            "implementation_status_by_language": implementation_status_by_language,
            "implementation_status_counts": implementation_status_counts,
            "repairer_modules": repairer_modules,
            "next_actions_by_language": next_actions_by_language,
            "reserved_only_repairer_modules": {
                language: repairer_modules[language] for language in reserved_only_languages
            },
            "bench_driven_rule_addition_required": True,
        },
    )


def query_director_repair_post_execution_schedule(
    query: QueryDirectorRepairPostExecutionScheduleV1 | None = None,
) -> DirectorRepairPostExecutionScheduleResultV1:
    """Return the runtime-owned post-execution deterministic repair schedule."""

    request = query or QueryDirectorRepairPostExecutionScheduleV1()
    internal_steps = post_execution_repair_schedule()
    ordered_steps = tuple(_public_post_execution_step(step) for step in internal_steps)
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
            "default_max_rounds": DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
            "convergence_loop_owned_by": "director.runtime.repair_kernel.scheduler",
            "cycle_breaker": "repeated_round_fingerprint",
        },
    )


def run_director_post_execution_repair_schedule(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> tuple[list[dict[str, Any]], tuple[DirectorRepairPostExecutionStepV1, ...]]:
    """Run migration callbacks through the runtime-owned post-execution schedule."""

    result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
        max_rounds=max_rounds,
    )
    return [dict(item) for item in result.tool_results], result.ordered_steps


def run_director_post_execution_repair_schedule_result(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> DirectorRepairPostExecutionScheduleRunResultV1:
    """Run migration callbacks and expose runtime-owned summary projections."""

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=lambda step: runner(_public_post_execution_step(step)),
        max_rounds=max_rounds,
    )
    run_payload = run.to_dict()
    return DirectorRepairPostExecutionScheduleRunResultV1(
        schema_version="director.repair_post_execution_schedule_run_result.v1",
        source="director.runtime.repair_kernel.scheduler",
        ordered_steps=tuple(_public_post_execution_step(step) for step in run.ordered_steps),
        tool_results=tuple(dict(item) for item in run_payload["tool_results"]),
        receipt_projections=tuple(dict(item) for item in run_payload["receipt_projections"]),
        summary=dict(run_payload["summary"]),
        max_rounds=int(run_payload["max_rounds"]),
        rounds_run=int(run_payload["rounds_run"]),
        convergence_status=str(run_payload["convergence_status"]),
        stopped_reason=str(run_payload["stopped_reason"]),
    )


def query_director_repair_materialization_quality_schedule(
    query: QueryDirectorRepairMaterializationQualityScheduleV1 | None = None,
) -> DirectorRepairMaterializationQualityScheduleResultV1:
    """Return the runtime-owned materialization-quality deterministic repair schedule."""

    request = query or QueryDirectorRepairMaterializationQualityScheduleV1()
    internal_steps = materialization_quality_repair_schedule()
    ordered_steps = tuple(_public_materialization_quality_step(step) for step in internal_steps)
    languages = sorted({step.language for step in ordered_steps})
    phases = sorted({step.phase for step in ordered_steps})
    priorities = sorted({step.priority for step in ordered_steps})
    return DirectorRepairMaterializationQualityScheduleResultV1(
        schema_version="director.repair_materialization_quality_schedule.v1",
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
            "default_max_rounds": DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
            "convergence_loop_owned_by": "director.runtime.repair_kernel.scheduler",
            "cycle_breaker": "repeated_round_fingerprint",
        },
    )


def run_director_materialization_quality_repair_schedule(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> tuple[list[dict[str, Any]], tuple[DirectorRepairMaterializationQualityStepV1, ...]]:
    """Run materialization-quality callbacks through the runtime-owned schedule."""

    result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
        max_rounds=max_rounds,
    )
    return [dict(item) for item in result.tool_results], result.ordered_steps


def run_director_materialization_quality_repair_schedule_result(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunnerV1,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> DirectorRepairMaterializationQualityScheduleRunResultV1:
    """Run materialization callbacks and expose runtime-owned summary projections."""

    run = run_materialization_quality_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=lambda step: runner(_public_materialization_quality_step(step)),
        max_rounds=max_rounds,
    )
    run_payload = run.to_dict()
    return DirectorRepairMaterializationQualityScheduleRunResultV1(
        schema_version="director.repair_materialization_quality_schedule_run_result.v1",
        source="director.runtime.repair_kernel.scheduler",
        ordered_steps=tuple(_public_materialization_quality_step(step) for step in run.ordered_steps),
        tool_results=tuple(dict(item) for item in run_payload["tool_results"]),
        receipt_projections=tuple(dict(item) for item in run_payload["receipt_projections"]),
        summary=dict(run_payload["summary"]),
        max_rounds=int(run_payload["max_rounds"]),
        rounds_run=int(run_payload["rounds_run"]),
        convergence_status=str(run_payload["convergence_status"]),
        stopped_reason=str(run_payload["stopped_reason"]),
    )


def _public_post_execution_step(step: PostExecutionRepairScheduleStep) -> DirectorRepairPostExecutionStepV1:
    return DirectorRepairPostExecutionStepV1(
        step_id=step.step_id,
        language=step.language,
        phase=step.phase,
        priority=step.priority,
        source_tool=step.source_tool,
        depends_on=step.depends_on,
    )


def _public_materialization_quality_step(
    step: MaterializationQualityRepairScheduleStep,
) -> DirectorRepairMaterializationQualityStepV1:
    return DirectorRepairMaterializationQualityStepV1(
        step_id=step.step_id,
        language=step.language,
        phase=step.phase,
        priority=step.priority,
        source_tool=step.source_tool,
        depends_on=step.depends_on,
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


def plan_director_repair(command: PlanDirectorRepairCommandV1) -> DirectorRepairPlanningResultV1:
    """Plan a deterministic repair through the generic public runtime surface."""

    public_advisor_notes = tuple(command.advisor_notes or ())
    planning = plan_runtime_repair(
        source_tool=command.source_tool,
        base_files=command.base_files,
        artifact_quality_errors=_artifact_quality_errors_from_command(command),
        advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
        mode=command.mode,
    )
    return _to_public_repair_planning_result(planning, public_advisor_notes=public_advisor_notes)


def run_director_repair(
    command: RunDirectorRepairCommandV1,
    *,
    writer: WriteFileFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
    revalidator: DirectorRepairRevalidatorFn | None = None,
) -> DirectorRepairResultV1:
    """Run a deterministic repair through the generic public runtime surface."""

    public_advisor_notes = tuple(command.advisor_notes or ())
    internal_run = run_runtime_repair(
        source_tool=command.source_tool,
        workspace=command.workspace,
        base_files=command.base_files,
        artifact_quality_errors=_artifact_quality_errors_from_command(command),
        writer=writer,
        editor=editor,
        deleter=deleter,
        allowed_paths=command.allowed_paths,
        advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
        mode=command.mode,
    )
    planning_result = _to_public_repair_planning_result(
        internal_run.planning,
        advisor_notes=public_advisor_notes,
    )
    metadata: dict[str, Any] = {"planning": planning_result.to_dict()}
    if internal_run.planning.error_code or internal_run.planning.error_message:
        metadata["planning_error"] = {
            "error_code": internal_run.planning.error_code,
            "error_message": internal_run.planning.error_message,
        }
    if internal_run.plan_decision is not None:
        metadata["plan_policy"] = internal_run.plan_decision.to_dict()
    if internal_run.composition_decision is not None:
        metadata["composition_policy"] = internal_run.composition_decision.to_dict()
    if internal_run.execution_result is not None:
        metadata["execution_error"] = internal_run.execution_result.error
        execution_error_code = _repair_execution_error_code(internal_run.execution_result.error)
        if execution_error_code is not None:
            metadata["execution_error_code"] = execution_error_code
        metadata["rolled_back"] = internal_run.execution_result.rolled_back

    if internal_run.execution_result is None:
        return DirectorRepairResultV1(
            ok=False,
            error_code=internal_run.error_code,
            error_message=internal_run.error_message,
            metadata=metadata,
        )

    internal_receipt = internal_run.execution_result.receipt
    residual_diagnostics: tuple[RepairDiagnostic, ...] = ()
    revalidation_error_message: str | None = None
    if revalidator is not None:
        internal_receipt, residual_diagnostics, revalidation_error_message = _attach_native_revalidation_evidence(
            command,
            internal_receipt,
            revalidator,
        )

    receipt = _to_public_repair_receipt(internal_receipt)
    revalidation_failed = revalidator is not None and receipt.status == "failed_revalidation"
    error_code = internal_run.error_code
    error_message = internal_run.error_message
    if revalidation_failed:
        error_code = "repair_revalidation_failed"
        error_message = revalidation_error_message or "Repair revalidation failed."
    return DirectorRepairResultV1(
        ok=bool(internal_run.execution_result.ok) and not revalidation_failed,
        receipts=(receipt,),
        residual_diagnostics=tuple(_to_public_repair_diagnostic(item) for item in residual_diagnostics),
        error_code=error_code,
        error_message=error_message,
        metadata=metadata,
    )


def run_director_repair_convergence(
    command: RunDirectorRepairConvergenceCommandV1,
    *,
    writer: WriteFileFn,
    verifier: DirectorRepairConvergenceVerifierFn,
    editor: EditFileFn | None = None,
    deleter: DeleteFileFn | None = None,
) -> DirectorRepairConvergenceResultV1:
    """Run typed Director Runtime repair convergence through the public surface.

    The verifier is an adapter-supplied effect boundary. This function only
    converts the callback result into the internal verifier snapshot; it never
    runs verifier commands itself.
    """

    public_advisor_notes = tuple(command.advisor_notes or ())
    initial_diagnostics = tuple(normalize_artifact_quality_errors(list(command.artifact_quality_errors)))

    def _verifier(round_number: int, receipts: tuple[RepairReceipt, ...]) -> RepairVerifierSnapshot:
        public_receipts = tuple(_to_public_repair_receipt(receipt) for receipt in receipts)
        request = DirectorRepairConvergenceVerifierRequestV1(
            task_id=command.task_id,
            workspace=command.workspace,
            round_number=round_number,
            source_tools=command.source_tools,
            receipts=public_receipts,
            max_rounds=command.max_rounds,
            metadata={
                "public_entrypoint": "run_director_repair_convergence",
                "effect_boundary": "adapter_supplied_verifier_callback_no_command_execution",
                "command_metadata": dict(command.metadata),
            },
        )
        try:
            verifier_input = verifier(request)
        except Exception as exc:
            raise _PublicConvergenceVerifierError(
                f"Repair convergence verifier failed: {type(exc).__name__}: {exc}",
                metadata={
                    "verifier_failure_reason": "verifier_exception",
                    "verifier_error_type": type(exc).__name__,
                    "verifier_error": str(exc),
                    "round_number": round_number,
                },
            ) from exc

        if not isinstance(verifier_input, DirectorRepairVerifierSnapshotInputV1):
            raise _PublicConvergenceVerifierError(
                "Repair convergence verifier returned invalid evidence type.",
                metadata={
                    "verifier_failure_reason": "invalid_verifier_snapshot_type",
                    "verifier_result_type": type(verifier_input).__name__,
                    "round_number": round_number,
                },
            )

        _validate_public_convergence_verifier_evidence(verifier_input, round_number=round_number)
        diagnostics = tuple(normalize_artifact_quality_errors(list(verifier_input.residual_artifact_quality_errors)))
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=verifier_input.command,
            exit_code=verifier_input.exit_code,
            raw_output_ref=verifier_input.raw_output_ref,
            metadata={
                **dict(verifier_input.metadata),
                "public_entrypoint": "run_director_repair_convergence",
                "effect_boundary": "adapter_supplied_verifier_callback_no_command_execution",
                "round_number": round_number,
            },
        )

    try:
        internal_result = run_runtime_repair_convergence(
            source_tools=command.source_tools,
            workspace=command.workspace,
            base_files=command.base_files,
            artifact_quality_errors=command.artifact_quality_errors,
            verifier=_verifier,
            writer=writer,
            editor=editor,
            deleter=deleter,
            allowed_paths=command.allowed_paths,
            advisor_notes=_to_internal_advisor_notes(public_advisor_notes),
            mode=command.mode,
            max_rounds=command.max_rounds,
        )
    except _PublicConvergenceVerifierError as exc:
        return _failed_public_convergence_result(
            command,
            status=exc.status,
            final_diagnostics=initial_diagnostics,
            error_code=exc.error_code,
            error_message=str(exc),
            metadata=exc.metadata,
            editor=editor,
            deleter=deleter,
        )
    except Exception as exc:  # noqa: BLE001 - public convergence boundary must not pretend success on runtime errors.
        return _failed_public_convergence_result(
            command,
            status="convergence_runtime_error",
            final_diagnostics=initial_diagnostics,
            error_code="convergence_runtime_error",
            error_message=f"Director repair convergence failed: {type(exc).__name__}: {exc}",
            metadata={
                "runtime_failure_reason": "internal_convergence_exception",
                "runtime_error_type": type(exc).__name__,
                "runtime_error": str(exc),
                "runtime_error_code": _repair_execution_error_code(str(exc)),
            },
            editor=editor,
            deleter=deleter,
        )

    return _to_public_convergence_result(command, internal_result, editor=editor, deleter=deleter)


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

    diagnostics_after = normalize_artifact_quality_errors(list(revalidation_input.residual_artifact_quality_errors))
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
) -> DirectorRepairPlanningResultV1:
    notes = tuple(public_advisor_notes if public_advisor_notes is not None else advisor_notes or ())
    if planning.plan is None:
        return DirectorRepairPlanningResultV1(
            ok=False,
            planned=False,
            source_tool=planning.source_tool,
            diagnostic_count=len(planning.diagnostics),
            advisor_notes=notes,
            error_code=planning.error_code,
            error_message=planning.error_message,
        )

    return DirectorRepairPlanningResultV1(
        ok=bool(planning.composition and planning.composition.ok),
        planned=True,
        source_tool=planning.plan.source_tool,
        diagnostic_count=len(planning.plan.diagnostics),
        plan_summary=_to_public_repair_plan_summary(planning.plan, advisor_note_count=len(notes)),
        composition_summary=_to_public_repair_composition_summary(planning.composition),
        advisor_notes=notes,
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
    return RepairDiagnosticV1(
        source=diagnostic.source,
        code=diagnostic.code,
        message=diagnostic.message,
        path=diagnostic.path,
        severity=diagnostic.severity,
        metadata=diagnostic.metadata,
    )


def _artifact_quality_errors_from_command(
    command: PlanDirectorRepairCommandV1 | RunDirectorRepairCommandV1,
) -> tuple[str, ...]:
    artifact_errors = tuple(str(item) for item in command.artifact_quality_errors if str(item or "").strip())
    if artifact_errors:
        return artifact_errors
    return tuple(_artifact_quality_error_from_diagnostic(diagnostic) for diagnostic in command.diagnostics)


def _artifact_quality_error_from_diagnostic(diagnostic: Any) -> str:
    raw = str(getattr(diagnostic, "metadata", {}).get("raw") or "").strip()
    if raw:
        return raw
    path = str(getattr(diagnostic, "path", "") or "").strip()
    code = str(getattr(diagnostic, "code", "") or "").strip()
    message = str(getattr(diagnostic, "message", "") or "").strip()
    if path and code:
        return f"{path}: error {code}: {message}"
    if code:
        return f"error {code}: {message}"
    return message


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


__all__ = [
    "AttachDirectorRepairRevalidationEvidenceV1",
    "DirectorRepairConvergenceResultV1",
    "DirectorRepairConvergenceRoundResultV1",
    "DirectorRepairConvergenceVerifierFn",
    "DirectorRepairConvergenceVerifierRequestV1",
    "DirectorRepairKernelSummaryProjectionResultV1",
    "DirectorRepairMaterializationQualityScheduleResultV1",
    "DirectorRepairMaterializationQualityStepV1",
    "DirectorRepairPostExecutionScheduleResultV1",
    "DirectorRepairPostExecutionStepV1",
    "DirectorRepairRevalidationInputV1",
    "DirectorRepairRevalidationProjectionResultV1",
    "DirectorRepairRevalidationRequestV1",
    "DirectorRepairRevalidatorFn",
    "DirectorRepairVerifierSnapshotInputV1",
    "ProjectDirectorRepairKernelSummaryV1",
    "RunDirectorRepairConvergenceCommandV1",
    "attach_director_repair_revalidation_evidence",
    "build_director_repair_kernel_summary",
    "compare_director_repair_shadow_run",
    "plan_director_repair",
    "project_director_repair_kernel_summary",
    "project_director_repair_revalidation_evidence",
    "query_director_repair_advisory_policy",
    "query_director_repair_coverage",
    "query_director_repair_language_slots",
    "query_director_repair_materialization_quality_schedule",
    "query_director_repair_post_execution_schedule",
    "query_director_repair_strategy_catalog",
    "run_director_materialization_quality_repair_schedule",
    "run_director_post_execution_repair_schedule",
    "run_director_repair",
    "run_director_repair_convergence",
    "validate_director_repair_advisory",
]
