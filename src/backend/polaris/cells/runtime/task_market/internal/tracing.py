"""OpenTelemetry tracing wrapper for ``runtime.task_market`` operations.

Follows the same pattern as ``polaris.kernelone.cognitive.telemetry.CognitiveTelemetry``:
- Enabled via ``KERNELONE_TASK_MARKET_TRACING_ENABLED`` (default: ``false``)
- NoOpSpan fallback when disabled
- Lazy OTel SDK import to avoid hard dependency at module load
"""

from __future__ import annotations

import os
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any


class NoOpSpan:
    """No-op span context manager for when tracing is disabled."""

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass


class TaskMarketTracer:
    """OTel tracing wrapper for task_market operations.

    Usage::

        tracer = get_task_market_tracer()
        with tracer.start_span("task_market.publish", {"task_id": "t-1", "stage": "pending_exec"}):
            service.publish_work_item(command)
    """

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is not None:
            self._enabled = enabled
        else:
            raw = str(os.environ.get("KERNELONE_TASK_MARKET_TRACING_ENABLED", "false") or "false").strip().lower()
            self._enabled = raw in {"1", "true", "yes", "on"}

        self._tracer: Any = None
        if self._enabled:
            try:
                from opentelemetry import trace

                self._tracer = trace.get_tracer("runtime.task_market")
            except ImportError:
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> AbstractContextManager[Any]:
        """Start a new OTel span for a task_market operation.

        When tracing is disabled, returns a ``NoOpSpan``.
        """
        if not self._enabled or self._tracer is None:
            return NoOpSpan()
        return self._tracer.start_as_current_span(name, attributes=attributes)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_tracer_lock: Any = __import__("threading").Lock()
_tracer_singleton: TaskMarketTracer | None = None


def get_task_market_tracer() -> TaskMarketTracer:
    """Return the global TaskMarketTracer singleton."""
    global _tracer_singleton
    if _tracer_singleton is not None:
        return _tracer_singleton
    with _tracer_lock:
        if _tracer_singleton is None:
            _tracer_singleton = TaskMarketTracer()
        return _tracer_singleton


def reset_task_market_tracer_for_testing(enabled: bool = False) -> TaskMarketTracer:
    """Create a fresh tracer singleton for test isolation."""
    global _tracer_singleton
    with _tracer_lock:
        _tracer_singleton = TaskMarketTracer(enabled=enabled)
    return _tracer_singleton


__all__ = [
    "NoOpSpan",
    "TaskMarketTracer",
    "get_task_market_tracer",
    "reset_task_market_tracer_for_testing",
]
