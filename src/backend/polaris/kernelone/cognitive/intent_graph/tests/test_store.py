"""Tests for IntentGraphStore.

Covers save/load/query/delete operations following KernelOne testing patterns.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from polaris.kernelone.cognitive.intent_graph.store import IntentGraphStore
from polaris.kernelone.cognitive.perception.models import (
    IntentChain,
    IntentEdge,
    IntentGraph,
    IntentNode,
)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def store(temp_workspace: str) -> IntentGraphStore:
    """Create a store instance with temp workspace."""
    return IntentGraphStore(workspace=temp_workspace)


@pytest.fixture
def sample_graph() -> IntentGraph:
    """Create a sample intent graph for testing."""
    node1 = IntentNode(
        node_id="node_1",
        intent_type="surface",
        content="I want to refactor this code",
        confidence=0.9,
        source_event_id="event_1",
        uncertainty_factors=(),
        metadata={"sequence": 1},
    )
    node2 = IntentNode(
        node_id="node_2",
        intent_type="deep",
        content="Improve code maintainability",
        confidence=0.8,
        source_event_id="event_2",
        uncertainty_factors=("ambiguous_scope",),
        metadata={"sequence": 2},
    )

    edge = IntentEdge(
        from_node_id="node_1",
        to_node_id="node_2",
        edge_type="refines",
        confidence=0.85,
        reasoning="Surface intent refines to deeper goal",
    )

    chain = IntentChain(
        chain_id="chain_1",
        surface_intent=node1,
        deep_intent=node2,
        uncertainty=0.2,
        confidence_level="high",
        unstated_needs=(),
    )

    return IntentGraph(
        graph_id="test_graph_001",
        session_id="session_001",
        created_at="2026-04-11T10:00:00+00:00",
        updated_at="2026-04-11T10:05:00+00:00",
        nodes=(node1, node2),
        edges=(edge,),
        chains=(chain,),
    )


class TestSave:
    """Test save operation."""

    def test_save_creates_file(self, store: IntentGraphStore, temp_workspace: str, sample_graph: IntentGraph) -> None:
        """Test that save creates a JSON file on disk."""
        store.save("session_001", sample_graph)

        expected_path = Path(temp_workspace) / ".polaris" / "intent_graphs" / "session_001.json"
        assert expected_path.exists()

    def test_save_updates_cache(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that save updates the in-memory cache."""
        store.save("session_001", sample_graph)

        assert "session_001" in store._cache
        assert store._cache["session_001"] == sample_graph

    def test_save_valid_json(self, store: IntentGraphStore, temp_workspace: str, sample_graph: IntentGraph) -> None:
        """Test that saved file contains valid JSON."""
        store.save("session_001", sample_graph)

        expected_path = Path(temp_workspace) / ".polaris" / "intent_graphs" / "session_001.json"
        data = json.loads(expected_path.read_text(encoding="utf-8"))

        assert data["graph_id"] == "test_graph_001"
        assert data["session_id"] == "session_001"
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert len(data["chains"]) == 1

    def test_save_overwrites_existing(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that save overwrites existing graph."""
        store.save("session_001", sample_graph)

        # Modify and save again
        modified_graph = IntentGraph(
            graph_id="test_graph_002",
            session_id="session_001",
            created_at=sample_graph.created_at,
            updated_at="2026-04-11T11:00:00+00:00",
            nodes=(),
            edges=(),
            chains=(),
        )
        store.save("session_001", modified_graph)

        loaded = store.load("session_001")
        assert loaded is not None
        assert loaded.graph_id == "test_graph_002"


class TestLoad:
    """Test load operation."""

    def test_load_from_cache(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that load returns cached graph if available."""
        store._cache["session_001"] = sample_graph

        loaded = store.load("session_001")

        assert loaded == sample_graph

    def test_load_from_disk(self, store: IntentGraphStore, temp_workspace: str, sample_graph: IntentGraph) -> None:
        """Test that load reads from disk if not in cache."""
        # Save directly to disk without cache
        store.save("session_001", sample_graph)
        store.clear_cache()

        loaded = store.load("session_001")

        assert loaded is not None
        assert loaded.graph_id == sample_graph.graph_id
        assert loaded.session_id == sample_graph.session_id
        assert len(loaded.nodes) == len(sample_graph.nodes)
        assert len(loaded.edges) == len(sample_graph.edges)
        assert len(loaded.chains) == len(sample_graph.chains)

    def test_load_nonexistent_returns_none(self, store: IntentGraphStore) -> None:
        """Test that load returns None for non-existent session."""
        loaded = store.load("nonexistent_session")

        assert loaded is None

    def test_load_populates_cache(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that load populates cache after disk read."""
        store.save("session_001", sample_graph)
        store.clear_cache()

        store.load("session_001")

        assert "session_001" in store._cache


class TestQueryBeliefs:
    """Test query_beliefs operation."""

    def test_query_all_beliefs(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test querying all beliefs without filters."""
        store.save("session_001", sample_graph)

        beliefs = store.query_beliefs({})

        assert len(beliefs) == 2
        belief_contents = {b.content for b in beliefs}
        assert "I want to refactor this code" in belief_contents
        assert "Improve code maintainability" in belief_contents

    def test_query_by_intent_type(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test filtering beliefs by intent type."""
        store.save("session_001", sample_graph)

        beliefs = store.query_beliefs({"intent_type": "surface"})

        assert len(beliefs) == 1
        assert beliefs[0].content == "I want to refactor this code"

    def test_query_by_min_confidence(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test filtering beliefs by minimum confidence."""
        store.save("session_001", sample_graph)

        beliefs = store.query_beliefs({"min_confidence": 0.85})

        assert len(beliefs) == 1
        assert beliefs[0].confidence == 0.9

    def test_query_by_session_id(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test filtering beliefs by session ID."""
        store.save("session_001", sample_graph)

        # Create second graph
        node3 = IntentNode(
            node_id="node_3",
            intent_type="surface",
            content="Another session intent",
            confidence=0.7,
            source_event_id="event_3",
            uncertainty_factors=(),
            metadata={},
        )
        graph2 = IntentGraph(
            graph_id="test_graph_003",
            session_id="session_002",
            created_at="2026-04-11T12:00:00+00:00",
            updated_at="2026-04-11T12:00:00+00:00",
            nodes=(node3,),
            edges=(),
            chains=(),
        )
        store.save("session_002", graph2)

        beliefs = store.query_beliefs({"session_id": "session_001"})

        assert len(beliefs) == 2
        assert all(b.source_session == "session_001" for b in beliefs)

    def test_query_belief_structure(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that returned beliefs have correct structure."""
        store.save("session_001", sample_graph)

        beliefs = store.query_beliefs({"intent_type": "deep"})

        assert len(beliefs) == 1
        belief = beliefs[0]
        assert belief.belief_id.startswith("belief_")
        assert belief.source.startswith("intent_node:")
        assert belief.source_session == "session_001"
        assert 0.0 <= belief.confidence <= 1.0
        assert 1 <= belief.importance <= 10


class TestDelete:
    """Test delete operation."""

    def test_delete_removes_from_cache(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that delete removes graph from cache."""
        store.save("session_001", sample_graph)

        result = store.delete("session_001")

        assert result is True
        assert "session_001" not in store._cache

    def test_delete_removes_from_disk(
        self, store: IntentGraphStore, temp_workspace: str, sample_graph: IntentGraph
    ) -> None:
        """Test that delete removes file from disk."""
        store.save("session_001", sample_graph)
        expected_path = Path(temp_workspace) / ".polaris" / "intent_graphs" / "session_001.json"

        store.delete("session_001")

        assert not expected_path.exists()

    def test_delete_nonexistent_returns_false(self, store: IntentGraphStore) -> None:
        """Test that delete returns False for non-existent session."""
        result = store.delete("nonexistent_session")

        assert result is False

    def test_delete_after_reload(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test deleting a graph loaded from disk."""
        store.save("session_001", sample_graph)
        store.clear_cache()
        store.load("session_001")  # Load from disk into cache

        result = store.delete("session_001")

        assert result is True
        assert store.load("session_001") is None


class TestListSessions:
    """Test list_sessions operation."""

    def test_list_empty_store(self, store: IntentGraphStore) -> None:
        """Test listing sessions when store is empty."""
        sessions = store.list_sessions()

        assert sessions == []

    def test_list_multiple_sessions(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test listing multiple sessions."""
        store.save("session_001", sample_graph)

        graph2 = IntentGraph(
            graph_id="test_graph_002",
            session_id="session_002",
            created_at="2026-04-11T12:00:00+00:00",
            updated_at="2026-04-11T12:00:00+00:00",
            nodes=(),
            edges=(),
            chains=(),
        )
        store.save("session_002", graph2)

        sessions = store.list_sessions()

        assert len(sessions) == 2
        assert "session_001" in sessions
        assert "session_002" in sessions

    def test_list_sorted(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that sessions are returned sorted."""
        store.save("session_z", sample_graph)

        graph2 = IntentGraph(
            graph_id="test_graph_002",
            session_id="session_a",
            created_at=sample_graph.created_at,
            updated_at=sample_graph.updated_at,
            nodes=(),
            edges=(),
            chains=(),
        )
        store.save("session_a", graph2)

        sessions = store.list_sessions()

        assert sessions == ["session_a", "session_z"]


class TestCacheOperations:
    """Test cache-related operations."""

    def test_clear_cache(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test clearing the cache."""
        store.save("session_001", sample_graph)
        assert "session_001" in store._cache

        store.clear_cache()

        assert "session_001" not in store._cache

    def test_cache_persists_data(self, store: IntentGraphStore, sample_graph: IntentGraph) -> None:
        """Test that data persists after cache clear due to disk storage."""
        store.save("session_001", sample_graph)
        store.clear_cache()

        # Should still be loadable from disk
        loaded = store.load("session_001")
        assert loaded is not None
        assert loaded.graph_id == sample_graph.graph_id


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_graph_save_load(self, store: IntentGraphStore) -> None:
        """Test saving and loading an empty graph."""
        empty_graph = IntentGraph(
            graph_id="empty_graph",
            session_id="empty_session",
            created_at="2026-04-11T10:00:00+00:00",
            updated_at="2026-04-11T10:00:00+00:00",
            nodes=(),
            edges=(),
            chains=(),
        )

        store.save("empty_session", empty_graph)
        loaded = store.load("empty_session")

        assert loaded is not None
        assert len(loaded.nodes) == 0
        assert len(loaded.edges) == 0
        assert len(loaded.chains) == 0

    def test_graph_with_unstated_needs(self, store: IntentGraphStore) -> None:
        """Test graph with unstated needs in chains."""
        surface = IntentNode(
            node_id="surface_1",
            intent_type="surface",
            content="Surface intent",
            confidence=0.9,
            source_event_id="event_1",
            uncertainty_factors=(),
            metadata={},
        )
        unstated = IntentNode(
            node_id="unstated_1",
            intent_type="unstated",
            content="Hidden need",
            confidence=0.5,
            source_event_id="event_2",
            uncertainty_factors=("inferred",),
            metadata={},
        )
        chain = IntentChain(
            chain_id="chain_with_unstated",
            surface_intent=surface,
            deep_intent=None,
            uncertainty=0.5,
            confidence_level="low",
            unstated_needs=(unstated,),
        )
        graph = IntentGraph(
            graph_id="graph_with_unstated",
            session_id="session_unstated",
            created_at="2026-04-11T10:00:00+00:00",
            updated_at="2026-04-11T10:00:00+00:00",
            nodes=(surface, unstated),
            edges=(),
            chains=(chain,),
        )

        store.save("session_unstated", graph)
        loaded = store.load("session_unstated")

        assert loaded is not None
        assert len(loaded.chains) == 1
        assert len(loaded.chains[0].unstated_needs) == 1
        assert loaded.chains[0].unstated_needs[0].content == "Hidden need"

    def test_special_characters_in_content(self, store: IntentGraphStore) -> None:
        """Test handling of special characters in content."""
        node = IntentNode(
            node_id="special_node",
            intent_type="surface",
            content='Special chars: "quotes" \n newline \t tab 中文',
            confidence=0.9,
            source_event_id="event_1",
            uncertainty_factors=(),
            metadata={"key": "value with spaces"},
        )
        graph = IntentGraph(
            graph_id="special_graph",
            session_id="special_session",
            created_at="2026-04-11T10:00:00+00:00",
            updated_at="2026-04-11T10:00:00+00:00",
            nodes=(node,),
            edges=(),
            chains=(),
        )

        store.save("special_session", graph)
        loaded = store.load("special_session")

        assert loaded is not None
        assert loaded.nodes[0].content == 'Special chars: "quotes" \n newline \t tab 中文'
