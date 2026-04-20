"""Tests for the long-term memory system."""

from __future__ import annotations

import pytest
from polaris.kernelone.memory.long_term import KnowledgeItem, LongTermMemory


class TestKnowledgeItem:
    """Tests for the KnowledgeItem dataclass."""

    def test_knowledge_item_creation(self) -> None:
        """Test creating a KnowledgeItem with all fields."""
        item = KnowledgeItem(
            id="test_123",
            content="Python async patterns are powerful",
            source_session="sess_abc",
            created_at="2026-04-06T10:00:00Z",
            last_accessed="2026-04-06T11:00:00Z",
            access_count=5,
            tags=("python", "async", "patterns"),
            metadata={"key": "value"},
        )

        assert item.id == "test_123"
        assert item.content == "Python async patterns are powerful"
        assert item.source_session == "sess_abc"
        assert item.created_at == "2026-04-06T10:00:00Z"
        assert item.last_accessed == "2026-04-06T11:00:00Z"
        assert item.access_count == 5
        assert item.tags == ("python", "async", "patterns")
        assert item.metadata == {"key": "value"}

    def test_knowledge_item_defaults(self) -> None:
        """Test KnowledgeItem with default values."""
        item = KnowledgeItem(
            id="test_456",
            content="Some content",
            source_session="sess_xyz",
            created_at="2026-04-06T10:00:00Z",
        )

        assert item.last_accessed is None
        assert item.access_count == 0
        assert item.tags == ()
        assert item.metadata == {}

    def test_knowledge_item_immutable(self) -> None:
        """Test that KnowledgeItem is immutable (frozen)."""
        item = KnowledgeItem(
            id="test_789",
            content="Immutable content",
            source_session="sess_imm",
            created_at="2026-04-06T10:00:00Z",
        )

        with pytest.raises(AttributeError):
            item.content = "modified"  # type: ignore


class TestLongTermMemory:
    """Tests for the LongTermMemory class."""

    @pytest.fixture
    def memory(self) -> LongTermMemory:
        """Create a LongTermMemory instance without workspace for testing."""
        return LongTermMemory(workspace=None)

    @pytest.mark.asyncio
    async def test_consolidate_empty_events(self, memory: LongTermMemory) -> None:
        """Test consolidating an empty event list."""
        items = await memory.consolidate("sess_empty", [])
        assert items == []

    @pytest.mark.asyncio
    async def test_consolidate_single_event(self, memory: LongTermMemory) -> None:
        """Test consolidating a single event."""
        events = [
            {
                "type": "tool_call",
                "role": "director",
                "tool": "repo_read",
                "content": "Reading Python file for async optimization",
            }
        ]

        items = await memory.consolidate("sess_1", events)

        assert len(items) == 1
        item = items[0]
        assert item.source_session == "sess_1"
        assert "Reading Python file" in item.content
        assert "tool_call" in item.tags
        assert "role:director" in item.tags
        assert "tool:repo_read" in item.tags

    @pytest.mark.asyncio
    async def test_consolidate_deduplication(self, memory: LongTermMemory) -> None:
        """Test that duplicate content is deduplicated."""
        events = [
            {"type": "message", "content": "Important knowledge to remember"},
            {"type": "message", "content": "Important knowledge to remember"},
        ]

        items = await memory.consolidate("sess_dedup", events)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_consolidate_skips_short_content(self, memory: LongTermMemory) -> None:
        """Test that content shorter than 10 characters is skipped."""
        events = [{"type": "ping", "content": "short"}]

        items = await memory.consolidate("sess_short", events)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_retrieve_relevant(self, memory: LongTermMemory) -> None:
        """Test retrieving relevant knowledge items."""
        await memory.consolidate(
            "sess_retrieve",
            [
                {
                    "type": "tool_call",
                    "content": "Python asyncio.create_task is used for concurrency",
                },
                {
                    "type": "tool_call",
                    "content": "Java Spring Boot uses annotations for dependency injection",
                },
                {
                    "type": "message",
                    "content": "TypeScript interface extends other interfaces",
                },
            ],
        )

        results = await memory.retrieve_relevant("Python asyncio", limit=5)

        assert len(results) > 0
        assert "Python asyncio" in results[0].content or "asyncio" in results[0].content

    @pytest.mark.asyncio
    async def test_retrieve_respects_limit(self, memory: LongTermMemory) -> None:
        """Test that retrieve_relevant respects the limit parameter."""
        for i in range(20):
            await memory.consolidate(
                f"sess_{i}",
                [{"type": "message", "content": f"Content item number {i} with enough length"}],
            )

        results = await memory.retrieve_relevant("Content", limit=5)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_access_item(self, memory: LongTermMemory) -> None:
        """Test accessing a knowledge item updates access statistics."""
        items = await memory.consolidate(
            "sess_access",
            [{"type": "message", "content": "Knowledge to access multiple times"}],
        )
        item_id = items[0].id

        accessed_item = await memory.access_item(item_id)
        assert accessed_item is not None
        assert accessed_item.access_count == 1
        assert accessed_item.last_accessed is not None

        accessed_again = await memory.access_item(item_id)
        assert accessed_again is not None
        assert accessed_again.access_count == 2

    @pytest.mark.asyncio
    async def test_access_nonexistent_item(self, memory: LongTermMemory) -> None:
        """Test accessing a non-existent item returns None."""
        result = await memory.access_item("nonexistent_id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_statistics(self, memory: LongTermMemory) -> None:
        """Test getting memory statistics."""
        await memory.consolidate(
            "sess_stats_1",
            [
                {"type": "tool_call", "content": "First session content about Python"},
                {"type": "message", "content": "Another message from first session"},
            ],
        )
        await memory.consolidate(
            "sess_stats_2",
            [{"type": "message", "content": "Second session content about TypeScript"}],
        )

        stats = await memory.get_statistics()

        assert stats["total_items"] == 3
        assert stats["total_sessions"] == 2
        assert stats["total_accesses"] == 0
        assert "tool_call" in stats["tag_counts"]
        assert "message" in stats["tag_counts"]
        assert stats["items_by_session"]["sess_stats_1"] == 2
        assert stats["items_by_session"]["sess_stats_2"] == 1

    @pytest.mark.asyncio
    async def test_get_statistics_empty(self, memory: LongTermMemory) -> None:
        """Test statistics on empty memory."""
        stats = await memory.get_statistics()

        assert stats["total_items"] == 0
        assert stats["total_sessions"] == 0
        assert stats["total_accesses"] == 0
        assert stats["tag_counts"] == {}
        assert stats["items_by_session"] == {}

    @pytest.mark.asyncio
    async def test_clear_session(self, memory: LongTermMemory) -> None:
        """Test clearing all items from a specific session."""
        await memory.consolidate(
            "sess_clear",
            [
                {"type": "message", "content": "Item 1 to clear from session"},
                {"type": "message", "content": "Item 2 to clear from session"},
            ],
        )
        await memory.consolidate(
            "sess_keep",
            [{"type": "message", "content": "Item to keep from other session"}],
        )

        cleared = await memory.clear_session("sess_clear")
        assert cleared == 2

        stats = await memory.get_statistics()
        assert stats["total_items"] == 1
        assert stats["total_sessions"] == 1

    @pytest.mark.asyncio
    async def test_clear_nonexistent_session(self, memory: LongTermMemory) -> None:
        """Test clearing a session that doesn't exist."""
        cleared = await memory.clear_session("nonexistent_session")
        assert cleared == 0

    @pytest.mark.asyncio
    async def test_retrieve_updates_access_count(self, memory: LongTermMemory) -> None:
        """Test that retrieve_relevant updates access counts."""
        items = await memory.consolidate(
            "sess_access_count",
            [{"type": "message", "content": "Knowledge for access count testing"}],
        )
        item_id = items[0].id

        await memory.retrieve_relevant("access count")

        accessed_item = await memory.access_item(item_id)
        assert accessed_item is not None
        assert accessed_item.access_count >= 1
