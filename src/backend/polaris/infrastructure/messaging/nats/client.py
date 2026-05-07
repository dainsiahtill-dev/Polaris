r"""Async NATS client for Polaris runtime messaging.

This module provides a robust async NATS client with connection management,
reconnection logic, and error handling for the JetStream messaging layer.

CRITICAL: All text I/O must use UTF-8 encoding explicitly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants
from polaris.kernelone import _runtime_config
from polaris.kernelone.constants import DEFAULT_NATS_URL, DEFAULT_SHORT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from nats.aio.client import Client as NatsClientType
    from nats.js.client import JetStreamContext

logger = logging.getLogger(__name__)
nats = SimpleNamespace(connect=None)
NATSTimeoutError = TimeoutError


def _sanitize_server_url(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return text
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return urlunparse(parsed._replace(netloc=netloc))


def _ensure_nats_imported() -> None:
    global NATSTimeoutError

    if getattr(nats, "connect", None) is not None:
        return
    try:
        import nats as _nats
        from nats.errors import TimeoutError as _NATSTimeoutError
    except ImportError as e:
        raise ImportError("nats-py is required. Install with: pip install nats-py") from e

    nats.connect = _nats.connect
    NATSTimeoutError = _NATSTimeoutError


# =============================================================================
# Configuration
# =============================================================================

_RUNTIME_STREAM_REPAIR_COOLDOWN_SEC = 2.0


def _env_nats_servers() -> list[str]:
    # Use _runtime_config for KERNELONE_* / KERNELONE_* fallback
    raw = _runtime_config.resolve_env_str("nats_url").strip()
    if not raw:
        return [DEFAULT_NATS_URL]
    servers = [item.strip() for item in raw.split(",") if item.strip()]
    return servers or [DEFAULT_NATS_URL]


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _build_runtime_stream_config() -> Any:
    config_kwargs = {
        "name": JetStreamConstants.STREAM_NAME,
        "description": JetStreamConstants.STREAM_DESCRIPTION,
        "subjects": JetStreamConstants.STREAM_SUBJECTS,
        "max_bytes": JetStreamConstants.STREAM_MAX_BYTES,
        "max_msg_size": JetStreamConstants.STREAM_MAX_MSG_SIZE,
        "max_age": JetStreamConstants.STREAM_MAX_AGE_SECONDS,
        "num_replicas": JetStreamConstants.STREAM_REPLICAS,
    }

    # Avoid importing nats.js.api in lightweight test paths where only a config
    # payload is needed. Use native StreamConfig only when already loaded.
    nats_api_module = sys.modules.get("nats.js.api")
    stream_config_cls = getattr(nats_api_module, "StreamConfig", None) if nats_api_module is not None else None
    if stream_config_cls is not None:
        return stream_config_cls(**config_kwargs)
    return SimpleNamespace(**config_kwargs)


@dataclass
class NATSConfig:
    r"""Configuration for NATS client connection.

    Attributes:
        servers: List of NATS server URLs.
        name: Client name for identification.
        max_reconnect_attempts: Maximum reconnection attempts.
        reconnect_time_wait: Time to wait between reconnection attempts.
        ping_interval: Interval for sending PINGs.
        max_outstanding_pings: Maximum outstanding PINGs.
        allow_reconnect: Whether to allow reconnection.
        connect_timeout: Connection timeout in seconds.
        default_timeout: Default operation timeout in seconds.
    """

    servers: list[str] = field(default_factory=_env_nats_servers)
    name: str = "polaris"
    max_reconnect_attempts: int = field(
        default_factory=lambda: _env_int(
            "KERNELONE_NATS_MAX_RECONNECT",
            10,
        )
    )
    reconnect_time_wait: float = field(
        default_factory=lambda: _env_float(
            "KERNELONE_NATS_RECONNECT_WAIT",
            0.5,
        )
    )
    ping_interval: int = 60
    max_outstanding_pings: int = 2
    allow_reconnect: bool = True
    connect_timeout: float = field(
        default_factory=lambda: _env_float(
            "KERNELONE_NATS_CONNECT_TIMEOUT",
            5.0,
        )
    )
    default_timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS


# =============================================================================
# Connection State
# =============================================================================


class ConnectionState:
    r"""NATS connection state enumeration."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"
    CLOSED = "closed"


# =============================================================================
# NATS Client
# =============================================================================


class NATSClient:
    r"""Async NATS client with JetStream support.

    Provides connection management, reconnection logic, and publish/subscribe
    capabilities for Polaris runtime events.

    Example:
        >>> config = NATSConfig(servers=["nats://localhost:4222"])
        >>> async with NATSClient(config) as client:
        ...     await client.publish("subject", {"data": "value"})
    """

    def __init__(self, config: NATSConfig | None = None) -> None:
        r"""Initialize NATS client.

        Args:
            config: NATS configuration. Uses defaults if not provided.
        """
        self._config = config or NATSConfig()
        self._nc: NatsClientType | None = None
        self._js: JetStreamContext | None = None
        self._state = ConnectionState.DISCONNECTED
        self._state_callbacks: list[Callable[[str], None]] = []
        self._lock = asyncio.Lock()
        self._last_runtime_stream_repair_at = 0.0

    @property
    def is_connected(self) -> bool:
        r"""Check if client is connected.

        Returns:
            True if connected to NATS server.
        """
        return self._nc is not None and self._nc.is_connected

    @property
    def state(self) -> str:
        r"""Get current connection state.

        Returns:
            Current connection state string.
        """
        return self._state

    def add_state_callback(self, callback: Callable[[str], None]) -> None:
        r"""Add callback for connection state changes.

        Args:
            callback: Function to call on state change.
        """
        self._state_callbacks.append(callback)

    def _set_state(self, new_state: str) -> None:
        r"""Update connection state and notify callbacks.

        Args:
            new_state: New connection state.
        """
        if self._state != new_state:
            self._state = new_state
            logger.info(f"NATS client state: {new_state}")
            for callback in self._state_callbacks:
                try:
                    callback(new_state)
                except (RuntimeError, ValueError) as e:
                    logger.error(f"State callback error: {e}")

    async def connect(self) -> None:
        r"""Establish connection to NATS server.

        Raises:
            RuntimeError: If connection fails.
        """
        async with self._lock:
            if self.is_connected:
                return

            self._set_state(ConnectionState.CONNECTING)
            _ensure_nats_imported()

            try:
                self._nc = await nats.connect(
                    self._config.servers,
                    name=self._config.name,
                    max_reconnect_attempts=self._config.max_reconnect_attempts,
                    reconnect_time_wait=self._config.reconnect_time_wait,
                    ping_interval=self._config.ping_interval,
                    max_outstanding_pings=self._config.max_outstanding_pings,
                    allow_reconnect=self._config.allow_reconnect,
                    connect_timeout=self._config.connect_timeout,
                    disconnected_cb=self._on_disconnect,
                    reconnected_cb=self._on_reconnect,
                    closed_cb=self._on_close,
                )

                # Create JetStream context
                self._js = self._nc.jetstream()

                self._set_state(ConnectionState.CONNECTED)
                logger.info(f"Connected to NATS: {self._config.servers}")

            except (RuntimeError, ValueError) as e:
                self._set_state(ConnectionState.DISCONNECTED)
                logger.error(f"Failed to connect to NATS: {e}")
                raise RuntimeError(f"NATS connection failed: {e}") from e

    async def disconnect(self) -> None:
        r"""Close NATS connection gracefully."""
        async with self._lock:
            if self._nc is None:
                return

            self._set_state(ConnectionState.CLOSING)

            try:
                await self._nc.close()
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Error during NATS disconnect: {e}")
            finally:
                self._nc = None
                self._js = None
                self._set_state(ConnectionState.CLOSED)

    async def _on_disconnect(self) -> None:
        r"""Handle disconnection event."""
        logger.warning("NATS disconnected")
        self._set_state(ConnectionState.DISCONNECTED)

    async def _on_reconnect(self) -> None:
        r"""Handle reconnection event."""
        logger.info("NATS reconnected")
        self._set_state(ConnectionState.CONNECTED)

    async def _on_close(self) -> None:
        r"""Handle connection close event."""
        logger.info("NATS connection closed")
        self._set_state(ConnectionState.CLOSED)

    # -------------------------------------------------------------------------
    # Publish Operations
    # -------------------------------------------------------------------------

    async def publish(
        self,
        subject: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> bool:
        r"""Publish message to subject.

        Args:
            subject: NATS subject to publish to.
            payload: Message payload as dictionary.
            timeout: Operation timeout in seconds.

        Returns:
            True if publish succeeded.

        Raises:
            RuntimeError: If not connected or publish fails.
        """
        if not self.is_connected:
            raise RuntimeError("NATS client not connected")

        # At this point, self._nc is guaranteed non-None (checked by is_connected)
        assert self._nc is not None

        timeout = timeout or self._config.default_timeout

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            # Check payload size against server max_payload limit (default ~1MB)
            max_payload = getattr(self._nc, "max_payload", 1024 * 1024)  # 1MB fallback
            if len(data) > max_payload:
                logger.error(
                    "Payload size %d bytes exceeds server max_payload %d bytes for subject %s; message dropped.",
                    len(data),
                    max_payload,
                    subject,
                )
                raise RuntimeError(f"Payload size {len(data)} bytes exceeds NATS max_payload {max_payload} bytes")

            if self._js:
                return await self._publish_runtime_aware_jetstream(
                    subject=subject,
                    data=data,
                    timeout=timeout,
                )
            else:
                # Fallback to raw NATS publish
                await self._nc.publish(subject, data)

            logger.debug(f"Published to {subject}: {payload}")
            return True

        except NATSTimeoutError:
            logger.error(f"Timeout publishing to {subject}")
            raise RuntimeError(f"Publish timeout: {subject}") from None
        except (RuntimeError, ValueError) as e:
            logger.error(f"Publish error to {subject}: {e}")
            raise RuntimeError(f"Publish failed: {e}") from e

    async def publish_event(
        self,
        subject: str,
        event_data: dict[str, Any],
        timeout: float | None = None,
    ) -> bool:
        r"""Publish runtime event with timestamp.

        Args:
            subject: NATS subject.
            event_data: Event data dictionary.
            timeout: Operation timeout.

        Returns:
            True if publish succeeded.
        """
        enriched_data = {
            **event_data,
            "_published_at": datetime.now(timezone.utc).isoformat(),
        }
        return await self.publish(subject, enriched_data, timeout)

    # -------------------------------------------------------------------------
    # Subscribe Operations
    # -------------------------------------------------------------------------

    async def subscribe(
        self,
        subject: str,
        queue: str | None = None,
        max_messages: int | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        r"""Subscribe to subject and yield messages.

        Args:
            subject: NATS subject to subscribe to.
            queue: Optional queue group name.
            max_messages: Maximum messages to receive.
            timeout: Subscription timeout.

        Yields:
            Message payloads as dictionaries.

        Example:
            >>> async for msg in client.subscribe("subject"):
            ...     print(msg)
        """
        if not self.is_connected:
            raise RuntimeError("NATS client not connected")

        # At this point, self._nc is guaranteed non-None (checked by is_connected)
        assert self._nc is not None

        timeout = timeout or self._config.default_timeout
        messages_received = 0

        subscription = await self._nc.subscribe(
            subject,
            queue=queue if queue is not None else "",
        )

        try:
            async for msg in subscription.messages:
                if max_messages and messages_received >= max_messages:
                    break

                try:
                    data = json.loads(msg.data.decode("utf-8"))
                    yield data
                    messages_received += 1
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in message: {e}")
                except (RuntimeError, ValueError) as e:
                    logger.error(f"Message processing error: {e}")
        finally:
            await subscription.unsubscribe()

    # -------------------------------------------------------------------------
    # JetStream Operations
    # -------------------------------------------------------------------------

    @property
    def jetstream(self) -> JetStreamContext | None:
        r"""Get JetStream context.

        Returns:
            JetStream context or None if not connected.
        """
        return self._js

    async def publish_js(
        self,
        stream: str,
        subject: str,
        payload: dict[str, Any],
        timeout: float | None = None,
    ) -> bool:
        r"""Publish to JetStream with stream acknowledgment.

        Args:
            stream: Stream name.
            subject: Subject within stream.
            payload: Message payload.
            timeout: Operation timeout.

        Returns:
            True if publish succeeded with ack.
        """
        if not self._js:
            raise RuntimeError("JetStream not available")

        timeout = timeout or self._config.default_timeout

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            return await self._publish_runtime_aware_jetstream(
                subject=subject,
                data=data,
                timeout=timeout,
                stream=stream,
            )

        except (RuntimeError, ValueError) as e:
            logger.error(f"JetStream publish error: {e}")
            raise

    async def _publish_runtime_aware_jetstream(
        self,
        *,
        subject: str,
        data: bytes,
        timeout: float,
        stream: str | None = None,
    ) -> bool:
        if not self._js:
            raise RuntimeError("JetStream not available")

        try:
            await self._jetstream_publish(
                subject=subject,
                data=data,
                timeout=timeout,
                stream=stream,
            )
            return True
        except NATSTimeoutError:
            raise
        except (RuntimeError, ValueError) as exc:
            if await self._recover_runtime_stream_if_needed(
                subject=subject,
                stream=stream,
                cause=exc,
            ):
                await self._jetstream_publish(
                    subject=subject,
                    data=data,
                    timeout=timeout,
                    stream=stream,
                )
                logger.warning(
                    "JetStream runtime stream self-healed after publish failure; subject=%s stream=%s",
                    subject,
                    stream or JetStreamConstants.STREAM_NAME,
                )
                return True
            raise

    async def _jetstream_publish(
        self,
        *,
        subject: str,
        data: bytes,
        timeout: float,
        stream: str | None = None,
    ) -> Any:
        if not self._js:
            raise RuntimeError("JetStream not available")
        if stream:
            return await self._js.publish(
                subject,
                data,
                stream=stream,
                timeout=timeout,
            )
        return await self._js.publish(
            subject,
            data,
            timeout=timeout,
        )

    async def _recover_runtime_stream_if_needed(
        self,
        *,
        subject: str,
        stream: str | None,
        cause: Exception,
    ) -> bool:
        if not self._js:
            return False
        if stream and stream != JetStreamConstants.STREAM_NAME:
            return False
        if not str(subject or "").startswith(f"{JetStreamConstants.SUBJECT_PREFIX}."):
            return False

        now = time.monotonic()
        if now - self._last_runtime_stream_repair_at < _RUNTIME_STREAM_REPAIR_COOLDOWN_SEC:
            return False

        self._last_runtime_stream_repair_at = now
        logger.warning(
            "JetStream runtime stream publish failed; attempting stream self-heal: %s",
            str(cause or type(cause).__name__),
        )

        try:
            await self._js.add_stream(_build_runtime_stream_config())
            return True
        except (RuntimeError, ValueError) as repair_exc:
            logger.error("JetStream runtime stream self-heal failed: %s", repair_exc)
            return False

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    async def health_check(self) -> bool:
        r"""Check if NATS connection is healthy.

        Returns:
            True if connected and responsive.
        """
        if not self.is_connected:
            return False

        # At this point, self._nc is guaranteed non-None (checked by is_connected)
        assert self._nc is not None

        try:
            # Flush sends a ping/round-trip without closing the connection.
            await self._nc.flush(timeout=1)
            return bool(self._nc and self._nc.is_connected)
        except (RuntimeError, ValueError):
            return False

    def get_server_info(self) -> dict[str, Any] | None:
        r"""Get connected server information.

        Returns:
            Server info dictionary or None if not connected.
        """
        if not self._nc or not self._nc.connected_url:
            return None

        return {
            "server_url": _sanitize_server_url(self._nc.connected_url),
            "client_id": self._nc.client_id,
            "max_payload": self._nc.max_payload,
            "connected": self.is_connected,
        }


# =============================================================================
# Context Manager
# =============================================================================


@asynccontextmanager
async def create_nats_client(
    config: NATSConfig | None = None,
) -> AsyncIterator[NATSClient]:
    r"""Create and manage NATS client lifecycle.

    Args:
        config: NATS configuration.

    Yields:
        Configured NATSClient instance.

    Example:
        >>> async with create_nats_client() as client:
        ...     await client.publish("subject", {"data": "value"})
    """
    client = NATSClient(config)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()


# =============================================================================
# Default Instance
# =============================================================================

_default_client: NATSClient | None = None
_default_client_lock = threading.Lock()
_last_connect_failure_at: float = 0.0
_last_connect_failure_error: Exception | None = None
_connect_failure_cooldown_sec: float = 5.0


@asynccontextmanager
async def _acquire_default_client_lock() -> AsyncIterator[None]:
    """Cross-loop safe guard for default NATS client state."""
    await asyncio.to_thread(_default_client_lock.acquire)
    try:
        yield
    finally:
        _default_client_lock.release()


async def get_default_client(
    config: NATSConfig | None = None,
) -> NATSClient:
    r"""Get or create default NATS client singleton.

    Args:
        config: NATS configuration (used only on first call).

    Returns:
        Default NATSClient instance.
    """
    global _default_client, _last_connect_failure_at, _last_connect_failure_error

    async with _acquire_default_client_lock():
        now = time.monotonic()
        if _last_connect_failure_error is not None and (now - _last_connect_failure_at) < _connect_failure_cooldown_sec:
            raise RuntimeError(
                f"NATS temporarily unavailable (cooldown {_connect_failure_cooldown_sec:.1f}s)"
            ) from _last_connect_failure_error

        if _default_client is None:
            candidate = NATSClient(config)
            try:
                await candidate.connect()
            except (RuntimeError, ValueError) as exc:
                _last_connect_failure_at = time.monotonic()
                _last_connect_failure_error = exc
                with suppress(Exception):
                    await candidate.disconnect()
                raise
            _default_client = candidate
            _last_connect_failure_error = None
            _last_connect_failure_at = 0.0
        elif not _default_client.is_connected:
            try:
                await _default_client.connect()
            except (RuntimeError, ValueError) as exc:
                _last_connect_failure_at = time.monotonic()
                _last_connect_failure_error = exc
                with suppress(Exception):
                    await _default_client.disconnect()
                _default_client = None
                raise
            _last_connect_failure_error = None
            _last_connect_failure_at = 0.0

    return _default_client


async def close_default_client() -> None:
    r"""Close default NATS client."""
    global _default_client

    async with _acquire_default_client_lock():
        if _default_client:
            await _default_client.disconnect()
            _default_client = None


def get_default_client_snapshot() -> dict[str, Any]:
    """Return current default NATS client state without opening a connection."""

    client = _default_client
    server_info = client.get_server_info() if client is not None else None
    last_failure_age = (
        max(0.0, time.monotonic() - _last_connect_failure_at)
        if _last_connect_failure_at and _last_connect_failure_error is not None
        else None
    )
    return {
        "default_client_exists": client is not None,
        "state": client.state if client is not None else ConnectionState.DISCONNECTED,
        "is_connected": bool(client is not None and client.is_connected),
        "server_info": server_info,
        "last_connect_failure": {
            "error_type": type(_last_connect_failure_error).__name__ if _last_connect_failure_error else None,
            "message": str(_last_connect_failure_error) if _last_connect_failure_error else None,
            "age_seconds": round(last_failure_age, 3) if last_failure_age is not None else None,
            "cooldown_seconds": _connect_failure_cooldown_sec,
        },
    }


__all__ = [
    "ConnectionState",
    "NATSClient",
    "NATSConfig",
    "close_default_client",
    "create_nats_client",
    "get_default_client",
    "get_default_client_snapshot",
]
