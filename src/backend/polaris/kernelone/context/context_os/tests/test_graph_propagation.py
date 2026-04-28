"""Tests for Graph Propagation (ContextOS 3.0 P1)."""

from polaris.kernelone.context.context_os.attention.graph import Edge, EdgeType, EventGraph
from polaris.kernelone.context.context_os.attention.propagation import (
    GraphPropagator,
    PropagationConfig,
    PropagationResult,
)


class TestEdgeType:
    """Test EdgeType enum."""

    def test_enum_values(self) -> None:
        assert EdgeType.SAME_FILE.value == "same_file"
        assert EdgeType.CONTRADICTS.value == "contradicts"
        assert EdgeType.SUPERSEDES.value == "supersedes"


class TestEdge:
    """Test Edge dataclass."""

    def test_create_edge(self) -> None:
        edge = Edge(
            source_id="evt_001",
            target_id="evt_002",
            edge_type=EdgeType.SAME_FILE,
            weight=0.3,
        )
        assert edge.source_id == "evt_001"
        assert edge.target_id == "evt_002"
        assert edge.weight == 0.3

    def test_to_dict(self) -> None:
        edge = Edge(
            source_id="evt_001",
            target_id="evt_002",
            edge_type=EdgeType.SAME_FILE,
            weight=0.3,
        )
        d = edge.to_dict()
        assert d["source_id"] == "evt_001"
        assert d["edge_type"] == "same_file"


class TestEventGraph:
    """Test EventGraph class."""

    def test_create_graph(self) -> None:
        graph = EventGraph()
        assert len(graph._adjacency) == 0

    def test_add_event(self) -> None:
        graph = EventGraph()
        event = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        graph.add_event(event)
        assert "evt_001" in graph._adjacency

    def test_same_file_edge(self) -> None:
        graph = EventGraph()
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "File created: test.py",
                "metadata": {},
            },
        )()
        event2 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_002",
                "content": "Modified test.py",
                "metadata": {},
            },
        )()
        graph.add_event(event1)
        graph.add_event(event2)

        edges = graph.get_edges("evt_001")
        same_file_edges = [e for e in edges if e.edge_type == EdgeType.SAME_FILE]
        assert len(same_file_edges) > 0

    def test_same_symbol_edge(self) -> None:
        graph = EventGraph()
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        event2 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_002",
                "content": "def test_function(): return True",
                "metadata": {},
            },
        )()
        graph.add_event(event1)
        graph.add_event(event2)

        edges = graph.get_edges("evt_001")
        same_symbol_edges = [e for e in edges if e.edge_type == EdgeType.SAME_SYMBOL]
        assert len(same_symbol_edges) > 0

    def test_get_neighbors(self) -> None:
        graph = EventGraph()
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        event2 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_002",
                "content": "def test_function(): return True",
                "metadata": {},
            },
        )()
        graph.add_event(event1)
        graph.add_event(event2)

        neighbors = graph.get_neighbors("evt_001")
        assert "evt_002" in neighbors

    def test_stats(self) -> None:
        graph = EventGraph()
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        graph.add_event(event1)

        stats = graph.stats
        assert stats["total_events"] == 1
        assert stats["total_edges"] == 0


class TestPropagationConfig:
    """Test PropagationConfig dataclass."""

    def test_default_config(self) -> None:
        config = PropagationConfig()
        assert config.decay_factor == 0.85
        assert config.max_hops == 3
        assert config.convergence_threshold == 0.01
        assert config.max_iterations == 10

    def test_custom_config(self) -> None:
        config = PropagationConfig(
            decay_factor=0.9,
            max_hops=5,
        )
        assert config.decay_factor == 0.9
        assert config.max_hops == 5


class TestPropagationResult:
    """Test PropagationResult dataclass."""

    def test_create_result(self) -> None:
        result = PropagationResult(
            event_id="evt_001",
            base_score=0.5,
            propagated_score=0.2,
            final_score=0.7,
            propagation_sources=["evt_002"],
        )
        assert result.event_id == "evt_001"
        assert result.final_score == 0.7

    def test_to_dict(self) -> None:
        result = PropagationResult(
            event_id="evt_001",
            base_score=0.5,
            propagated_score=0.2,
            final_score=0.7,
        )
        d = result.to_dict()
        assert d["event_id"] == "evt_001"
        assert d["final_score"] == 0.7


class TestGraphPropagator:
    """Test GraphPropagator class."""

    def test_create_propagator(self) -> None:
        propagator = GraphPropagator()
        assert propagator._config.decay_factor == 0.85

    def test_propagate_basic(self) -> None:
        propagator = GraphPropagator()
        graph = EventGraph()

        # Add events with same symbol (both define test_function)
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        event2 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_002",
                "content": "def test_function(): return True",
                "metadata": {},
            },
        )()
        graph.add_event(event1)
        graph.add_event(event2)

        base_scores = {"evt_001": 0.5, "evt_002": 0.6}
        results = propagator.propagate(graph, base_scores)

        assert len(results) == 2
        # Both should be boosted due to same_symbol edge
        assert results["evt_001"].final_score > 0.5
        assert results["evt_002"].final_score > 0.6

    def test_propagate_no_neighbors(self) -> None:
        propagator = GraphPropagator()
        graph = EventGraph()

        event = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "No related content",
                "metadata": {},
            },
        )()
        graph.add_event(event)

        base_scores = {"evt_001": 0.5}
        results = propagator.propagate(graph, base_scores)

        assert len(results) == 1
        assert results["evt_001"].final_score == 0.5  # No change

    def test_propagate_single(self) -> None:
        propagator = GraphPropagator()
        graph = EventGraph()

        # Add events with same symbol (both define test_function)
        event1 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_001",
                "content": "def test_function(): pass",
                "metadata": {},
            },
        )()
        event2 = type(
            "MockEvent",
            (),
            {
                "event_id": "evt_002",
                "content": "def test_function(): return True",
                "metadata": {},
            },
        )()
        graph.add_event(event1)
        graph.add_event(event2)

        base_scores = {"evt_001": 0.5, "evt_002": 0.6}
        result = propagator.propagate_single(graph, "evt_001", 0.5, base_scores)

        assert result.event_id == "evt_001"
        assert result.final_score > 0.5
