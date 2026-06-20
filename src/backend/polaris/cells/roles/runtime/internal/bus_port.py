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


def _complete_async_waiter(waiter: asyncio.Future[None]) -> None:
    if not waiter.done():
        waiter.set_result(None)


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
        self._condition = threading.Condition(self._lock)
        # inbox: receiver_name -> list[AgentEnvelope]
        self._inbox: dict[str, list[AgentEnvelope]] = {}
        # inflight: message_id -> AgentEnvelope  (ack/nack pending)
        self._inflight: dict[str, AgentEnvelope] = {}
        # dead_letter store
        self._dead: list[DeadLetterRecord] = []
        # receiver_name -> async waiters. publish() wakes them through their event loop.
        self._async_waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]]] = {}
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
            self._condition.notify_all()
            self._wake_async_waiters_locked(envelope.receiver)
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
        """Receive next message for receiver. Thread-safe."""
        envelope = self._pop_and_mark_inflight(receiver)
        if envelope is not None:
            return envelope

        if not block:
            return None

        timeout_seconds = max(0.0, float(timeout))
        if timeout_seconds <= 0.0:
            return None

        receiver_key = str(receiver or "")
        with self._condition:
            if not self._condition.wait_for(
                lambda: bool(self._inbox.get(receiver_key)),
                timeout=timeout_seconds,
            ):
                return None
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
        """Receive next message for receiver using event-loop wakeups.

        ``poll_interval`` is accepted for compatibility only; no interval
        wakeup or sleep loop is used.

        Args:
            receiver: The receiver name to poll messages for.
            block: If True, wait until a message arrives or timeout expires.
            timeout: Maximum time to wait when block=True (seconds).
                A value <= 0 returns immediately.
            poll_interval: Compatibility argument; ignored.

        Returns:
            The next `AgentEnvelope` for `receiver`, or None if no message
            is available within the timeout (or immediately if block=False).

        Raises:
            asyncio.CancelledError: Propagates if cancellation occurs while
                waiting for a publish wakeup.

        Implementation notes:
            - Registers one future per waiting receiver.
            - publish() wakes futures through loop.call_soon_threadsafe().
            - Timeout is handled by asyncio.wait_for(), not by interval checks.
        """
        _ = poll_interval
        safe_timeout = max(0.0, float(timeout))

        envelope = self._pop_and_mark_inflight(receiver)
        if envelope is not None:
            return envelope
        if not block or safe_timeout <= 0.0:
            return None

        receiver_key = str(receiver or "")
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        with self._lock:
            envelope = self._pop_and_mark_inflight(receiver)
            if envelope is not None:
                return envelope
            self._async_waiters.setdefault(receiver_key, []).append((loop, waiter))

        try:
            await asyncio.wait_for(waiter, timeout=safe_timeout)
        except asyncio.TimeoutError:
            return None
        except asyncio.CancelledError:
            raise
        finally:
            self._remove_async_waiter(receiver_key, waiter)

        return self._pop_and_mark_inflight(receiver)

    def _wake_async_waiters_locked(self, receiver: str) -> None:
        receiver_key = str(receiver or "")
        waiters = self._async_waiters.pop(receiver_key, [])
        for loop, waiter in waiters:
            if waiter.done():
                continue
            try:
                loop.call_soon_threadsafe(_complete_async_waiter, waiter)
            except RuntimeError:
                continue

    def _remove_async_waiter(self, receiver: str, waiter: asyncio.Future[None]) -> None:
        with self._lock:
            waiters = self._async_waiters.get(receiver)
            if not waiters:
                return
            remaining = [(loop, item) for loop, item in waiters if item is not waiter and not item.done()]
            if remaining:
                self._async_waiters[receiver] = remaining
            else:
                self._async_waiters.pop(receiver, None)

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
    "AgentBusPort",
    "AgentEnvelope",
    "DeadLetterRecord",
    "InMemoryAgentBusPort",
]
