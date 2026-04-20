"""AuditHashSink - UEP v2.0 Consumer for HMAC Chain AuditStore.

Subscribes to MessageBus audit events and appends them to the AuditStore
HMAC chain via KernelAuditStorePort.
"""

from __future__ import annotations

import logging
from datetime import datetime

from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType
from polaris.kernelone.audit.registry import get_audit_store
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.events.topics import TOPIC_RUNTIME_AUDIT
from polaris.kernelone.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


def _resolve_audit_event_type(event_type: str) -> KernelAuditEventType:
    """Map UEP event type to KernelAuditEventType."""
    mapping = {
        "call_start": KernelAuditEventType.LLM_CALL,
        "call_end": KernelAuditEventType.LLM_CALL,
        "call_error": KernelAuditEventType.LLM_CALL,
        "tool_execution": KernelAuditEventType.TOOL_EXECUTION,
        "task_start": KernelAuditEventType.TASK_START,
        "task_complete": KernelAuditEventType.TASK_COMPLETE,
        "task_failed": KernelAuditEventType.TASK_FAILED,
        "verification": KernelAuditEventType.VERIFICATION,
        "policy_check": KernelAuditEventType.POLICY_CHECK,
        "audit_verdict": KernelAuditEventType.AUDIT_VERDICT,
        "file_change": KernelAuditEventType.FILE_CHANGE,
        "security_violation": KernelAuditEventType.SECURITY_VIOLATION,
    }
    return mapping.get(event_type, KernelAuditEventType.INTERNAL_AUDIT_FAILURE)


async def _audit_hash_sink_handler(message: Message) -> None:
    """Async handler for MessageBus subscribe."""
    payload = message.payload
    if not isinstance(payload, dict):
        return
    if payload.get("topic") != TOPIC_RUNTIME_AUDIT:
        return

    workspace = str(payload.get("workspace") or "")
    run_id = str(payload.get("run_id") or "")
    role = str(payload.get("role") or "unknown")
    event_type_raw = str(payload.get("event_type") or "")
    data = payload.get("data", {})
    timestamp_raw = str(payload.get("timestamp") or "")

    if not workspace:
        logger.debug("AuditHashSink skipping event without workspace: run_id=%s", run_id)
        return

    try:
        from pathlib import Path

        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(workspace)
        runtime_root = Path(roots.runtime_root)
    except (RuntimeError, ValueError) as exc:
        logger.error(
            "AuditHashSink failed to resolve runtime_root: workspace=%s error=%s",
            workspace,
            exc,
        )
        return

    try:
        store = get_audit_store(runtime_root)
    except (RuntimeError, ValueError) as exc:
        logger.error(
            "AuditHashStore failed to resolve store: workspace=%s error=%s",
            workspace,
            exc,
        )
        return

    try:
        timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    except (RuntimeError, ValueError):
        timestamp = utc_now()

    audit_event = KernelAuditEvent(
        event_id=run_id or "unknown",
        timestamp=timestamp,
        event_type=_resolve_audit_event_type(event_type_raw),
        source={"role": role, "workspace": workspace, "event_type": event_type_raw},
        task={"run_id": run_id},
        resource={},
        action={"type": event_type_raw},
        data=dict(data),
        context={"uep": True},
    )

    try:
        store.append(audit_event)
        logger.debug(
            "AuditHashSink appended event: run_id=%s event_type=%s",
            run_id,
            event_type_raw,
        )
    except (RuntimeError, ValueError) as exc:
        logger.error(
            "AuditHashSink append failed: run_id=%s event_type=%s error=%s",
            run_id,
            event_type_raw,
            exc,
        )


class AuditHashSink:
    """UEP consumer that writes audit events to the HMAC chain."""

    def __init__(self, bus: MessageBus) -> None:
        """Initialize sink.

        Args:
            bus: MessageBus instance to subscribe to.
        """
        self._bus = bus
        self._subscribed = False

    async def start(self) -> None:
        """Subscribe to EVENT messages on the bus."""
        if not self._subscribed:
            await self._bus.subscribe(MessageType.RUNTIME_EVENT, _audit_hash_sink_handler)
            self._subscribed = True
            logger.info("AuditHashSink started")

    async def stop(self) -> None:
        """Unsubscribe from EVENT messages."""
        if self._subscribed:
            await self._bus.unsubscribe(MessageType.RUNTIME_EVENT, _audit_hash_sink_handler)
            self._subscribed = False
            logger.info("AuditHashSink stopped")


__all__ = ["AuditHashSink", "_audit_hash_sink_handler"]
