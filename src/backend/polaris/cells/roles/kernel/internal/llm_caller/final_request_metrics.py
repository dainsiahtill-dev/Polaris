"""Canonical metrics shared by final-request audit and qualification."""

from __future__ import annotations

import json
import re
from typing import Any

from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash
from polaris.kernelone.llm.engine.native_request_metrics import (
    canonical_json_chars,
    provider_native_request_metrics,
)

_HASH64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NATIVE_PROTOCOLS = frozenset({"anthropic_messages", "openai_chat_completions", "openai_responses"})
_REQUIRED_NATIVE_INT_FIELDS = (
    "message_count",
    "message_chars",
    "message_token_estimate",
    "tool_schema_count",
    "tool_schema_chars",
    "tool_schema_token_estimate",
    "response_format_chars",
    "response_format_token_estimate",
    "request_control_chars",
    "request_control_token_estimate",
    "final_request_token_estimate",
    "context_window_tokens",
    "available_token_headroom",
)


def canonical_message_chars(messages: list[dict[str, Any]]) -> int:
    """Count every JSON field in the provider-visible message array."""

    try:
        return len(
            json.dumps(
                messages,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError):
        return len(str(messages))


def validated_final_context_evidence(
    port: Any,
    *,
    expected_port_type: type[Any],
) -> tuple[str, dict[str, Any]] | None:
    """Return only a complete physical-ref/native-audit pair.

    Pre-dispatch ports and test doubles may expose no final evidence yet. They
    must not be mistaken for the strict two-item evidence tuple, and a physical
    snapshot ref must never be paired with a semantic audit.
    """

    if port is None or type(port) is not expected_port_type:
        return None
    getter = getattr(port, "final_context_evidence", None)
    if not callable(getter):
        return None
    evidence = getter()
    if type(evidence) is not tuple or len(evidence) != 2:
        return None
    context_snapshot_ref, audit = evidence
    if type(context_snapshot_ref) is not str:
        return None
    try:
        validate_context_hash(context_snapshot_ref)
    except ValueError:
        return None
    if type(audit) is not dict or audit.get("audit_scope") != "provider_native_wire":
        return None
    if audit.get("native_protocol") not in _NATIVE_PROTOCOLS:
        return None
    wire_hash = audit.get("final_physical_wire_hash")
    if type(wire_hash) is not str or _HASH64_PATTERN.fullmatch(wire_hash) is None:
        return None
    identity = audit.get("request_identity")
    if type(identity) is not dict or any(
        type(identity.get(key)) is not str or not identity[key]
        for key in ("run_id", "turn_id", "call_id", "request_freeze_id")
    ):
        return None
    if any(type(audit.get(key)) is not int or audit[key] < 0 for key in _REQUIRED_NATIVE_INT_FIELDS):
        return None
    if audit["final_request_token_estimate"] <= 0 or audit["context_window_tokens"] <= 0:
        return None
    utilization = audit.get("context_window_utilization")
    if isinstance(utilization, bool) or not isinstance(utilization, (int, float)) or utilization < 0:
        return None
    coverage = audit.get("final_request_evidence_coverage")
    if type(coverage) is not dict or coverage.get("context_snapshot_ref") != context_snapshot_ref:
        return None
    return context_snapshot_ref, audit


__all__ = [
    "canonical_json_chars",
    "canonical_message_chars",
    "provider_native_request_metrics",
    "validated_final_context_evidence",
]
