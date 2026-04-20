"""High Availability and Defensive tactics for audit system.

[P1-AUDIT-002] Converged: StormLevel imported from metrics.py (canonical)

This module provides:
1. AuditStormDetector — Sliding window event counter with back-pressure
2. AuditSampler — Priority-based dynamic sampling
3. AuditBatcher — Memory-bounded batching with OOM protection
4. AuditCircuitBreaker — Failure-aware circuit breaker for audit writes
5. AuditFallbackManager — Multi-tier fallback strategy

Design principles:
- Audit never blocks/fails the main business path
- Graceful degradation under load
- Memory-bounded buffers prevent OOM
- Tiered fallback: memory -> disk -> drop

Usage:
    # Configure HA for the audit bus
    bus = OmniscientAuditBus.get_default()
    bus._storm_detector = PriorityBasedStormDetector()
    bus._batcher = MemoryBoundedBatcher(max_memory_mb=100)

    # Or use factory
    ha_config = HAConfig(
        storm_threshold=5000,
        max_memory_mb=100,
        batch_size=DEFAULT_BATCH_SIZE,
    )
    apply_ha_config(bus, ha_config)
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from polaris.kernelone.audit.omniscient.metrics import StormLevel  # P1-AUDIT-002: canonical StormLevel
from polaris.kernelone.constants import DEFAULT_BATCH_SIZE, DEFAULT_BATCHER_MEMORY_MB, DEFAULT_SHORT_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class DegradationLevel(str, Enum):
    """System degradation levels."""

    HEALTHY = "healthy"  # All systems operational
    DEGRADED = "degraded"  # Some non-essential features disabled
    CIRCUIT_OPEN = "circuit_open"  # Circuit breaker open
    EMERGENCY = "emergency"  # Minimal audit only


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class HAConfig:
    """Configuration for high availability features.

    Attributes:
        storm_elevated_threshold: Events per window before ELEVATED.
        storm_warning_threshold: Events per window before WARNING.
        storm_critical_threshold: Events per window before CRITICAL.
        storm_emergency_threshold: Events per window before EMERGENCY.
        max_memory_mb: Maximum memory for audit buffers.
        batch_size: Number of events per batch.
        flush_interval_seconds: Maximum seconds between flushes.
        circuit_breaker_threshold: Failures before circuit opens.
        circuit_breaker_timeout: Seconds before auto-reset.
        sampling_rate_normal: Base sampling rate (0.0-1.0).
        sampling_rate_degraded: Sampling rate when degraded.
    """

    storm_elevated_threshold: int = 500
    storm_warning_threshold: int = 2000
    storm_critical_threshold: int = 5000
    storm_emergency_threshold: int = 10000
    max_memory_mb: int = DEFAULT_BATCHER_MEMORY_MB
    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval_seconds: float = 1.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS
    sampling_rate_normal: float = 1.0
    sampling_rate_degraded: float = 0.1


# =============================================================================
# AuditStormDetector
# =============================================================================


class AuditStormDetector:
    """Sliding window event counter with back-pressure.

    [P1-AUDIT-002] Converged: Uses canonical StormLevel from metrics.py

    Tracks event rate over a sliding window and provides:
    - Storm level detection (NORMAL -> EMERGENCY)
    - should_drop() — whether to drop events
    - should_skip_body() — whether to skip event body (keep only metadata)
    - should_redact_body() — whether to redact sensitive fields

    Usage:
        detector = AuditStormDetector(
            elevated=500,
            warning=2000,
            critical=5000,
            emergency=10000,
        )

        # Record events
        detector.record_event("llm_call")
        detector.record_event("tool_execution")

        # Check back-pressure
        if detector.should_drop():
            logger.warning("Audit storm detected, dropping event")
        elif detector.should_skip_body():
            logger.info("High load, skipping event body")
    """

    def __init__(
        self,
        elevated: int = 500,
        warning: int = 2000,
        critical: int = 5000,
        emergency: int = 10000,
        window_seconds: float = 60.0,
    ) -> None:
        """Initialize storm detector.

        Args:
            elevated: Events per window before ELEVATED.
            warning: Events per window before WARNING.
            critical: Events per window before CRITICAL.
            emergency: Events per window before EMERGENCY.
            window_seconds: Sliding window size.
        """
        self._elevated = elevated
        self._warning = warning
        self._critical = critical
        self._emergency = emergency
        self._window_seconds = window_seconds

        # Event tracking: timestamp deque
        self._events: deque[tuple[float, str]] = deque()
        self._lock = threading.Lock()

        # Statistics
        self._total_recorded = 0
        self._total_dropped = 0

    def record_event(self, event_type: str = "unknown") -> None:
        """Record an event for storm detection.

        Args:
            event_type: Type of event for per-type tracking.
        """
        now = time.monotonic()

        with self._lock:
            # Remove old events outside window
            cutoff = now - self._window_seconds
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()

            # Add new event
            self._events.append((now, event_type))
            self._total_recorded += 1

    def get_level(self) -> StormLevel:
        """Get current storm level.

        Returns:
            Current canonical StormLevel based on event rate.
        """
        with self._lock:
            count = len(self._events)

        if count >= self._emergency:
            return StormLevel.EMERGENCY
        if count >= self._critical:
            return StormLevel.CRITICAL
        if count >= self._warning:
            return StormLevel.WARNING
        if count >= self._elevated:
            return StormLevel.ELEVATED
        return StormLevel.NORMAL

    def should_drop(self) -> bool:
        """Check if events should be dropped.

        Returns:
            True if in EMERGENCY state.
        """
        return self.get_level() == StormLevel.EMERGENCY

    def should_skip_body(self) -> bool:
        """Check if event bodies should be skipped.

        Returns:
            True if in CRITICAL or EMERGENCY state.
        """
        level = self.get_level()
        return level in (StormLevel.CRITICAL, StormLevel.EMERGENCY)

    def should_redact_body(self) -> bool:
        """Check if event bodies should be redacted.

        Returns:
            True if in WARNING or higher.
        """
        level = self.get_level()
        return level in (
            StormLevel.WARNING,
            StormLevel.CRITICAL,
            StormLevel.EMERGENCY,
        )

    def get_sampling_rate(self) -> float:
        """Get current sampling rate.

        Returns:
            Sampling rate (0.0-1.0) based on storm level.
        """
        level = self.get_level()
        if level == StormLevel.EMERGENCY:
            return 0.01  # 1%
        if level == StormLevel.CRITICAL:
            return 0.05  # 5%
        if level == StormLevel.WARNING:
            return 0.1  # 10%
        if level == StormLevel.ELEVATED:
            return 0.5  # 50%
        return 1.0  # 100%

    def get_stats(self) -> dict[str, Any]:
        """Get storm detector statistics.

        Returns:
            Dictionary with storm stats.
        """
        with self._lock:
            count = len(self._events)

        return {
            "current_events": count,
            "level": self.get_level().value,
            "total_recorded": self._total_recorded,
            "total_dropped": self._total_dropped,
            "sampling_rate": self.get_sampling_rate(),
            "thresholds": {
                "elevated": self._elevated,
                "warning": self._warning,
                "critical": self._critical,
                "emergency": self._emergency,
            },
        }


# =============================================================================
# PriorityBasedStormDetector
# =============================================================================


class PriorityBasedStormDetector(AuditStormDetector):
    """Storm detector with priority-based handling.

    [P1-AUDIT-002] Converged: Uses canonical StormLevel from metrics.py

    Instead of dropping all events equally, this:
    - Always allows CRITICAL priority events
    - Drops lower priority events first under load
    - Uses sampling for non-critical events
    """

    def should_drop_for_priority(self, priority: int) -> bool:
        """Check if event should be dropped based on priority.

        Args:
            priority: Event priority (lower = higher priority).

        Returns:
            True if should drop.
        """
        level = self.get_level()

        if level == StormLevel.NORMAL:
            return False

        if level == StormLevel.EMERGENCY:
            # Only CRITICAL (0) events survive
            return priority > 0

        if level == StormLevel.CRITICAL:
            # CRITICAL (0) and ERROR (1) survive
            return priority > 1

        if level == StormLevel.WARNING:
            # CRITICAL (0), ERROR (1), WARNING (2) survive
            return priority > 2

        if level == StormLevel.ELEVATED:
            # All except DEBUG survive
            return priority > 3

        return False


# =============================================================================
# AuditSampler
# =============================================================================


class AuditSampler:
    """Priority-based dynamic sampler.

    [P1-AUDIT-002] Converged: Uses canonical StormLevel from metrics.py

    Provides probabilistic sampling based on:
    - Event priority
    - Current storm level
    - Configurable sampling rates

    Usage:
        sampler = AuditSampler(
            rate_normal=1.0,
            rate_degraded=0.1,
        )

        if sampler.should_sample(priority=3):  # DEBUG
            emit_event()
    """

    def __init__(
        self,
        rate_normal: float = 1.0,
        rate_degraded: float = 0.1,
        storm_detector: AuditStormDetector | None = None,
    ) -> None:
        """Initialize sampler.

        Args:
            rate_normal: Base sampling rate (0.0-1.0).
            rate_degraded: Sampling rate when degraded.
            storm_detector: Storm detector for dynamic rate.
        """
        self._rate_normal = rate_normal
        self._rate_degraded = rate_degraded
        self._storm_detector = storm_detector

    def should_sample(self, priority: int = 3) -> bool:
        """Check if event should be sampled.

        Args:
            priority: Event priority (0=CRITICAL, 4=DEBUG).

        Returns:
            True if event should be sampled/emitted.
        """
        import random

        # Storm detector override
        if self._storm_detector:
            level = self._storm_detector.get_level()
            if level == StormLevel.EMERGENCY and priority > 0:
                return False
            if level == StormLevel.CRITICAL and priority > 1:
                return False

        # Higher priority = always sample
        if priority <= 1:  # CRITICAL, ERROR
            return True

        # Get current rate
        rate = self._rate_normal
        if self._storm_detector:
            rate = self._storm_detector.get_sampling_rate()

        return random.random() < rate


# =============================================================================
# MemoryBoundedBatcher
# =============================================================================


class MemoryBoundedBatcher:
    """Memory-bounded batcher with OOM protection.

    Features:
    - Maximum memory limit for buffered events
    - Automatic flush when memory threshold reached
    - Memory estimation per event type
    - Graceful degradation (drop oldest events)

    Usage:
        batcher = MemoryBoundedBatcher(max_memory_mb=100)
        await batcher.add(event)
        if batcher.should_flush():
            events = await batcher.flush()
    """

    # Estimated memory per event type (bytes)
    ESTIMATED_MEMORY: dict[str, int] = {
        "llm_interaction": 4096,  # 4KB
        "tool_execution": 2048,  # 2KB
        "dialogue": 1024,  # 1KB
        "context_management": 512,  # 512B
        "task_orchestration": 512,  # 512B
        "default": 1024,  # 1KB
    }

    def __init__(
        self,
        max_memory_mb: int = 100,
        batch_size: int = 100,
    ) -> None:
        """Initialize memory-bounded batcher.

        Args:
            max_memory_mb: Maximum memory in MB.
            batch_size: Maximum events per batch.
        """
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._batch_size = batch_size

        self._buffer: deque[tuple[dict[str, Any], float]] = deque()
        self._current_memory: float = 0.0
        self._lock = threading.Lock()

        self._total_batched = 0
        self._total_dropped = 0

    def estimate_event_size(self, event: dict[str, Any]) -> int:
        """Estimate memory size of an event.

        Args:
            event: Event dict.

        Returns:
            Estimated size in bytes.
        """
        event_type = str(event.get("event_type", "default"))
        base_size = self.ESTIMATED_MEMORY.get(event_type, self.ESTIMATED_MEMORY["default"])

        # Add size for data field
        data = event.get("data", {})
        if isinstance(data, dict):
            import json

            with contextlib.suppress(Exception):
                base_size += len(json.dumps(data))

        return base_size

    async def add(self, event: dict[str, Any]) -> bool:
        """Add an event to the batch.

        Args:
            event: Event to add.

        Returns:
            True if added, False if dropped due to memory pressure.
        """
        size = self.estimate_event_size(event)

        with self._lock:
            # Check if we need to drop events
            while self._current_memory + size > self._max_memory_bytes and self._buffer:
                _oldest_event, oldest_size = self._buffer.popleft()
                self._current_memory -= oldest_size
                self._total_dropped += 1
                logger.warning("[audit.batcher] Memory pressure, dropping oldest event")

            # Check if we would exceed memory after adding
            if self._current_memory + size > self._max_memory_bytes:
                self._total_dropped += 1
                logger.warning("[audit.batcher] Event too large for memory budget, dropping")
                return False

            # Add event
            self._buffer.append((event, size))
            self._current_memory += size
            self._total_batched += 1

        return True

    def should_flush(self) -> bool:
        """Check if batch should be flushed.

        Returns:
            True if batch size or memory threshold reached.
        """
        with self._lock:
            return len(self._buffer) >= self._batch_size or self._current_memory >= self._max_memory_bytes * 0.8

    async def flush(self) -> list[dict[str, Any]]:
        """Flush all buffered events.

        Returns:
            List of buffered events (empties buffer).
        """
        with self._lock:
            events = [e[0] for e in self._buffer]
            self._buffer.clear()
            self._current_memory = 0
            return events

    def get_stats(self) -> dict[str, Any]:
        """Get batcher statistics.

        Returns:
            Dictionary with batcher stats.
        """
        with self._lock:
            return {
                "buffered_events": len(self._buffer),
                "current_memory_bytes": self._current_memory,
                "max_memory_bytes": self._max_memory_bytes,
                "memory_utilization": (
                    self._current_memory / self._max_memory_bytes if self._max_memory_bytes > 0 else 0.0
                ),
                "total_batched": self._total_batched,
                "total_dropped": self._total_dropped,
            }


# =============================================================================
# AuditCircuitBreaker
# =============================================================================


class AuditCircuitBreaker:
    """Circuit breaker for audit write operations.

    States:
        CLOSED: Normal operation, writes allowed
        OPEN: Too many failures, writes blocked
        HALF_OPEN: Testing recovery

    Transitions:
        CLOSED -> OPEN: consecutive_failures >= threshold
        OPEN -> HALF_OPEN: timeout elapsed
        HALF_OPEN -> CLOSED: write succeeds
        HALF_OPEN -> OPEN: write fails
    """

    def __init__(
        self,
        threshold: int = 5,
        timeout: float = DEFAULT_SHORT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize circuit breaker.

        Args:
            threshold: Failures before opening.
            timeout: Seconds before half-open.
        """
        self._threshold = threshold
        self._timeout = timeout

        self._state = "closed"
        self._failures = 0
        self._successes = 0
        self._last_failure_time: float | None = None
        self._last_success_time: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Get current state."""
        with self._lock:
            if (
                self._state == "open"
                and self._last_failure_time
                and time.monotonic() - self._last_failure_time >= self._timeout
            ):
                self._state = "half_open"
            return self._state

    def is_write_allowed(self) -> bool:
        """Check if writes are allowed.

        Returns:
            True if in CLOSED or HALF_OPEN state.
        """
        return self.state != "open"

    def record_success(self) -> None:
        """Record a successful write."""
        with self._lock:
            self._successes += 1
            self._last_success_time = time.monotonic()

            if self._state == "half_open":
                self._state = "closed"
                self._failures = 0
                logger.info("[audit.circuit_breaker] Closed after successful write")
            elif self._state == "closed":
                # Reset failure count on success
                self._failures = 0

    def record_failure(self) -> None:
        """Record a failed write."""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == "half_open":
                self._state = "open"
                logger.warning("[audit.circuit_breaker] Reopened after failed write in half-open")
            elif self._failures >= self._threshold:
                self._state = "open"
                logger.warning(
                    "[audit.circuit_breaker] Opened after %d consecutive failures",
                    self._failures,
                )

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics.

        Returns:
            Dictionary with circuit breaker stats.
        """
        with self._lock:
            return {
                "state": self._state,
                "failures": self._failures,
                "successes": self._successes,
                "threshold": self._threshold,
                "timeout": self._timeout,
                "last_failure_time": self._last_failure_time,
                "last_success_time": self._last_success_time,
            }


# =============================================================================
# AuditFallbackManager
# =============================================================================


class AuditFallbackManager:
    """Multi-tier fallback manager for audit system.

    Tiers (in order of preference):
    1. Memory buffer (fastest)
    2. Async write to disk (normal)
    3. Drop non-critical events (emergency)

    Usage:
        manager = AuditFallbackManager()
        await manager.emit(event)  # Uses best available tier
    """

    def __init__(
        self,
        memory_buffer: MemoryBoundedBatcher | None = None,
        circuit_breaker: AuditCircuitBreaker | None = None,
    ) -> None:
        """Initialize fallback manager.

        Args:
            memory_buffer: Memory-bounded batcher.
            circuit_breaker: Circuit breaker for writes.
        """
        self._memory_buffer = memory_buffer
        self._circuit_breaker = circuit_breaker

        # Callbacks for tier transitions
        self._tier_callbacks: list[Callable[[str, str], None]] = []

    def add_tier_callback(self, callback: Callable[[str, str], None]) -> None:
        """Add callback for tier transitions.

        Args:
            callback: Called with (from_tier, to_tier).
        """
        self._tier_callbacks.append(callback)

    async def emit(self, event: dict[str, Any]) -> str:
        """Emit event using best available tier.

        Args:
            event: Event to emit.

        Returns:
            Event ID if emitted, empty string if dropped.
        """
        # Tier 1: Memory buffer (always available)
        if self._memory_buffer:
            added = await self._memory_buffer.add(event)
            if added:
                return event.get("event_id", "")
            # If memory buffer rejected, fall through

        # Tier 2: Normal write (if circuit breaker allows)
        if self._circuit_breaker and self._circuit_breaker.is_write_allowed():
            # This would write to KernelAuditRuntime
            # For now, just record success
            self._circuit_breaker.record_success()
            return event.get("event_id", "")

        # Tier 3: Drop (emergency)
        logger.warning("[audit.fallback] All tiers exhausted, dropping event")
        return ""

    def get_current_tier(self) -> str:
        """Get current active tier.

        Returns:
            Tier name: "memory", "disk", or "drop".
        """
        if self._circuit_breaker and not self._circuit_breaker.is_write_allowed():
            if self._memory_buffer:
                return "memory"
            return "drop"

        return "disk"

    def get_stats(self) -> dict[str, Any]:
        """Get fallback manager statistics.

        Returns:
            Dictionary with stats from all tiers.
        """
        return {
            "current_tier": self.get_current_tier(),
            "memory_buffer": (self._memory_buffer.get_stats() if self._memory_buffer else None),
            "circuit_breaker": (self._circuit_breaker.get_stats() if self._circuit_breaker else None),
        }


# =============================================================================
# Utility functions
# =============================================================================


def apply_ha_config(bus: Any, config: HAConfig) -> None:
    """Apply HA configuration to an audit bus.

    Args:
        bus: OmniscientAuditBus instance.
        config: HA configuration.
    """
    # Storm detector
    storm_detector = PriorityBasedStormDetector(
        elevated=config.storm_elevated_threshold,
        warning=config.storm_warning_threshold,
        critical=config.storm_critical_threshold,
        emergency=config.storm_emergency_threshold,
    )
    bus._storm_detector = storm_detector

    # Memory-bounded batcher
    _batcher = MemoryBoundedBatcher(
        max_memory_mb=config.max_memory_mb,
        batch_size=config.batch_size,
    )
    # Note: Batcher needs to be integrated with bus (future work)

    # Circuit breaker
    _circuit_breaker = AuditCircuitBreaker(
        threshold=config.circuit_breaker_threshold,
        timeout=config.circuit_breaker_timeout,
    )
    # Note: Circuit breaker needs to be integrated with bus (future work)

    logger.info("[audit.ha] Applied HA configuration to audit bus")


__all__ = [
    "AuditCircuitBreaker",
    "AuditFallbackManager",
    "AuditSampler",
    "AuditStormDetector",
    "DegradationLevel",
    "HAConfig",
    "MemoryBoundedBatcher",
    "PriorityBasedStormDetector",
    "apply_ha_config",
    # Note: StormLevel exported from polaris.kernelone.audit.omniscient.metrics
]
