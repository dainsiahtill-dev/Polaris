"""Compatibility bridge from legacy deterministic repair results to receipts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import RepairDiagnostic, RepairReceipt, RepairRevalidationEvidence, stable_id
from .diagnostics import normalize_artifact_quality_errors
from .receipt_context import build_repair_receipt_context
from .registry import build_repair_coverage_report
from .shadow import compare_legacy_and_kernel_repairs
from .strategy_catalog import summarize_deterministic_repair_source_tools


def build_legacy_repair_kernel_summary(
    *,
    stage: str,
    tool_results: Sequence[dict[str, Any]],
    artifact_quality_errors: list[str] | None = None,
    mode: str = "commit",
) -> dict[str, Any]:
    """Build repair-kernel audit metadata for existing deterministic results."""

    diagnostics = normalize_artifact_quality_errors(list(artifact_quality_errors or []))
    receipts = _receipts_from_tool_results(stage=stage, tool_results=tool_results, diagnostics=diagnostics, mode=mode)
    coverage_report = build_repair_coverage_report(diagnostics)
    shadow_comparison = compare_legacy_and_kernel_repairs(
        legacy_tool_results=tool_results,
        kernel_receipts=receipts,
    )
    shadow_payload = _legacy_projection_shadow_payload(shadow_comparison.to_dict())
    revalidation_coverage = summarize_repair_revalidation_coverage(receipts)
    return {
        "version": 1,
        "stage": stage,
        "mode": mode,
        "authoritative": (
            mode == "commit"
            and bool(receipts)
            and revalidation_coverage["receipts_missing_revalidation"] == 0
            and revalidation_coverage["failed_revalidation_receipt_count"] == 0
        ),
        "requires_revalidation": revalidation_coverage["requires_revalidation"],
        "pending_revalidation_count": revalidation_coverage["pending_revalidation_count"],
        "receipts_with_revalidation": revalidation_coverage["receipts_with_revalidation"],
        "revalidation_coverage": revalidation_coverage,
        "receipt_count": len(receipts),
        "receipts": [receipt.to_dict() for receipt in receipts],
        "receipt_context": build_repair_receipt_context(receipts),
        "source_tool_profiles": summarize_deterministic_repair_source_tools(
            [receipt.source_tool for receipt in receipts]
        ),
        "coverage_report": coverage_report.to_dict(),
        "dark_launch_comparison": shadow_payload,
        "agi_advisory": {
            "supported": True,
            "active": False,
            "authoritative": False,
            "writes_allowed": False,
        },
    }


ReceiptLike = RepairReceipt | Mapping[str, Any]


def _legacy_projection_shadow_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Mark legacy receipt projection comparisons as non-cutover evidence."""

    comparison = dict(payload or {})
    metadata = dict(comparison.get("metadata") or {})
    metadata.update(
        {
            "comparison_mode": "legacy_projection_self_check",
            "cutover_ready": False,
            "cutover_blockers": ["independent_shadow_required"],
            "independent_shadow_required": True,
            "read_only": True,
            "writes_performed": False,
        }
    )
    comparison["metadata"] = metadata
    comparison["comparison_mode"] = "legacy_projection_self_check"
    comparison["cutover_ready"] = False
    comparison["cutover_blockers"] = ["independent_shadow_required"]
    comparison["independent_shadow_required"] = True
    comparison["independent_shadow_satisfied"] = False
    return comparison


def summarize_repair_revalidation_coverage(receipts: Sequence[ReceiptLike]) -> dict[str, Any]:
    """Summarize whether receipts have post-check evidence attached."""

    receipt_count = len(receipts)
    status_counts: dict[str, int] = {}
    missing_receipt_ids: list[str] = []
    missing_source_tools: set[str] = set()
    source_tools_with_revalidation: set[str] = set()
    receipts_with_revalidation = 0
    pending_revalidation_count = 0
    failed_receipt_count = 0
    authoritative_receipt_count = 0
    residual_diagnostic_receipt_count = 0
    failed_revalidation_receipt_count = 0
    failed_revalidation_receipt_ids: list[str] = []
    failed_revalidation_source_tools: set[str] = set()
    evidence_required_count = 0

    for receipt in receipts:
        status = _receipt_status(receipt)
        status_counts[status] = status_counts.get(status, 0) + 1
        source_tool = _receipt_source_tool(receipt)
        receipt_id = _receipt_id(receipt)
        evidence = _receipt_revalidation_evidence(receipt)
        has_evidence = bool(evidence)
        if has_evidence:
            receipts_with_revalidation += 1
            if source_tool:
                source_tools_with_revalidation.add(source_tool)
            metadata = evidence.get("metadata")
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            if evidence.get("residual_diagnostic_ids") or metadata_dict.get("residual_diagnostic_signatures"):
                residual_diagnostic_receipt_count += 1
            exit_code = _coerce_optional_int(evidence.get("exit_code"))
            if exit_code not in (None, 0):
                failed_revalidation_receipt_count += 1
                if receipt_id:
                    failed_revalidation_receipt_ids.append(receipt_id)
                if source_tool:
                    failed_revalidation_source_tools.add(source_tool)
        if status == "pending_revalidation":
            pending_revalidation_count += 1
        if status == "failed":
            failed_receipt_count += 1
        if bool(_receipt_value(receipt, "authoritative")):
            authoritative_receipt_count += 1
        evidence_required = _receipt_requires_revalidation(receipt, status=status, has_evidence=has_evidence)
        if evidence_required:
            evidence_required_count += 1
            missing_receipt_ids.append(receipt_id)
            if source_tool:
                missing_source_tools.add(source_tool)

    missing_receipt_ids = sorted(receipt_id for receipt_id in missing_receipt_ids if receipt_id)
    missing_source_tool_list = sorted(missing_source_tools)
    failed_revalidation_receipt_ids = sorted(receipt_id for receipt_id in failed_revalidation_receipt_ids if receipt_id)
    failed_revalidation_source_tool_list = sorted(failed_revalidation_source_tools)
    return {
        "receipt_count": receipt_count,
        "evidence_required_count": evidence_required_count,
        "receipts_with_revalidation": receipts_with_revalidation,
        "receipts_missing_revalidation": len(missing_receipt_ids),
        "pending_revalidation_count": pending_revalidation_count,
        "failed_receipt_count": failed_receipt_count,
        "authoritative_receipt_count": authoritative_receipt_count,
        "post_check_evidence_available": receipts_with_revalidation > 0,
        "post_check_evidence_complete": evidence_required_count == 0,
        "requires_revalidation": evidence_required_count > 0,
        "missing_revalidation_receipt_ids": missing_receipt_ids,
        "missing_revalidation_source_tools": missing_source_tool_list,
        "source_tools_with_revalidation": sorted(source_tools_with_revalidation),
        "source_tools_missing_revalidation": missing_source_tool_list,
        "status_counts": dict(sorted(status_counts.items())),
        "residual_diagnostic_receipt_count": residual_diagnostic_receipt_count,
        "failed_revalidation_receipt_count": failed_revalidation_receipt_count,
        "failed_revalidation_receipt_ids": failed_revalidation_receipt_ids,
        "failed_revalidation_source_tools": failed_revalidation_source_tool_list,
    }


def _receipt_value(receipt: ReceiptLike, key: str) -> Any:
    if isinstance(receipt, RepairReceipt):
        return getattr(receipt, key, None)
    return receipt.get(key)


def _receipt_status(receipt: ReceiptLike) -> str:
    return str(_receipt_value(receipt, "status") or "unknown").strip() or "unknown"


def _receipt_id(receipt: ReceiptLike) -> str:
    return str(_receipt_value(receipt, "receipt_id") or "").strip()


def _receipt_source_tool(receipt: ReceiptLike) -> str:
    return str(_receipt_value(receipt, "source_tool") or "").strip()


def _receipt_revalidation_evidence(receipt: ReceiptLike) -> dict[str, Any]:
    value = _receipt_value(receipt, "revalidation_evidence")
    if isinstance(value, RepairRevalidationEvidence):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _receipt_requires_revalidation(receipt: ReceiptLike, *, status: str, has_evidence: bool) -> bool:
    metadata = _receipt_value(receipt, "metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    if bool(metadata_dict.get("requires_revalidation")):
        return True
    return status == "pending_revalidation" or (status == "applied" and not has_evidence)


def _receipts_from_tool_results(
    *,
    stage: str,
    tool_results: Sequence[dict[str, Any]],
    diagnostics: tuple[RepairDiagnostic, ...],
    mode: str,
) -> list[RepairReceipt]:
    receipts: list[RepairReceipt] = []
    for index, item in enumerate(tool_results):
        result = item.get("result")
        result_payload = result if isinstance(result, dict) else {}
        embedded_receipts = _kernel_receipts_from_tool_result(
            stage=stage,
            item=item,
            result_payload=result_payload,
            diagnostics=diagnostics,
            mode=mode,
        )
        if embedded_receipts:
            receipts.extend(embedded_receipts)
            continue
        source_tool = str(result_payload.get("source_tool") or item.get("tool_name") or item.get("tool") or "")
        if not source_tool:
            source_tool = "deterministic_unknown_repair"
        file_path = str(result_payload.get("file") or "").strip()
        operation = str(result_payload.get("operation") or result_payload.get("action") or "modify")
        plan_id = stable_id("legacy_plan", stage, source_tool, file_path, operation, index)
        before_hash = str(result_payload.get("before_hash") or "")
        after_hash = str(result_payload.get("after_hash") or "")
        revalidation_evidence = _revalidation_evidence_from_payload(result_payload.get("revalidation"))
        round_number = _coerce_optional_int(result_payload.get("round_number"))
        if round_number is None and revalidation_evidence is not None:
            round_number = revalidation_evidence.round_number
        success = bool(item.get("success"))
        revalidation_failed = _revalidation_failed(revalidation_evidence)
        status = "failed"
        if success:
            if revalidation_evidence is None:
                status = "pending_revalidation"
            elif revalidation_failed:
                status = "failed_revalidation"
            else:
                status = "applied"
        receipts.append(
            RepairReceipt(
                plan_id=plan_id,
                rule_id=source_tool,
                source_tool=source_tool,
                status=status,
                mode=mode,
                authoritative=mode == "commit"
                and success
                and revalidation_evidence is not None
                and not revalidation_failed,
                files_changed=(file_path,) if file_path else (),
                operation_ids=(stable_id("legacy_op", plan_id, file_path, operation),),
                diagnostics=diagnostics,
                before_hashes={file_path: before_hash} if file_path and before_hash else {},
                after_hashes={file_path: after_hash} if file_path and after_hash else {},
                round_number=round_number,
                revalidation_evidence=revalidation_evidence,
                metadata={
                    "stage": stage,
                    "operation": operation,
                    "bytes_written": result_payload.get("bytes_written"),
                    "broadcast_ok": result_payload.get("broadcast_ok"),
                    "director_policy": result_payload.get("director_policy"),
                    "phase": result_payload.get("phase"),
                    "priority": result_payload.get("priority"),
                    "requires_revalidation": success and revalidation_evidence is None,
                },
            )
        )
    return receipts


def _kernel_receipts_from_tool_result(
    *,
    stage: str,
    item: dict[str, Any],
    result_payload: dict[str, Any],
    diagnostics: tuple[RepairDiagnostic, ...],
    mode: str,
) -> list[RepairReceipt]:
    kernel = result_payload.get("repair_kernel")
    if not isinstance(kernel, dict):
        return []
    receipts_payload = kernel.get("receipts")
    if isinstance(receipts_payload, list | tuple) and receipts_payload:
        receipts: list[RepairReceipt] = []
        for receipt_payload in receipts_payload:
            if not isinstance(receipt_payload, dict):
                continue
            receipt = _repair_receipt_from_payload(
                receipt_payload,
                fallback_stage=stage,
                fallback_result=result_payload,
                fallback_diagnostics=diagnostics,
                fallback_mode=mode,
                fallback_success=bool(item.get("success")),
            )
            if receipt is not None:
                receipts.append(receipt)
        return receipts
    if not kernel.get("receipt_id") and not kernel.get("plan_id"):
        return []
    source_tool = str(result_payload.get("source_tool") or item.get("tool_name") or item.get("tool") or "")
    if not source_tool:
        source_tool = "deterministic_unknown_repair"
    file_path = str(result_payload.get("file") or "").strip()
    operation = str(result_payload.get("operation") or result_payload.get("action") or "modify")
    success = bool(item.get("success"))
    status = str(kernel.get("status") or ("applied" if success else "failed"))
    execution_status = status
    before_hashes = _coerce_str_mapping(kernel.get("before_hashes"))
    after_hashes = _coerce_str_mapping(kernel.get("after_hashes"))
    if file_path and result_payload.get("before_hash") and file_path not in before_hashes:
        before_hashes[file_path] = str(result_payload.get("before_hash") or "")
    if file_path and result_payload.get("after_hash") and file_path not in after_hashes:
        after_hashes[file_path] = str(result_payload.get("after_hash") or "")
    revalidation_evidence = _revalidation_evidence_from_payload(result_payload.get("revalidation"))
    revalidation_failed = _revalidation_failed(revalidation_evidence)
    metadata = dict(kernel.get("metadata") or {})
    metadata.update(
        {
            "stage": stage,
            "operation": operation,
            "execution_status": execution_status,
            "bytes_written": result_payload.get("bytes_written"),
            "broadcast_ok": result_payload.get("broadcast_ok"),
            "director_policy": result_payload.get("director_policy"),
            "projection_source": "embedded_repair_kernel",
            "requires_revalidation": success and revalidation_evidence is None,
        }
    )
    if success and revalidation_evidence is None and status == "applied":
        status = "pending_revalidation"
    if success and revalidation_failed and status == "applied":
        status = "failed_revalidation"
    return [
        RepairReceipt(
            receipt_id=str(kernel.get("receipt_id") or ""),
            plan_id=str(kernel.get("plan_id") or stable_id("kernel_plan", stage, source_tool, file_path, operation)),
            rule_id=str(kernel.get("rule_id") or source_tool),
            source_tool=source_tool,
            status=status,
            mode=mode,
            authoritative=(
                mode == "commit"
                and bool(kernel.get("authoritative"))
                and revalidation_evidence is not None
                and not revalidation_failed
            ),
            files_changed=(file_path,) if file_path else _coerce_str_tuple(kernel.get("files_changed")),
            operation_ids=_coerce_str_tuple(kernel.get("operation_ids"))
            or (stable_id("kernel_op", kernel.get("plan_id") or source_tool, file_path, operation),),
            diagnostics=diagnostics,
            before_hashes=before_hashes,
            after_hashes=after_hashes,
            round_number=_coerce_optional_int(kernel.get("round_number")),
            revalidation_evidence=revalidation_evidence,
            metadata=metadata,
        )
    ]


def _repair_receipt_from_payload(
    payload: dict[str, Any],
    *,
    fallback_stage: str,
    fallback_result: dict[str, Any],
    fallback_diagnostics: tuple[RepairDiagnostic, ...],
    fallback_mode: str,
    fallback_success: bool,
) -> RepairReceipt | None:
    source_tool = str(payload.get("source_tool") or fallback_result.get("source_tool") or "")
    if not source_tool:
        return None
    files_changed = _coerce_str_tuple(payload.get("files_changed"))
    if not files_changed and fallback_result.get("file"):
        files_changed = (str(fallback_result.get("file") or ""),)
    operation = str(fallback_result.get("operation") or fallback_result.get("action") or "modify")
    revalidation_evidence = _revalidation_evidence_from_payload(payload.get("revalidation_evidence"))
    if revalidation_evidence is None:
        revalidation_evidence = _revalidation_evidence_from_payload(fallback_result.get("revalidation"))
    revalidation_failed = _revalidation_failed(revalidation_evidence)
    metadata = dict(payload.get("metadata") or {})
    status = str(payload.get("status") or ("applied" if fallback_success else "failed"))
    execution_status = status
    metadata.update(
        {
            "stage": fallback_stage,
            "operation": operation,
            "execution_status": execution_status,
            "projection_source": "embedded_repair_kernel_receipt",
            "requires_revalidation": fallback_success and revalidation_evidence is None,
        }
    )
    if fallback_success and revalidation_evidence is None and status == "applied":
        status = "pending_revalidation"
    if fallback_success and revalidation_failed and status == "applied":
        status = "failed_revalidation"
    return RepairReceipt(
        receipt_id=str(payload.get("receipt_id") or ""),
        plan_id=str(payload.get("plan_id") or stable_id("kernel_plan", fallback_stage, source_tool, files_changed)),
        rule_id=str(payload.get("rule_id") or source_tool),
        source_tool=source_tool,
        status=status,
        mode=str(payload.get("mode") or fallback_mode),
        authoritative=bool(payload.get("authoritative"))
        and revalidation_evidence is not None
        and not revalidation_failed,
        files_changed=files_changed,
        operation_ids=_coerce_str_tuple(payload.get("operation_ids"))
        or (stable_id("kernel_op", payload.get("plan_id") or source_tool, files_changed, operation),),
        diagnostics=_coerce_revalidation_diagnostics(payload.get("diagnostics")) or fallback_diagnostics,
        before_hashes=_coerce_str_mapping(payload.get("before_hashes")),
        after_hashes=_coerce_str_mapping(payload.get("after_hashes")),
        round_number=_coerce_optional_int(payload.get("round_number")),
        revalidation_evidence=revalidation_evidence,
        metadata=metadata,
    )


def _coerce_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if not isinstance(value, int | float | str | bytes | bytearray):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _revalidation_failed(evidence: RepairRevalidationEvidence | None) -> bool:
    if evidence is None:
        return False
    return evidence.exit_code not in (None, 0)


def _revalidation_evidence_from_payload(value: object) -> RepairRevalidationEvidence | None:
    if not isinstance(value, dict):
        return None
    command_value = value.get("command")
    command = tuple(str(item) for item in command_value) if isinstance(command_value, list | tuple) else ()
    diagnostics_before = _coerce_revalidation_diagnostics(value.get("diagnostics_before"))
    diagnostics_after = _coerce_revalidation_diagnostics(value.get("diagnostics_after"))
    return RepairRevalidationEvidence(
        command=command,
        exit_code=_coerce_optional_int(value.get("exit_code")),
        diagnostics_before=diagnostics_before,
        diagnostics_after=diagnostics_after,
        errors_before_count=_coerce_optional_int(value.get("errors_before")),
        errors_after_count=_coerce_optional_int(value.get("errors_after")),
        resolved_diagnostic_ids=_coerce_str_tuple(value.get("resolved_diagnostic_ids")),
        residual_diagnostic_ids=_coerce_str_tuple(value.get("residual_diagnostic_ids")),
        round_number=_coerce_optional_int(value.get("round_number")),
        raw_output_ref=str(value.get("raw_output_ref") or "").strip() or None,
        metadata={
            key: payload_value
            for key, payload_value in value.items()
            if key
            not in {
                "command",
                "exit_code",
                "diagnostics_before",
                "diagnostics_after",
                "errors_before",
                "errors_after",
                "resolved_diagnostic_ids",
                "residual_diagnostic_ids",
                "round_number",
                "raw_output_ref",
            }
        },
    )


def _coerce_revalidation_diagnostics(value: object) -> tuple[RepairDiagnostic, ...]:
    if not isinstance(value, list | tuple):
        return ()
    diagnostics: list[RepairDiagnostic] = []
    raw_messages: list[str] = []
    for item in value:
        if isinstance(item, str):
            raw_messages.append(item)
            continue
        if not isinstance(item, dict):
            continue
        metadata_value = item.get("metadata")
        metadata: dict[str, Any] = {}
        if isinstance(metadata_value, dict):
            metadata = {str(key): value for key, value in metadata_value.items()}
        diagnostics.append(
            RepairDiagnostic(
                source=str(item.get("source") or "legacy_revalidation"),
                code=str(item.get("code") or "unknown"),
                message=str(item.get("message") or item.get("raw") or ""),
                severity=str(item.get("severity") or "error"),
                path=str(item.get("path")) if item.get("path") else None,
                line=_coerce_optional_int(item.get("line")),
                column=_coerce_optional_int(item.get("column")),
                diagnostic_id=str(item.get("diagnostic_id") or ""),
                raw=str(item.get("raw") or item.get("message") or ""),
                metadata=metadata,
            )
        )
    if raw_messages:
        diagnostics.extend(normalize_artifact_quality_errors(raw_messages))
    return tuple(diagnostics)


def _coerce_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if str(item or "").strip())


def _coerce_str_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(payload_value) for key, payload_value in value.items() if str(key or "").strip()}
