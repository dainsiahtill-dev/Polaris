"""Tests for KnowledgeSharingBus.

Tests cover:
1. Knowledge publishing
2. Knowledge querying by topic
3. Knowledge querying by keywords
4. Subscription management
5. Notifications
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.kernelone.multi_agent.knowledge_share import (
    KnowledgeItem,
    KnowledgeQuery,
    KnowledgeSharingBus,
)


@pytest.fixture
def bus() -> KnowledgeSharingBus:
    """Create a fresh KnowledgeSharingBus for each test."""
    return KnowledgeSharingBus()


@pytest.fixture
def sample_knowledge() -> KnowledgeItem:
    """Create a sample knowledge item."""
    return KnowledgeItem(
        id="know_001",
        topic="architecture",
        content="Use adapter pattern for external services",
        source_agent="architect",
        timestamp=datetime.now(timezone.utc).isoformat(),
        metadata={"importance": "high"},
        tags=("design-pattern", "architecture"),
    )


class TestKnowledgePublishing:
    """Tests for knowledge publishing."""

    @pytest.mark.asyncio
    async def test_publish_knowledge(self, bus: KnowledgeSharingBus, sample_knowledge: KnowledgeItem) -> None:
        """Test publishing a knowledge item."""
        await bus.publish_knowledge("architect", sample_knowledge)

        # Verify it was stored
        stats = bus.get_stats()
        assert stats["total_knowledge_items"] == 1

    @pytest.mark.asyncio
    async def test_publish_multiple_knowledge(self, bus: KnowledgeSharingBus) -> None:
        """Test publishing multiple knowledge items."""
        item1 = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        item2 = KnowledgeItem(
            id="know_002",
            topic="testing",
            content="Write unit tests for all public methods",
            source_agent="qa",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        await bus.publish_knowledge("architect", item1)
        await bus.publish_knowledge("qa", item2)

        stats = bus.get_stats()
        assert stats["total_knowledge_items"] == 2
        assert stats["total_topics"] == 2  # architecture and testing


class TestKnowledgeQuerying:
    """Tests for knowledge querying."""

    @pytest.mark.asyncio
    async def test_query_by_topic(self, bus: KnowledgeSharingBus) -> None:
        """Test querying knowledge by topic."""
        # Publish knowledge items
        item1 = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern for external services",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        item2 = KnowledgeItem(
            id="know_002",
            topic="testing",
            content="Write unit tests for all public methods",
            source_agent="qa",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        item3 = KnowledgeItem(
            id="know_003",
            topic="architecture",
            content="Prefer composition over inheritance",
            source_agent="chief_engineer",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        await bus.publish_knowledge("architect", item1)
        await bus.publish_knowledge("qa", item2)
        await bus.publish_knowledge("chief_engineer", item3)

        # Query by architecture topic
        query = KnowledgeQuery(topics=("architecture",))
        results = await bus.query_knowledge("director", query)

        assert len(results) == 2
        assert all(item.topic == "architecture" for item in results)

    @pytest.mark.asyncio
    async def test_query_by_keywords(self, bus: KnowledgeSharingBus) -> None:
        """Test querying knowledge by keywords."""
        item = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern for external services",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
            tags=("design-pattern",),
        )

        await bus.publish_knowledge("architect", item)

        # Query by keyword
        query = KnowledgeQuery(topics=(), keywords=("adapter", "pattern"))
        results = await bus.query_knowledge("director", query)

        assert len(results) == 1
        assert "adapter pattern" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_query_by_source_agent(self, bus: KnowledgeSharingBus) -> None:
        """Test querying knowledge by source agent."""
        item1 = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        item2 = KnowledgeItem(
            id="know_002",
            topic="architecture",
            content="Prefer composition",
            source_agent="chief_engineer",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        await bus.publish_knowledge("architect", item1)
        await bus.publish_knowledge("chief_engineer", item2)

        # Query by source agent
        query = KnowledgeQuery(topics=("architecture",), source_agent="architect")
        results = await bus.query_knowledge("director", query)

        assert len(results) == 1
        assert results[0].source_agent == "architect"

    @pytest.mark.asyncio
    async def test_query_with_limit(self, bus: KnowledgeSharingBus) -> None:
        """Test querying with a result limit."""
        for i in range(20):
            item = KnowledgeItem(
                id=f"know_{i:03d}",
                topic="general",
                content=f"Knowledge item {i}",
                source_agent="system",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            await bus.publish_knowledge("system", item)

        query = KnowledgeQuery(topics=("general",), limit=5)
        results = await bus.query_knowledge("director", query)

        assert len(results) == 5


class TestSubscriptionManagement:
    """Tests for subscription management."""

    @pytest.mark.asyncio
    async def test_subscribe(self, bus: KnowledgeSharingBus) -> None:
        """Test subscribing to topics."""
        await bus.subscribe("director", ("architecture", "testing"))

        subscriptions = await bus.get_subscriptions("director")
        assert "architecture" in subscriptions
        assert "testing" in subscriptions

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus: KnowledgeSharingBus) -> None:
        """Test unsubscribing from topics."""
        await bus.subscribe("director", ("architecture", "testing"))
        await bus.unsubscribe("director", ("architecture",))

        subscriptions = await bus.get_subscriptions("director")
        assert "architecture" not in subscriptions
        assert "testing" in subscriptions

    @pytest.mark.asyncio
    async def test_get_subscriptions_empty(self, bus: KnowledgeSharingBus) -> None:
        """Test getting subscriptions for non-subscribed agent."""
        subscriptions = await bus.get_subscriptions("unknown_agent")
        assert subscriptions == ()

    @pytest.mark.asyncio
    async def test_subscribe_and_notify(self, bus: KnowledgeSharingBus) -> None:
        """Test that subscribed agents receive notifications."""
        # Subscribe director to architecture topic
        await bus.subscribe("director", ("architecture",))

        # Publish architecture knowledge
        item = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        await bus.publish_knowledge("architect", item)

        # Get notifications for director
        notifications = await bus.get_notifications("director")

        assert len(notifications) == 1
        assert notifications[0].id == "know_001"

    @pytest.mark.asyncio
    async def test_subscribe_to_tag(self, bus: KnowledgeSharingBus) -> None:
        """Test that agents subscribed to tags receive notifications."""
        # Subscribe director to "design-pattern" tag
        await bus.subscribe("director", ("design-pattern",))

        # Publish knowledge with the tag
        item = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
            tags=("design-pattern",),
        )
        await bus.publish_knowledge("architect", item)

        # Get notifications for director
        notifications = await bus.get_notifications("director")

        assert len(notifications) == 1
        assert notifications[0].id == "know_001"


class TestKnowledgeSharingBusStats:
    """Tests for bus statistics."""

    @pytest.mark.asyncio
    async def test_initial_stats(self, bus: KnowledgeSharingBus) -> None:
        """Test initial statistics are empty."""
        stats = bus.get_stats()
        assert stats["total_knowledge_items"] == 0
        assert stats["total_topics"] == 0
        assert stats["total_subscribers"] == 0

    @pytest.mark.asyncio
    async def test_stats_after_publish(self, bus: KnowledgeSharingBus, sample_knowledge: KnowledgeItem) -> None:
        """Test statistics after publishing."""
        await bus.publish_knowledge("architect", sample_knowledge)

        stats = bus.get_stats()
        assert stats["total_knowledge_items"] == 1
        assert stats["total_topics"] >= 1  # architecture + tags

    @pytest.mark.asyncio
    async def test_stats_after_subscribe(self, bus: KnowledgeSharingBus) -> None:
        """Test statistics after subscribing."""
        await bus.subscribe("director", ("architecture",))

        stats = bus.get_stats()
        assert stats["total_subscribers"] == 1
