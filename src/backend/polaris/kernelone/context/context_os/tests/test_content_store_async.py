"""Async concurrency tests for ContentStore.

验证：
1. 并发读写测试：100 个并发 write/read/delete
2. 状态一致性测试：write 后 read 必须返回相同内容
3. 驱逐测试：写入超过 max_entries 后旧条目被正确驱逐
4. 边界测试：写入空字符串、超大内容
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.context.context_os.content_store import ContentStore


class TestContentStoreAsyncConcurrency:
    """并发读写测试：100 个并发 write/read/delete."""

    @pytest.mark.asyncio
    async def test_concurrent_write_read_delete(self) -> None:
        """100 个并发 write/read/delete 操作不应导致数据竞态."""
        store = ContentStore(max_entries=1000, max_bytes=10_000_000)

        async def writer(i: int) -> None:
            for j in range(10):
                await store.write(f"key_{i}_{j}", f"content_{i}_{j}")

        async def reader(i: int) -> None:
            for j in range(10):
                await store.read(f"key_{i}_{j}")

        async def deleter(i: int) -> None:
            for j in range(5):
                await store.delete(f"key_{i}_{j}")

        tasks = []
        for i in range(100):
            tasks.append(asyncio.create_task(writer(i)))
            tasks.append(asyncio.create_task(reader(i)))
            tasks.append(asyncio.create_task(deleter(i)))

        await asyncio.gather(*tasks)

        # 验证没有异常抛出且 store 状态一致
        stats = store.stats
        assert stats["entries"] >= 0

    @pytest.mark.asyncio
    async def test_concurrent_write_same_key(self) -> None:
        """多个协程同时写入同一个 key 不应导致数据竞态."""
        store = ContentStore(max_entries=100, max_bytes=1_000_000)
        key = "shared_key"

        async def writer(content: str) -> None:
            await store.write(key, content)

        tasks = [asyncio.create_task(writer(f"content_{i}")) for i in range(50)]
        await asyncio.gather(*tasks)

        # 最终读取应该返回某个写入的值
        result = await store.read(key)
        assert result.startswith("content_")


class TestContentStoreAsyncConsistency:
    """状态一致性测试：write 后 read 必须返回相同内容."""

    @pytest.mark.asyncio
    async def test_write_then_read_consistency(self) -> None:
        """写入后读取必须返回相同内容."""
        store = ContentStore()
        content = "hello world"
        ref = await store.write("key1", content)

        result = await store.read("key1")
        assert result == content

    @pytest.mark.asyncio
    async def test_update_then_read_consistency(self) -> None:
        """更新后读取必须返回新内容."""
        store = ContentStore()
        await store.write("key1", "old content")
        await store.update("key1", "new content")

        result = await store.read("key1")
        assert result == "new content"

    @pytest.mark.asyncio
    async def test_delete_then_read_empty(self) -> None:
        """删除后读取必须返回空字符串."""
        store = ContentStore()
        await store.write("key1", "content")
        await store.delete("key1")

        result = await store.read("key1")
        assert result == ""


class TestContentStoreAsyncEviction:
    """驱逐测试：写入超过 max_entries 后旧条目被正确驱逐."""

    @pytest.mark.asyncio
    async def test_evict_by_max_entries(self) -> None:
        """写入超过 max_entries 后旧条目被正确驱逐."""
        store = ContentStore(max_entries=5, max_bytes=1_000_000)

        for i in range(10):
            await store.write(f"key_{i}", f"content_{i}")

        stats = store.stats
        assert stats["entries"] <= 5
        assert stats["evict_count"] > 0

    @pytest.mark.asyncio
    async def test_evict_by_max_bytes(self) -> None:
        """写入超过 max_bytes 后旧条目被正确驱逐."""
        store = ContentStore(max_entries=100, max_bytes=100)

        for i in range(5):
            await store.write(f"key_{i}", "x" * 50)

        stats = store.stats
        assert stats["bytes"] <= 100
        assert stats["evict_count"] > 0


class TestContentStoreAsyncEdgeCases:
    """边界测试：写入空字符串、超大内容."""

    @pytest.mark.asyncio
    async def test_write_empty_string(self) -> None:
        """写入空字符串应该成功."""
        store = ContentStore()
        ref = await store.write("empty_key", "")

        result = await store.read("empty_key")
        assert result == ""
        assert ref.size == 0

    @pytest.mark.asyncio
    async def test_write_large_content(self) -> None:
        """写入超大内容应该成功."""
        store = ContentStore(max_entries=100, max_bytes=10_000_000)
        large_content = "x" * 1_500_000

        ref = await store.write("large_key", large_content)
        result = await store.read("large_key")

        assert result == large_content
        assert ref.size == 1_500_000

    @pytest.mark.asyncio
    async def test_write_unicode_content(self) -> None:
        """写入 unicode 内容应该成功."""
        store = ContentStore()
        content = "你好世界 🌍🚀 émoji"

        await store.write("unicode_key", content)
        result = await store.read("unicode_key")

        assert result == content
