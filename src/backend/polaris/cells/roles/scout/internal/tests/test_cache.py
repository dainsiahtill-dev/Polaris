"""Tests for TTLCache (UTF-8)."""
from polaris.cells.roles.scout.internal.cache import TTLCache


def test_cache_returns_value_within_ttl_then_expires() -> None:
    clock = {"t": 100.0}
    cache: TTLCache[str] = TTLCache(ttl_seconds=30, now=lambda: clock["t"])
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock["t"] = 131.0  # 31s later > ttl
    assert cache.get("k") is None


def test_cache_miss_returns_none() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=30, now=lambda: 0.0)
    assert cache.get("absent") is None
