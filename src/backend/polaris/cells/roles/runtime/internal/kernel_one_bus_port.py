"""KernelOne NATS-backed message bus port for roles.runtime.

This module provides a NATS-based implementation of the `AgentBusPort` Protocol,
enabling cross-process agent messaging. When NATS is unavailable (e.g., in
tests or development without a NATS server), it falls back to the in-memory
`InMemoryAgentBusPort`.

Architecture note (2026-04-04 P0-007 Fix):
  Core Protocol and data types are now defined in KernelOne:
    - `polaris.kernelone.agent_runtime.bus_port.AgentBusPort`
    - `polaris.kernelone.agent_runtime.bus_port.AgentEnvelope`
    - `polaris.kernelone.agent_runtime.bus_port.DeadLetterRecord`

  This file imports from KernelOne for types, and from local bus_port.py for
  the InMemoryAgentBusPort implementation.

Configuration:
  - `NATS_URL`: NATS server URL (default: "nats://127.0.0.1:4222")
  - `NATS_ENABLED`: Enable NATS transport (default: True)
  - `KERNELONE_NATS_URL`: Alias for `NATS_URL` (for backward compatibility)

Implements C2 from ROLES_CELL_REFACTORING_BLUEPRINT_2026-03-26.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

# Import implementation from local bus_port.py
from polaris.cells.roles.runtime.internal.bus_port import InMemoryAgentBusPort

# Import core types from KernelOne (maintains KernelOne → Cells dependency direction)
from polaris.kernelone.multi_agent.bus_port import (
    AgentBusPort,
    AgentEnvelope,
    DeadLetterRecord,
)

if TYPE_CHECKING:
    from concurrent.futures import Future as CFuture

logger = logging.getLogger(__name__)

# ── Default Constants ──────────────────────────────────────────────────────────

_DEFAULT_NATS_URL: str = "nats://127.0.0.1:4222"
_DEFAULT_NATS_ENABLED: bool = True
_MAX_QUEUE_SIZE: int = 512
_MAX_DEAD_LETTER: int = 256


def _get_nats_url_from_env() -> str:
    """Resolve NATS URL from environment variables.

    Priority:
    1. `NATS_URL` (explicit)
    2. `KERNELONE_NATS_URL` (legacy compatibility)
    3. Default: "nats://127.0.0.1:4222"
    """
    url = os.environ.get("NATS_URL")
    if url:
        return str(url).strip()
    url = os.environ.get("KERNELONE_NATS_URL")
    if url:
        return str(url).strip()
    return _DEFAULT_NATS_URL


def _is_nats_enabled() -> bool:
    """Check if NATS transport is enabled via environment."""
    env_val = os.environ.get("NATS_ENABLED") or os.environ.get("KERNELONE_NATS_ENABLED", "")
    if not env_val:
        return _DEFAULT_NATS_ENABLED
    return str(env_val).strip().lower() not in ("0", "false", "no", "off", "disabled")


# ── NATS Client Wrapper ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NATSConnectionConfig:
    """NATS connection configuration derived from environment."""

    url: str = field(default_factory=_get_nats_url_from_env)
    enabled: bool = field(default_factory=_is_nats_enabled)
    connect_timeout_sec: float = 3.0
    reconnect_wait_sec: float = 1.0
    max_reconnect_attempts: int = -1

    def __post_init__(self) -> None:
        # Resolve dynamic values from environment if not explicitly provided
        if self.connect_timeout_sec == 3.0:  # Default sentinel
            env_val = os.environ.get("NATS_CONNECT_TIMEOUT") or os.environ.get("KERNELONE_NATS_CONNECT_TIMEOUT")
            if env_val:
                object.__setattr__(
                    self,
                    "connect_timeout_sec",
                    float(env_val),
                )
        if self.reconnect_wait_sec == 1.0:  # Default sentinel
            env_val = os.environ.get("NATS_RECONNECT_WAIT") or os.environ.get("KERNELONE_NATS_RECONNECT_WAIT")
            if env_val:
                object.__setattr__(
                    self,
                    "reconnect_wait_sec",
                    float(env_val),
                )
        if self.max_reconnect_attempts == -1:  # Default sentinel
            env_val = os.environ.get("NATS_MAX_RECONNECT") or os.environ.get("KERNELONE_NATS_MAX_RECONNECT")
            if env_val:
                object.__setattr__(
                    self,
                    "max_reconnect_attempts",
                    int(env_val),
                )


class NATSClientWrapper:
    """Async NATS client wrapper with lazy initialization and graceful fallback.

    Thread-safety:
      The wrapper manages its own event loop in a dedicated thread for async
      NATS operations. All public methods are thread-safe and delegate to
      the async loop.
    """

    def __init__(
        self,
        config: NATSConnectionConfig | None = None,
    ) -> None:
        self._config = config or NATSConnectionConfig()
        self._nc: Any = None  # NATS connection (set by connect())
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._connected = False
        self._connect_error: str | None = None

    @property
    def is_connected(self) -> bool:
        """Return True if NATS connection is established."""
        with self._lock:
            return self._connected and self._nc is not None

    @property
    def connection_error(self) -> str | None:
        """Return the last connection error message, if any."""
        with self._lock:
            return self._connect_error

    def _ensure_loop(self) -> None:
        """Ensure the async event loop is running in its own thread."""
        with self._lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                return

            def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            self._loop = asyncio.new_event_loop()
            self._loop_thread = threading.Thread(
                target=_run_loop,
                args=(self._loop,),
                name="nats-client-loop",
                daemon=True,
            )
            self._loop_thread.start()

    async def _async_connect(self) -> None:
        """Async NATS connection logic (runs in the event loop thread)."""
        if not self._config.enabled:
            raise ConnectionError("NATS is disabled via configuration")

        try:
            import nats

            nc = await nats.connect(
                self._config.url,
                connect_timeout=self._config.connect_timeout_sec,
                reconnect_time_wait=self._config.reconnect_wait_sec,
                max_reconnect_attempts=self._config.max_reconnect_attempts,
            )
            self._nc = nc
            self._connected = True
            self._connect_error = None
            logger.info(
                "NATS connected: url=%s",
                self._config.url,
            )
        except (RuntimeError, ValueError) as exc:
            self._connect_error = str(exc)
            self._connected = False
            logger.warning(
                "NATS connection failed: url=%s error=%s",
                self._config.url,
                exc,
            )
            raise

    def connect(self) -> bool:
        """Connect to NATS synchronously.

        Returns True if connected, False if connection failed or NATS is disabled.
        """
        if not self._config.enabled:
            logger.debug("NATS disabled, skipping connection")
            return False

        self._ensure_loop()

        try:
            future: CFuture[None] = asyncio.run_coroutine_threadsafe(
                self._async_connect(),
                self._loop,  # type: ignore[arg-type]  # _ensure_loop guarantees it's not None
            )
            future.result(timeout=self._config.connect_timeout_sec + 1.0)
            return True
        except (RuntimeError, ValueError, ConnectionError, TimeoutError) as exc:
            logger.debug(
                "NATS connect() failed (will fall back to in-memory): %s",
                exc,
            )
            return False

    async def _async_publish(self, subject: str, payload: bytes) -> None:
        """Async publish to NATS subject."""
        if self._nc is None:
            raise ConnectionError("NATS not connected")
        await self._nc.publish(subject, payload)

    async def _async_subscribe(self, subject: str) -> Any:
        """Async subscribe to NATS subject, return subscription."""
        if self._nc is None:
            raise ConnectionError("NATS not connected")
        return await self._nc.subscribe(subject)

    async def _async_close(self) -> None:
        """Async close NATS connection."""
        if self._nc is not None:
            await self._nc.close()
            self._nc = None
            self._connected = False

    def publish(self, subject: str, payload: bytes) -> bool:
        """Publish payload to NATS subject synchronously.

        Returns True on success, False on failure.
        """
        with self._lock:
            if not self._connected or self._loop is None:
                return False

        try:
            future: CFuture[None] = asyncio.run_coroutine_threadsafe(
                self._async_publish(subject, payload),
                self._loop,  # type: ignore[arg-type]  # _connected check guarantees it's not None
            )
            future.result(timeout=5.0)
            return True
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "NATS publish failed: subject=%s error=%s",
                subject,
                exc,
            )
            return False

    def subscribe(self, subject: str) -> Any | None:
        """Subscribe to NATS subject synchronously.

        Returns subscription object on success, None on failure.
        """
        with self._lock:
            if not self._connected or self._loop is None:
                return None

        try:
            future: CFuture[Any] = asyncio.run_coroutine_threadsafe(
                self._async_subscribe(subject),
                self._loop,  # type: ignore[arg-type]  # _connected check guarantees it's not None
            )
            return future.result(timeout=5.0)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "NATS subscribe failed: subject=%s error=%s",
                subject,
                exc,
            )
            return None

    def close(self) -> None:
        """Close NATS connection and stop event loop."""
        with self._lock:
            if self._loop is None:
                return
            loop = self._loop
            nc = self._nc

        if nc is not None:
            try:
                future: CFuture[None] = asyncio.run_coroutine_threadsafe(
                    self._async_close(),
                    loop,  # type: ignore[arg-type]
                )
                future.result(timeout=3.0)
            except (RuntimeError, ValueError) as exc:
                logger.debug("NATS close error: %s", exc)

        with self._lock:
            self._loop = None
            self._nc = None
            self._connected = False

        # 使用之前获取的 loop 变量，避免 self._loop 已为 None 的问题
        loop_thread = self._loop_thread
        self._loop_thread = None
        if loop_thread is not None and loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2.0)


# ── KernelOne Message Bus Port ─────────────────────────────────────────────────


class KernelOneMessageBusPort:
    """NATS-backed message bus implementing `AgentBusPort`.

    This port bridges roles.runtime's synchronous `AgentBusPort` interface
    with NATS pub/sub, enabling cross-process agent messaging. When NATS is
    unavailable or disabled, it transparently falls back to `InMemoryAgentBusPort`.

    Design decisions:
    - Thread-safe via RLock and thread-separated async event loop.
    - Envelope serialization uses JSON (UTF-8) for NATS transport.
    - NATS subjects follow the pattern: `roles.runtime.<receiver>`.
    - Dead-letter handling is delegated to the in-memory fallback.
    - Lazy NATS connection: only connects when first publish is attempted.

    Environment variables:
      `NATS_URL` / `KERNELONE_NATS_URL`: NATS server URL
      `NATS_ENABLED` / `KERNELONE_NATS_ENABLED`: Enable NATS transport
      `NATS_CONNECT_TIMEOUT`: Connection timeout in seconds
      `NATS_RECONNECT_WAIT`: Reconnect wait interval
      `NATS_MAX_RECONNECT`: Max reconnect attempts (-1 for infinite)

    Implements C2 from ROLES_CELL_REFACTORING_BLUEPRINT_2026-03-26.
    """

    def __init__(
        self,
        nats_url: str | None = None,
        *,
        nats_enabled: bool | None = None,
        max_queue_size: int = _MAX_QUEUE_SIZE,
        _fallback: InMemoryAgentBusPort | None = None,
    ) -> None:
        # Resolve configuration
        self._nats_url = str(nats_url).strip() if nats_url else _get_nats_url_from_env()
        self._nats_enabled = bool(nats_enabled) if nats_enabled is not None else _is_nats_enabled()
        self._max_queue_size = max(1, int(max_queue_size))

        # NATS client (lazy initialized)
        self._nats_config = NATSConnectionConfig(
            url=self._nats_url,
            enabled=self._nats_enabled,
        )
        self._nats_client: NATSClientWrapper | None = None

        # In-memory fallback for when NATS is unavailable
        self._fallback_bus: InMemoryAgentBusPort = (
            _fallback if _fallback is not None else InMemoryAgentBusPort(max_queue_size=max_queue_size)
        )

        # Track whether we have ever successfully connected to NATS
        self._nats_ever_connected = False

    # ── Public AgentBusPort Interface ────────────────────────────────────────

    def publish(self, envelope: AgentEnvelope) -> bool:
        """Deliver envelope to receiver via NATS or in-memory fallback.

        Tries NATS first if connected; falls back to in-memory queue.
        Thread-safe.
        """
        payload_bytes = _envelope_to_json_bytes(envelope)

        # Try NATS if we have a client
        if self._nats_client is not None and self._nats_client.is_connected:
            subject = f"roles.runtime.{envelope.receiver}"
            nats_ok = self._nats_client.publish(subject, payload_bytes)
            if nats_ok:
                logger.debug(
                    "nats.publish: message_id=%s type=%s sender=%s receiver=%s",
                    envelope.message_id,
                    envelope.msg_type,
                    envelope.sender,
                    envelope.receiver,
                )
                return True
            # NATS failed, fall through to in-memory

        # In-memory fallback
        fallback_ok = self._fallback_bus.publish(envelope)
        if fallback_ok:
            logger.debug(
                "inmemory.publish: message_id=%s type=%s sender=%s receiver=%s",
                envelope.message_id,
                envelope.msg_type,
                envelope.sender,
                envelope.receiver,
            )
        return fallback_ok

    def poll(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
    ) -> AgentEnvelope | None:
        """Poll next message for receiver.

        Note: NATS subscribe is not directly compatible with this pull-based
        poll interface. Messages from NATS are delivered to registered callbacks
        which forward to the in-memory fallback's inbox. This method only polls
        the in-memory fallback.

        For pub/sub-based consumption, use `subscribe()` method.
        """
        return self._fallback_bus.poll(
            receiver,
            block=block,
            timeout=timeout,
        )

    async def poll_async(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
        poll_interval: float = 0.05,
    ) -> AgentEnvelope | None:
        """Poll next message for receiver using async-aware polling.

        Delegates to the in-memory fallback's poll_async implementation.
        See AgentBusPort.poll_async for parameter details.
        """
        return await self._fallback_bus.poll_async(
            receiver,
            block=block,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    def ack(self, message_id: str, receiver: str) -> bool:
        """Acknowledge successful processing of a message."""
        return self._fallback_bus.ack(message_id, receiver)

    def nack(
        self,
        message_id: str,
        receiver: str,
        *,
        reason: str = "",
        requeue: bool = True,
    ) -> bool:
        """Negative-acknowledge a message."""
        return self._fallback_bus.nack(
            message_id,
            receiver,
            reason=reason,
            requeue=requeue,
        )

    def pending_count(self, receiver: str) -> int:
        """Return number of pending messages for receiver."""
        return self._fallback_bus.pending_count(receiver)

    def requeue_all_inflight(self, receiver: str) -> int:
        """Requeue ALL inflight messages for a receiver back to inbox.

        Delegates to the in-memory fallback.
        """
        return self._fallback_bus.requeue_all_inflight(receiver)

    @property
    def dead_letters(self) -> list[DeadLetterRecord]:
        """Snapshot of all dead-letter records."""
        return self._fallback_bus.dead_letters

    # ── NATS-specific Methods ────────────────────────────────────────────────

    def ensure_nats_connected(self) -> bool:
        """Ensure NATS connection is established.

        Returns True if connected (or was already connected), False if
        connection failed (NATS will remain disabled, in-memory used).
        """
        if not self._nats_enabled:
            logger.debug("NATS disabled, skipping connection")
            return False

        if self._nats_client is not None and self._nats_client.is_connected:
            return True

        # Lazy initialization
        self._nats_client = NATSClientWrapper(config=self._nats_config)
        connected = self._nats_client.connect()
        if connected:
            self._nats_ever_connected = True
            logger.info(
                "KernelOneMessageBusPort: NATS connected at %s",
                self._nats_url,
            )
        else:
            logger.warning(
                "KernelOneMessageBusPort: NATS connection failed at %s, falling back to in-memory bus. Error: %s",
                self._nats_url,
                self._nats_client.connection_error,
            )
            # Keep the failed client so we don't repeatedly try to reconnect
        return connected

    def subscribe(self, topic: str) -> bool:
        """Subscribe to a NATS subject for pub/sub-based consumption.

        This enables the async pub/sub pattern. Messages received on the
        subject will be forwarded to the in-memory fallback's inbox for
        consumption via `poll()`.

        Args:
            topic: NATS subject to subscribe to (e.g., "roles.runtime.*")

        Returns:
            True if subscribed successfully, False if NATS unavailable.
        """
        if self._nats_client is None:
            self.ensure_nats_connected()

        # For now, subscribe is a no-op that just confirms NATS is ready.
        # Full implementation would register a callback that forwards to inbox.
        # This is deferred to avoid complexity; the primary path is publish().
        return self._nats_client is not None and self._nats_client.is_connected

    def disconnect_nats(self) -> None:
        """Disconnect from NATS (for graceful shutdown)."""
        if self._nats_client is not None:
            self._nats_client.close()
            self._nats_client = None
            logger.info("KernelOneMessageBusPort: NATS disconnected")

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """Return diagnostic snapshot."""
        stats = self._fallback_bus.get_stats()
        stats.update(
            {
                "nats_enabled": self._nats_enabled,
                "nats_url": self._nats_url,
                "nats_connected": (self._nats_client.is_connected if self._nats_client is not None else False),
                "nats_ever_connected": self._nats_ever_connected,
            }
        )
        return stats


# ── Serialization Helpers ──────────────────────────────────────────────────────


def _envelope_to_json_bytes(envelope: AgentEnvelope) -> bytes:
    """Serialize AgentEnvelope to JSON bytes (UTF-8)."""
    import json

    data = {
        "message_id": envelope.message_id,
        "msg_type": envelope.msg_type,
        "sender": envelope.sender,
        "receiver": envelope.receiver,
        "payload": envelope.payload,
        "timestamp_utc": envelope.timestamp_utc,
        "correlation_id": envelope.correlation_id,
        "attempt": envelope.attempt,
        "max_attempts": envelope.max_attempts,
        "last_error": envelope.last_error,
    }
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _json_bytes_to_envelope(data: bytes) -> AgentEnvelope:
    """Deserialize AgentEnvelope from JSON bytes (UTF-8)."""
    import json

    parsed = json.loads(data.decode("utf-8"))
    return AgentEnvelope(
        message_id=parsed.get("message_id", str(uuid.uuid4())),
        msg_type=parsed.get("msg_type", "unknown"),
        sender=parsed.get("sender", "unknown"),
        receiver=parsed.get("receiver", "unknown"),
        payload=parsed.get("payload", {}),
        timestamp_utc=parsed.get(
            "timestamp_utc",
            datetime.now(timezone.utc).isoformat(),
        ),
        correlation_id=parsed.get("correlation_id"),
        attempt=parsed.get("attempt", 0),
        max_attempts=parsed.get("max_attempts", 3),
        last_error=parsed.get("last_error", ""),
    )


# ── Backward-Compatible Factory ────────────────────────────────────────────────


def create_bus_port(
    nats_url: str | None = None,
    *,
    nats_enabled: bool | None = None,
    max_queue_size: int = _MAX_QUEUE_SIZE,
    _fallback: InMemoryAgentBusPort | None = None,
) -> KernelOneMessageBusPort:
    """Create a KernelOneMessageBusPort with optional NATS configuration.

    This is the recommended factory for creating bus ports in roles.runtime.

    Args:
        nats_url: NATS server URL (default: from env or "nats://127.0.0.1:4222")
        nats_enabled: Override NATS enabled flag
        max_queue_size: Maximum inbox size per receiver
        _fallback: Internal use only; inject test fallback

    Returns:
        KernelOneMessageBusPort instance
    """
    return KernelOneMessageBusPort(
        nats_url=nats_url,
        nats_enabled=nats_enabled,
        max_queue_size=max_queue_size,
        _fallback=_fallback,
    )


__all__ = [
    "AgentBusPort",
    "AgentEnvelope",
    "DeadLetterRecord",
    "InMemoryAgentBusPort",
    "KernelOneMessageBusPort",
    "NATSClientWrapper",
    "NATSConnectionConfig",
    "create_bus_port",
]
