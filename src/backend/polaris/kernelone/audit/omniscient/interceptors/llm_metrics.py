"""LLMMetricsStore — time-windowed LLM call metrics aggregation.

Provides a bounded ring buffer of recent LLM call events with
sliding-window aggregation by role / provider / model dimensions.

Thread-safe. Designed to be driven by the existing LLMCallInterceptor
event path — no new polling, SSE, or file-based real-time links.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EVENTS = 2000
_DEFAULT_WINDOW_SECONDS = 300


@dataclass(slots=True, frozen=True)
class LLMEventRecord:
    """Single bounded LLM call event record."""

    ts: float
    role: str
    provider: str
    model: str
    latency_ms: float
    is_error: bool
    prompt_tokens: int
    completion_tokens: int


@dataclass
class _DimensionAccumulator:
    """Internal per-(role, provider, model) accumulator."""

    total_calls: int = 0
    error_calls: int = 0
    latency_sum_ms: float = 0.0
    latency_min_ms: float = float("inf")
    latency_max_ms: float = 0.0
    prompt_tokens_sum: int = 0
    completion_tokens_sum: int = 0


class LLMMetricsStore:
    """Time-windowed LLM metrics store with bounded ring buffer.

    Usage::

        store = LLMMetricsStore()
        store.record(role="director", provider="openai", model="gpt-4",
                     latency_ms=500.0, is_error=False)
        snapshot = store.query(window_seconds=60)

    The store retains at most *max_events* records (FIFO eviction).
    ``query()`` filters by ``now - ts <= window_seconds`` and aggregates
    by ``(role, provider, model)``.
    """

    def __init__(
        self,
        *,
        max_events: int = _DEFAULT_MAX_EVENTS,
        default_window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._max_events = max_events
        self._default_window_seconds = default_window_seconds
        self._events: list[LLMEventRecord] = []
        self._lock = Lock()

    # -- recording ----------------------------------------------------------

    def record(
        self,
        *,
        role: str = "",
        provider: str = "",
        model: str = "",
        latency_ms: float = 0.0,
        is_error: bool = False,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Record a single LLM call completion event."""
        rec = LLMEventRecord(
            ts=time.time(),
            role=role or "unknown",
            provider=provider or "unknown",
            model=model or "unknown",
            latency_ms=max(0.0, latency_ms),
            is_error=is_error,
            prompt_tokens=max(0, prompt_tokens),
            completion_tokens=max(0, completion_tokens),
        )
        with self._lock:
            self._events.append(rec)
            if len(self._events) > self._max_events:
                overflow = len(self._events) - self._max_events
                self._events = self._events[overflow:]

    # -- querying -----------------------------------------------------------

    def query(
        self,
        *,
        window_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Return aggregated metrics for the requested window.

        Returns a dict matching the ``LLMMetricsResponse`` schema.
        """
        win = window_seconds if window_seconds is not None and window_seconds > 0 else self._default_window_seconds
        now = time.time()
        cutoff = now - win

        with self._lock:
            recent = [e for e in self._events if e.ts >= cutoff]

        if not recent:
            return self._empty_snapshot(win, now)

        dims: dict[tuple[str, str, str], _DimensionAccumulator] = {}
        total = _DimensionAccumulator()

        for ev in recent:
            key = (ev.role, ev.provider, ev.model)
            if key not in dims:
                dims[key] = _DimensionAccumulator()
            acc = dims[key]
            acc.total_calls += 1
            if ev.is_error:
                acc.error_calls += 1
            acc.latency_sum_ms += ev.latency_ms
            acc.latency_min_ms = min(acc.latency_min_ms, ev.latency_ms)
            acc.latency_max_ms = max(acc.latency_max_ms, ev.latency_ms)
            acc.prompt_tokens_sum += ev.prompt_tokens
            acc.completion_tokens_sum += ev.completion_tokens

            total.total_calls += 1
            if ev.is_error:
                total.error_calls += 1
            total.latency_sum_ms += ev.latency_ms
            total.latency_min_ms = min(total.latency_min_ms, ev.latency_ms)
            total.latency_max_ms = max(total.latency_max_ms, ev.latency_ms)
            total.prompt_tokens_sum += ev.prompt_tokens
            total.completion_tokens_sum += ev.completion_tokens

        rows = self._build_rows(dims)
        total_row = self._build_total_row(total)

        return {
            "window_seconds": win,
            "generated_at": now,
            "total": total_row,
            "dimensions": rows,
        }

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _build_rows(dims: dict[tuple[str, str, str], _DimensionAccumulator]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for (role, provider, model), acc in sorted(dims.items()):
            count = acc.total_calls
            success = count - acc.error_calls
            rows.append(
                {
                    "role": role,
                    "provider": provider,
                    "model": model,
                    "total_calls": count,
                    "error_calls": acc.error_calls,
                    "error_rate": round(acc.error_calls / count, 4) if count > 0 else 0.0,
                    "avg_latency_ms": round(acc.latency_sum_ms / count, 2) if count > 0 else 0.0,
                    "p50_latency_ms": 0.0,
                    "p95_latency_ms": 0.0,
                    "p99_latency_ms": 0.0,
                    "min_latency_ms": round(acc.latency_min_ms, 2) if acc.latency_min_ms != float("inf") else 0.0,
                    "max_latency_ms": round(acc.latency_max_ms, 2),
                    "prompt_tokens_sum": acc.prompt_tokens_sum,
                    "completion_tokens_sum": acc.completion_tokens_sum,
                    "cache_hit_rate": round(success / count, 4) if count > 0 else 0.0,
                }
            )
        return rows

    @staticmethod
    def _build_total_row(acc: _DimensionAccumulator) -> dict[str, Any]:
        count = acc.total_calls
        success = count - acc.error_calls
        return {
            "total_calls": count,
            "error_calls": acc.error_calls,
            "error_rate": round(acc.error_calls / count, 4) if count > 0 else 0.0,
            "avg_latency_ms": round(acc.latency_sum_ms / count, 2) if count > 0 else 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
            "min_latency_ms": round(acc.latency_min_ms, 2) if acc.latency_min_ms != float("inf") else 0.0,
            "max_latency_ms": round(acc.latency_max_ms, 2),
            "prompt_tokens_sum": acc.prompt_tokens_sum,
            "completion_tokens_sum": acc.completion_tokens_sum,
            "cache_hit_rate": round(success / count, 4) if count > 0 else 0.0,
        }

    def _empty_snapshot(self, window_seconds: int, now: float) -> dict[str, Any]:
        empty_row = {
            "total_calls": 0,
            "error_calls": 0,
            "error_rate": 0.0,
            "avg_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "p99_latency_ms": 0.0,
            "min_latency_ms": 0.0,
            "max_latency_ms": 0.0,
            "prompt_tokens_sum": 0,
            "completion_tokens_sum": 0,
            "cache_hit_rate": 0.0,
        }
        return {
            "window_seconds": window_seconds,
            "generated_at": now,
            "total": dict(empty_row),
            "dimensions": [],
        }

    def reset(self) -> None:
        """Clear all buffered events."""
        with self._lock:
            self._events.clear()

    @property
    def event_count(self) -> int:
        """Number of buffered events."""
        with self._lock:
            return len(self._events)


# -- singleton accessor ---------------------------------------------------

_metrics_store: LLMMetricsStore | None = None
_store_lock = Lock()


def get_llm_metrics_store() -> LLMMetricsStore:
    """Return the process-global ``LLMMetricsStore`` singleton."""
    global _metrics_store
    with _store_lock:
        if _metrics_store is None:
            _metrics_store = LLMMetricsStore()
        return _metrics_store


__all__ = ["LLMMetricsStore", "get_llm_metrics_store"]
