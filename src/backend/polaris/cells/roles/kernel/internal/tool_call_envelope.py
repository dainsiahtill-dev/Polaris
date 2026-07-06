"""Canonical tool-call envelope helpers for the role kernel.

This module is the kernel-side facade over Run Ledger's typed tool lifecycle
contracts. It owns response/stream event projection into native tool-call
payloads, while Run Ledger still owns envelope identity, lifecycle receipts, and
failure evidence. Callers should use this module instead of carrying local
``native_tool_calls`` / ``tool_calls`` alias tables.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    build_native_tool_call_envelope_payloads,
    native_tool_call_envelope_refs_from_metadata,
    native_tool_call_facts_from_raw_calls,
    native_tool_call_names_from_facts,
)


def native_tool_call_name(call: Mapping[str, Any]) -> str:
    """Return the Run Ledger projected tool name for one native call.

    Complexity:
        O(k) time and memory over the call payload size.
    """

    facts = native_tool_call_facts_from_raw_calls([call])
    names = native_tool_call_names_from_facts(facts)
    return names[0] if names else ""


def native_tool_calls_from_response(response: Any) -> list[dict[str, Any]]:
    """Return provider-native tool-call payloads from a response-like object.

    Boundary:
        This helper only copies already-decoded response objects. It does not
        parse assistant text, infer tool calls from prose, authorize tools, or
        dispatch anything.

    Complexity:
        O(n) time and memory for copying mapping-shaped tool calls.
    """

    native_calls = getattr(response, "native_tool_calls", None)
    if isinstance(native_calls, list):
        return [dict(item) for item in native_calls if isinstance(item, Mapping)]

    alias_calls = getattr(response, "tool_calls", None)
    if isinstance(alias_calls, list):
        return [dict(item) for item in alias_calls if isinstance(item, Mapping)]

    if isinstance(response, Mapping):
        raw_calls = response.get("native_tool_calls")
        if not isinstance(raw_calls, list):
            raw_calls = response.get("tool_calls")
        if isinstance(raw_calls, list):
            return [dict(item) for item in raw_calls if isinstance(item, Mapping)]

    return []


def native_tool_call_envelopes_from_metadata(metadata: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], ...]:
    """Return authoritative native tool-call envelope payloads from metadata."""

    return native_tool_call_envelope_refs_from_metadata(metadata)


def native_tool_call_provider_from_metadata(metadata: Mapping[str, Any] | None) -> str:
    """Return the canonical provider label for native tool-call envelopes."""

    if not isinstance(metadata, Mapping):
        return "auto"
    for key in ("tool_call_provider", "decision_caller_tool_call_provider", "provider", "provider_id"):
        token = str(metadata.get(key) or "").strip().lower()
        if token:
            return token
    return "auto"


def native_tool_call_envelopes_from_response(
    response: Any,
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return envelope payloads for native tool calls in a response-like object.

    Existing metadata/lifecycle envelopes remain authoritative. Raw response
    calls are wrapped only when no structured envelope evidence is present.
    """

    metadata_envelopes = [dict(item) for item in native_tool_call_envelopes_from_metadata(metadata)]
    if metadata_envelopes:
        return metadata_envelopes

    raw_calls = native_tool_calls_from_response(response)
    if not raw_calls:
        return []

    return build_native_tool_call_envelope_payloads(
        raw_calls,
        provider=native_tool_call_provider_from_metadata(metadata),
    )


def build_native_tool_call_from_stream_event(
    *,
    tool_name: str,
    tool_args: Mapping[str, Any] | None,
    call_id: str,
    ordinal: int,
) -> dict[str, Any]:
    """Convert one streamed tool-call event into decoder-native shape."""

    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        return {}
    normalized_call_id = str(call_id or "").strip() or f"stream_tool_call_{max(1, ordinal)}"
    return {
        "id": normalized_call_id,
        "type": "function",
        "function": {
            "name": normalized_tool_name,
            "arguments": dict(tool_args or {}),
        },
    }


def normalize_stream_tool_call_payload(
    *,
    tool_name: str,
    tool_args: dict[str, Any] | None,
    call_id: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Normalize stream tool events into an executable native payload.

    ``call_stream()`` emits provider-neutral tool events after the provider
    adapter has decoded raw deltas. Provider metadata is debug/context evidence;
    this function only normalizes already-structured fields.
    """

    safe_args = dict(tool_args) if isinstance(tool_args, dict) else {}
    safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}

    raw_native = safe_metadata.get("native_tool_call")
    if not isinstance(raw_native, dict):
        raw_native = safe_metadata.get("tool_call")
    candidate = dict(raw_native) if isinstance(raw_native, dict) else {}
    candidate_type = str(candidate.get("type") or "").strip().lower()

    if candidate_type == "function" and isinstance(candidate.get("function"), dict):
        candidate_function = dict(candidate["function"])
        candidate_tool_name = str(candidate_function.get("name") or tool_name or "").strip()
        candidate_args = candidate_function.get("arguments")
        if not isinstance(candidate_args, dict):
            candidate_args = safe_args
        candidate_call_id = str(candidate.get("id") or call_id or "").strip()
        if not candidate_tool_name:
            return None, "auto"
        return (
            {
                "id": candidate_call_id,
                "type": "function",
                "function": {
                    "name": candidate_tool_name,
                    "arguments": dict(candidate_args),
                },
            },
            "openai",
        )

    if candidate_type == "tool_use":
        return candidate, "anthropic"

    candidate_tool_name = str(candidate.get("tool") or candidate.get("name") or tool_name or "").strip()
    candidate_args = candidate.get("arguments")
    if not isinstance(candidate_args, dict):
        candidate_args = candidate.get("input")
    if not isinstance(candidate_args, dict):
        candidate_args = safe_args
    candidate_call_id = str(candidate.get("call_id") or candidate.get("id") or call_id or "").strip()

    if not candidate_tool_name:
        return None, "auto"

    return (
        {
            "id": candidate_call_id,
            "type": "function",
            "function": {
                "name": candidate_tool_name,
                "arguments": dict(candidate_args),
            },
        },
        "openai",
    )
