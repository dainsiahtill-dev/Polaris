"""Compatibility bridge from legacy deterministic repair results to receipts."""

from __future__ import annotations

from collections.abc import Sequence
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
    pending_revalidation_count = sum(1 for receipt in receipts if receipt.status == "pending_revalidation")
    receipts_with_revalidation = sum(1 for receipt in receipts if receipt.revalidation_evidence is not None)
    return {
        "version": 1,
        "stage": stage,
        "mode": mode,
        "authoritative": mode == "commit" and bool(receipts) and pending_revalidation_count == 0,
        "requires_revalidation": pending_revalidation_count > 0,
        "pending_revalidation_count": pending_revalidation_count,
        "receipts_with_revalidation": receipts_with_revalidation,
        "receipt_count": len(receipts),
        "receipts": [receipt.to_dict() for receipt in receipts],
        "receipt_context": build_repair_receipt_context(receipts),
        "source_tool_profiles": summarize_deterministic_repair_source_tools(
            [receipt.source_tool for receipt in receipts]
        ),
        "coverage_report": coverage_report.to_dict(),
        "dark_launch_comparison": shadow_comparison.to_dict(),
        "agi_advisory": {
            "supported": True,
            "active": False,
            "authoritative": False,
            "writes_allowed": False,
        },
    }


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
        status = "failed"
        if success:
            status = "applied" if revalidation_evidence is not None else "pending_revalidation"
        receipts.append(
            RepairReceipt(
                plan_id=plan_id,
                rule_id=source_tool,
                source_tool=source_tool,
                status=status,
                mode=mode,
                authoritative=mode == "commit" and success and revalidation_evidence is not None,
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


def _coerce_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if not isinstance(value, int | float | str | bytes | bytearray):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
