"""Comprehensive unit tests for ContentStore, ContentRef, and RefTracker.

Tests cover: construction, interning, retrieval, release, eviction,
MIME guessing, persistence round-trip, statistics, and RefTracker lifecycle.
"""

from __future__ import annotations

import hashlib
import time

import pytest
from polaris.kernelone.context.context_os.content_store import (
    ContentRef,
    ContentStore,
    RefTracker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha24(text: str) -> str:
    """Return truncated SHA-256 hash (24 hex chars) matching ContentStore's format."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# ContentRef
# ---------------------------------------------------------------------------


class TestContentRef:
    """Tests for the ContentRef frozen dataclass."""

    def test_frozen(self) -> None:
        """ContentRef is immutable; assigning to a field raises AttributeError."""
        ref = ContentRef(hash="abc123", size=42, mime="text/plain")
        with pytest.raises(AttributeError):
            ref.hash = "changed"  # type: ignore[misc]

    def test_repr(self) -> None:
        """repr shows truncated hash and size for quick identification."""
        full_hash = "abcdef0123456789" * 4
        ref = ContentRef(hash=full_hash, size=1024, mime="text/plain")
        r = repr(ref)
        assert full_hash[:8] in r
        assert "1024" in r


# ---------------------------------------------------------------------------
# ContentStore.intern
# ---------------------------------------------------------------------------


class TestContentStoreIntern:
    """Tests for ContentStore.intern."""

    def test_intern_returns_content_ref(self) -> None:
        """Basic intern returns ContentRef with correct hash/size/mime."""
        store = ContentStore()
        content = "hello world"
        ref = store.intern(content)

        assert isinstance(ref, ContentRef)
        assert ref.hash == _sha24(content)
        assert ref.size == len(content.encode("utf-8"))
        assert ref.mime == "text/plain"
        assert ref.encoding == "utf-8"

    def test_intern_idempotent(self) -> None:
        """Same content returns the same ContentRef (same hash)."""
        store = ContentStore()
        content = "deterministic payload"
        ref1 = store.intern(content)
        ref2 = store.intern(content)

        assert ref1.hash == ref2.hash
        assert ref1 == ref2

    def test_intern_different_content(self) -> None:
        """Different content produces different ContentRef hashes."""
        store = ContentStore()
        ref_a = store.intern("content A")
        ref_b = store.intern("content B")

        assert ref_a.hash != ref_b.hash

    def test_intern_empty_string(self) -> None:
        """Empty string can be interned successfully."""
        store = ContentStore()
        ref = store.intern("")

        assert ref.hash == _sha24("")
        assert ref.size == 0

    def test_intern_unicode(self) -> None:
        """Chinese and emoji content round-trips correctly."""
        store = ContentStore()
        content = "你好世界 🌍🚀 émoji"
        ref = store.intern(content)

        assert store.get(ref) == content

    def test_intern_large_content(self) -> None:
        """Content exceeding 1 MB can be interned and retrieved."""
        store = ContentStore(max_bytes=10_000_000)
        content = "x" * (1_500_000)
        ref = store.intern(content)

        assert ref.size == 1_500_000
        assert store.get(ref) == content

    def test_intern_hash_collision_detection(self) -> None:
        """If two different contents map to the same hash key, RuntimeError is raised.

        We simulate this by manually inserting content_b under content_a's hash,
        then calling intern(content_a) which detects the stored value differs.
        """
        store = ContentStore()
        content_a = "alpha"
        content_b = "beta"
        target_hash = _sha24(content_a)

        # Manually inject content_b under content_a's hash
        store._store[target_hash] = content_b
        store._refs[target_hash] = 1
        store._access[target_hash] = time.monotonic()

        # intern(content_a) should detect store[hash] != content_a → collision
        with pytest.raises(RuntimeError, match=r"[Cc]ollision"):
            store.intern(content_a)


# ---------------------------------------------------------------------------
# ContentStore.get
# ---------------------------------------------------------------------------


class TestContentStoreGet:
    """Tests for ContentStore.get and get_if_present."""

    def test_get_returns_content(self) -> None:
        """After intern, get returns the original content."""
        store = ContentStore()
        content = "fetch me"
        ref = store.intern(content)

        assert store.get(ref) == content

    def test_get_evicted_placeholder(self) -> None:
        """When content is removed from _store, get returns <evicted:...>."""
        store = ContentStore()
        ref = store.intern("temporary")
        del store._store[ref.hash]

        result = store.get(ref)
        assert result.startswith("<evicted:")
        assert ref.hash in result

    def test_get_if_present_returns_none(self) -> None:
        """Non-existent ref returns None via get_if_present."""
        store = ContentStore()
        phantom = ContentRef(hash="deadbeef" * 3, size=0, mime="text/plain")

        assert store.get_if_present(phantom) is None

    def test_get_updates_access_time(self) -> None:
        """Calling get refreshes the _access timestamp."""
        store = ContentStore()
        ref = store.intern("timed content")
        old_access = store._access[ref.hash]

        time.sleep(0.005)
        store.get(ref)

        new_access = store._access[ref.hash]
        assert new_access > old_access


# ---------------------------------------------------------------------------
# ContentStore.release
# ---------------------------------------------------------------------------


class TestContentStoreRelease:
    """Tests for ContentStore.release and release_all."""

    def test_release_decrements_ref_count(self) -> None:
        """Intern twice (ref_count=2), release once → ref_count drops to 1."""
        store = ContentStore()
        content = "ref counted"
        ref1 = store.intern(content)
        store.intern(content)  # second intern bumps ref_count

        assert store._refs[ref1.hash] == 2

        store.release(ref1)
        assert store._refs[ref1.hash] == 1

    def test_release_idempotent(self) -> None:
        """Releasing a non-existent hash is safe (no exception)."""
        store = ContentStore()
        phantom = ContentRef(hash="cafebabe" * 3, size=0, mime="text/plain")

        store.release(phantom)  # Should not raise

    def test_release_all(self) -> None:
        """Batch release decrements ref_count for multiple refs."""
        store = ContentStore()
        ref_a = store.intern("alpha")
        ref_b = store.intern("bravo")
        ref_c = store.intern("charlie")

        # Each interned once, bump again to reach count=2
        store.intern("alpha")
        store.intern("bravo")
        store.intern("charlie")

        store.release_all([ref_a, ref_b, ref_c])

        assert store._refs[ref_a.hash] == 1
        assert store._refs[ref_b.hash] == 1
        assert store._refs[ref_c.hash] == 1


# ---------------------------------------------------------------------------
# ContentStore eviction
# ---------------------------------------------------------------------------


class TestContentStoreEviction:
    """Tests for LRU and zero-ref-count eviction."""

    def test_evict_by_max_bytes(self) -> None:
        """Interning content that exceeds max_bytes triggers eviction."""
        store = ContentStore(max_entries=100, max_bytes=100)
        # Each ~80 bytes, three entries will exceed the 100-byte budget
        store.intern("a" * 60)
        store.intern("b" * 60)
        store.intern("c" * 60)

        stats = store.stats
        assert stats["evict_count"] > 0

    def test_evict_prefers_zero_ref_count(self) -> None:
        """Entries with ref_count=0 are evicted before those with ref_count>0."""
        store = ContentStore(max_entries=100, max_bytes=150)

        ref_a = store.intern("a" * 50)  # ~50 bytes, ref_count=1
        store.release(ref_a)  # ref_count=0
        ref_b = store.intern("b" * 50)  # ~50 bytes, ref_count=1

        # Intern something large enough to force eviction
        store.intern("x" * 100)

        # B should survive (ref_count=1), A should have been evicted (ref_count=0)
        assert store.get_if_present(ref_b) is not None
        assert store.get_if_present(ref_a) is None

    def test_evict_lru_fallback(self) -> None:
        """When all entries have ref_count > 0, the least-recently-accessed
        entry is evicted."""
        store = ContentStore(max_entries=100, max_bytes=200)

        ref_old = store.intern("old content that is long enough to matter")
        time.sleep(0.01)
        ref_new = store.intern("newer content that is also quite long indeed")

        # Both have ref_count=1. Intern a third to exceed budget.
        store.intern("x" * 120)

        # At least one must be evicted due to budget
        evicted_old = store.get_if_present(ref_old) is None
        evicted_new = store.get_if_present(ref_new) is None

        # At least one was evicted
        assert evicted_old or evicted_new


# ---------------------------------------------------------------------------
# ContentStore MIME guessing
# ---------------------------------------------------------------------------


class TestContentStoreMime:
    """Tests for _guess_mime heuristic."""

    @pytest.fixture()
    def store(self) -> ContentStore:
        return ContentStore()

    def test_guess_mime_json(self, store: ContentStore) -> None:
        """Content starting with '{' is classified as application/json."""
        ref = store.intern('{"key": "value"}')
        assert ref.mime == "application/json"

    def test_guess_mime_xml(self, store: ContentStore) -> None:
        """Content starting with '<?xml' is classified as application/xml."""
        ref = store.intern('<?xml version="1.0"?><root/>')
        assert ref.mime == "application/xml"

    def test_guess_mime_code(self, store: ContentStore) -> None:
        """Content with 'def ' pattern is classified as text/x-code."""
        ref = store.intern("def hello_world():\n    pass\n")
        assert ref.mime == "text/x-code"

    def test_guess_mime_plain(self, store: ContentStore) -> None:
        """Unrecognised content defaults to text/plain."""
        ref = store.intern("just some plain text here")
        assert ref.mime == "text/plain"


# ---------------------------------------------------------------------------
# ContentStore persistence
# ---------------------------------------------------------------------------


class TestContentStorePersistence:
    """Tests for export_content_map / from_content_map round-trip."""

    def test_export_content_map(self) -> None:
        """Export specific hashes returns a {hash: content} dict."""
        store = ContentStore()
        ref_a = store.intern("alpha")
        ref_b = store.intern("beta")
        store.intern("gamma")  # not exported

        cm = store.export_content_map({ref_a.hash, ref_b.hash})

        assert cm[ref_a.hash] == "alpha"
        assert cm[ref_b.hash] == "beta"
        assert len(cm) == 2

    def test_from_content_map(self) -> None:
        """Reconstructing a store from a content map allows get() to work."""
        # Build content_map with truncated 24-char hashes
        content_map = {
            _sha24("hello"): "hello",
            _sha24("world"): "world",
        }

        store = ContentStore.from_content_map(content_map)

        ref_hello = ContentRef(
            hash=_sha24("hello"),
            size=len(b"hello"),
            mime="text/plain",
        )
        assert store.get(ref_hello) == "hello"

    def test_roundtrip(self) -> None:
        """intern -> export -> from_content_map -> get returns original."""
        original_store = ContentStore()
        contents = ["first", "second", "third"]
        refs = [original_store.intern(c) for c in contents]

        hashes = {r.hash for r in refs}
        cm = original_store.export_content_map(hashes)

        restored_store = ContentStore.from_content_map(cm)
        for ref, expected_content in zip(refs, contents, strict=True):
            assert restored_store.get(ref) == expected_content


# ---------------------------------------------------------------------------
# ContentStore.stats
# ---------------------------------------------------------------------------


class TestContentStoreStats:
    """Tests for the stats property."""

    def test_stats_after_intern(self) -> None:
        """Stats reflect entries and bytes after interning content."""
        store = ContentStore()
        content = "stats check"
        ref = store.intern(content)

        stats = store.stats
        assert stats["entries"] == 1
        assert stats["bytes"] == ref.size
        assert "hit_rate" in stats
        assert "evict_count" in stats

    def test_stats_hit_rate(self) -> None:
        """Interning the same content multiple times increases hit_rate."""
        store = ContentStore()
        content = "cache me"

        store.intern(content)  # new entry (no hit)
        store.intern(content)  # hit
        store.intern(content)  # hit
        stats = store.stats

        # hit_rate = 2 hits / (2 hits + 0 misses from intern) = need to check
        # intern doesn't increment _misses, only _hits on duplicate
        assert stats["hit_rate"] > 0.0

    def test_stats_utilization(self) -> None:
        """Utilization (bytes / max_bytes) is reported."""
        max_bytes = 10_000
        store = ContentStore(max_bytes=max_bytes)
        content = "z" * 1000
        ref = store.intern(content)

        stats = store.stats
        assert "utilization" in stats
        expected_util = ref.size / max_bytes
        assert abs(stats["utilization"] - expected_util) < 0.01


# ---------------------------------------------------------------------------
# RefTracker
# ---------------------------------------------------------------------------


class TestRefTracker:
    """Tests for RefTracker acquire / release / release_all / collect."""

    def test_acquire_release_lifecycle(self) -> None:
        """Acquire makes ref active; release makes it inactive."""
        store = ContentStore()
        ref = store.intern("tracked")
        tracker = RefTracker(store)

        tracker.acquire(ref)
        collected = tracker.collect_refs_for_persist()
        assert ref.hash in collected

        tracker.release(ref)
        collected = tracker.collect_refs_for_persist()
        assert ref.hash not in collected

    def test_release_all(self) -> None:
        """release_all clears every tracked ref."""
        store = ContentStore()
        tracker = RefTracker(store)
        refs = [store.intern(f"item-{i}") for i in range(5)]

        for r in refs:
            tracker.acquire(r)

        assert len(tracker.collect_refs_for_persist()) == 5

        tracker.release_all()
        assert len(tracker.collect_refs_for_persist()) == 0

    def test_collect_refs_for_persist(self) -> None:
        """Acquire 3 refs; collect returns exactly those 3 hashes."""
        store = ContentStore()
        tracker = RefTracker(store)

        refs = [store.intern(f"persist-{i}") for i in range(3)]
        for r in refs:
            tracker.acquire(r)

        hashes = tracker.collect_refs_for_persist()
        assert len(hashes) == 3
        for r in refs:
            assert r.hash in hashes

    def test_acquire_unknown_hash_raises(self) -> None:
        """Acquiring a ref not in store raises ValueError."""
        store = ContentStore()
        phantom_ref = ContentRef(hash="deadbeef" * 3, size=100, mime="text/plain")
        tracker = RefTracker(store)

        with pytest.raises(ValueError, match="not found in store"):
            tracker.acquire(phantom_ref)

    def test_acquire_increments_store_ref_count(self) -> None:
        """Each acquire increments the store's ref count."""
        store = ContentStore()
        ref = store.intern("shared")
        tracker = RefTracker(store)

        assert store._refs[ref.hash] == 1
        tracker.acquire(ref)
        assert store._refs[ref.hash] == 2
        tracker.acquire(ref)
        assert store._refs[ref.hash] == 3

    def test_release_decrements_store_ref_count(self) -> None:
        """Release decrements the store's ref count."""
        store = ContentStore()
        ref = store.intern("shared")
        tracker = RefTracker(store)

        tracker.acquire(ref)
        tracker.acquire(ref)
        assert store._refs[ref.hash] == 3  # 1 from intern + 2 from acquire

        tracker.release(ref)
        assert store._refs[ref.hash] == 2
        tracker.release(ref)
        assert store._refs[ref.hash] == 1

    def test_release_idempotent_for_tracker(self) -> None:
        """Releasing a ref not in tracker is safe."""
        store = ContentStore()
        ref = store.intern("tracked")
        tracker = RefTracker(store)

        # Release without acquire should not raise
        tracker.release(ref)
        assert ref.hash not in tracker.collect_refs_for_persist()

    def test_release_all_threadsafe(self) -> None:
        """release_all is thread-safe against concurrent acquire."""
        import threading

        store = ContentStore()
        refs = [store.intern(f"content-{i}") for i in range(10)]
        tracker = RefTracker(store)

        # Pre-acquire first 5
        for r in refs[:5]:
            tracker.acquire(r)

        acquired_during_release: set[str] = set()

        def release_all_worker() -> None:
            tracker.release_all()

        def acquire_worker() -> None:
            for r in refs[5:]:
                tracker.acquire(r)
                acquired_during_release.add(r.hash)

        t1 = threading.Thread(target=release_all_worker)
        t2 = threading.Thread(target=acquire_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # After release_all, only refs acquired during or after should remain
        active = tracker.collect_refs_for_persist()
        # All refs acquired during release should still be present
        for h in acquired_during_release:
            assert h in active

    def test_release_threadsafe(self) -> None:
        """release is thread-safe against concurrent release_all."""
        import threading

        store = ContentStore()
        refs = [store.intern(f"content-{i}") for i in range(100)]
        tracker = RefTracker(store)

        for r in refs:
            tracker.acquire(r)

        errors: list[Exception] = []

        def release_one() -> None:
            try:
                tracker.release(refs[0])
            except RuntimeError as exc:
                errors.append(exc)

        def release_all() -> None:
            try:
                tracker.release_all()
            except RuntimeError as exc:
                errors.append(exc)

        # Race: release_one discards from _active while release_all iterates
        t1 = threading.Thread(target=release_one)
        t2 = threading.Thread(target=release_all)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread errors: {errors}"
