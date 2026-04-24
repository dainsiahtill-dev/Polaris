"""roles.runtime Bus Port — KernelOne Bus abstraction boundary.

This module provides the in-memory implementation of the AgentBusPort Protocol
defined in KernelOne.

Architecture note (2026-04-04 P0-007 Fix):
  Core Protocol and data types are now defined in KernelOne:
    - `polaris.kernelone.multi_agent.bus_port.AgentBusPort`
    - `polaris.kernelone.multi_agent.bus_port.AgentEnvelope`
    - `polaris.kernelone.multi_agent.bus_port.DeadLetterRecord`

  This file provides:
    1. `InMemoryAgentBusPort` — default implementation backed by asyncio queues
       (replaces the old file-system inbox/inflight/dead_letter).
    2. Re-export of KernelOne types for backward compatibility.

  This ensures KernelOne → Cells import fence is maintained (single direction).

Gap logged:
  - Full KernelOne Bus integration (topic routing, durable delivery, cross-
    process) is NOT implemented here. That requires a KernelOne Bus adapter
    that maps AgentMessageType -> KernelOne MessageType and provides a NATS/
    in-process transport. Tracked as governance gap in cell.yaml.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

# Import queue size constants from KernelOne (single source of truth)
from polaris.kernelone.events.bus_constants import (
    DEFAULT_MAX_DEAD_LETTERS as _MAX_DEAD_LETTER,
    DEFAULT_MAX_QUEUE_SIZE as _MAX_QUEUE_SIZE,
)

# Import core types from KernelOne (maintains KernelOne → Cells dependency direction)
from polaris.kernelone.multi_agent.bus_port import (
    _DEFAULT_POLL_INTERVAL_SEC,
    AgentBusPort,
    AgentEnvelope,
    DeadLetterRecord,
)

logger = logging.getLogger(__name__)

# Upper bound on async poll cancellation delay when shutdown is requested (seconds).
# Prevents indefinite blocking if the caller ignores cancellation.
_MAX_CANCEL_DELAY_SEC: float = 1.0


class InMemoryAgentBusPort:
    """In-memory Bus Port backed by threading.Event + dict-of-lists.

    Design decisions:
    - Thread-safe via a single re-entrant lock (same thread safety class as
      the old file-system queue, which used shutil.move which is not atomic
      on all platforms).
    - Inbox is a list of AgentEnvelope; inflight is tracked separately.
    - Dead-letter is bounded to _MAX_DEAD_LETTER; oldest records are evicted
      and a WARNING is logged.
    - No file I/O — satisfies the KernelOne Bus unification requirement.
    - publish() also delivers to subscribers registered via subscribe(), so
      the port can forward messages to a KernelOne MessageBus when one is
      injected later (forward-compatible hook).
    """

    def __init__(self, max_queue_size: int = _MAX_QUEUE_SIZE) -> None:
        self._max_queue_size = max(1, int(max_queue_size))
        self._lock = threading.RLock()
        # inbox: receiver_name -> list[AgentEnvelope]
        self._inbox: dict[str, list[AgentEnvelope]] = {}
        # inflight: message_id -> AgentEnvelope  (ack/nack pending)
        self._inflight: dict[str, AgentEnvelope] = {}
        # dead_letter store
        self._dead: list[DeadLetterRecord] = []
        # optional downstream forwarding callbacks (e.g. KernelOne MessageBus)
        self._subscribers: list[Any] = []

    # ------------------------------------------------------------------
    # AgentBusPort interface
    # ------------------------------------------------------------------

    def publish(self, envelope: AgentEnvelope) -> bool:
        """Deliver envelope to receiver's inbox. Thread-safe."""
        with self._lock:
            inbox = self._inbox.setdefault(envelope.receiver, [])
            if len(inbox) >= self._max_queue_size:
                logger.warning(
                    "bus_port.publish: inbox full for receiver=%s (size=%d), dropping message_id=%s type=%s sender=%s",
                    envelope.receiver,
                    len(inbox),
                    envelope.message_id,
                    envelope.msg_type,
                    envelope.sender,
                )
                return False
            inbox.append(envelope)
        logger.debug(
            "bus_port.publish: queued message_id=%s type=%s sender=%s receiver=%s",
            envelope.message_id,
            envelope.msg_type,
            envelope.sender,
            envelope.receiver,
        )
        return True

    def poll(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
    ) -> AgentEnvelope | None:
        """Poll next message for receiver. Thread-safe.

        Uses blocking `time.sleep()` which cannot be cancelled by asyncio.
        For async contexts, use `poll_async()` instead.
        """
        envelope = self._pop_and_mark_inflight(receiver)
        if envelope is not None:
            return envelope

        if not block:
            return None

        # Simple blocking poll: sleep in small intervals
        deadline = time.monotonic() + max(0.0, float(timeout))
        interval = min(0.1, max(0.01, float(timeout) / 10))
        while time.monotonic() < deadline:
            time.sleep(interval)
            envelope = self._pop_and_mark_inflight(receiver)
            if envelope is not None:
                return envelope
        return None

    async def poll_async(
        self,
        receiver: str,
        *,
        block: bool = False,
        timeout: float = 1.0,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_SEC,
    ) -> AgentEnvelope | None:
        """Poll next message for receiver using async-aware polling.

        This method yields to the event loop and can be cancelled via
        asyncio.cancel(). It is the async-safe alternative to `poll()`.

        Args:
            receiver: The receiver name to poll messages for.
            block: If True, wait until a message arrives or timeout expires.
            timeout: Maximum time to wait when block=True (seconds).
                A value <= 0 returns immediately.
            poll_interval: Time between polls (seconds). Must be positive.
                Defaults to 0.05 seconds for responsive cancellation.

        Returns:
            The next `AgentEnvelope` for `receiver`, or None if no message
            is available within the timeout (or immediately if block=False).

        Raises:
            asyncio.CancelledError: Propagates if cancellation occurs
                during a sleep interval. Caught and converted to return None
                if cancellation occurs at the boundary (last_interval case).

        Implementation notes:
            - Uses `asyncio.sleep()` instead of `time.sleep()` to yield.
            - Calculates remaining time per iteration to respect total timeout.
            - Applies a hard upper bound on cancellation delay to prevent
              indefinite blocking when the event loop is stuck.
        """
        # Validate and normalize poll_interval
        safe_interval = max(0.001, float(poll_interval))
        safe_timeout = max(0.0, float(timeout))

        # Fast path: non-blocking or zero timeout
        if not block or safe_timeout <= 0.0:
            envelope = self._pop_and_mark_inflight(receiver)
            if envelope is not None:
                return envelope
            return None

        deadline = time.monotonic() + safe_timeout

        while True:
            # Calculate remaining time
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Timeout expired
                return None

            # Use smaller of remaining time or poll_interval for this sleep
            sleep_time = min(remaining, safe_interval)

            # Apply hard upper bound to prevent indefinite blocking on
            # cancellation (e.g., if event loop is stuck). This ensures
            # the coroutine returns within a bounded time even if cancelled.
            bounded_sleep = min(sleep_time, _MAX_CANCEL_DELAY_SEC)

            try:
                await asyncio.sleep(bounded_sleep)
            except asyncio.CancelledError:
                # Propagate cancellation upward; this is the expected
                # behavior when a caller explicitly cancels the task.
                raise

            # Check for message after sleeping
            envelope = self._pop_and_mark_inflight(receiver)
            if envelope is not None:
                return envelope

            # Loop will continue with updated remaining time calculation

    def ack(self, message_id: str, receiver: str) -> bool:
        """Acknowledge message — remove from inflight."""
        with self._lock:
            env = self._inflight.pop(str(message_id or ""), None)
        if env is None:
            return False
        logger.debug("bus_port.ack: message_id=%s receiver=%s", message_id, receiver)
        return True

    def nack(
        self,
        message_id: str,
        receiver: str,
        *,
        reason: str = "",
        requeue: bool = True,
    ) -> bool:
        """Nack message — requeue or dead-letter."""
        with self._lock:
            env = self._inflight.pop(str(message_id or ""), None)
            if env is None:
                return False

            env.last_error = str(reason or "").strip()
            env.attempt += 1

            if requeue and env.attempt < env.max_attempts:
                logger.warning(
                    "bus_port.nack: requeuing message_id=%s attempt=%d/%d reason=%r",
                    message_id,
                    env.attempt,
                    env.max_attempts,
                    env.last_error,
                )
                # Re-insert at front of inbox so delivery order is preserved
                inbox = self._inbox.setdefault(env.receiver, [])
                inbox.insert(0, env)
            else:
                reason_str = reason or "max_attempts_exceeded"
                logger.warning(
                    "bus_port.nack: dead-letter message_id=%s receiver=%s reason=%r",
                    message_id,
                    env.receiver,
                    reason_str,
                )
                self._add_dead_letter(env, reason_str)

        return True

    def pending_count(self, receiver: str) -> int:
        """Return inbox size for receiver."""
        with self._lock:
            return len(self._inbox.get(str(receiver or ""), []))

    def requeue_all_inflight(self, receiver: str) -> int:
        """Requeue ALL inflight messages for a receiver back to inbox.

        Preserves FIFO order by requeuing in reverse order with insert(0).
        Thread-safe.

        Example: Original inbox = [msg1, msg2, msg3]
          poll() -> inflight in FIFO order: [msg1, msg2, msg3]
          reverse() -> [msg3, msg2, msg1]
          insert(0, ...) -> inbox = [msg1, msg2, msg3] (restored!)
        """
        receiver_key = str(receiver or "").strip()
        with self._lock:
            # Collect inflight messages for this receiver, preserving order
            to_requeue: list[AgentEnvelope] = []
            for msg_id, env in list(self._inflight.items()):
                if env.receiver == receiver_key:
                    to_requeue.append(env)
                    del self._inflight[msg_id]

            if not to_requeue:
                return 0

            # Reverse and insert at front to restore original FIFO order
            # Original inbox: [msg1, msg2, msg3]
            # poll() drains in FIFO: [msg1, msg2, msg3] (oldest first)
            # reverse(): [msg3, msg2, msg1] (newest first)
            # insert(0, ...) builds: [msg1, msg2, msg3] (restored FIFO)
            to_requeue.reverse()
            for env in to_requeue:
                inbox = self._inbox.setdefault(env.receiver, [])
                inbox.insert(0, env)

            return len(to_requeue)

    @property
    def dead_letters(self) -> list[DeadLetterRecord]:
        """Snapshot of all dead-letter records."""
        with self._lock:
            return list(self._dead)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pop_and_mark_inflight(self, receiver: str) -> AgentEnvelope | None:
        """Atomically pop from inbox and mark as inflight.

        Thread-safe: single lock protects both operations to prevent
        race conditions between concurrent poll() calls.
        """
        with self._lock:
            inbox = self._inbox.get(str(receiver or ""), [])
            if not inbox:
                return None
            envelope = inbox.pop(0)
            envelope.attempt += 1
            self._inflight[envelope.message_id] = envelope
            return envelope

    def _pop_inbox(self, receiver: str) -> AgentEnvelope | None:
        """Pop from inbox without marking inflight (legacy helper)."""
        with self._lock:
            inbox = self._inbox.get(str(receiver or ""), [])
            if not inbox:
                return None
            return inbox.pop(0)

    def _mark_inflight(self, envelope: AgentEnvelope) -> AgentEnvelope:
        """Mark envelope as inflight (assumes already popped from inbox).

        Note: For new code, prefer _pop_and_mark_inflight() for atomicity.
        """
        with self._lock:
            self._inflight[envelope.message_id] = envelope
        return envelope

    def _add_dead_letter(self, env: AgentEnvelope, reason: str) -> None:
        record = DeadLetterRecord(envelope=env, reason=reason)
        with self._lock:
            self._dead.append(record)
            # Evict oldest records when dead_letter store is full
            if len(self._dead) > _MAX_DEAD_LETTER:
                evicted = self._dead.pop(0)
                logger.warning(
                    "bus_port.dead_letter: evicted oldest record message_id=%s to keep store under limit=%d",
                    evicted.envelope.message_id,
                    _MAX_DEAD_LETTER,
                )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return a diagnostic snapshot. Safe to call at any time."""
        with self._lock:
            return {
                "receivers": {r: len(msgs) for r, msgs in self._inbox.items() if msgs},
                "inflight_count": len(self._inflight),
                "dead_letter_count": len(self._dead),
            }


__all__ = [
    "_DEFAULT_POLL_INTERVAL_SEC",
    "_MAX_CANCEL_DELAY_SEC",
    "AgentBusPort",
    "AgentEnvelope",
    "DeadLetterRecord",
    "InMemoryAgentBusPort",
]
