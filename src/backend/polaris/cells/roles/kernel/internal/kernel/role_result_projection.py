"""Projection helpers for RoleTurnResult fields."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger

_LLM_RESPONSE_METADATA_KEYS: tuple[str, ...] = (
    "final_request_context_audit",
    "context_snapshot_ref",
    "context_snapshot_degraded",
    "context_snapshot_degraded_reason",
    "context_tokens_after",
    "contextTokens",
    "usage",
    "usage_source",
)


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


def role_result_metadata_from_profile(
    *,
    profile: Any,
    tool_filter_audit: dict[str, Any] | None = None,
    ledger: Any = None,
    llm_response_metadata: dict[str, Any] | None = None,
    monitoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project stable RoleTurnResult metadata from profile, ledger, and LLM evidence."""
    metadata: dict[str, Any] = {}
    provider_id = str(getattr(profile, "provider_id", "") or "").strip()
    model = str(getattr(profile, "model", "") or "").strip()
    if provider_id:
        metadata["provider_id"] = provider_id
    if model:
        metadata["model"] = model
    if tool_filter_audit is not None:
        metadata["tool_filter_audit"] = tool_filter_audit

    if ledger is not None:
        context_os_audit_summary = summarize_context_os_audit_from_ledger(ledger)
        if context_os_audit_summary:
            metadata["context_os_audit"] = context_os_audit_summary

    if isinstance(llm_response_metadata, dict):
        for key in _LLM_RESPONSE_METADATA_KEYS:
            if key in llm_response_metadata and key not in metadata:
                value = llm_response_metadata.get(key)
                metadata[key] = dict(value) if isinstance(value, dict) else value
        if "context_os_audit" in llm_response_metadata and "context_os_audit" not in metadata:
            raw_context_os_audit = llm_response_metadata.get("context_os_audit")
            metadata["context_os_audit"] = (
                dict(raw_context_os_audit) if isinstance(raw_context_os_audit, dict) else raw_context_os_audit
            )

    if isinstance(monitoring, dict) and "context_os_audit" not in metadata:
        context_os_audit = monitoring.get("context_os_audit")
        if isinstance(context_os_audit, dict):
            metadata["context_os_audit"] = dict(context_os_audit)

    return metadata
