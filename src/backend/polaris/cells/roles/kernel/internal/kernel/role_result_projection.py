"""Projection helpers for RoleTurnResult fields.

The RoleExecutionKernel decides retry, validation, and completion semantics.
This module owns the mechanical projection from TransactionKernel turn results
into public RoleTurnResult values so result shape drift has one owner.
"""

from __future__ import annotations

from typing import Any, Protocol

from polaris.cells.roles.profile.public.service import RoleTurnResult
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


class QualityProjection(Protocol):
    """Minimal quality-result fields needed for RoleTurnResult projection."""

    quality_score: float
    suggestions: list[str]


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


def role_turn_result_from_transaction_result(
    *,
    transaction_result: RoleTurnResult,
    profile: Any,
    fingerprint: Any,
    quality_result: QualityProjection | None,
    platform_retry_count: int,
    kernel_repair_retry_count: int,
    kernel_repair_reasons: list[str],
    kernel_repair_exhausted: bool,
    error: str | None,
    is_complete: bool,
    structured_output: dict[str, Any] | None = None,
    content_override: str | None = None,
) -> RoleTurnResult:
    """Project a TransactionKernel result into the public role-turn result.

    Boundary:
        - Caller owns retry/exhaustion decisions and supplies explicit
          ``error`` / ``is_complete`` / ``kernel_repair_exhausted`` values.
        - This function owns stable field copying, quality projection, and
          execution-stat merging.

    Complexity:
        O(n + m) time and memory where ``n`` is the number of tool/history
        records and ``m`` is the number of execution-stat keys copied.
    """
    transaction_stats = getattr(transaction_result, "execution_stats", {}) or {}
    execution_stats = {
        "platform_retry_count": platform_retry_count,
        "kernel_repair_retry_count": kernel_repair_retry_count,
        "kernel_repair_reasons": list(kernel_repair_reasons),
        "kernel_repair_exhausted": kernel_repair_exhausted,
        **dict(transaction_stats),
    }

    batch_receipt = getattr(transaction_result, "batch_receipt", None)
    return RoleTurnResult(
        content=content_override if content_override is not None else (transaction_result.content or ""),
        thinking=transaction_result.thinking,
        structured_output=structured_output,
        tool_calls=list(transaction_result.tool_calls or []),
        tool_results=list(transaction_result.tool_results or []),
        batch_receipt=dict(batch_receipt) if isinstance(batch_receipt, dict) else None,
        profile_version=str(getattr(profile, "version", "") or ""),
        prompt_fingerprint=fingerprint,
        tool_policy_id=str(getattr(getattr(profile, "tool_policy", None), "policy_id", "") or ""),
        quality_score=quality_result.quality_score if quality_result else 0.0,
        quality_suggestions=list(quality_result.suggestions) if quality_result else [],
        error=error,
        is_complete=is_complete,
        tool_execution_error=getattr(transaction_result, "tool_execution_error", None),
        should_retry=bool(getattr(transaction_result, "should_retry", False)),
        execution_stats=execution_stats,
        turn_history=list(transaction_result.turn_history) if transaction_result.turn_history else [],
        turn_events_metadata=list(transaction_result.turn_events_metadata)
        if transaction_result.turn_events_metadata
        else [],
        metadata=dict(getattr(transaction_result, "metadata", {}) or {}),
    )
