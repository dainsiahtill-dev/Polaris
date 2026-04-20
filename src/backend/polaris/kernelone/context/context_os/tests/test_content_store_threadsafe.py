"""Tests for ContentStore thread-safety.

验证：
1. 多线程并发 intern/release 不导致引用计数错误
2. 使用 threading.Thread 启动 10 个线程，每个线程 intern 100 次
"""

from __future__ import annotations

import threading
from typing import Any

from polaris.kernelone.context.context_os.content_store import ContentStore


class TestContentStoreThreadSafe:
    """Thread-safety regression tests for ContentStore reference counting."""

    def test_concurrent_intern_release_ref_count(self) -> None:
        """10 threads x 100 ops each must not corrupt ref counts."""
        store = ContentStore(max_entries=1000, max_bytes=10_000_000)
        errors: list[Exception] = []
        refs: list[Any] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                local_refs = []
                for i in range(100):
                    content = f"thread_content_{threading.current_thread().ident}_{i}"
                    ref = store.intern(content)
                    local_refs.append(ref)
                # Release half, keep half
                for ref in local_refs[:50]:
                    store.release(ref)
                with lock:
                    refs.extend(local_refs[50:])
            except RuntimeError as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        # All remaining refs must be retrievable
        for ref in refs:
            content = store.get(ref)
            assert not content.startswith("<evicted:"), f"Content evicted unexpectedly for ref {ref.hash}"

    def test_concurrent_intern_same_content(self) -> None:
        """Multiple threads interning identical content must deduplicate safely."""
        store = ContentStore(max_entries=100, max_bytes=1_000_000)
        shared_content = "shared_content_for_dedup"
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker() -> None:
            try:
                for _ in range(100):
                    store.intern(shared_content)
            except RuntimeError as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        # Should only have 1 entry due to deduplication
        assert len(store._store) == 1
        # Ref count should be 1000 (10 threads x 100)
        assert store._refs[next(iter(store._store.keys()))] == 1000
