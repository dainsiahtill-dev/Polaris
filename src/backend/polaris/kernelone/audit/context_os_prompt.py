"""Prompt-level audit helpers for ContextOS projections.

These helpers are intentionally read-only: they inspect the messages that are
about to be sent to an LLM and produce compact evidence that the prompt was
derived from ContextOS projection, not from control-plane runtime state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_CONTROL_PLANE_KEYS = frozenset(
    {
        "_transaction_kernel_forced_tool_choice",
        "_transaction_kernel_forced_tool_definitions",
        "_transaction_kernel_prebuilt_messages",
        "allowed_provider_ids",
        "allowed_provider_types",
        "blocked_provider_ids",
        "blocked_provider_types",
        "cognitive_runtime_enabled",
        "cognitive_runtime_mode",
        "cognitive_runtime_required",
        "cognitive_strategy_override",
        "context_os_expected",
        "context_os_snapshot",
        "factory_run_id",
        "host_kind",
        "llm_provider_policy",
        "metadata",
        "model_allowlist",
        "model_blocklist",
        "provider_allowlist",
        "provider_blocklist",
        "provider_policy",
        "role_runtime_required",
        "runtime_session_id",
        "session_context_config",
        "session_id",
        "state_first_context_os",
        "strategy_override",
        "stream_options",
        "task_id",
        "workspace_root",
    }
)

_TURN_BLOCKED_KEYS = frozenset(
    {
        "budget_status",
        "metrics",
        "policy_verdict",
        "raw_output",
        "system_warnings",
        "telemetry",
        "telemetry_events",
        "thinking",
        "thinking_content",
    }
)

_CONTROL_CONTENT_TOKENS = tuple(sorted(f"<{key}>" for key in _CONTROL_PLANE_KEYS)) + (
    "_transaction_kernel_prebuilt_messages",
    "_transaction_kernel_forced_tool_definitions",
    "llm_provider_policy",
    "provider_policy",
    "raw_output",
    "system_warnings",
    "telemetry_events",
)


def _message_role(message: Mapping[str, Any]) -> str:
    return str(message.get("role") or "").strip().lower()


def _message_content(message: Mapping[str, Any]) -> str:
    return str(message.get("content") or "")


def _message_metadata(message: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = message.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def _find_metadata_key_hits(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    hits: list[str] = []
    for message in messages:
        metadata = _message_metadata(message)
        for key in metadata.keys():
            key_text = str(key or "").strip()
            if key_text in _CONTROL_PLANE_KEYS or key_text in _TURN_BLOCKED_KEYS:
                hits.append(key_text)
    return _dedupe(hits)


def _find_content_hits(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    hits: list[str] = []
    for message in messages:
        content = _message_content(message).lower()
        if not content:
            continue
        for token in _CONTROL_CONTENT_TOKENS:
            if str(token).lower() in content:
                hits.append(str(token))
    return _dedupe(hits)


def _count_roles(messages: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        role = _message_role(message) or "unknown"
        counts[role] = counts.get(role, 0) + 1
    return counts


def audit_context_os_prompt_messages(
    *,
    messages: Sequence[Mapping[str, Any]],
    context_sources: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
    expected: bool = False,
) -> dict[str, Any]:
    """Audit LLM-ready messages generated from ContextOS.

    Args:
        messages: The exact message list used to build the provider request.
        context_sources: Context assembly source tags.
        metadata: Optional context result metadata.
        expected: Whether this request is expected to be backed by ContextOS.

    Returns:
        A JSON-serializable audit payload.
    """
    normalized_messages = [dict(item) for item in messages if isinstance(item, Mapping)]
    source_tags = _dedupe(str(item) for item in context_sources)
    metadata_payload = dict(metadata or {})
    state_first_from_sources = any("state_first_context_os" in item for item in source_tags)
    state_first_from_metadata = bool(metadata_payload.get("state_first_mode_active"))
    prebuilt_from_transaction_kernel = bool(metadata_payload.get("prebuilt_projection_messages"))
    state_first_projected = state_first_from_sources or state_first_from_metadata or prebuilt_from_transaction_kernel

    metadata_key_hits = _find_metadata_key_hits(normalized_messages)
    content_hits = _find_content_hits(normalized_messages)
    empty_content_count = sum(1 for item in normalized_messages if not _message_content(item).strip())
    receipt_ref_count = sum(1 for item in normalized_messages if "receipt_refs" in item)
    role_counts = _count_roles(normalized_messages)
    final_role = _message_role(normalized_messages[-1]) if normalized_messages else ""
    current_user_final = final_role == "user"
    control_plane_isolated = not metadata_key_hits and not content_hits
    data_plane_non_empty = bool(normalized_messages) and empty_content_count < len(normalized_messages)
    truth_source_ok = state_first_projected if expected else True
    ok = bool(truth_source_ok and control_plane_isolated and data_plane_non_empty)

    return {
        "ok": ok,
        "expected": bool(expected),
        "source": "kernelone.audit.context_os_prompt",
        "message_count": len(normalized_messages),
        "role_counts": role_counts,
        "final_role": final_role,
        "state_first_context_os": {
            "projected": bool(state_first_projected),
            "sources": source_tags,
            "from_metadata": bool(state_first_from_metadata),
            "from_transaction_kernel_prebuilt": bool(prebuilt_from_transaction_kernel),
        },
        "control_plane": {
            "isolated": bool(control_plane_isolated),
            "metadata_key_hits": metadata_key_hits,
            "content_hits": content_hits,
        },
        "data_plane": {
            "non_empty": bool(data_plane_non_empty),
            "empty_content_count": int(empty_content_count),
            "receipt_ref_count": int(receipt_ref_count),
        },
        "requirements": {
            "truth_source_context_os": bool(truth_source_ok),
            "prompt_projection_read_only": True,
            "control_plane_isolated": bool(control_plane_isolated),
            "current_user_final": bool(current_user_final),
        },
    }


__all__ = ["audit_context_os_prompt_messages"]
