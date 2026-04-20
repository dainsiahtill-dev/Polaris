"""Knowledge Sharing Bus - Inter-Agent Knowledge Transfer.

This module provides a bus for publishing, querying, and subscribing to knowledge
items shared between agents. Agents can publish knowledge items to topics, subscribe
to topics to receive notifications, and query the knowledge base.

Key components:
1. KnowledgeItem: Immutable knowledge entry with topic, content, metadata
2. KnowledgeQuery: Query parameters for searching knowledge
3. KnowledgeSharingBus: Central bus for publishing, querying, and subscribing

Usage::

    bus = KnowledgeSharingBus()

    # Publish knowledge
    item = KnowledgeItem(
        id="know_001",
        topic="architecture",
        content="Use adapter pattern for external services",
        source_agent="architect",
        timestamp=datetime.now(timezone.utc).isoformat(),
        tags=("design-pattern", "architecture"),
    )
    await bus.publish_knowledge("architect", item)

    # Subscribe to topics
    await bus.subscribe("director", ("architecture", "design"))

    # Query knowledge
    query = KnowledgeQuery(topics=("architecture",))
    results = await bus.query_knowledge("director", query)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(frozen=True)
class KnowledgeItem:
    """A piece of knowledge shared between agents.

    This is an immutable dataclass representing a knowledge entry that can be
    published to and queried from the knowledge sharing bus.

    Attributes:
        id: Unique identifier for this knowledge item.
        topic: Primary topic/category for this knowledge.
        content: The actual knowledge content (text).
        source_agent: Agent ID that published this knowledge.
        timestamp: ISO format timestamp when this was created.
        metadata: Additional metadata as key-value pairs.
        tags: Semantic tags for categorization and search.
    """

    id: str
    topic: str
    content: str
    source_agent: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class KnowledgeQuery:
    """Query for searching knowledge.

    Attributes:
        topics: Tuple of topics to search within.
        keywords: Tuple of keywords for text matching.
        source_agent: Filter by specific source agent (optional).
        limit: Maximum number of results to return.
    """

    topics: tuple[str, ...]
    keywords: tuple[str, ...] = field(default_factory=tuple)
    source_agent: str | None = None
    limit: int = 10


# ============================================================================
# Knowledge Sharing Bus
# ============================================================================


class KnowledgeSharingBus:
    """Bus for sharing knowledge between agents.

    This bus provides:
    1. Publishing: Agents can publish knowledge items to topics
    2. Querying: Agents can search knowledge by topic, keywords, source
    3. Subscriptions: Agents can subscribe to topics for notifications

    Thread safety:
        - Uses asyncio.Lock for thread-safe operations on shared state.

    Usage::

        bus = KnowledgeSharingBus()

        # Publish
        item = KnowledgeItem(
            id="know_001",
            topic="architecture",
            content="Use adapter pattern",
            source_agent="architect",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        await bus.publish_knowledge("architect", item)

        # Subscribe
        await bus.subscribe("director", ("architecture",))

        # Query
        query = KnowledgeQuery(topics=("architecture",))
        results = await bus.query_knowledge("director", query)
    """

    def __init__(self) -> None:
        """Initialize the knowledge sharing bus."""
        # Knowledge storage: knowledge_id -> KnowledgeItem
        self._knowledge_store: dict[str, KnowledgeItem] = {}

        # Topic index: topic -> set of knowledge_ids
        self._topic_index: dict[str, set[str]] = {}

        # Agent subscriptions: agent_id -> set of topics
        self._subscriptions: dict[str, set[str]] = {}

        # Notification queue for subscribed agents: agent_id -> list of KnowledgeItem
        self._notification_queue: dict[str, list[KnowledgeItem]] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info("KnowledgeSharingBus initialized")

    async def publish_knowledge(self, agent_id: str, knowledge: KnowledgeItem) -> None:
        """Publish a knowledge item to the bus.

        The knowledge item is stored and indexed by topic. All agents subscribed
        to the topic will receive a notification.

        Args:
            agent_id: ID of the agent publishing the knowledge.
            knowledge: The KnowledgeItem to publish.
        """
        async with self._lock:
            # Store the knowledge item
            self._knowledge_store[knowledge.id] = knowledge

            # Index by topic
            if knowledge.topic not in self._topic_index:
                self._topic_index[knowledge.topic] = set()
            self._topic_index[knowledge.topic].add(knowledge.id)

            # Also index by tags
            for tag in knowledge.tags:
                if tag not in self._topic_index:
                    self._topic_index[tag] = set()
                self._topic_index[tag].add(knowledge.id)

            logger.debug(
                "Published knowledge %s from agent %s to topic %s",
                knowledge.id,
                agent_id,
                knowledge.topic,
            )

        # Notify subscribed agents (outside lock to avoid deadlocks)
        await self._notify_subscribers(knowledge)

    async def query_knowledge(
        self,
        agent_id: str,
        query: KnowledgeQuery,
    ) -> list[KnowledgeItem]:
        """Query knowledge items based on topics and keywords.

        Args:
            agent_id: ID of the agent making the query.
            query: KnowledgeQuery with search parameters.

        Returns:
            List of matching KnowledgeItem, sorted by relevance.
        """
        async with self._lock:
            # Collect matching knowledge IDs
            candidate_ids: set[str] = set()

            # Match by topics
            if query.topics:
                for topic in query.topics:
                    if topic in self._topic_index:
                        candidate_ids.update(self._topic_index[topic])

            # If no topics specified, search all knowledge
            if not query.topics:
                candidate_ids.update(self._knowledge_store.keys())

            # Filter and collect results
            results: list[KnowledgeItem] = []

            for kid in candidate_ids:
                if kid not in self._knowledge_store:
                    continue

                item = self._knowledge_store[kid]

                # Filter by source agent if specified
                if query.source_agent and item.source_agent != query.source_agent:
                    continue

                # Match by keywords
                if query.keywords:
                    content_lower = item.content.lower()
                    tag_content = " ".join(item.tags).lower()
                    keyword_matches = any(
                        kw.lower() in content_lower or kw.lower() in tag_content for kw in query.keywords
                    )
                    if not keyword_matches:
                        continue

                results.append(item)

            # Sort by timestamp (newest first)
            results.sort(key=lambda x: x.timestamp, reverse=True)

            # Apply limit
            return results[: query.limit]

    async def subscribe(self, agent_id: str, topics: tuple[str, ...]) -> None:
        """Subscribe an agent to receive notifications for specific topics.

        Args:
            agent_id: ID of the agent to subscribe.
            topics: Tuple of topics to subscribe to.
        """
        async with self._lock:
            if agent_id not in self._subscriptions:
                self._subscriptions[agent_id] = set()
                self._notification_queue[agent_id] = []

            for topic in topics:
                self._subscriptions[agent_id].add(topic)

            logger.debug(
                "Agent %s subscribed to topics: %s",
                agent_id,
                topics,
            )

    async def unsubscribe(self, agent_id: str, topics: tuple[str, ...]) -> None:
        """Unsubscribe an agent from receiving notifications for specific topics.

        Args:
            agent_id: ID of the agent to unsubscribe.
            topics: Tuple of topics to unsubscribe from.
        """
        async with self._lock:
            if agent_id not in self._subscriptions:
                return

            for topic in topics:
                self._subscriptions[agent_id].discard(topic)

            logger.debug(
                "Agent %s unsubscribed from topics: %s",
                agent_id,
                topics,
            )

    async def get_subscriptions(self, agent_id: str) -> tuple[str, ...]:
        """Get the list of topics an agent is subscribed to.

        Args:
            agent_id: ID of the agent.

        Returns:
            Tuple of subscribed topics.
        """
        async with self._lock:
            if agent_id not in self._subscriptions:
                return ()
            return tuple(self._subscriptions[agent_id])

    async def get_notifications(self, agent_id: str) -> list[KnowledgeItem]:
        """Get and clear pending notifications for an agent.

        Args:
            agent_id: ID of the agent.

        Returns:
            List of KnowledgeItem notifications.
        """
        async with self._lock:
            if agent_id not in self._notification_queue:
                return []
            notifications = self._notification_queue[agent_id]
            self._notification_queue[agent_id] = []
            return notifications

    async def _notify_subscribers(self, knowledge: KnowledgeItem) -> None:
        """Notify all subscribers of a new knowledge item.

        Args:
            knowledge: The knowledge item to notify about.
        """
        async with self._lock:
            for agent_id, topics in self._subscriptions.items():
                # Check if agent is subscribed to the knowledge's topic or tags
                if knowledge.topic in topics or any(tag in topics for tag in knowledge.tags):
                    if agent_id not in self._notification_queue:
                        self._notification_queue[agent_id] = []
                    self._notification_queue[agent_id].append(knowledge)

                    logger.debug(
                        "Queued notification for agent %s: %s",
                        agent_id,
                        knowledge.id,
                    )

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about the knowledge sharing bus.

        Returns:
            Dictionary with statistics.
        """
        return {
            "total_knowledge_items": len(self._knowledge_store),
            "total_topics": len(self._topic_index),
            "total_subscribers": len(self._subscriptions),
        }


__all__ = [
    "KnowledgeItem",
    "KnowledgeQuery",
    "KnowledgeSharingBus",
]
