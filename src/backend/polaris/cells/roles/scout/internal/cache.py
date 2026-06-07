"""Tiny in-process TTL cache for in-Turn probe de-duplication (UTF-8)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Single-process TTL cache. Not shared across processes; cleared on GC."""

    def __init__(self, ttl_seconds: float = 60.0, now: Callable[[], float] | None = None) -> None:
        self._ttl = float(ttl_seconds)
        self._now = now or time.monotonic
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._now() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T) -> None:
        self._store[key] = (self._now() + self._ttl, value)
