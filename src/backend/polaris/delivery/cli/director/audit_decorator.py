"""@audit_stream_turn audit decorator for the Director TUI.

Wraps the ``stream_turn`` async generator so that every event emitted
is:
1. Fingerprinted (for tool_call / tool_result events).
2. Published to the audit bus as an ``AuditStreamEventV1``.
3. Collected for end-of-turn archival via the ``StreamArchiver``.

Architecture constraints
-----------------------
- All text I/O uses UTF-8.
- Audit events go through ``bus.publish``; no direct file writes here.
- Silent exception swallowing is prohibited: all failures are logged
  with structured fields.
- Events are never dropped: even on archive failure they are published
  to the bus first.

Usage
-----
Apply to any method that follows the ``stream_turn`` calling convention::

    @audit_stream_turn(bus=my_bus, workspace="/path/to/workspace")
    async def stream_turn(self, session_id: str, user_text: str, ...):
        async for event in self._underlying_stream(...):
            yield event

The decorator is method-oriented (expects ``self`` as first positional arg)
but is intentionally bus-agnostic so it can be tested in isolation.
"""

from __future__ import annotations

import functools
import logging
import warnings
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from polaris.kernelone.events.message_bus import MessageBus

import contextlib

from polaris.kernelone.events.message_bus import MessageType
from polaris.kernelone.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bus message type (shared with consumers via audit.evidence)
# ---------------------------------------------------------------------------

_AUDIT_STREAM_TOPIC = "audit.stream.turn"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Backward compatibility alias
_utc_now = utc_now_iso


async def _publish_audit_event(
    bus: MessageBus | None,
    topic: str,
    payload: dict[str, Any],
) -> None:
    """Publish one audit event, logging any failure (never raising)."""
    if bus is None:
        logger.debug("No bus available; skipping publish to %s", topic)
        return

    try:
        # MessageBus.publish expects a Message; construct one ad-hoc
        from polaris.kernelone.events.message_bus import Message

        msg = Message(
            type=MessageType.AUDIT_COMPLETED,
            sender="audit_stream_turn",
            recipient=None,
            payload=payload,
        )
        await bus.publish(msg)
    except (RuntimeError, ValueError) as exc:
        # Architecture constraint: never silently swallow audit failures
        logger.error(
            "audit_stream_turn publish failed: topic=%s error=%s payload_keys=%s",
            topic,
            exc,
            list(payload.keys()),
        )


def _audit_event_payload(
    turn_id: str,
    session_id: str,
    event: dict[str, Any],
    # TODO: ToolFingerprint exposed via public contract (audit.diagnosis.public)
    fingerprint: Any,
) -> dict[str, Any]:
    """Build a structured audit event payload from a stream_turn event."""
    event_type = str(event.get("type", ""))
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}

    payload: dict[str, Any] = {
        "turn_id": turn_id,
        "session_id": session_id,
        "event_type": event_type,
        "timestamp": _utc_now(),
        "data": data,
    }

    if fingerprint is not None and hasattr(fingerprint, "to_dict"):
        payload["fingerprint"] = fingerprint.to_dict()

    return payload


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def audit_stream_turn(
    *,
    bus: MessageBus | None = None,
    workspace: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory: wraps ``stream_turn`` with full audit trail.

    Parameters
    ----------
    bus:
        Optional ``MessageBus`` instance.  When provided, every event is
        published to the bus under the ``audit.stream.turn`` topic.
        If None, events are still collected and archived but not published.
    workspace:
        Workspace path used to initialise the ``StreamArchiver`` when
        archiving the turn.  Required for archival; a warning is logged
        if omitted.

    Returns
    -------
    A decorator that replaces the wrapped method with an audited version.
    """

    def decorator(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        async def wrapper(
            self: Any,
            session_id: str,
            user_text: str,
            *args: Any,
            **kwargs: Any,
        ) -> AsyncIterator[dict[str, Any]]:
            warnings.warn(
                "@audit_stream_turn is deprecated. UEP v2.0 sinks handle stream audit automatically.",
                DeprecationWarning,
                stacklevel=2,
            )
            # UEP v2.0: transparent pass-through. JournalSink and ArchiveSink
            # consume events from the MessageBus, so manual wrapping is no longer
            # required and would produce duplicates.
            async for event in method(self, session_id, user_text, *args, **kwargs):
                yield event

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Global bus registry (matches kernelone/events/registry.py pattern)
# ---------------------------------------------------------------------------

_global_bus: MessageBus | None = None


def _get_global_bus() -> MessageBus | None:
    """Look up the globally registered bus, logging resolution status."""
    global _global_bus
    if _global_bus is None:
        try:
            from polaris.kernelone.events.registry import get_global_bus

            _global_bus = get_global_bus()
        except (RuntimeError, ValueError) as exc:
            logger.debug("Global bus not available: %s", exc)
    return _global_bus


def register_audit_bus(bus: MessageBus) -> None:
    """Allow callers to inject the bus before the first stream call."""
    global _global_bus
    _global_bus = bus


# ---------------------------------------------------------------------------
# Internal archival helper (lazy import to avoid cross-cell import cycles)
# ---------------------------------------------------------------------------


def _set_archive_id(archive_id: str, events: list[dict[str, Any]]) -> None:
    """Patch archive_id into the first event for traceability."""
    if events:
        events[0]["archive_id"] = archive_id


async def _archive_turn_events(
    workspace: str,
    session_id: str,
    turn_id: str,
    events: list[dict[str, Any]],
    on_archive_id: Callable[[str], None],
    on_error: Callable[[str], None],
) -> None:
    """Archive collected events via StreamArchiver.

    This is a fire-and-forget background operation:
    - Failures are logged but never raised (archival is best-effort).
    - Archive ID is patched back into the events list for traceability.
    """
    if not workspace:
        logger.warning(
            "audit_stream_turn: workspace not set; skipping archival: turn_id=%s",
            turn_id,
        )
        return

    try:
        # Lazy import to keep audit_decorator.py independent of archive internals.
        # TODO: StreamArchiver.archive_turn() has turn-level semantics (session_id, turn_id, events)
        # which differs from public archive_run() (run-level). This needs a public contract
        # via polaris.cells.archive.run_archive.public before this lazy import can be removed.
        # Technical debt: tracked in delivery跨层导入修复 issue.
        from polaris.cells.archive.run_archive.public.service import create_stream_archiver

        archiver = create_stream_archiver(workspace)
        archive_id = await archiver.archive_turn(
            session_id=session_id,
            turn_id=turn_id,
            events=events,
        )
        on_archive_id(archive_id)
        logger.info(
            "audit_stream_turn archived: turn_id=%s archive_id=%s event_count=%s",
            turn_id,
            archive_id,
            len(events),
        )
    except (RuntimeError, ValueError) as exc:
        # Archival failure is logged but never raises:
        # events are already on the bus, so traceability is not lost.
        logger.error(
            "audit_stream_turn archival failed (events on bus are safe): turn_id=%s error=%s",
            turn_id,
            exc,
        )
        with contextlib.suppress(Exception):
            on_error(str(exc))


# ---------------------------------------------------------------------------
# Convenience: apply decorator to a RoleConsoleHost instance
# ---------------------------------------------------------------------------


def apply_audit_decorator(
    host_instance: Any,
    *,
    bus: MessageBus | None = None,
    workspace: str = "",
) -> None:
    """Apply ``@audit_stream_turn`` to the ``stream_turn`` method of a host instance.

    .. deprecated::
        UEP v2.0 sinks handle stream audit automatically. This function is
        retained for backward compatibility but does nothing.
    """
    warnings.warn(
        "apply_audit_decorator is deprecated. UEP v2.0 sinks handle stream audit automatically.",
        DeprecationWarning,
        stacklevel=2,
    )
    # UEP v2.0: transparent no-op. The decorator is deprecated and produces
    # a warning, but we keep the method replacement to avoid breaking callers.
    if not hasattr(host_instance, "stream_turn"):
        raise TypeError(f"Expected object with 'stream_turn' method; got {type(host_instance).__name__}")

    original_method = host_instance.stream_turn

    # Build a decorated version
    decorated = audit_stream_turn(bus=bus, workspace=workspace)(original_method)

    # Replace on the instance
    host_instance.stream_turn = decorated

    logger.info(
        "audit_stream_turn applied: workspace=%s bus=%s",
        workspace,
        "provided" if bus else "global/default",
    )


__all__ = [
    "apply_audit_decorator",
    "audit_stream_turn",
    "register_audit_bus",
]
