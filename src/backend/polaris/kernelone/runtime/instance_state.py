"""Instance-scoped state primitives for workspace/runtime isolation."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

T = TypeVar("T")
logger = logging.getLogger(__name__)

_current_instance: ContextVar[str | None] = ContextVar(
    "kernelone_current_instance",
    default=None,
)


def normalize_workspace_instance_id(workspace: str | Path) -> str:
    token = str(workspace or "").strip()
    if not token:
        raise ValueError("workspace must be a non-empty string")
    return str(Path(token).resolve())


@dataclass
class _InstanceEntry(Generic[T]):
    value: T
    cleanup_hooks: list[Callable[[T], None]] = field(default_factory=list)


class InstanceScopedStateStore(Generic[T]):
    """Thread-safe, lazily initialized store keyed by instance/workspace id."""

    def __init__(
        self,
        *,
        normalizer: Callable[[str | Path], str] | None = None,
        on_dispose: Callable[[T], None] | None = None,
    ) -> None:
        self._normalizer = normalizer or (str)
        self._on_dispose = on_dispose
        self._entries: dict[str, _InstanceEntry[T]] = {}
        self._lock = threading.RLock()

    def get_or_create(self, instance_id: str | Path, factory: Callable[[], T]) -> T:
        key = self._normalize(instance_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return entry.value
            value = factory()
            self._entries[key] = _InstanceEntry(value=value)
            return value

    def get(self, instance_id: str | Path) -> T | None:
        key = self._normalize(instance_id)
        with self._lock:
            entry = self._entries.get(key)
            return entry.value if entry is not None else None

    def has(self, instance_id: str | Path) -> bool:
        key = self._normalize(instance_id)
        with self._lock:
            return key in self._entries

    def register_cleanup(self, instance_id: str | Path, callback: Callable[[T], None]) -> None:
        key = self._normalize(instance_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError(f"instance not found: {key}")
            entry.cleanup_hooks.append(callback)

    def dispose(self, instance_id: str | Path) -> bool:
        key = self._normalize(instance_id)
        with self._lock:
            entry = self._entries.pop(key, None)
        if entry is None:
            return False
        self._run_cleanup_hooks(entry)
        return True

    def clear(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            self._run_cleanup_hooks(entry)

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._entries.keys())

    def _normalize(self, instance_id: str | Path) -> str:
        key = str(self._normalizer(instance_id) or "").strip()
        if not key:
            raise ValueError("instance_id must resolve to a non-empty string")
        return key

    def _run_cleanup_hooks(self, entry: _InstanceEntry[T]) -> None:
        if self._on_dispose is not None:
            try:
                self._on_dispose(entry.value)
            except (RuntimeError, ValueError) as exc:
                logger.warning("instance on_dispose hook failed: %s", exc, exc_info=True)
        for callback in entry.cleanup_hooks:
            try:
                callback(entry.value)
            except (RuntimeError, ValueError) as exc:
                logger.warning("instance cleanup hook failed: %s", exc, exc_info=True)


@contextmanager
def scoped_instance(instance_id: str | Path) -> Iterator[str]:
    """Set the current instance id in context for the current execution scope."""
    normalized = str(instance_id or "").strip()
    if not normalized:
        raise ValueError("instance_id is required")
    token = _current_instance.set(normalized)
    try:
        yield normalized
    finally:
        _current_instance.reset(token)


def get_current_instance_id() -> str | None:
    return _current_instance.get()


__all__ = [
    "InstanceScopedStateStore",
    "get_current_instance_id",
    "normalize_workspace_instance_id",
    "scoped_instance",
]
