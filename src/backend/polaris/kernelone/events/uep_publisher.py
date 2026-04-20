"""UEP (Unified Event Pipeline) v2.0 Publisher.

Provides a unified interface for publishing runtime events to the Polaris
MessageBus and TypedEvent system. All producers should use this publisher
instead of manually constructing Bus messages.

Architecture:
    UEPEventPublisher.publish_*()
            │
            ▼
    ┌───────────────────────────────────────┐
    │  TypedEventBusAdapter.emit_to_both()   │
    │  (双重写入 when adapter available)    │
    └───────────────┬───────────────────────┘
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    EventRegistry        MessageBus
    (实时订阅)           (持久化 Sink)
                              │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
    JournalSink          ArchiveSink          AuditHashSink
    ──► journal.jsonl  ──► stream_events.gz ──► HMAC chain
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.events.message_bus import Message, MessageType
from polaris.kernelone.events.registry import get_global_bus
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.utils import utc_now_iso

from .topics import (
    TOPIC_RUNTIME_AUDIT,
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_STREAM,
)
from .uep_contracts import (
    UEPAuditEventPayload,
    UEPFingerprintEventPayload,
    UEPLifecycleEventPayload,
    UEPStreamEventPayload,
)
from .uep_typed_converter import UEPToTypedEventConverter

if TYPE_CHECKING:
    from polaris.kernelone.events.typed import TypedEventBusAdapter

logger = logging.getLogger(__name__)


class UEPEventPublisher:
    """Unified Event Pipeline publisher.

    Publishes canonical runtime events through TypedEventBusAdapter (when available)
    for dual-write to both TypedEvent system (real-time subscriptions) and
    MessageBus (persistence via Sinks).

    Falls back to direct MessageBus publish if adapter is unavailable.

    Architecture:
        UEPEventPublisher.publish_*()
                │
                ▼
        TypedEventBusAdapter.emit_to_both()
                │
        ┌───────┴───────┐
        ▼               ▼
    EventRegistry   MessageBus
    (实时订阅)      (持久化 Sink)
    """

    def __init__(self, bus: Any | None = None) -> None:
        """Initialize publisher.

        Args:
            bus: Optional MessageBus instance. Defaults to global bus.
        """
        self._bus = bus
        self._converter = UEPToTypedEventConverter()
        self._adapter: TypedEventBusAdapter | None = None

    def _get_bus(self) -> Any | None:
        if self._bus is not None:
            return self._bus
        try:
            return get_global_bus()
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to get global bus: %s", e)
            return None

    def _get_adapter(self) -> TypedEventBusAdapter | None:
        """Get the TypedEventBusAdapter singleton.

        Returns:
            TypedEventBusAdapter if initialized, None otherwise.
        """
        if self._get_bus() is None:
            return None

        if self._adapter is not None:
            return self._adapter

        try:
            # Lazy import to avoid circular dependency
            from polaris.kernelone.events.typed import get_default_adapter

            adapter = get_default_adapter()
            if adapter is not None:
                self._adapter = adapter
            return adapter
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to get default adapter: %s", e)
            return None

    def _utc_now(self) -> str:
        return utc_now_iso()

    async def publish_stream_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
    ) -> bool:
        """Publish a stream chunk/tool_call/complete/error event.

        Publishes through TypedEventBusAdapter when available for dual-write,
        otherwise falls back to direct MessageBus publish.

        Args:
            workspace: Workspace path.
            run_id: Run identifier.
            role: Role name.
            event_type: Stream event type (content_chunk, tool_call, etc.).
            payload: Event payload dict.
            turn_id: Optional turn identifier.

        Returns:
            True if published successfully, False otherwise.
        """
        # Build UEP payload dict for conversion
        uep_payload: dict[str, Any] = {
            "topic": TOPIC_RUNTIME_STREAM,
            "event_type": event_type,
            "workspace": workspace,
            "run_id": run_id,
            "role": role,
            "turn_id": turn_id,
            "payload": dict(payload),
            "timestamp": self._utc_now(),
        }

        # Try TypedEventBusAdapter first (dual-write mode)
        adapter = self._get_adapter()
        if adapter is not None:
            return await self._emit_via_adapter(adapter, uep_payload)

        # Fallback: direct MessageBus publish
        return await self._publish_stream_to_bus(workspace, run_id, role, event_type, payload, turn_id)

    async def _emit_via_adapter(
        self,
        adapter: TypedEventBusAdapter,
        uep_payload: dict[str, Any],
    ) -> bool:
        """Emit event through TypedEventBusAdapter.

        Args:
            adapter: TypedEventBusAdapter instance.
            uep_payload: UEP payload dict.

        Returns:
            True if published successfully, False otherwise.
        """
        try:
            typed_event = self._converter.convert(uep_payload)
            if typed_event is not None:
                # Dual-write: emit_to_both publishes typed event to EventRegistry + MessageBus
                # (typed MessageType). _publish_via_bus_from_payload additionally publishes
                # raw UEP payload as RUNTIME_EVENT so JournalSink (which subscribes to
                # RUNTIME_EVENT only) receives it.
                await adapter.emit_to_both(typed_event)
                await self._publish_via_bus_from_payload(uep_payload)
                return True
            else:
                # Fallback to direct bus publish if conversion fails
                logger.debug(
                    "TypedEvent conversion failed, falling back to direct publish: %s",
                    uep_payload.get("event_type"),
                )
                return await self._publish_via_bus_from_payload(uep_payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "TypedEventBusAdapter emit failed, falling back to direct publish: %s",
                exc,
            )
            return await self._publish_via_bus_from_payload(uep_payload)

    async def _publish_via_bus_from_payload(self, payload: dict[str, Any]) -> bool:
        """Publish event directly to MessageBus from UEP payload dict.

        When the global bus is unavailable (e.g., benchmark runs without
        assemble_core_services()), falls back to writing directly to the
        journal JSONL file to ensure events are never silently dropped.

        Args:
            payload: UEP payload dict with topic key.

        Returns:
            True if published successfully, False otherwise.
        """
        bus = self._get_bus()
        if bus is None:
            # Fallback: write directly to journal JSONL when bus is unavailable.
            # This mirrors the legacy emit_llm_event() behavior for llm_call_end,
            # ensuring tool_call/tool_result events are persisted even when
            # UEP sinks (JournalSink) are not registered.
            self._emit_stream_event_to_disk_fallback(payload)
            return False

        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="uep.publisher",
            recipient=None,
            payload=payload,
        )
        try:
            await bus.publish(msg)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error("UEP direct publish failed: topic=%s error=%s", payload.get("topic"), exc)
            return False

    def _emit_stream_event_to_disk_fallback(self, payload: dict[str, Any]) -> None:
        """Write stream event directly to journal JSONL when MessageBus is unavailable.

        This is the safety-net fallback for benchmark scenarios where
        assemble_core_services() was not called, ensuring tool_call and
        tool_result events are persisted to the same journal file that
        llm_call_end events write to.

        Path: {runtime_root}/events/{role}.llm.events.jsonl
        """
        try:
            import json
            import os
            import time
            import uuid

            from polaris.kernelone.utils import utc_now_iso

            run_id = str(payload.get("run_id") or "").strip()
            if not run_id:
                logger.debug("UEP stream event dropped (no run_id): topic=%s", payload.get("topic"))
                return

            workspace = str(payload.get("workspace") or "").strip()
            if not workspace:
                workspace = os.environ.get("POLARIS_WORKSPACE", os.getcwd())
            workspace = os.path.abspath(workspace)

            role = str(payload.get("role") or "unknown").strip().lower() or "unknown"
            event_type = str(payload.get("event_type") or "").strip()
            topic = str(payload.get("topic") or "").strip()

            # Resolve runtime root via storage layout
            try:
                from polaris.cells.storage.layout import resolve_polaris_roots

                roots = resolve_polaris_roots(workspace)
                runtime_root = roots.runtime_root
            except (RuntimeError, ValueError):
                runtime_root = resolve_runtime_path(workspace, "runtime")

            # Build journal path: {runtime_root}/events/{role}.llm.events.jsonl
            events_dir = os.path.join(runtime_root, "events")
            os.makedirs(events_dir, exist_ok=True)
            journal_path = os.path.join(events_dir, f"{role}.llm.events.jsonl")

            # Build payload matching emit_llm_event_to_disk structure
            data = payload.get("payload", {})
            journal_entry = {
                "schema_version": 1,
                "ts": utc_now_iso(),
                "ts_epoch": time.time(),
                "seq": int(time.time() * 1000) % 1000000,
                "event_id": str(uuid.uuid4())[:8],
                "run_id": run_id,
                "iteration": data.get("iteration", 0),
                "role": role,
                "source": "uep.publisher.fallback",
                "event": event_type,
                "data": {
                    "event_type": event_type,
                    "role": role,
                    "run_id": run_id,
                    "topic": topic,
                    **data,
                },
            }

            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(journal_entry, ensure_ascii=False) + "\n")

            logger.debug(
                "UEP stream event written to fallback journal: path=%s, event=%s, run_id=%s",
                journal_path,
                event_type,
                run_id,
            )
        except (RuntimeError, ValueError):
            # Audit emission must never break the main flow.
            logger.warning(
                "UEP stream event fallback journal write failed (event will be dropped): topic=%s",
                payload.get("topic"),
                exc_info=True,
            )

    async def _publish_stream_to_bus(
        self,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None,
    ) -> bool:
        """Publish stream event directly to MessageBus (fallback path).

        Args:
            workspace: Workspace path.
            run_id: Run identifier.
            role: Role name.
            event_type: Stream event type.
            payload: Event payload dict.
            turn_id: Optional turn identifier.

        Returns:
            True if published successfully, False otherwise.
        """
        bus = self._get_bus()
        if bus is None:
            # No bus configured - this is expected in CLI mode without message bus.
            # Log at debug level to avoid debug noise while still tracking the path.
            logger.debug("UEP lifecycle event skipped: no message bus configured")
            return False

        event_payload = UEPStreamEventPayload(
            workspace=workspace,
            run_id=run_id,
            role=role,
            turn_id=turn_id,
            event_type=event_type,
            payload=dict(payload),
            timestamp=self._utc_now(),
        )
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="uep.publisher",
            recipient=None,
            payload={"topic": event_payload.topic, **event_payload.__dict__},
        )
        try:
            await bus.publish(msg)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error(
                "UEP publish_stream_event failed: run_id=%s event_type=%s error=%s",
                run_id,
                event_type,
                exc,
            )
            return False

    async def publish_llm_lifecycle_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Publish an LLM lifecycle event.

        Publishes through TypedEventBusAdapter when available for dual-write.

        Args:
            workspace: Workspace path.
            run_id: Run identifier.
            role: Role name.
            event_type: Lifecycle event type (call_start, call_end, etc.).
            metadata: Optional metadata dict.

        Returns:
            True if published successfully, False otherwise.
        """
        uep_payload: dict[str, Any] = {
            "topic": TOPIC_RUNTIME_LLM,
            "event_type": event_type,
            "workspace": workspace,
            "run_id": run_id,
            "role": role,
            "payload": {},
            "metadata": dict(metadata) if metadata else {},
            "timestamp": self._utc_now(),
        }

        adapter = self._get_adapter()
        if adapter is not None:
            return await self._emit_via_adapter(adapter, uep_payload)

        return await self._publish_llm_to_bus(workspace, run_id, role, event_type, metadata)

    async def _publish_llm_to_bus(
        self,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        metadata: dict[str, Any] | None,
    ) -> bool:
        """Publish LLM lifecycle event directly to MessageBus (fallback)."""
        bus = self._get_bus()
        if bus is None:
            # No bus configured - expected in CLI mode without message bus
            return False

        event_payload = UEPLifecycleEventPayload(
            workspace=workspace,
            run_id=run_id,
            role=role,
            event_type=event_type,
            metadata=dict(metadata) if metadata else {},
            timestamp=self._utc_now(),
        )
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="uep.publisher",
            recipient=None,
            payload={"topic": event_payload.topic, **event_payload.__dict__},
        )
        try:
            await bus.publish(msg)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error(
                "UEP publish_llm_lifecycle_event failed: run_id=%s event_type=%s error=%s",
                run_id,
                event_type,
                exc,
            )
            return False

    async def publish_fingerprint_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        fingerprint: dict[str, Any],
    ) -> bool:
        """Publish a strategy fingerprint event.

        Args:
            workspace: Workspace path.
            run_id: Run identifier.
            role: Role name.
            fingerprint: Fingerprint payload dict.

        Returns:
            True if published successfully, False otherwise.
        """
        uep_payload: dict[str, Any] = {
            "topic": TOPIC_RUNTIME_FINGERPRINT,
            "event_type": "fingerprint",
            "workspace": workspace,
            "run_id": run_id,
            "role": role,
            "payload": dict(fingerprint),
            "timestamp": self._utc_now(),
        }

        adapter = self._get_adapter()
        if adapter is not None:
            return await self._emit_via_adapter(adapter, uep_payload)

        return await self._publish_fingerprint_to_bus(workspace, run_id, role, fingerprint)

    async def _publish_fingerprint_to_bus(
        self,
        workspace: str,
        run_id: str,
        role: str,
        fingerprint: dict[str, Any],
    ) -> bool:
        """Publish fingerprint event directly to MessageBus (fallback)."""
        bus = self._get_bus()
        if bus is None:
            # No bus configured - expected in CLI mode without message bus
            return False

        event_payload = UEPFingerprintEventPayload(
            workspace=workspace,
            run_id=run_id,
            role=role,
            fingerprint=dict(fingerprint),
            timestamp=self._utc_now(),
        )
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="uep.publisher",
            recipient=None,
            payload={"topic": event_payload.topic, **event_payload.__dict__},
        )
        try:
            await bus.publish(msg)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error(
                "UEP publish_fingerprint_event failed: run_id=%s error=%s",
                run_id,
                exc,
            )
            return False

    async def publish_audit_event(
        self,
        *,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """Publish an audit event.

        Args:
            workspace: Workspace path.
            run_id: Run identifier.
            role: Role name.
            event_type: Audit event type.
            data: Optional data dict.

        Returns:
            True if published successfully, False otherwise.
        """
        uep_payload: dict[str, Any] = {
            "topic": TOPIC_RUNTIME_AUDIT,
            "event_type": event_type,
            "workspace": workspace,
            "run_id": run_id,
            "role": role,
            "payload": {"event_type": event_type, "data": dict(data) if data else {}},
            "timestamp": self._utc_now(),
        }

        adapter = self._get_adapter()
        if adapter is not None:
            return await self._emit_via_adapter(adapter, uep_payload)

        return await self._publish_audit_to_bus(workspace, run_id, role, event_type, data)

    async def _publish_audit_to_bus(
        self,
        workspace: str,
        run_id: str,
        role: str,
        event_type: str,
        data: dict[str, Any] | None,
    ) -> bool:
        """Publish audit event directly to MessageBus (fallback)."""
        bus = self._get_bus()
        if bus is None:
            # No bus configured - expected in CLI mode without message bus
            return False

        event_payload = UEPAuditEventPayload(
            workspace=workspace,
            run_id=run_id,
            role=role,
            event_type=event_type,
            data=dict(data) if data else {},
            timestamp=self._utc_now(),
        )
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="uep.publisher",
            recipient=None,
            payload={"topic": event_payload.topic, **event_payload.__dict__},
        )
        try:
            await bus.publish(msg)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.error(
                "UEP publish_audit_event failed: run_id=%s event_type=%s error=%s",
                run_id,
                event_type,
                exc,
            )
            return False
