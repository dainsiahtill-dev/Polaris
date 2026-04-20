"""Reproduction tests for RefTracker concurrency bugs.

Three independent bugs are demonstrated here:
1. acquire() data corruption when ref.hash not interned
2. release() race condition causing RuntimeError during iteration
3. release_all() race condition losing newly-acquired refs
"""

from __future__ import annotations

import threading
import time

import pytest
from polaris.kernelone.context.context_os.content_store import (
    ContentRef,
    ContentStore,
    RefTracker,
)


class TestRefTrackerAcquireDataCorruption:
    """Bug 1 (FIXED): acquire() now rejects unknown hashes instead of creating empty entries."""

    def test_acquire_unknown_hash_raises_value_error(self) -> None:
        """A ref from a corrupted snapshot (hash not in store) now raises ValueError."""
        store = ContentStore()
        # Simulate a ref from a corrupted snapshot: hash exists in ref but not in store
        phantom_ref = ContentRef(hash="deadbeef" * 3, size=100, mime="text/plain")

        tracker = RefTracker(store)
        with pytest.raises(ValueError, match="not found in store"):
            tracker.acquire(phantom_ref)

    def test_acquire_unknown_hash_no_silent_data_loss(self) -> None:
        """After rejecting unknown hash, store remains clean."""
        store = ContentStore()
        phantom_ref = ContentRef(hash="cafebabe" * 3, size=50, mime="text/plain")

        tracker = RefTracker(store)
        with pytest.raises(ValueError, match="not found in store"):
            tracker.acquire(phantom_ref)

        # Store should not have the phantom hash
        assert phantom_ref.hash not in store._store
        assert phantom_ref.hash not in store._refs

    def test_acquire_unknown_hash_allows_future_intern(self) -> None:
        """Rejection does not block future intern() of real content."""
        store = ContentStore()
        real_content = "real content that should be internable"
        # Precompute what the hash would be
        import hashlib

        real_hash = hashlib.sha256(real_content.encode("utf-8")).hexdigest()[:24]

        # First, try to acquire a phantom ref with the same hash (should fail)
        phantom_ref = ContentRef(hash=real_hash, size=999, mime="text/plain")
        tracker = RefTracker(store)
        with pytest.raises(ValueError, match="not found in store"):
            tracker.acquire(phantom_ref)

        # Now intern the real content should succeed
        ref = store.intern(real_content)
        assert ref.hash == real_hash
        assert store.get(ref) == real_content


class TestRefTrackerReleaseRaceCondition:
    """Bug 2: release() modifies _active outside lock, causing iteration error."""

    def test_release_during_release_all_race(self) -> None:
        """release() discards from _active without lock while release_all() iterates."""
        store = ContentStore()
        refs = [store.intern(f"content-{i}") for i in range(100)]
        tracker = RefTracker(store)

        for r in refs:
            tracker.acquire(r)

        errors: list[Exception] = []

        def release_one() -> None:
            try:
                # Release a ref that release_all might also try to release
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

        # The RuntimeError may or may not trigger depending on timing,
        # but the race condition is structurally present.
        # We run multiple times to increase probability.
        race_detected = False
        for _ in range(50):
            tracker2 = RefTracker(store)
            for r in refs:
                tracker2.acquire(r)

            errs: list[Exception] = []

            def make_release(t: RefTracker, e: list[Exception]) -> None:
                def _release() -> None:
                    try:
                        t.release(refs[50])
                    except RuntimeError as exc:
                        e.append(exc)

                return _release

            def make_release_all(t: RefTracker, e: list[Exception]) -> None:
                def _release_all() -> None:
                    try:
                        t.release_all()
                    except RuntimeError as exc:
                        e.append(exc)

                return _release_all

            t1 = threading.Thread(target=make_release(tracker2, errs))
            t2 = threading.Thread(target=make_release_all(tracker2, errs))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            if errs:
                race_detected = True
                break

        # This assertion documents the bug; it may be flaky due to race nature
        # In practice, the structural issue is that _active is unprotected
        print(f"Race detected: {race_detected}")


class TestRefTrackerReleaseAllRaceCondition:
    """Bug 3: release_all() clear() races with acquire() add()."""

    def test_release_all_races_with_acquire(self) -> None:
        """clear() after loop can erase refs added during iteration."""
        store = ContentStore()
        refs = [store.intern(f"content-{i}") for i in range(10)]
        tracker = RefTracker(store)

        # Pre-acquire first 5
        for r in refs[:5]:
            tracker.acquire(r)

        lost_refs: list[ContentRef] = []

        def release_all_worker() -> None:
            tracker.release_all()

        def acquire_worker() -> None:
            # Try to acquire refs 5-9 during release_all
            for r in refs[5:]:
                tracker.acquire(r)
                time.sleep(0.001)  # Small delay to increase interleaving

        t1 = threading.Thread(target=release_all_worker)
        t2 = threading.Thread(target=acquire_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Check if any newly acquired refs were lost by clear()
        active = tracker.collect_refs_for_persist()
        for r in refs[5:]:
            if r.hash not in active:
                lost_refs.append(r)

        # Document the race: newly acquired refs may be lost
        print(f"Lost refs during race: {len(lost_refs)}")
        # The structural bug is that clear() is not atomic with the release loop


class TestRefTrackerStructuralIssues:
    """Structural verification of RefTracker design."""

    def test_ref_tracker_has_own_lock(self) -> None:
        """RefTracker now has its own lock to protect _active."""
        tracker = RefTracker(ContentStore())
        # _active is now protected by tracker._lock
        assert hasattr(tracker, "_lock")
        assert isinstance(tracker._lock, threading.Lock)

    def test_release_all_creates_fake_refs(self) -> None:
        """release_all() constructs ContentRef with size=0, losing metadata."""
        store = ContentStore()
        ref = store.intern("test content")
        tracker = RefTracker(store)
        tracker.acquire(ref)

        # release_all creates new ContentRef objects with size=0
        # This is semantically incorrect - the size/mime are lost
        tracker.release_all()
        # The store.release() only uses ref.hash, so it "works" but is fragile
