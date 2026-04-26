"""KernelOne debug stream interposition framework.

This module provides an opt-in, context-local debug event bus that can be
enabled by delivery hosts without coupling lower runtime layers to any
specific renderer. Producers emit structured debug events; the active host
decides whether and how to surface them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

DebugEventSink = Callable[[dict[str, Any]], None]

_DEBUG_ENABLED: ContextVar[bool] = ContextVar(
    "kernelone_debug_stream_enabled",
    default=False,
)
_DEBUG_SINK: ContextVar[DebugEventSink | None] = ContextVar(
    "kernelone_debug_stream_sink",
    default=None,
)
_DEBUG_TAGS: ContextVar[dict[str, Any]] = ContextVar("kernelone_debug_stream_tags", default={})  # noqa: B039


def _normalize_debug_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _normalize_debug_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalize_debug_value(item) for item in value]
    if is_dataclass(value):
        # Handle both instances and types
        if isinstance(value, type):
            # For types, create an instance (assuming no init args needed)
            instance = value()
            return _normalize_debug_value(asdict(instance))
        return _normalize_debug_value(asdict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _normalize_debug_value(to_dict())
        except TypeError:
            pass
    return str(value)


def is_debug_stream_enabled() -> bool:
    return bool(_DEBUG_ENABLED.get())


@contextmanager
def debug_stream_session(
    *,
    enabled: bool,
    sink: DebugEventSink | None = None,
    tags: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    """Enable a scoped debug stream for the current execution context."""

    enabled_token = _DEBUG_ENABLED.set(bool(enabled))
    sink_token = _DEBUG_SINK.set(sink if enabled else None)
    merged_tags = dict(_DEBUG_TAGS.get() or {})
    if tags:
        merged_tags.update({str(key): _normalize_debug_value(value) for key, value in dict(tags).items()})
    tags_token = _DEBUG_TAGS.set(merged_tags if enabled else {})
    try:
        yield
    finally:
        # ContextVar.reset() can raise ValueError if the token was created in a
        # different context (e.g., during asyncio shutdown or task cancellation).
        # This is safe to ignore - the context will be cleaned up anyway.
        with suppress(ValueError):
            _DEBUG_TAGS.reset(tags_token)
        with suppress(ValueError):
            _DEBUG_SINK.reset(sink_token)
        with suppress(ValueError):
            _DEBUG_ENABLED.reset(enabled_token)


def emit_debug_event(
    *,
    category: str,
    label: str,
    payload: Mapping[str, Any] | None = None,
    source: str = "",
) -> dict[str, Any] | None:
    """Emit one structured debug event to the active sink."""

    if not is_debug_stream_enabled():
        return None

    # Normalize tags to handle any non-serializable values (e.g., methods)
    raw_tags = _DEBUG_TAGS.get() or {}
    normalized_tags = {str(k): _normalize_debug_value(v) for k, v in raw_tags.items()}

    # Normalize payload to handle any non-serializable values
    normalized_payload = _normalize_debug_value(dict(payload or {}))

    event = {
        "timestamp": _utc_now_iso(),
        "category": str(category or "debug").strip() or "debug",
        "label": str(label or "event").strip() or "event",
        "source": str(source or "").strip(),
        "tags": normalized_tags,
        "payload": normalized_payload,
    }
    sink = _DEBUG_SINK.get()
    if callable(sink):
        with suppress(Exception):
            # Sink errors should not propagate to prevent debug stream from breaking execution
            sink(event)
    return event


__all__ = [
    "DebugEventSink",
    "debug_stream_session",
    "emit_debug_event",
    "is_debug_stream_enabled",
]
