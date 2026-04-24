"""Dedicated JetStream publisher for the canonical log pipeline.

This module owns a single background publishing runtime for canonical log
events. The publisher keeps its own event loop and NATS connection so
sync callers never need to create ad-hoc loops or reuse async clients across
different event loops.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any

from polaris.infrastructure.messaging.nats.client import NATSClient
from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_SIZE = max(
    256,
    int(os.environ.get("KERNELONE_JETSTREAM_PUBLISH_QUEUE_SIZE", "4096")),
)
_DEFAULT_RETRY_ATTEMPTS = max(
    1,
    int(os.environ.get("KERNELONE_JETSTREAM_PUBLISH_MAX_ATTEMPTS", "6")),
)
_DEFAULT_RETRY_BASE_SEC = max(
    0.05,
    float(os.environ.get("KERNELONE_JETSTREAM_PUBLISH_RETRY_BASE_SEC", "0.25")),
)
_DEFAULT_RETRY_MAX_SEC = max(
    _DEFAULT_RETRY_BASE_SEC,
    float(os.environ.get("KERNELONE_JETSTREAM_PUBLISH_RETRY_MAX_SEC", "5.0")),
)
_QUEUE_GET_TIMEOUT_SEC = 0.25


@dataclass(frozen=True)
class JetStreamPublishRequest:
    """One publish request enqueued after durable disk write."""

    subject: str
    payload: dict[str, Any]


class JetStreamPublisher:
    """Background JetStream publisher with dedicated connection ownership."""

    def __init__(
        self,
        *,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        max_attempts: int = _DEFAULT_RETRY_ATTEMPTS,
        retry_base_sec: float = _DEFAULT_RETRY_BASE_SEC,
        retry_max_sec: float = _DEFAULT_RETRY_MAX_SEC,
    ) -> None:
        self._queue: queue.Queue[JetStreamPublishRequest] = queue.Queue(maxsize=max(64, queue_size))
        self._max_attempts = max(1, max_attempts)
        self._retry_base_sec = max(0.05, retry_base_sec)
        self._retry_max_sec = max(self._retry_base_sec, retry_max_sec)
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._client: NATSClient | None = None

    def start(self) -> None:
        """Start the background publisher thread lazily."""
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="log-jetstream-publisher",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background publisher and close the dedicated client."""
        self._stop_event.set()
        thread = None
        with self._thread_lock:
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=max(0.1, timeout))

    def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
        """Queue an event for asynchronous publish.

        Returns:
            True when the event is accepted into the publisher queue.
        """
        if self._stop_event.is_set():
            return False
        self.start()
        try:
            self._queue.put_nowait(
                JetStreamPublishRequest(
                    subject=str(subject or "").strip(),
                    payload=dict(payload or {}),
                )
            )
            return True
        except queue.Full:
            logger.error("JetStream publisher queue full; dropping subject=%s", subject)
            return False

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stop_event.is_set():
                try:
                    request = self._queue.get(timeout=_QUEUE_GET_TIMEOUT_SEC)
                except queue.Empty:
                    continue
                try:
                    loop.run_until_complete(self._publish_with_retry(request))
                finally:
                    self._queue.task_done()
        finally:
            try:
                loop.run_until_complete(self._shutdown_async())
            finally:
                loop.close()

    async def _publish_with_retry(self, request: JetStreamPublishRequest) -> None:
        delay = self._retry_base_sec
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            try:
                client = await self._get_client()
                published = await client.publish(request.subject, request.payload)
                if published:
                    return
                last_error = RuntimeError("publish returned False")
            except (RuntimeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "JetStream publish attempt %s/%s failed for %s: %s",
                    attempt,
                    self._max_attempts,
                    request.subject,
                    exc,
                )
                await self._reset_client()

            if attempt < self._max_attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self._retry_max_sec)

        logger.error(
            "P0: JetStream publish dropped after %s attempts; subject=%s error=%s",
            self._max_attempts,
            request.subject,
            str(last_error or "unknown"),
        )

    async def _get_client(self) -> NATSClient:
        if self._client and self._client.is_connected:
            return self._client

        if self._client:
            await self._reset_client()

        client = NATSClient()
        await client.connect()
        await self._ensure_runtime_stream(client)
        self._client = client
        return client

    async def _reset_client(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except (RuntimeError, ValueError):
            logger.debug("Failed to close JetStream publisher client", exc_info=True)

    async def _ensure_runtime_stream(self, client: NATSClient) -> None:
        jetstream = client.jetstream
        if jetstream is None:
            raise RuntimeError("JetStream context unavailable")

        from nats.js.api import StreamConfig

        try:
            await jetstream.stream_info(JetStreamConstants.STREAM_NAME)
        except (OSError, RuntimeError, ValueError):
            await jetstream.add_stream(
                StreamConfig(
                    name=JetStreamConstants.STREAM_NAME,
                    subjects=JetStreamConstants.STREAM_SUBJECTS,
                )
            )

    async def _shutdown_async(self) -> None:
        await self._reset_client()


_publisher_singleton: JetStreamPublisher | None = None
_publisher_singleton_lock = threading.Lock()


def get_log_jetstream_publisher() -> JetStreamPublisher:
    """Return the process-wide canonical log JetStream publisher."""
    global _publisher_singleton
    with _publisher_singleton_lock:
        if _publisher_singleton is None:
            _publisher_singleton = JetStreamPublisher()
        return _publisher_singleton


def shutdown_log_jetstream_publisher(timeout: float = 5.0) -> None:
    """Stop the process-wide publisher if it has been created."""
    global _publisher_singleton
    with _publisher_singleton_lock:
        publisher = _publisher_singleton
        _publisher_singleton = None
    if publisher is not None:
        publisher.stop(timeout=timeout)


__all__ = [
    "JetStreamPublishRequest",
    "JetStreamPublisher",
    "get_log_jetstream_publisher",
    "shutdown_log_jetstream_publisher",
]
