"""Thread-safe runtime state registry for in-memory API state."""

from __future__ import annotations

import threading
import uuid
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

T = TypeVar("T")


class RuntimeStateRegistry(Generic[T]):
    """Single-writer registry with snapshot reads for concurrent request handlers."""

    def __init__(self, *, id_prefix: str = "") -> None:
        self._lock = threading.RLock()
        self._items: dict[str, T] = {}
        self._id_prefix = str(id_prefix or "")

    def create_id(self, *, prefix: str | None = None) -> str:
        token = str(prefix if prefix is not None else self._id_prefix)
        while True:
            candidate = f"{token}{uuid.uuid4().hex}"
            with self._lock:
                if candidate not in self._items:
                    return candidate

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._items[str(key)] = value

    def get(self, key: str) -> T | None:
        with self._lock:
            return self._items.get(str(key))

    def pop(self, key: str) -> T | None:
        with self._lock:
            return self._items.pop(str(key), None)

    def contains(self, key: str) -> bool:
        with self._lock:
            return str(key) in self._items

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def values_snapshot(self) -> list[T]:
        with self._lock:
            return list(self._items.values())

    def items_snapshot(self) -> list[tuple[str, T]]:
        with self._lock:
            return list(self._items.items())

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def mutate(
        self,
        key: str,
        mutator: Callable[[T], None],
        *,
        default_factory: Callable[[], T] | None = None,
    ) -> T | None:
        with self._lock:
            token = str(key)
            if token not in self._items:
                if default_factory is None:
                    return None
                self._items[token] = default_factory()
            value = self._items[token]
            mutator(value)
            return value

    def prune(self, predicate: Callable[[str, T], bool]) -> list[str]:
        removed: list[str] = []
        with self._lock:
            for key, value in list(self._items.items()):
                if predicate(key, value):
                    self._items.pop(key, None)
                    removed.append(key)
        return removed

    def update_many(self, records: Iterable[tuple[str, T]]) -> None:
        with self._lock:
            for key, value in records:
                self._items[str(key)] = value
