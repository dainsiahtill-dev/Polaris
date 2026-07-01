"""Projection helpers for RoleTurnResult fields."""

from __future__ import annotations

from typing import Any


def tool_calls_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project RoleTurnResult.tool_calls from a TransactionKernel batch receipt."""
    if not isinstance(batch_receipt, dict):
        return []
    raw_results = batch_receipt.get("results")
    if not isinstance(raw_results, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        tool_calls.append(
            {
                "tool": result.get("tool_name", ""),
                "args": result.get("arguments") or {},
                "call_id": result.get("call_id", ""),
            }
        )
    return tool_calls


def tool_results_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Project RoleTurnResult.tool_results from a TransactionKernel batch receipt."""
    if not isinstance(batch_receipt, dict):
        return []
    raw_results = batch_receipt.get("results")
    if not isinstance(raw_results, list):
        return []
    tool_results: list[dict[str, Any]] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        tool_results.append(
            {
                "tool": result.get("tool_name", ""),
                "tool_name": result.get("tool_name", ""),
                "result": result.get("result"),
                "success": result.get("status") == "success",
                "status": result.get("status"),
                "call_id": result.get("call_id", ""),
                "arguments": result.get("arguments"),
                "effect_receipt": result.get("effect_receipt"),
                "raw_result": dict(result),
            }
        )
    return tool_results
