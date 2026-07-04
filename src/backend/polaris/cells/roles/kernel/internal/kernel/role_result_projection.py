"""Projection helpers for RoleTurnResult fields.

The RoleExecutionKernel decides retry, validation, and completion semantics.
This module owns the mechanical projection from TransactionKernel turn results
into public RoleTurnResult values so result shape drift has one owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from polaris.cells.control_plane.run_ledger.public import (
    project_completion_dispatch_evidence_to_metadata,
    project_lifecycle_failure_evidence_to_metadata,
    project_native_tool_call_facts_from_evidence_to_metadata,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger

_LLM_RESPONSE_METADATA_KEYS: tuple[str, ...] = (
    "final_request_context_audit",
    "context_snapshot_ref",
    "context_snapshot_degraded",
    "context_snapshot_degraded_reason",
    "native_tool_calls_count",
    "decision_caller_native_tool_calls_count",
    "native_tool_call_names",
    "tool_call_lifecycle",
    "tool_call_lifecycle_receipt",
    "tool_call_lifecycle_receipts",
    "native_tool_call_envelopes",
    "native_tool_call_envelope_refs",
    "tool_call_provider",
    "decision_caller_tool_call_provider",
    "context_tokens_after",
    "contextTokens",
    "usage",
    "usage_source",
    "failure_evidence",
    "failure_evidence_summary",
)

_ROLE_RESULT_COMPLETION_EVIDENCE_KEYS: tuple[str, ...] = (
    "native_tool_calls_count",
    "native_tool_call_names",
    "failure_evidence",
    "failure_evidence_summary",
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
        project_tool_lifecycle_metadata(metadata)

    if isinstance(monitoring, dict) and "context_os_audit" not in metadata:
        context_os_audit = monitoring.get("context_os_audit")
        if isinstance(context_os_audit, dict):
            metadata["context_os_audit"] = dict(context_os_audit)

    return metadata


def project_completion_audit_evidence(metadata: dict[str, Any], evidence: Mapping[str, Any] | None) -> None:
    """Copy completion audit evidence and derive lifecycle projections.

    Stream completion carries final-request audit facts inside its monitoring
    payload. Non-stream role results receive the same facts as LLM metadata.
    This helper keeps the stream path from maintaining a second key list or
    lifecycle/failure projection sequence.

    Complexity:
        O(k + n) time where ``k`` is the fixed evidence key count and ``n`` is
        the number of lifecycle-derived evidence rows.
    """

    if not isinstance(evidence, Mapping):
        return
    project_completion_dispatch_evidence_to_metadata(metadata, evidence)
    for key in _ROLE_RESULT_COMPLETION_EVIDENCE_KEYS:
        if key in evidence and key not in metadata:
            value = evidence[key]
            metadata[key] = dict(value) if isinstance(value, dict) else value
    project_tool_lifecycle_metadata(metadata)


def _project_canonical_tool_lifecycle_receipt(metadata: dict[str, Any]) -> None:
    """Ensure RoleTurnResult metadata exposes the canonical lifecycle receipt key."""

    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
    if receipts:
        metadata["tool_call_lifecycle_receipt"] = dict(receipts[0])


def project_tool_lifecycle_metadata(metadata: dict[str, Any]) -> None:
    """Project canonical lifecycle, failure, and native tool facts together.

    Boundary:
        This helper owns the RoleTurnResult metadata projection for tool-call
        lifecycle evidence. It does not create lifecycle receipts; it only
        canonicalizes existing receipt evidence and derives dependent metadata.

    Complexity:
        O(n) time and memory for native tool-name / failure-evidence rows, where
        n is the number of lifecycle envelope or dropped-call references.
    """

    _project_canonical_tool_lifecycle_receipt(metadata)
    project_failure_evidence_from_tool_lifecycle(metadata)
    project_native_tool_call_facts_from_evidence_to_metadata(metadata, metadata)


def project_failure_evidence_from_tool_lifecycle(metadata: dict[str, Any]) -> None:
    """Project failure evidence from canonical lifecycle receipt without dropping existing evidence."""

    raw = metadata.get("tool_call_lifecycle_receipt")
    if not isinstance(raw, dict):
        return
    project_lifecycle_failure_evidence_to_metadata(metadata, raw)


def role_turn_error_result(
    *,
    error: str,
    profile: Any | None = None,
    fingerprint: Any = None,
    is_complete: bool = False,
    content: str = "",
    execution_stats: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RoleTurnResult:
    """Build a public role-turn error result with stable profile projection.

    Boundary:
        This helper is for errors that occur at role-kernel adapter boundaries
        before a normal TransactionKernel result can be projected. Callers own
        the failure classification and evidence payload; this function only
        applies the shared RoleTurnResult field shape.

    Complexity:
        O(n + m) time and memory for copying execution-stat and metadata keys.
    """
    return RoleTurnResult(
        content=content,
        error=error,
        is_complete=is_complete,
        profile_version=str(getattr(profile, "version", "") or "") if profile is not None else "",
        prompt_fingerprint=fingerprint,
        tool_policy_id=str(getattr(getattr(profile, "tool_policy", None), "policy_id", "") or "")
        if profile is not None
        else "",
        execution_stats=dict(execution_stats or {}),
        metadata=dict(metadata or {}),
    )


def role_turn_completion_result(
    *,
    content: str,
    thinking: str | None,
    structured_output: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    batch_receipt: dict[str, Any] | None,
    profile: Any,
    fingerprint: Any,
    error: str | None,
    is_complete: bool,
    execution_stats: dict[str, Any],
    turn_history: list[tuple[str, str]],
    turn_events_metadata: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> RoleTurnResult:
    """Build a public role-turn completion result from committed turn facts.

    Boundary:
        ``TransactionTurnExecutor`` coordinates committed transaction facts.
        This helper owns only the final public result projection after those
        facts are available.

    Complexity:
        O(n + m) time and memory for copying list/dict projections.
    """
    return RoleTurnResult(
        content=content,
        thinking=thinking,
        structured_output=dict(structured_output) if isinstance(structured_output, dict) else None,
        tool_calls=list(tool_calls),
        tool_results=list(tool_results),
        batch_receipt=dict(batch_receipt) if isinstance(batch_receipt, dict) else None,
        profile_version=str(getattr(profile, "version", "") or ""),
        prompt_fingerprint=fingerprint,
        tool_policy_id=str(getattr(getattr(profile, "tool_policy", None), "policy_id", "") or ""),
        error=error,
        is_complete=is_complete,
        execution_stats=dict(execution_stats),
        turn_history=list(turn_history),
        turn_events_metadata=list(turn_events_metadata),
        metadata=dict(metadata),
    )


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
