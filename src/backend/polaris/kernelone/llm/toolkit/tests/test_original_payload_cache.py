"""Tests for the reversible CCR original-payload cache."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.llm.toolkit.original_payload_cache import (
    DEFAULT_TTL_SECONDS,
    OriginalPayloadCache,
    build_ref_marker,
    get_default_cache,
    hash_content,
    strip_ref_markers,
)


def test_hash_is_stable_and_content_addressed() -> None:
    payload = "def add(a, b):\n    return a + b\n"
    assert hash_content(payload) == hash_content(payload)
    assert hash_content(payload) != hash_content(payload + "\n")
    assert len(hash_content(payload)) == 24


def test_put_returns_marker_and_get_round_trips() -> None:
    cache = OriginalPayloadCache(ttl_seconds=60)
    original = "x" * 5000
    marker = cache.put(original)
    assert marker.startswith("<<ref:") and marker.endswith(">>")
    assert cache.get(marker) == original
    # Bare hash (without the marker wrapper) also resolves.
    bare = strip_ref_markers(marker)
    assert cache.get(bare) == original


def test_dedup_same_content_same_marker() -> None:
    cache = OriginalPayloadCache()
    a = cache.put("hello world")
    b = cache.put("hello world")
    assert a == b
    assert len(cache) == 1


@pytest.mark.parametrize(
    ("wrapped", "inner"),
    [
        ("<<ref:abc123>>", "abc123"),
        ("<<ccr:DEADBEEF>>", "DEADBEEF"),
        ("[receipt_ref:job-42]", "job-42"),
        ("<receipt_ref:job-42>", "job-42"),
        ("[See src/app/main.py]", "src/app/main.py"),
        ("  <<ref:  spaced  >>  ", "spaced"),
        ("plainhash", "plainhash"),
        ("", ""),
    ],
)
def test_strip_ref_markers_unwraps_all_known_forms(wrapped: str, inner: str) -> None:
    assert strip_ref_markers(wrapped) == inner


def test_get_resolves_when_model_pastes_whole_marker() -> None:
    cache = OriginalPayloadCache()
    marker = cache.put("payload-body")
    # A weak model may paste the full marker into the ref argument.
    assert cache.get(marker) == "payload-body"


def test_ttl_expiry_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(
        "polaris.kernelone.llm.toolkit.original_payload_cache.time.monotonic",
        lambda: clock["t"],
    )
    cache = OriginalPayloadCache(ttl_seconds=10)
    marker = cache.put("ephemeral")
    assert cache.get(marker) == "ephemeral"
    clock["t"] = 1009.0  # still within TTL
    assert cache.get(marker) == "ephemeral"
    clock["t"] = 1011.0  # past TTL
    assert cache.get(marker) is None
    assert len(cache) == 0  # lazily purged


def test_zero_or_negative_ttl_falls_back_to_default() -> None:
    cache = OriginalPayloadCache(ttl_seconds=0)
    assert cache._ttl == DEFAULT_TTL_SECONDS
    cache_neg = OriginalPayloadCache(ttl_seconds=-5)
    assert cache_neg._ttl == DEFAULT_TTL_SECONDS


def test_max_entries_eviction_bounds_memory() -> None:
    cache = OriginalPayloadCache(ttl_seconds=300, max_entries=3)
    for i in range(10):
        cache.put(f"item-{i}")
    assert len(cache) <= 3


def test_unknown_ref_returns_none() -> None:
    cache = OriginalPayloadCache()
    assert cache.get("<<ref:nope>>") is None
    assert cache.get("") is None


def test_sqlite_backing_round_trips_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "ccr_originals.db"
    cache_a = OriginalPayloadCache(ttl_seconds=300, sqlite_path=db)
    marker = cache_a.put("durable-content")
    ref_hash = strip_ref_markers(marker)

    # A fresh instance (cold in-memory tier) resolves via sqlite.
    cache_b = OriginalPayloadCache(ttl_seconds=300, sqlite_path=db)
    assert cache_b.get(ref_hash) == "durable-content"


def test_sqlite_expired_row_is_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 500.0}
    monkeypatch.setattr(
        "polaris.kernelone.llm.toolkit.original_payload_cache.time.monotonic",
        lambda: clock["t"],
    )
    db = tmp_path / "ccr_originals.db"
    cache_a = OriginalPayloadCache(ttl_seconds=10, sqlite_path=db)
    marker = cache_a.put("short-lived")
    ref_hash = strip_ref_markers(marker)

    clock["t"] = 600.0  # well past TTL
    cache_b = OriginalPayloadCache(ttl_seconds=10, sqlite_path=db)
    assert cache_b.get(ref_hash) is None


def test_build_ref_marker_shape() -> None:
    assert build_ref_marker("deadbeef") == "<<ref:deadbeef>>"


def test_default_cache_is_singleton() -> None:
    assert get_default_cache() is get_default_cache()


def test_clear_empties_cache() -> None:
    cache = OriginalPayloadCache()
    cache.put("a")
    cache.put("b")
    cache.clear()
    assert len(cache) == 0
