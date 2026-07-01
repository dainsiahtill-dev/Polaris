"""Delivery-mode marker handling for RoleExecutionKernel.

Stateless helpers that detect and restore the ``[mode:materialize]`` delivery
marker after ContextOS projection. Extracted verbatim from ``core.py`` to keep
the Facade focused; callers import these helpers from this module directly.
"""

from __future__ import annotations

from typing import Any

_MATERIALIZE_DELIVERY_MODE_VALUES = frozenset({"materialize", "materialize_changes"})
_MATERIALIZE_DELIVERY_MODE_MARKERS = frozenset({"[mode:materialize]", "[mode:materialize_changes]"})


def _context_requests_materialize_delivery(context_override: Any) -> bool:
    if not isinstance(context_override, dict):
        return False
    value = context_override.get("delivery_mode")
    if value is None:
        metadata = context_override.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get("delivery_mode")
    return str(value or "").strip().lower() in _MATERIALIZE_DELIVERY_MODE_VALUES


def _text_requests_materialize_delivery(text: Any) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _MATERIALIZE_DELIVERY_MODE_MARKERS)


def _ensure_context_delivery_mode_marker(
    messages: list[dict[str, Any]],
    context_override: Any,
    source_message: Any = None,
) -> list[dict[str, Any]]:
    """Restore the materialize marker after ContextOS projection when needed."""

    if not _context_requests_materialize_delivery(context_override) and not _text_requests_materialize_delivery(
        source_message
    ):
        return messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if str(message.get("role") or "").lower() != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if _text_requests_materialize_delivery(content):
            return messages
        patched_messages = list(messages)
        patched_messages[index] = {**message, "content": f"[mode:materialize]\n{content}"}
        return patched_messages
    return messages


def _platform_tool_contract_from_context(context_override: Any) -> dict[str, Any]:
    if not isinstance(context_override, dict):
        return {}
    metadata = context_override.get("metadata")
    metadata_mapping = metadata if isinstance(metadata, dict) else {}
    for candidate in (
        context_override.get("tool_contract"),
        context_override.get("platform_tool_contract"),
        metadata_mapping.get("tool_contract"),
        metadata_mapping.get("platform_tool_contract"),
    ):
        if isinstance(candidate, dict) and candidate:
            return dict(candidate)
    return {}


def _ensure_platform_tool_contract_metadata(
    messages: list[dict[str, Any]],
    context_override: Any,
) -> list[dict[str, Any]]:
    """Project platform tool-contract metadata onto the latest user message."""
    tool_contract = _platform_tool_contract_from_context(context_override)
    if not tool_contract:
        return messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        patched_messages = list(messages)
        metadata = message.get("metadata")
        patched_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        existing = patched_metadata.get("tool_contract")
        if isinstance(existing, dict):
            patched_metadata["tool_contract"] = {**existing, **tool_contract}
        else:
            patched_metadata["tool_contract"] = dict(tool_contract)
        patched_messages[index] = {**message, "metadata": patched_metadata}
        return patched_messages
    return messages


def _latest_user_content_preview(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "")
        return content[:160]
    return ""
