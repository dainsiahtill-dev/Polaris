"""Unified streaming event protocol for the Director CLI layer.

This module defines the canonical event types and conversion utilities used
by all role console clients (director, pm, architect, chief_engineer, qa).
Events emitted by RoleExecutionKernel are normalised into StandardStreamEvent
instances so that the rendering layer has a stable contract regardless of the
upstream role.

Usage
-----
    from polaris.delivery.cli.director.console_protocol import (
        StreamEventType,
        StandardStreamEvent,
        to_standard_event,
        from_kernel_event,
    )

Architecture notes
------------------
- ``StreamEventType`` is defined in this module as the CLI-facing stable
  protocol boundary. Upstream provider/kernel variants are normalized into it.
- ``StandardStreamEvent`` is the canonical output type for the console host
  layer; it is a ``dataclass`` (not a TypedDict) so that it can be
  instantiated without a factory function.
- ``to_standard_event`` handles any upstream event format (kernel dict,
  StandardStreamEvent, raw dict, etc.).
- ``from_kernel_event`` handles only ``RoleExecutionKernel`` raw dict events.
- For backward compatibility, kernel event type strings (``thinking_chunk``,
  ``content_chunk``) are mapped to canonical enum values.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Event type enum ─────────────────────────────────────────────────────────────


class StreamEventType(str, Enum):
    """Canonical stream event types for the console protocol boundary.

    Note:
        This enum intentionally keeps the legacy CLI-facing names
        (``content_chunk``/``thinking_chunk``/``done``) as public contract.
        Newer upstream provider/kernel names are normalized in ``from_string``.
    """

    THINKING_CHUNK = "thinking_chunk"
    CONTENT_CHUNK = "content_chunk"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FINGERPRINT = "fingerprint"
    COMPLETE = "complete"
    ERROR = "error"
    DONE = "done"

    @classmethod
    def from_string(cls, value: str) -> StreamEventType:
        token = str(value or "").strip()
        if not token:
            return cls.ERROR

        # Upstream aliases (provider/kernel internal names) -> CLI contract names.
        alias_map: dict[str, StreamEventType] = {
            "chunk": cls.CONTENT_CHUNK,
            "reasoning_chunk": cls.THINKING_CHUNK,
            "tool_start": cls.TOOL_CALL,
            "tool_end": cls.TOOL_RESULT,
            "meta": cls.FINGERPRINT,
        }
        mapped = alias_map.get(token)
        if mapped is not None:
            return mapped

        try:
            return cls(token)
        except ValueError:
            logger.warning(
                "Unknown stream event type %r, treating as error",
                token,
                extra={"event_type_raw": token},
            )
            return cls.ERROR


def _from_string(value: str) -> StreamEventType:
    """Convert a string to a StreamEventType.

    Handles both canonical values and legacy kernel event type strings
    (``thinking_chunk``, ``content_chunk``) for backward compatibility.
    Unknown values are mapped to ``ERROR``.
    """
    return StreamEventType.from_string(value)


# ── Standard event dataclass ────────────────────────────────────────────────────


@dataclass
class StandardStreamEvent:
    """Canonical streaming event for the Director CLI layer.

    Attributes
    ----------
    type:
        One of the ``StreamEventType`` enum values.
    data:
        Event-specific payload.  The shape of ``data`` depends on ``type``:

        - ``chunk`` / ``reasoning_chunk``: ``{"content": str}``
        - ``tool_call``: ``{"tool": str, "args": dict}``
        - ``tool_result``: ``{"tool": str, "result": dict, "success": bool}``
        - ``fingerprint``: ``{"fingerprint": str|dict}``
        - ``complete``: ``{"content": str, "thinking": str|None}``
        - ``error``: ``{"error": str}``
        - ``done``: ``{}``
    metadata:
        Optional per-event metadata (e.g. timestamp, token count).  Empty by
        default; consumers may attach additional fields.
    """

    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event to a plain dict for streaming / JSON serialisation."""
        return {
            "type": self.type.value,
            "data": dict(self.data),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StandardStreamEvent:
        """Reconstruct a StandardStreamEvent from a plain dict (e.g. from JSON)."""
        raw_type = str(payload.get("type") or "")
        event_type = _from_string(raw_type)
        return cls(
            type=event_type,
            data=dict(payload.get("data") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )


# ── Conversion utilities ─────────────────────────────────────────────────────────


def to_standard_event(event: Any) -> StandardStreamEvent | None:
    """Normalise any upstream event format into a StandardStreamEvent.

    Supported input types
    --------------------
    - ``StandardStreamEvent``: returned as-is.
    - ``dict``: interpreted as a kernel event dict or a previously serialised
      StandardStreamEvent dict.
    - Anything else (None, strings, numbers, etc.): logged and returns ``None``,
      signalling that the caller should skip this event.

    Returns
    -------
    ``StandardStreamEvent`` when the event can be normalised, or ``None`` when
    the event should be silently skipped.
    """
    # Already the target type
    if isinstance(event, StandardStreamEvent):
        return event

    # Plain dict from kernel or serialised form
    if isinstance(event, dict):
        raw_type = str(event.get("type") or "").strip()
        if not raw_type:
            logger.debug("Skipping dict event with no type field: %s", event)
            return None

        event_type = _from_string(raw_type)

        # Extract the payload.  Kernel events store the event type at the top
        # level; serialised StandardStreamEvent wraps it under "data".
        if "data" in event:
            # Already in StandardStreamEvent serialised form
            data: dict[str, Any] = dict(event.get("data") or {})
            metadata: dict[str, Any] = dict(event.get("metadata") or {})
        else:
            # Kernel raw dict form — the full event IS the data payload
            data = dict(event)
            metadata = {}

        return StandardStreamEvent(
            type=event_type,
            data=data,
            metadata=metadata,
        )

    # Unexpected type — log and skip
    if event is not None:
        logger.warning(
            "Skipping event of unsupported type %s: %r",
            type(event).__name__,
            event,
        )
    return None


def from_kernel_event(kernel_event: dict[str, Any]) -> StandardStreamEvent | None:
    """Convert a ``RoleExecutionKernel`` raw event dict to a StandardStreamEvent.

    This is a specialised wrapper around ``to_standard_event`` that assumes the
    input is a kernel event dict.  It is useful when the caller already knows
    the event comes from the kernel and wants to document that in the type
    signature.

    Parameters
    ----------
    kernel_event:
        A dict emitted by ``RoleExecutionKernel.run_stream()``.  Required
        fields: ``type`` (str), and type-specific fields documented in
        ``StandardStreamEvent``.

    Returns
    -------
    ``StandardStreamEvent`` when the event has a recognised type, or ``None``
    when the event lacks a type field.
    """
    if not isinstance(kernel_event, dict):
        logger.warning(
            "from_kernel_event received non-dict input: %s",
            type(kernel_event).__name__,
        )
        return None

    raw_type = str(kernel_event.get("type") or "").strip()
    if not raw_type:
        logger.debug("Kernel event missing 'type' field, skipping: %s", kernel_event)
        return None

    event_type = _from_string(raw_type)

    return StandardStreamEvent(
        type=event_type,
        data=dict(kernel_event),
        metadata={"source": "RoleExecutionKernel"},
    )


__all__ = [
    "StandardStreamEvent",
    "StreamEventType",
    "from_kernel_event",
    "to_standard_event",
]
