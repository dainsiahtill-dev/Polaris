"""Ephemeral invocation lifetime control; never an execution capability."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)


class ProviderInvocationBudget:
    """An irreversible stop signal and a non-extendable monotonic deadline.

    Cancellation requests local teardown; it does NOT prove that a transport
    is quiescent. The physical-attempt owner still settles actual termination.
    """

    def __init__(self, deadline: float) -> None:
        if not math.isfinite(deadline):
            raise ValueError("provider invocation deadline must be finite")
        self._deadline = deadline
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reason = ""
        self._callbacks: dict[object, Callable[[], None]] = {}
        self._callback_errors: list[str] = []

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def stop_reason(self) -> str:
        with self._lock:
            return self._reason

    def remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self.cancel("deadline")
        if self.stop_reason:
            raise TimeoutError(f"provider_invocation_{self.stop_reason}")
        return remaining

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self._reason:
                return
            self._reason = reason
            self._stop_event.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            self._notify(callback)

    def _notify(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except (RuntimeError, ValueError, TypeError, OSError) as error:
            error_type = type(error).__name__
            with self._lock:
                self._callback_errors.append(error_type)
            logger.warning("provider cancellation callback failed: %s", error_type)

    @property
    def cancellation_callback_errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._callback_errors)

    def wait_for_stop(self, seconds: float) -> bool:
        """Interrupt worker backoff on stop/deadline, not a product poll."""
        try:
            delay = min(max(0.0, seconds), self.remaining())
        except TimeoutError:
            return True
        return self._stop_event.wait(delay)

    def on_cancel(self, callback: Callable[[], None]) -> Callable[[], None]:
        key = object()
        with self._lock:
            stopped = bool(self._reason)
            if not stopped:
                self._callbacks[key] = callback
        if stopped:
            self._notify(callback)

        def unsubscribe() -> None:
            with self._lock:
                self._callbacks.pop(key, None)

        return unsubscribe


_budget: ContextVar[ProviderInvocationBudget | None] = ContextVar("provider_invocation_budget", default=None)


def get_provider_invocation_budget() -> ProviderInvocationBudget | None:
    return _budget.get()


@contextmanager
def bind_provider_invocation_budget(timeout_seconds: float) -> Iterator[ProviderInvocationBudget]:
    timeout = float(timeout_seconds)
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("provider invocation timeout must be positive and finite")
    parent = _budget.get()
    deadline = time.monotonic() + timeout
    if parent is not None:
        deadline = min(deadline, parent.deadline)
    budget = ProviderInvocationBudget(deadline)
    unsubscribe = parent.on_cancel(lambda: budget.cancel(parent.stop_reason)) if parent else None
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        budget.cancel("scope_closed")
        _budget.reset(token)
        if unsubscribe:
            unsubscribe()


async def invoke_with_budget(coro: Any, timeout: float) -> Any:
    """Share the caller deadline/cancel signal with copied worker contexts."""
    with bind_provider_invocation_budget(timeout) as budget:
        try:
            return await asyncio.wait_for(coro, timeout=budget.remaining())
        except asyncio.TimeoutError:
            budget.cancel("deadline")
            raise
        except asyncio.CancelledError:
            budget.cancel("caller_cancelled")
            raise
