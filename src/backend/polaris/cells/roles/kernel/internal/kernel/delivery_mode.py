"""Delivery-mode marker handling for RoleExecutionKernel.

Stateless helpers that detect and restore the ``[mode:materialize]`` delivery
marker after ContextOS projection. Extracted verbatim from ``core.py`` to keep
the Facade focused; ``core.py`` re-exports every symbol for backward
compatibility (tests reach these via the ``core`` module namespace).
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


def _latest_user_content_preview(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "")
        return content[:160]
    return ""
