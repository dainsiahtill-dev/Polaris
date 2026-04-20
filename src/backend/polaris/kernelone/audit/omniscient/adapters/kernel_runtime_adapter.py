"""KernelRuntimeAdapter — async batched writes to KernelAuditRuntime.

Design:
- Buffers audit events in memory
- Flushes to KernelAuditRuntime in batches (configurable size/interval)
- Integrates SanitizationHook for PII protection
- Implements circuit breaker for fault tolerance
- Supports file partitioning by workspace/date

Key features:
- Non-blocking emit(): events queued immediately, persisted async
- Batching: configurable batch_size (default 100) and flush_interval (default 1s)
- Circuit breaker: opens after consecutive failures, auto-resets after timeout
- Partitioning: writes to workspace/YYYY-MM-DD/{channel}.jsonl

Usage:
    adapter = KernelRuntimeAdapter(runtime_root=Path("/path/to/runtime"))
    await adapter.start()

    # Non-blocking emit
    await adapter.emit(event_dict)

    # Flush on shutdown
    await adapter.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    SanitizationHook,
    get_default_sanitizer,
)
from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class KernelRuntimeAdapterConfig:
    """Configuration for KernelRuntimeAdapter.

    Attributes:
        batch_size: Number of events to batch before flushing.
        flush_interval_seconds: Maximum seconds between flushes.
        max_buffer_size: Maximum events in buffer before drop (0 = unlimited).
        circuit_breaker_threshold: Consecutive failures before circuit opens.
        circuit_breaker_timeout: Seconds before auto-reset.
        partition_by_workspace: Create workspace subdirectories.
        partition_by_date: Create YYYY-MM-DD subdirectories.
        channel_prefix: Prefix for JSONL filenames.
        sanitize: Whether to apply sanitization before persistence.
    """

    batch_size: int = 100
    flush_interval_seconds: float = 1.0
    max_buffer_size: int = 10000
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS
    partition_by_workspace: bool = True
    partition_by_date: bool = True
    channel_prefix: str = "audit"
    sanitize: bool = True

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive")


# =============================================================================
# Circuit Breaker State
# =============================================================================


class CircuitBreaker:
    """Thread-safe circuit breaker for audit writes.

    States:
        CLOSED: Normal operation, writes allowed
        OPEN: Too many failures, writes blocked
        HALF_OPEN: Testing if service recovered
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        threshold: int = 5,
        timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS,
    ) -> None:
        self._threshold = threshold
        self._timeout = timeout
        self._failures = 0
        self._last_failure_time: float | None = None
        self._state = self.CLOSED
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Get current circuit breaker state."""
        with self._lock:
            if (
                self._state == self.OPEN
                and self._last_failure_time
                and time.monotonic() - self._last_failure_time >= self._timeout
            ):
                self._state = self.HALF_OPEN
            return self._state

    def record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()
            if self._failures >= self._threshold:
                self._state = self.OPEN
                logger.warning(
                    "[audit_adapter] Circuit breaker opened after %d consecutive failures",
                    self._failures,
                )

    def is_write_allowed(self) -> bool:
        """Check if writes are allowed in current state."""
        return self.state != self.OPEN


# =============================================================================
# Audit Event Buffer Entry
# =============================================================================


@dataclass
class BufferedEvent:
    """An audit event with metadata in the buffer."""

    event: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = 0


# =============================================================================
# KernelRuntimeAdapter
# =============================================================================


class KernelRuntimeAdapter:
    """Async batched adapter for KernelAuditRuntime.

    Features:
    - Non-blocking emit with in-memory buffer
    - Configurable batch size and flush interval
    - Circuit breaker for fault tolerance
    - File partitioning by workspace and date
    - Integrated sanitization

    Usage:
        adapter = KernelRuntimeAdapter(runtime_root=Path("/path"))
        await adapter.start()

        await adapter.emit({"event_type": "test", "data": {"key": "value"}})

        await adapter.stop()
    """

    def __init__(
        self,
        runtime_root: Path,
        config: KernelRuntimeAdapterConfig | None = None,
        sanitizer: SanitizationHook | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            runtime_root: Root path for audit files.
            config: Adapter configuration.
            sanitizer: Sanitization hook (uses default if None).
        """
        self._runtime_root = Path(runtime_root)
        self._config = config or KernelRuntimeAdapterConfig()
        self._sanitizer = sanitizer or get_default_sanitizer()

        # Buffer for batched events
        self._buffer: list[BufferedEvent] = []
        self._buffer_lock = threading.Lock()

        # Circuit breaker
        self._circuit_breaker = CircuitBreaker(
            threshold=self._config.circuit_breaker_threshold,
            timeout=self._config.circuit_breaker_timeout,
        )

        # Async task for background flush
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_in_progress = False
        self._flush_lock = asyncio.Lock()
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Statistics
        self._events_emitted = 0
        self._events_persisted = 0
        self._events_dropped = 0

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the adapter (starts background flush loop)."""
        if self._running:
            return

        self._running = True
        self._shutdown_event.clear()
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.debug("[audit_adapter] Started")

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the adapter and flush remaining events.

        Args:
            timeout: Maximum seconds to wait for flush.
        """
        if not self._running:
            return

        logger.debug("[audit_adapter] Stopping...")
        self._running = False
        self._shutdown_event.set()

        # Flush remaining events
        await self._flush_buffer()

        # Cancel flush task
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self._flush_task, timeout=timeout)

        logger.debug("[audit_adapter] Stopped")

    # -------------------------------------------------------------------------
    # Event Emission (non-blocking)
    # -------------------------------------------------------------------------

    async def emit(self, event: dict[str, Any]) -> str:
        """Queue an event for async persistence.

        This method returns immediately. The event is buffered
        and persisted in the background flush loop.

        Args:
            event: Audit event dict to persist.

        Returns:
            Event ID if queued, empty string if dropped.
        """
        if not self._running:
            logger.warning("[audit_adapter] Emit called on stopped adapter")
            return ""

        # Check circuit breaker
        if not self._circuit_breaker.is_write_allowed():
            self._events_dropped += 1
            logger.debug("[audit_adapter] Circuit open, event dropped")
            return ""

        # Get event ID
        event_id = str(event.get("event_id", ""))
        if not event_id:
            import uuid

            event_id = uuid.uuid4().hex
            event["event_id"] = event_id

        # Apply sanitization
        if self._config.sanitize:
            event = self._sanitizer.sanitize(event)

        # Add to buffer
        buffered_event = BufferedEvent(event=event)

        batch_size_reached = False
        with self._buffer_lock:
            # Check buffer size limit
            if self._config.max_buffer_size > 0 and len(self._buffer) >= self._config.max_buffer_size:
                self._events_dropped += 1
                logger.warning("[audit_adapter] Buffer full, event dropped")
                return ""

            self._buffer.append(buffered_event)
            self._events_emitted += 1
            batch_size_reached = len(self._buffer) >= self._config.batch_size

        # Trigger flush if batch size reached (async, non-blocking)
        if batch_size_reached:
            asyncio.create_task(self._flush_async())

        return event_id

    # -------------------------------------------------------------------------
    # Batch Flush
    # -------------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Background loop that flushes buffer periodically."""
        while self._running or not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._config.flush_interval_seconds,
                )
                # Shutdown was signaled
                break
            except asyncio.TimeoutError:
                # Timeout, flush the buffer
                await self._flush_async()

    async def _flush_async(self) -> None:
        """Flush with async lock to prevent concurrent flushes."""
        if self._flush_in_progress:
            return
        self._flush_in_progress = True
        try:
            await self._flush_buffer()
        finally:
            self._flush_in_progress = False

    async def _flush_buffer(self) -> None:
        """Flush buffered events to KernelAuditRuntime."""
        # Grab current buffer
        with self._buffer_lock:
            if not self._buffer:
                return
            events_to_flush = list(self._buffer)
            self._buffer.clear()

        # Write events
        try:
            await self._write_batch(events_to_flush)
            self._circuit_breaker.record_success()
            self._events_persisted += len(events_to_flush)
            logger.debug(
                "[audit_adapter] Flushed %d events",
                len(events_to_flush),
            )
        except (RuntimeError, ValueError) as exc:
            self._circuit_breaker.record_failure()
            logger.error("[audit_adapter] Batch write failed: %s", exc)
            # Re-queue failed events (with retry limit)
            await self._requeue_failed(events_to_flush)

    async def _write_batch(self, events: list[BufferedEvent]) -> None:
        """Write a batch of events to JSONL files.

        Args:
            events: List of buffered events to write.
        """
        # Group events by partition
        partitions: dict[str, list[BufferedEvent]] = {}
        for buffered_event in events:
            partition_key = self._get_partition_key(buffered_event.event)
            if partition_key not in partitions:
                partitions[partition_key] = []
            partitions[partition_key].append(buffered_event)

        # Write each partition
        for partition_key, partition_events in partitions.items():
            await self._write_partition(partition_key, partition_events)

    def _get_partition_key(self, event: dict[str, Any]) -> str:
        """Get partition key for an event.

        Partition format: {workspace}/{date}/{channel}.jsonl

        Args:
            event: Event to partition.

        Returns:
            Partition key string.
        """
        parts = []

        # Workspace
        if self._config.partition_by_workspace:
            workspace = str(event.get("workspace", "default")).strip("/")
            workspace = workspace.replace("/", "_")
            parts.append(workspace or "default")

        # Date
        if self._config.partition_by_date:
            ts = event.get("timestamp", "")
            if isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    dt = datetime.now(timezone.utc)
            elif isinstance(ts, datetime):
                dt = ts
            else:
                dt = datetime.now(timezone.utc)
            parts.append(dt.strftime("%Y-%m-%d"))

        # Channel
        event_type = str(event.get("event_type", "general"))
        parts.append(f"{self._config.channel_prefix}.{event_type}.jsonl")

        return "/".join(parts)

    async def _write_partition(
        self,
        partition_key: str,
        events: list[BufferedEvent],
    ) -> None:
        """Write a partition of events to a JSONL file.

        Args:
            partition_key: Partition key string.
            events: Events to write.
        """
        file_path = self._runtime_root / "audit" / partition_key
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Build JSONL content
        lines: list[str] = []
        for buffered_event in events:
            try:
                line = json.dumps(
                    buffered_event.event,
                    ensure_ascii=False,
                    default=str,
                )
                lines.append(line)
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "[audit_adapter] Failed to serialize event %s: %s",
                    buffered_event.event.get("event_id", "?"),
                    exc,
                )

        if not lines:
            return

        # Atomic append - run sync I/O in thread pool to avoid blocking event loop
        try:
            await asyncio.to_thread(self._write_partition_sync, file_path, lines)
        except (RuntimeError, ValueError) as exc:
            logger.error("[audit_adapter] Failed to write to %s: %s", file_path, exc)
            raise

    def _write_partition_sync(self, file_path: Path, lines: list[str]) -> None:
        """Sync file write - runs in thread pool via asyncio.to_thread().

        Args:
            file_path: Path to write to.
            lines: Lines to write.
        """
        with open(file_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()

    async def _requeue_failed(self, events: list[BufferedEvent]) -> None:
        """Re-queue failed events for retry.

        Args:
            events: Failed events to requeue.
        """
        with self._buffer_lock:
            for buffered_event in events:
                if buffered_event.retry_count < 3:
                    buffered_event.retry_count += 1
                    self._buffer.append(buffered_event)
                    logger.debug(
                        "[audit_adapter] Requeued event %s (retry %d)",
                        buffered_event.event.get("event_id", "?"),
                        buffered_event.retry_count,
                    )
                else:
                    self._events_dropped += 1
                    logger.warning(
                        "[audit_adapter] Dropped event %s after max retries",
                        buffered_event.event.get("event_id", "?"),
                    )

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Get adapter statistics.

        Returns:
            Dictionary with event counts and buffer status.
        """
        with self._buffer_lock:
            buffer_size = len(self._buffer)

        return {
            "events_emitted": self._events_emitted,
            "events_persisted": self._events_persisted,
            "events_dropped": self._events_dropped,
            "buffer_size": buffer_size,
            "circuit_breaker_state": self._circuit_breaker.state,
        }

    # -------------------------------------------------------------------------
    # Partition Statistics
    # -------------------------------------------------------------------------

    def get_partition_stats(self) -> dict[str, Any]:
        """Get statistics about existing audit partitions.

        Scans the audit directory structure to gather partition metadata.
        Does NOT scan file contents (efficient O(directory) operation).

        Returns:
            Dictionary with partition statistics:
            - total_partitions: Number of partition files
            - total_size_bytes: Sum of all partition file sizes
            - by_workspace: Per-workspace statistics
            - by_date: Per-date statistics
            - by_channel: Per-channel (event_type) statistics
        """
        import re

        audit_root = self._runtime_root / "audit"
        if not audit_root.exists():
            return {
                "total_partitions": 0,
                "total_size_bytes": 0,
                "by_workspace": {},
                "by_date": {},
                "by_channel": {},
            }

        # Patterns
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        file_pattern = re.compile(rf"^{re.escape(self._config.channel_prefix)}\.([^.]+)\.jsonl$")

        total_partitions = 0
        total_size = 0
        by_workspace: dict[str, dict[str, Any]] = {}
        by_date: dict[str, dict[str, Any]] = {}
        by_channel: dict[str, dict[str, Any]] = {}

        def update_stats(
            stats_dict: dict[str, dict[str, Any]],
            key: str,
            size: int,
        ) -> None:
            if key not in stats_dict:
                stats_dict[key] = {"count": 0, "size_bytes": 0}
            stats_dict[key]["count"] += 1
            stats_dict[key]["size_bytes"] += size

        try:
            for ws_dir in audit_root.iterdir():
                if not ws_dir.is_dir() or ws_dir.name.startswith("."):
                    continue

                for date_dir in ws_dir.iterdir():
                    if not date_dir.is_dir():
                        continue
                    if not date_pattern.match(date_dir.name):
                        continue

                    for jsonl_file in date_dir.iterdir():
                        if not jsonl_file.is_file():
                            continue

                        match = file_pattern.match(jsonl_file.name)
                        if not match:
                            continue

                        try:
                            size = jsonl_file.stat().st_size
                        except OSError:
                            size = 0

                        total_partitions += 1
                        total_size += size
                        channel = match.group(1)

                        update_stats(by_workspace, ws_dir.name, size)
                        update_stats(by_date, date_dir.name, size)
                        update_stats(by_channel, channel, size)

        except OSError as exc:
            logger.warning(
                "[audit_adapter] Failed to scan partition stats: %s",
                exc,
            )

        return {
            "total_partitions": total_partitions,
            "total_size_bytes": total_size,
            "by_workspace": by_workspace,
            "by_date": by_date,
            "by_channel": by_channel,
        }


# =============================================================================
# Module-level singleton
# =============================================================================

_default_adapter: KernelRuntimeAdapter | None = None


def get_default_adapter(runtime_root: Path | None = None) -> KernelRuntimeAdapter:
    """Get the default adapter instance.

    Args:
        runtime_root: Runtime root path (only used on first call).

    Returns:
        Default adapter instance.
    """
    global _default_adapter
    if _default_adapter is None:
        if runtime_root is None:
            raise ValueError("runtime_root required on first call")
        _default_adapter = KernelRuntimeAdapter(runtime_root)
    return _default_adapter


def reset_default_adapter() -> None:
    """Reset the default adapter instance.

    This is primarily for testing.
    """
    global _default_adapter
    _default_adapter = None
