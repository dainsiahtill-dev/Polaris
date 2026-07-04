"""LLM response metadata projection for transaction ledger records.

This module owns the narrow projection from provider ``usage`` payloads into
``TurnLedger.record_llm_call`` metadata. Native tool-call facts are delegated to
Run Ledger public helpers so the transaction controller does not maintain a
second count/name interpretation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_facts_from_sources,
    project_native_tool_call_facts_to_metadata,
)

_LLM_RESPONSE_USAGE_METADATA_KEYS: tuple[str, ...] = (
    "context_os_audit",
    "final_request_context_audit",
    "context_snapshot_ref",
    "context_snapshot_degraded",
    "context_snapshot_degraded_reason",
    "context_tokens_after",
    "contextTokens",
    "usage",
    "usage_source",
    "tool_call_provider",
    "decision_caller_tool_call_provider",
)


def llm_response_metadata_from_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project provider usage evidence into TurnLedger LLM-call metadata.

    Boundary:
        This helper copies only stable provider/audit fields. It delegates native
        tool-call count and name projection to Run Ledger public helpers, which
        own lifecycle/envelope precedence and compatibility fields such as
        ``decision_caller_native_tool_calls_count``.

    Complexity:
        O(k + n) time and memory, where ``k`` is the fixed metadata key count and
        ``n`` is native tool lifecycle/envelope evidence size.
    """

    metadata: dict[str, Any] = {}
    if not isinstance(usage, Mapping):
        return metadata

    for key in _LLM_RESPONSE_USAGE_METADATA_KEYS:
        if key in usage:
            metadata[key] = _copy_metadata_value(usage.get(key))

    native_facts = native_tool_call_facts_from_sources(usage, [])
    if native_facts:
        project_native_tool_call_facts_to_metadata(
            metadata,
            native_facts,
            project_decision_caller_count="decision_caller_native_tool_calls_count" in usage,
        )

    return metadata


def _copy_metadata_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value
