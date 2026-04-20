"""Intent Graph Data Models for Perception Layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class IntentNode:
    """A single intent node in the graph."""

    node_id: str
    intent_type: str  # surface | deep | unstated | emotional
    content: str
    confidence: float  # 0.0-1.0
    source_event_id: str
    uncertainty_factors: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntentEdge:
    """Relationship between two intent nodes."""

    from_node_id: str
    to_node_id: str
    edge_type: str  # refines | contradicts | supports | leads_to | caused_by
    confidence: float
    reasoning: str


@dataclass(frozen=True, slots=True)
class IntentChain:
    """A reasoning chain: Surface → Deep → Unstated."""

    chain_id: str
    surface_intent: IntentNode | None
    deep_intent: IntentNode | None
    uncertainty: float
    confidence_level: str  # high | medium | low | unknown
    unstated_needs: tuple[IntentNode, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class IntentGraph:
    """The complete intent graph for a session."""

    graph_id: str
    session_id: str
    created_at: str
    updated_at: str
    nodes: tuple[IntentNode, ...] = field(default_factory=tuple)
    edges: tuple[IntentEdge, ...] = field(default_factory=tuple)
    chains: tuple[IntentChain, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class UncertaintyAssessment:
    """Uncertainty quantification for an intent."""

    uncertainty_score: float  # 0.0-1.0, higher = more uncertain
    confidence_lower: float
    confidence_upper: float
    recommended_action: str  # bypass | fast_think | full_pipe
    uncertainty_factors: tuple[str, ...] = field(default_factory=tuple)
