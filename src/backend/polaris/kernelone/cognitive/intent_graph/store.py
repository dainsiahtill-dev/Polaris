"""IntentGraphStore - Persistence and query layer for IntentGraph.

Based on KernelOne patterns from CognitiveSessionManager and EvolutionStore.
Provides JSON persistence with memory caching and cross-session belief querying.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.cognitive.evolution.models import Belief
from polaris.kernelone.cognitive.perception.models import IntentGraph

_logger = logging.getLogger(__name__)


class IntentGraphStore:
    """
    Store for IntentGraph persistence and cross-session belief querying.

    Design follows KernelOne patterns:
    - JSON persistence with atomic writes (temp file + rename)
    - In-memory LRU-style cache
    - UTF-8 encoding for all text operations
    - Workspace-scoped storage under <metadata_dir>/intent_graphs/
    """

    def __init__(self, workspace: str = ".") -> None:
        """Initialize the store with workspace path.

        Args:
            workspace: Base workspace directory. Intent graphs will be stored
                      under {workspace}/<metadata_dir>/intent_graphs/
        """
        self._workspace = workspace
        self._cache: dict[str, IntentGraph] = {}
        metadata_dir = get_workspace_metadata_dir_name()
        self._store_path = Path(workspace) / metadata_dir / "intent_graphs"
        self._store_path.mkdir(parents=True, exist_ok=True)
        self._load_all_from_disk()

    def _graph_file_path(self, session_id: str) -> Path:
        """Get the file path for a session's intent graph."""
        return self._store_path / f"{session_id}.json"

    def _load_all_from_disk(self) -> None:
        """Load all existing intent graphs from disk into cache."""
        for graph_file in self._store_path.glob("*.json"):
            try:
                data = json.loads(graph_file.read_text(encoding="utf-8"))
                graph = self._reconstruct_graph(data)
                if graph:
                    self._cache[graph.session_id] = graph
            except (RuntimeError, ValueError):
                _logger.exception("Failed to load intent graph file: %s", graph_file)

    def _reconstruct_graph(self, data: dict[str, Any]) -> IntentGraph | None:
        """Reconstruct IntentGraph from serialized JSON data.

        Args:
            data: Dictionary containing serialized graph data.

        Returns:
            Reconstructed IntentGraph or None if reconstruction fails.
        """
        try:
            from polaris.kernelone.cognitive.perception.models import (
                IntentChain,
                IntentEdge,
                IntentNode,
            )

            nodes = tuple(
                IntentNode(
                    node_id=n["node_id"],
                    intent_type=n["intent_type"],
                    content=n["content"],
                    confidence=n["confidence"],
                    source_event_id=n["source_event_id"],
                    uncertainty_factors=tuple(n.get("uncertainty_factors", [])),
                    metadata=dict(n.get("metadata", {})),
                )
                for n in data.get("nodes", [])
            )

            edges = tuple(
                IntentEdge(
                    from_node_id=e["from_node_id"],
                    to_node_id=e["to_node_id"],
                    edge_type=e["edge_type"],
                    confidence=e["confidence"],
                    reasoning=e["reasoning"],
                )
                for e in data.get("edges", [])
            )

            chains = []
            for c in data.get("chains", []):
                surface = None
                if c.get("surface_intent"):
                    s = c["surface_intent"]
                    surface = IntentNode(
                        node_id=s["node_id"],
                        intent_type=s["intent_type"],
                        content=s["content"],
                        confidence=s["confidence"],
                        source_event_id=s["source_event_id"],
                        uncertainty_factors=tuple(s.get("uncertainty_factors", [])),
                        metadata=dict(s.get("metadata", {})),
                    )
                deep = None
                if c.get("deep_intent"):
                    d = c["deep_intent"]
                    deep = IntentNode(
                        node_id=d["node_id"],
                        intent_type=d["intent_type"],
                        content=d["content"],
                        confidence=d["confidence"],
                        source_event_id=d["source_event_id"],
                        uncertainty_factors=tuple(d.get("uncertainty_factors", [])),
                        metadata=dict(d.get("metadata", {})),
                    )
                unstated = tuple(
                    IntentNode(
                        node_id=u["node_id"],
                        intent_type=u["intent_type"],
                        content=u["content"],
                        confidence=u["confidence"],
                        source_event_id=u["source_event_id"],
                        uncertainty_factors=tuple(u.get("uncertainty_factors", [])),
                        metadata=dict(u.get("metadata", {})),
                    )
                    for u in c.get("unstated_needs", [])
                )
                chains.append(
                    IntentChain(
                        chain_id=c["chain_id"],
                        surface_intent=surface,
                        deep_intent=deep,
                        uncertainty=c["uncertainty"],
                        confidence_level=c["confidence_level"],
                        unstated_needs=unstated,
                    )
                )

            return IntentGraph(
                graph_id=data["graph_id"],
                session_id=data["session_id"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                nodes=nodes,
                edges=edges,
                chains=tuple(chains),
            )
        except (RuntimeError, ValueError):
            _logger.exception("Failed to reconstruct intent graph from data")
            return None

    def _graph_to_dict(self, graph: IntentGraph) -> dict[str, Any]:
        """Convert IntentGraph to dictionary for JSON serialization.

        Args:
            graph: The IntentGraph to serialize.

        Returns:
            Dictionary representation of the graph.
        """
        return {
            "graph_id": graph.graph_id,
            "session_id": graph.session_id,
            "created_at": graph.created_at,
            "updated_at": graph.updated_at,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "intent_type": n.intent_type,
                    "content": n.content,
                    "confidence": n.confidence,
                    "source_event_id": n.source_event_id,
                    "uncertainty_factors": list(n.uncertainty_factors),
                    "metadata": n.metadata,
                }
                for n in graph.nodes
            ],
            "edges": [
                {
                    "from_node_id": e.from_node_id,
                    "to_node_id": e.to_node_id,
                    "edge_type": e.edge_type,
                    "confidence": e.confidence,
                    "reasoning": e.reasoning,
                }
                for e in graph.edges
            ],
            "chains": [
                {
                    "chain_id": c.chain_id,
                    "surface_intent": (
                        {
                            "node_id": c.surface_intent.node_id,
                            "intent_type": c.surface_intent.intent_type,
                            "content": c.surface_intent.content,
                            "confidence": c.surface_intent.confidence,
                            "source_event_id": c.surface_intent.source_event_id,
                            "uncertainty_factors": list(c.surface_intent.uncertainty_factors),
                            "metadata": c.surface_intent.metadata,
                        }
                        if c.surface_intent
                        else None
                    ),
                    "deep_intent": (
                        {
                            "node_id": c.deep_intent.node_id,
                            "intent_type": c.deep_intent.intent_type,
                            "content": c.deep_intent.content,
                            "confidence": c.deep_intent.confidence,
                            "source_event_id": c.deep_intent.source_event_id,
                            "uncertainty_factors": list(c.deep_intent.uncertainty_factors),
                            "metadata": c.deep_intent.metadata,
                        }
                        if c.deep_intent
                        else None
                    ),
                    "uncertainty": c.uncertainty,
                    "confidence_level": c.confidence_level,
                    "unstated_needs": [
                        {
                            "node_id": u.node_id,
                            "intent_type": u.intent_type,
                            "content": u.content,
                            "confidence": u.confidence,
                            "source_event_id": u.source_event_id,
                            "uncertainty_factors": list(u.uncertainty_factors),
                            "metadata": u.metadata,
                        }
                        for u in c.unstated_needs
                    ],
                }
                for c in graph.chains
            ],
        }

    def save(self, session_id: str, graph: IntentGraph) -> None:
        """Save an intent graph to disk and update cache.

        Uses atomic write pattern (temp file + rename) to prevent corruption.

        Args:
            session_id: The session identifier.
            graph: The IntentGraph to persist.
        """
        path = self._graph_file_path(session_id)
        data = self._graph_to_dict(graph)

        try:
            content = json.dumps(data, ensure_ascii=False, indent=2)
        except (RuntimeError, ValueError):
            _logger.exception("Failed to serialize intent graph %s to JSON", session_id)
            return

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
                temp_path = Path(f.name)

            # Retry loop for Windows replace() concurrency issues
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    temp_path.replace(path)
                    break
                except PermissionError:
                    if attempt < max_attempts - 1:
                        time.sleep(0.1 * (2**attempt))
                    else:
                        raise

            # Update cache after successful write
            self._cache[session_id] = graph

        except (RuntimeError, ValueError):
            _logger.exception("Failed to persist intent graph %s to disk", session_id)
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
                except (RuntimeError, ValueError) as cleanup_err:
                    _logger.warning("Failed to clean up temp file %s: %s", temp_path, cleanup_err)

    def load(self, session_id: str) -> IntentGraph | None:
        """Load an intent graph by session ID.

        Checks cache first, then falls back to disk if not in cache.

        Args:
            session_id: The session identifier.

        Returns:
            The IntentGraph if found, None otherwise.
        """
        # Check cache first
        if session_id in self._cache:
            return self._cache[session_id]

        # Fall back to disk
        path = self._graph_file_path(session_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            graph = self._reconstruct_graph(data)
            if graph:
                self._cache[session_id] = graph
            return graph
        except (RuntimeError, ValueError):
            _logger.exception("Failed to load intent graph %s from disk", session_id)
            return None

    def query_beliefs(self, filters: dict[str, Any]) -> list[Belief]:
        """Query beliefs across all stored intent graphs.

        Extracts beliefs from intent nodes based on filter criteria.
        Currently supports filtering by:
        - intent_type: str - Filter by intent node type
        - min_confidence: float - Minimum confidence threshold
        - session_id: str - Limit to specific session

        Args:
            filters: Dictionary of filter criteria.

        Returns:
            List of Belief objects matching the filters.
        """
        beliefs: list[Belief] = []

        intent_type_filter = filters.get("intent_type")
        min_confidence = filters.get("min_confidence", 0.0)
        session_filter = filters.get("session_id")

        graphs_to_search: dict[str, IntentGraph]
        if session_filter:
            graph = self.load(session_filter)
            graphs_to_search = {session_filter: graph} if graph else {}
        else:
            graphs_to_search = self._cache.copy()
            # Also check disk for any graphs not in cache
            for graph_file in self._store_path.glob("*.json"):
                sid = graph_file.stem
                if sid not in graphs_to_search:
                    graph = self.load(sid)
                    if graph:
                        graphs_to_search[sid] = graph

        for session_id, graph in graphs_to_search.items():
            for node in graph.nodes:
                # Apply filters
                if intent_type_filter and node.intent_type != intent_type_filter:
                    continue
                if node.confidence < min_confidence:
                    continue

                # Convert intent node to belief
                belief = Belief(
                    belief_id=f"belief_{node.node_id}",
                    content=node.content,
                    source=f"intent_node:{node.intent_type}",
                    source_session=session_id,
                    confidence=node.confidence,
                    importance=5,  # Default importance for intent-derived beliefs
                    created_at=graph.created_at,
                    verified_at=None,
                    falsified_at=None,
                    supersedes=None,
                    related_rules=(),
                )
                beliefs.append(belief)

        return beliefs

    def delete(self, session_id: str) -> bool:
        """Delete an intent graph by session ID.

        Removes from both cache and disk.

        Args:
            session_id: The session identifier.

        Returns:
            True if the graph was found and deleted, False otherwise.
        """
        found = False

        # Remove from cache
        if session_id in self._cache:
            del self._cache[session_id]
            found = True

        # Remove from disk
        path = self._graph_file_path(session_id)
        if path.exists():
            try:
                path.unlink()
                found = True
            except (RuntimeError, ValueError):
                _logger.exception("Failed to delete intent graph file: %s", path)

        return found

    def list_sessions(self) -> list[str]:
        """List all session IDs with stored intent graphs.

        Returns:
            List of session ID strings.
        """
        sessions = set(self._cache.keys())
        for graph_file in self._store_path.glob("*.json"):
            sessions.add(graph_file.stem)
        return sorted(sessions)

    def clear_cache(self) -> None:
        """Clear the in-memory cache.

        Note: This does not affect persisted data on disk.
        """
        self._cache.clear()
