"""Director runtime public service — _core submodule."""

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
    sha256_text,
)
from polaris.cells.director.runtime.internal.repair_kernel.environment import (
    environment_prep_catalog_summary,
)
from polaris.cells.director.runtime.internal.repair_kernel.registry import (
    default_repair_rule_registry,
)
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    runtime_repair_bindings,
    runtime_repair_source_tools,
)
from polaris.cells.director.runtime.internal.repair_kernel.strategy_catalog import (
    deterministic_repair_strategy_catalog as _deterministic_repair_strategy_catalog,
)
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairAdvisoryValidationResultV1,
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairEnvironmentPrepCatalogResultV1,
    DirectorRepairMaterializationQualityStepV1,
    DirectorRepairMetricsResultV1,
    DirectorRepairPostExecutionStepV1,
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationRequestV1,
    DirectorRepairStrategyCatalogResultV1,
    DirectorRepairVerifierSnapshotInputV1,
    ProjectDirectorRepairMetricsV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairEnvironmentPrepCatalogV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
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


def _ordered_unique(items: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return tuple(values)


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
    metadata_registered_source_tools = {
        rule.source_tool for rule in default_repair_rule_registry().rules() if not rule.runtime_plan_available
    }

    def _implementation_status(source_tool: str) -> str:
        if source_tool in runtime_source_tools:
            return "executable_runtime"
        if source_tool in metadata_registered_source_tools:
            return "metadata_rule_registered"
        return "adapter_strategy_host"

    def _execution_owner(source_tool: str) -> str:
        if source_tool in runtime_source_tools or source_tool in metadata_registered_source_tools:
            return "director.runtime"
        return "roles.adapters.strategy_host"

    all_items = [
        {
            **dict(item),
            "implementation_status": _implementation_status(str(item.get("source_tool") or "")),
            "execution_owner": _execution_owner(str(item.get("source_tool") or "")),
            "bench_driven_migration_required": _implementation_status(str(item.get("source_tool") or ""))
            == "adapter_strategy_host",
        }
        for item in raw_items
    ]
    visible_items = all_items[: request.max_items] if request.include_items else []
    runtime_bindings = [dict(item) for item in runtime_repair_bindings()]
    adapter_strategy_host_source_tools = [
        str(item.get("source_tool") or "")
        for item in all_items
        if item["implementation_status"] == "adapter_strategy_host"
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
        "adapter_strategy_host_count": len(adapter_strategy_host_source_tools),
        "adapter_strategy_host_source_tools": adapter_strategy_host_source_tools,
        "adapter_strategy_host_owner": "roles.adapters.internal.director.deterministic_repairs",
        "migration_target_owner": "director.runtime.repair_kernel",
        "bench_driven_migration_required": bool(adapter_strategy_host_source_tools),
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


def query_director_repair_environment_prep_catalog(
    query: QueryDirectorRepairEnvironmentPrepCatalogV1 | None = None,
) -> DirectorRepairEnvironmentPrepCatalogResultV1:
    """Return the read-only runtime-owned environment prep command catalog."""

    request = query or QueryDirectorRepairEnvironmentPrepCatalogV1()
    summary = environment_prep_catalog_summary()
    items = tuple(dict(item) for item in summary.get("items") or ()) if request.include_items else ()
    summary_without_items = {key: value for key, value in summary.items() if key != "items"}
    return DirectorRepairEnvironmentPrepCatalogResultV1(
        items=items,
        summary=summary_without_items,
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


def project_director_repair_metrics(command: ProjectDirectorRepairMetricsV1) -> DirectorRepairMetricsResultV1:
    """Project repair kernel health metrics from existing receipts and reports."""

    receipts = tuple(command.receipts or ())
    receipt_count = len(receipts)
    applied_receipt_count = sum(1 for receipt in receipts if receipt.status == "applied")
    failed_receipt_count = sum(1 for receipt in receipts if receipt.status != "applied")
    ineffective_receipts = tuple(
        receipt
        for receipt in receipts
        if receipt.errors_before is not None
        and receipt.errors_after is not None
        and (receipt.net_error_reduction or 0) <= 0
    )
    schedule_rounds = tuple(
        int(summary.get("rounds_run") or 0)
        for summary in command.schedule_run_summaries
        if int(summary.get("rounds_run") or 0) > 0
    )
    coverage_reports = tuple(command.coverage_reports or ())
    uncovered_diagnostic_count = sum(report.uncovered_diagnostic_count for report in coverage_reports)
    coverage_gap_count = sum(len(report.to_dict().get("coverage_gaps") or []) for report in coverage_reports)
    return DirectorRepairMetricsResultV1(
        schema_version="director.repair_metrics.v1",
        source="director.runtime.repair_kernel.metrics",
        access="read_only",
        receipt_count=receipt_count,
        applied_receipt_count=applied_receipt_count,
        failed_receipt_count=failed_receipt_count,
        ineffective_receipt_count=len(ineffective_receipts),
        success_rate=(applied_receipt_count / receipt_count) if receipt_count else 0.0,
        average_convergence_rounds=(sum(schedule_rounds) / len(schedule_rounds)) if schedule_rounds else 0.0,
        uncovered_diagnostic_count=uncovered_diagnostic_count,
        coverage_gap_count=coverage_gap_count,
        metadata={
            "ineffective_receipt_ids": [receipt.receipt_id for receipt in ineffective_receipts],
            "failed_receipt_ids": [receipt.receipt_id for receipt in receipts if receipt.status != "applied"],
            "source_tools": sorted({receipt.source_tool for receipt in receipts if receipt.source_tool}),
            "schedule_rounds": list(schedule_rounds),
            "coverage_report_count": len(coverage_reports),
            "advisory_metrics_only": True,
            "agi_execution_authority": False,
        },
    )
