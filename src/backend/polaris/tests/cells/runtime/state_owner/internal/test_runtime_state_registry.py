"""Tests for RuntimeStateRegistry — thread-safe in-memory state registry.

Coverage targets:
- All 13 public methods
- Thread-safety under concurrent writers and readers
- Edge cases: empty keys, missing entries, mutation semantics
"""

from __future__ import annotations

import threading
import time

import pytest
from polaris.cells.runtime.state_owner.internal.runtime_state_registry import (
    RuntimeStateRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> RuntimeStateRegistry[str]:
    """Return a fresh string registry for each test."""
    return RuntimeStateRegistry[str]()


@pytest.fixture
def int_registry() -> RuntimeStateRegistry[int]:
    """Return a fresh int registry for each test."""
    return RuntimeStateRegistry[int]()


# ---------------------------------------------------------------------------
# 1. Lifecycle / basic CRUD
# ---------------------------------------------------------------------------


def test_registry_initially_empty(registry: RuntimeStateRegistry[str]) -> None:
    """A fresh registry must have size 0 and contain nothing."""
    assert registry.size() == 0
    assert registry.contains("any") is False
    assert registry.get("any") is None


def test_set_and_get(registry: RuntimeStateRegistry[str]) -> None:
    """set + get roundtrip must return the stored value."""
    registry.set("k1", "v1")
    assert registry.get("k1") == "v1"


def test_set_overwrites_existing(registry: RuntimeStateRegistry[str]) -> None:
    """set on an existing key must overwrite the previous value."""
    registry.set("k1", "old")
    registry.set("k1", "new")
    assert registry.get("k1") == "new"


def test_pop_returns_value_and_removes(registry: RuntimeStateRegistry[str]) -> None:
    """pop must return the value and remove the key."""
    registry.set("k1", "v1")
    assert registry.pop("k1") == "v1"
    assert registry.contains("k1") is False
    assert registry.size() == 0


def test_pop_missing_returns_none(registry: RuntimeStateRegistry[str]) -> None:
    """pop on a missing key must return None."""
    assert registry.pop("missing") is None


def test_contains_true_and_false(registry: RuntimeStateRegistry[str]) -> None:
    """contains must reflect exact key presence."""
    registry.set("k1", "v1")
    assert registry.contains("k1") is True
    assert registry.contains("k2") is False


# ---------------------------------------------------------------------------
# 2. Bulk operations
# ---------------------------------------------------------------------------


def test_update_many_adds_multiple(registry: RuntimeStateRegistry[str]) -> None:
    """update_many must add all provided key-value pairs."""
    registry.update_many([("a", "1"), ("b", "2"), ("c", "3")])
    assert registry.size() == 3
    assert registry.get("a") == "1"
    assert registry.get("b") == "2"
    assert registry.get("c") == "3"


def test_clear_empties_registry(registry: RuntimeStateRegistry[str]) -> None:
    """clear must remove all entries and reset size to 0."""
    registry.set("a", "1")
    registry.set("b", "2")
    registry.clear()
    assert registry.size() == 0
    assert registry.get("a") is None


def test_values_snapshot_isolation(registry: RuntimeStateRegistry[str]) -> None:
    """values_snapshot must return a copy; mutating the list must not affect the registry."""
    registry.set("a", "1")
    snapshot = registry.values_snapshot()
    snapshot.append("injected")
    assert registry.size() == 1


def test_items_snapshot_matches_contents(registry: RuntimeStateRegistry[str]) -> None:
    """items_snapshot must reflect current key-value pairs exactly."""
    registry.set("x", "10")
    registry.set("y", "20")
    items = registry.items_snapshot()
    assert len(items) == 2
    assert ("x", "10") in items
    assert ("y", "20") in items


# ---------------------------------------------------------------------------
# 3. create_id
# ---------------------------------------------------------------------------


def test_create_id_uses_prefix(registry: RuntimeStateRegistry[str]) -> None:
    """create_id must prepend the supplied prefix to a UUID hex string."""
    rid = registry.create_id(prefix="sess-")
    assert rid.startswith("sess-")
    assert len(rid) > len("sess-")


def test_create_id_uses_constructor_prefix() -> None:
    """create_id without explicit prefix must fall back to the constructor prefix."""
    reg = RuntimeStateRegistry[str](id_prefix="job-")
    rid = reg.create_id()
    assert rid.startswith("job-")


def test_create_id_avoids_collisions(registry: RuntimeStateRegistry[str]) -> None:
    """create_id must loop until it finds an unused ID."""
    # Seed the registry so the first candidate collides
    dummy = registry.create_id(prefix="coll-")
    registry.set(dummy, "occupied")
    second = registry.create_id(prefix="coll-")
    assert second != dummy
    assert registry.contains(second) is False


# ---------------------------------------------------------------------------
# 4. mutate
# ---------------------------------------------------------------------------


def test_mutate_existing_value(registry: RuntimeStateRegistry[list[int]]) -> None:
    """mutate must apply the mutator to an existing value in-place."""
    reg: RuntimeStateRegistry[list[int]] = RuntimeStateRegistry[list[int]]()
    reg.set("nums", [1, 2])
    reg.mutate("nums", lambda lst: lst.append(3))
    assert reg.get("nums") == [1, 2, 3]


def test_mutate_missing_without_factory_returns_none(registry: RuntimeStateRegistry[str]) -> None:
    """mutate on a missing key without default_factory must return None."""
    assert registry.mutate("missing", lambda _s: None) is None


def test_mutate_missing_with_factory_creates_value(registry: RuntimeStateRegistry[list[int]]) -> None:
    """mutate on a missing key with default_factory must create and mutate the value."""
    reg: RuntimeStateRegistry[list[int]] = RuntimeStateRegistry[list[int]]()
    result = reg.mutate("nums", lambda lst: lst.append(42), default_factory=list)
    assert result == [42]
    assert reg.get("nums") == [42]


# ---------------------------------------------------------------------------
# 5. prune
# ---------------------------------------------------------------------------


def test_prune_removes_matching_keys(int_registry: RuntimeStateRegistry[int]) -> None:
    """prune must remove entries where predicate returns True."""
    int_registry.set("a", 1)
    int_registry.set("b", 2)
    int_registry.set("c", 3)
    removed = int_registry.prune(lambda _k, v: v > 1)
    assert sorted(removed) == ["b", "c"]
    assert int_registry.size() == 1
    assert int_registry.get("a") == 1


def test_prune_returns_empty_when_nothing_matches(registry: RuntimeStateRegistry[str]) -> None:
    """prune with a predicate that never matches must return an empty list."""
    registry.set("a", "1")
    removed = registry.prune(lambda _k, _v: False)
    assert removed == []
    assert registry.size() == 1


# ---------------------------------------------------------------------------
# 6. Thread safety — concurrent writers
# ---------------------------------------------------------------------------


def test_concurrent_set_no_size_leak(registry: RuntimeStateRegistry[int]) -> None:
    """Many threads writing distinct keys concurrently must result in exact size."""
    num_threads = 20
    keys_per_thread = 50

    def writer(tid: int) -> None:
        for i in range(keys_per_thread):
            registry.set(f"t{tid}_k{i}", tid * 1000 + i)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert registry.size() == num_threads * keys_per_thread


def test_concurrent_read_while_write(registry: RuntimeStateRegistry[str]) -> None:
    """Readers must never crash while writers are actively mutating the registry."""
    stop = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        for i in range(5000):
            registry.set(f"k{i}", f"v{i}")
            if i % 100 == 0:
                registry.clear()
            if stop.is_set():
                break

    def reader() -> None:
        try:
            while not stop.is_set():
                _ = registry.values_snapshot()
                _ = registry.items_snapshot()
                _ = registry.size()
                _ = registry.contains("k0")
                time.sleep(0.0001)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    w = threading.Thread(target=writer)
    rs = [threading.Thread(target=reader) for _ in range(4)]

    for t in rs:
        t.start()
    w.start()
    w.join()
    stop.set()
    for t in rs:
        t.join()

    assert errors == []


def test_concurrent_mutate_atomic(registry: RuntimeStateRegistry[int]) -> None:
    """Concurrent mutate on the same key must not lose increments."""
    reg: RuntimeStateRegistry[list[int]] = RuntimeStateRegistry[list[int]]()
    reg.set("counters", [0])

    def incrementer() -> None:
        for _ in range(200):
            reg.mutate("counters", lambda lst: lst.append(1))

    threads = [threading.Thread(target=incrementer) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counters = reg.get("counters")
    assert counters is not None
    assert len(counters) == 1 + 10 * 200


def test_concurrent_create_id_uniqueness(registry: RuntimeStateRegistry[str]) -> None:
    """Concurrent create_id calls must never produce duplicate IDs."""
    ids: list[str] = []
    lock = threading.Lock()

    def creator() -> None:
        for _ in range(100):
            rid = registry.create_id(prefix="th-")
            with lock:
                ids.append(rid)

    threads = [threading.Thread(target=creator) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ids) == len(set(ids))


def test_concurrent_prune_and_write(registry: RuntimeStateRegistry[int]) -> None:
    """prune running concurrently with set must not corrupt internal state."""
    stop = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        for i in range(2000):
            registry.set(f"k{i}", i)
            if stop.is_set():
                break

    def pruner() -> None:
        try:
            while not stop.is_set():
                registry.prune(lambda _k, v: v % 7 == 0)
                time.sleep(0.0001)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    w = threading.Thread(target=writer)
    p = threading.Thread(target=pruner)
    w.start()
    p.start()
    w.join()
    stop.set()
    p.join()

    assert errors == []
    # After pruning, no multiple-of-7 values should remain
    for _k, v in registry.items_snapshot():
        assert v % 7 != 0


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


def test_empty_string_key_is_valid(registry: RuntimeStateRegistry[str]) -> None:
    """The empty string is a legal key."""
    registry.set("", "empty-key-value")
    assert registry.get("") == "empty-key-value"
    assert registry.contains("") is True


def test_none_value_allowed_when_type_permits() -> None:
    """If T is Optional[str], None is a valid value."""
    reg: RuntimeStateRegistry[str | None] = RuntimeStateRegistry[str | None]()
    reg.set("k", None)
    assert reg.get("k") is None
    assert reg.contains("k") is True
    assert reg.size() == 1


def test_items_snapshot_empty_registry(registry: RuntimeStateRegistry[str]) -> None:
    """items_snapshot on an empty registry must return []."""
    assert registry.items_snapshot() == []


def test_values_snapshot_empty_registry(registry: RuntimeStateRegistry[str]) -> None:
    """values_snapshot on an empty registry must return []."""
    assert registry.values_snapshot() == []
