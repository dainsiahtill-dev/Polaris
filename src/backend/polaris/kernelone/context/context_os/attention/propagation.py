"""Graph Propagation: PageRank-style propagation for attention scoring.

This module implements the graph propagation algorithm for ContextOS 3.0.
Scores propagate through the graph with edge-type-specific weights.

Algorithm:
    node_score = base_score + Σ(neighbor_score * edge_weight * decay_factor ^ hops)

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Graph propagation enhances attention scoring but never overrides contract protection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .graph import EventGraph

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PropagationConfig:
    """Configuration for graph propagation."""

    # Decay factor for propagation (0-1)
    decay_factor: float = 0.85
    # Maximum hops for propagation
    max_hops: int = 3
    # Convergence threshold
    convergence_threshold: float = 0.01
    # Maximum iterations
    max_iterations: int = 10


@dataclass
class PropagationResult:
    """Result of graph propagation for a single event."""

    event_id: str
    base_score: float
    propagated_score: float
    final_score: float
    propagation_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "base_score": self.base_score,
            "propagated_score": self.propagated_score,
            "final_score": self.final_score,
            "propagation_sources": self.propagation_sources,
        }


class GraphPropagator:
    """PageRank-style graph propagation for attention scoring.

    This class propagates attention scores through the event graph
    with edge-type-specific weights and decay factors.

    Usage:
        propagator = GraphPropagator(config)
        results = propagator.propagate(graph, base_scores)
    """

    def __init__(self, config: PropagationConfig | None = None) -> None:
        self._config = config or PropagationConfig()

    def propagate(
        self,
        graph: EventGraph,
        base_scores: dict[str, float],
    ) -> dict[str, PropagationResult]:
        """Propagate scores through the graph.

        Args:
            graph: Event graph with edges
            base_scores: Base scores for each event (from AttentionScorer)

        Returns:
            Dictionary of PropagationResult for each event
        """
        if not base_scores:
            return {}

        # Initialize propagated scores with base scores
        propagated = dict(base_scores)
        propagation_sources: dict[str, list[str]] = {eid: [] for eid in base_scores}

        # Iterative propagation
        for iteration in range(self._config.max_iterations):
            new_propagated = dict(propagated)
            max_delta = 0.0

            for event_id in base_scores:
                # Get neighbors and their scores
                neighbors = graph.get_neighbors(event_id)
                if not neighbors:
                    continue

                # Calculate propagation from neighbors
                propagation_sum = 0.0
                sources: list[str] = []

                for neighbor_id in neighbors:
                    if neighbor_id not in propagated:
                        continue

                    # Get edges between event and neighbor
                    edges = graph.get_edges(event_id)
                    neighbor_edges = [e for e in edges if e.target_id == neighbor_id]

                    if not neighbor_edges:
                        continue

                    # Use maximum edge weight
                    max_weight = max(e.weight for e in neighbor_edges)

                    # Calculate propagation with decay
                    # For direct neighbors (hop=1), decay_factor^1
                    neighbor_contribution = propagated[neighbor_id] * max_weight * self._config.decay_factor
                    propagation_sum += neighbor_contribution
                    sources.append(neighbor_id)

                # Update score
                new_score = base_scores[event_id] + propagation_sum
                delta = abs(new_score - propagated[event_id])
                max_delta = max(max_delta, delta)

                new_propagated[event_id] = new_score
                propagation_sources[event_id] = sources

            propagated = new_propagated

            # Check convergence
            if max_delta < self._config.convergence_threshold:
                logger.debug(
                    "Graph propagation converged after %d iterations (delta=%.4f)",
                    iteration + 1,
                    max_delta,
                )
                break

        # Build results
        results: dict[str, PropagationResult] = {}
        for event_id in base_scores:
            results[event_id] = PropagationResult(
                event_id=event_id,
                base_score=base_scores[event_id],
                propagated_score=propagated[event_id] - base_scores[event_id],
                final_score=propagated[event_id],
                propagation_sources=propagation_sources.get(event_id, []),
            )

        logger.info(
            "Graph propagation: %d events, %d iterations, avg_final_score=%.3f",
            len(results),
            min(iteration + 1, self._config.max_iterations),
            sum(r.final_score for r in results.values()) / len(results) if results else 0,
        )

        return results

    def propagate_single(
        self,
        graph: EventGraph,
        event_id: str,
        base_score: float,
        all_base_scores: dict[str, float],
    ) -> PropagationResult:
        """Propagate score for a single event.

        Args:
            graph: Event graph
            event_id: Target event ID
            base_score: Base score for target event
            all_base_scores: Base scores for all events

        Returns:
            PropagationResult for target event
        """
        # Get neighbors
        neighbors = graph.get_neighbors(event_id)
        if not neighbors:
            return PropagationResult(
                event_id=event_id,
                base_score=base_score,
                propagated_score=0.0,
                final_score=base_score,
            )

        # Calculate propagation from neighbors
        propagation_sum = 0.0
        sources: list[str] = []

        for neighbor_id in neighbors:
            if neighbor_id not in all_base_scores:
                continue

            # Get edges between event and neighbor
            edges = graph.get_edges(event_id)
            neighbor_edges = [e for e in edges if e.target_id == neighbor_id]

            if not neighbor_edges:
                continue

            # Use maximum edge weight
            max_weight = max(e.weight for e in neighbor_edges)

            # Calculate propagation
            neighbor_contribution = all_base_scores[neighbor_id] * max_weight * self._config.decay_factor
            propagation_sum += neighbor_contribution
            sources.append(neighbor_id)

        return PropagationResult(
            event_id=event_id,
            base_score=base_score,
            propagated_score=propagation_sum,
            final_score=base_score + propagation_sum,
            propagation_sources=sources,
        )
