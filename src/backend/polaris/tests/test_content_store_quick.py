"""Quick validation tests for content_store.py refactor."""

import asyncio

import pytest

from polaris.kernelone.context.context_os.content_store import ContentRef, ContentStore, RefTracker


class TestContentStoreAsync:
    """Verify async methods work with threading.RLock."""

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        store = ContentStore()
        ref = await store.write("key1", "hello world")
        assert ref.hash is not None
        content = await store.read("key1")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_delete(self):
        store = ContentStore()
        await store.write("key1", "hello")
        deleted = await store.delete("key1")
        assert deleted is True
        content = await store.read("key1")
        assert content == ""

    @pytest.mark.asyncio
    async def test_update(self):
        store = ContentStore()
        await store.write("key1", "old")
        ref = await store.update("key1", "new")
        content = await store.read("key1")
        assert content == "new"

    def test_intern_sync_still_works(self):
        store = ContentStore()
        ref = store.intern("test content")
        assert store.get(ref) == "test content"

    def test_ref_tracker_sync(self):
        store = ContentStore()
        tracker = RefTracker(store)
        ref = store.intern("tracked content")
        tracker.acquire(ref)
        assert ref.hash in tracker._active
        tracker.release(ref)
        assert ref.hash not in tracker._active

    def test_ref_tracker_release_all(self):
        store = ContentStore()
        tracker = RefTracker(store)
        ref1 = store.intern("content1")
        ref2 = store.intern("content2")
        tracker.acquire(ref1)
        tracker.acquire(ref2)
        tracker.release_all()
        assert len(tracker._active) == 0

    @pytest.mark.asyncio
    async def test_mixed_sync_async_access(self):
        store = ContentStore()
        ref = store.intern("mixed")
        # Sync get
        assert store.get(ref) == "mixed"
        # Async write
        await store.write("k1", "async")
        # Sync intern again
        ref2 = store.intern("sync")
        # Async read
        content = await store.read("k1")
        assert content == "async"
        assert store.get(ref2) == "sync"


class TestContentStoreEviction:
    """Verify eviction works correctly."""

    def test_evict_zero_ref(self):
        store = ContentStore(max_entries=2, max_bytes=1000)
        ref1 = store.intern("a" * 100)
        ref2 = store.intern("b" * 100)
        ref3 = store.intern("c" * 100)
        # With max_entries=2, one should be evicted
        assert len(store._store) <= 2
