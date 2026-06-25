"""Compatibility bridge from legacy deterministic repair results to receipts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .contracts import RepairDiagnostic, RepairReceipt, stable_id
from .diagnostics import normalize_artifact_quality_errors
from .receipt_context import build_repair_receipt_context
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
    return {
        "version": 1,
        "stage": stage,
        "mode": mode,
        "authoritative": mode == "commit",
        "receipt_count": len(receipts),
        "receipts": [receipt.to_dict() for receipt in receipts],
        "receipt_context": build_repair_receipt_context(receipts),
        "source_tool_profiles": summarize_deterministic_repair_source_tools(
            [receipt.source_tool for receipt in receipts]
        ),
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
        receipts.append(
            RepairReceipt(
                plan_id=plan_id,
                rule_id=source_tool,
                source_tool=source_tool,
                status="applied" if bool(item.get("success")) else "failed",
                mode=mode,
                authoritative=mode == "commit" and bool(item.get("success")),
                files_changed=(file_path,) if file_path else (),
                operation_ids=(stable_id("legacy_op", plan_id, file_path, operation),),
                diagnostics=diagnostics,
                before_hashes={file_path: before_hash} if file_path and before_hash else {},
                after_hashes={file_path: after_hash} if file_path and after_hash else {},
                metadata={
                    "stage": stage,
                    "operation": operation,
                    "bytes_written": result_payload.get("bytes_written"),
                    "broadcast_ok": result_payload.get("broadcast_ok"),
                    "director_policy": result_payload.get("director_policy"),
                },
            )
        )
    return receipts
