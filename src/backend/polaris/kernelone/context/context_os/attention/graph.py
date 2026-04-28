"""Event Graph: construction and edge detection for ContextOS 3.0.

This module implements the graph structure for attention propagation.
Events are nodes, and relationships between events are edges.

Edge Types:
    - same_file: Events referencing the same file
    - same_symbol: Events referencing the same symbol/function/class
    - same_run_id: Events from the same run
    - mentions_same_task: Events mentioning the same task ID
    - derived_from_same_event: Events derived from the same source
    - contradicts: Events that contradict each other
    - supersedes: Events that supersede earlier events

Key Design Principle:
    "Attention is advisory, Contract is authoritative."
    Graph edges influence propagation, not contract protection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EdgeType(str, Enum):
    """Types of edges in the event graph."""

    SAME_FILE = "same_file"
    SAME_SYMBOL = "same_symbol"
    SAME_RUN_ID = "same_run_id"
    MENTIONS_SAME_TASK = "mentions_same_task"
    DERIVED_FROM_SAME_EVENT = "derived_from_same_event"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    # New edge types
    SAME_ROLE = "same_role"
    SAME_ROUTE = "same_route"
    TEMPORAL_ADJACENT = "temporal_adjacent"
    CONTENT_SIMILAR = "content_similar"
    ERROR_CHAIN = "error_chain"
    TOOL_SEQUENCE = "tool_sequence"


# Edge weights for propagation
EDGE_WEIGHTS: dict[EdgeType, float] = {
    EdgeType.SAME_FILE: 0.3,
    EdgeType.SAME_SYMBOL: 0.5,
    EdgeType.SAME_RUN_ID: 0.2,
    EdgeType.MENTIONS_SAME_TASK: 0.4,
    EdgeType.DERIVED_FROM_SAME_EVENT: 0.6,
    EdgeType.CONTRADICTS: 0.8,
    EdgeType.SUPERSEDES: 0.7,
    # New edge weights
    EdgeType.SAME_ROLE: 0.2,
    EdgeType.SAME_ROUTE: 0.15,
    EdgeType.TEMPORAL_ADJACENT: 0.25,
    EdgeType.CONTENT_SIMILAR: 0.4,
    EdgeType.ERROR_CHAIN: 0.6,
    EdgeType.TOOL_SEQUENCE: 0.35,
}


@dataclass(frozen=True, slots=True)
class Edge:
    """An edge in the event graph."""

    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
            "weight": self.weight,
        }


@dataclass
class EventGraph:
    """Graph of events with edges representing relationships.

    This class provides lazy construction and caching of edges.
    """

    # Adjacency list: event_id -> list of edges
    _adjacency: dict[str, list[Edge]] = field(default_factory=dict)
    # Cache for extracted metadata
    _file_cache: dict[str, set[str]] = field(default_factory=dict)
    _symbol_cache: dict[str, set[str]] = field(default_factory=dict)
    _task_cache: dict[str, set[str]] = field(default_factory=dict)

    def add_event(self, event: Any) -> None:
        """Add an event to the graph and detect edges."""
        event_id = str(getattr(event, "event_id", ""))
        if not event_id:
            return

        # Initialize adjacency list
        if event_id not in self._adjacency:
            self._adjacency[event_id] = []

        # Extract metadata for edge detection
        content = str(getattr(event, "content", "") or "")
        metadata = dict(getattr(event, "metadata", {}) or {})
        run_id = str(metadata.get("run_id", ""))
        source_event_id = str(metadata.get("source_event_id", ""))

        # Cache extracted data
        self._file_cache[event_id] = self._extract_file_paths(content)
        self._symbol_cache[event_id] = self._extract_symbols(content)
        self._task_cache[event_id] = self._extract_task_ids(content)

        # Detect edges with existing events
        for other_id in self._adjacency:
            if other_id == event_id:
                continue

            edges = self._detect_edges(event_id, other_id, content, metadata, run_id, source_event_id)
            self._adjacency[event_id].extend(edges)

            # Add reverse edges
            for edge in edges:
                reverse_edge = Edge(
                    source_id=edge.target_id,
                    target_id=edge.source_id,
                    edge_type=edge.edge_type,
                    weight=edge.weight,
                )
                self._adjacency[other_id].append(reverse_edge)

    def get_edges(self, event_id: str) -> list[Edge]:
        """Get all edges from an event."""
        return self._adjacency.get(event_id, [])

    def get_neighbors(self, event_id: str) -> list[str]:
        """Get all neighbor event IDs."""
        edges = self.get_edges(event_id)
        return [edge.target_id for edge in edges]

    def _detect_edges(
        self,
        event_id: str,
        other_id: str,
        content: str,
        metadata: dict[str, Any],
        run_id: str,
        source_event_id: str,
    ) -> list[Edge]:
        """Detect edges between two events."""
        edges: list[Edge] = []

        # Same file
        event_files = self._file_cache.get(event_id, set())
        other_files = self._file_cache.get(other_id, set())
        if event_files & other_files:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SAME_FILE,
                    weight=EDGE_WEIGHTS[EdgeType.SAME_FILE],
                )
            )

        # Same symbol
        event_symbols = self._symbol_cache.get(event_id, set())
        other_symbols = self._symbol_cache.get(other_id, set())
        if event_symbols & other_symbols:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SAME_SYMBOL,
                    weight=EDGE_WEIGHTS[EdgeType.SAME_SYMBOL],
                )
            )

        # Same run ID
        other_metadata = dict(getattr(self, "_metadata_cache", {}).get(other_id, {}) or {})
        other_run_id = str(other_metadata.get("run_id", ""))
        if run_id and run_id == other_run_id:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SAME_RUN_ID,
                    weight=EDGE_WEIGHTS[EdgeType.SAME_RUN_ID],
                )
            )

        # Mentions same task
        event_tasks = self._task_cache.get(event_id, set())
        other_tasks = self._task_cache.get(other_id, set())
        if event_tasks & other_tasks:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.MENTIONS_SAME_TASK,
                    weight=EDGE_WEIGHTS[EdgeType.MENTIONS_SAME_TASK],
                )
            )

        # Derived from same event
        other_source = str(other_metadata.get("source_event_id", ""))
        if source_event_id and source_event_id == other_source:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.DERIVED_FROM_SAME_EVENT,
                    weight=EDGE_WEIGHTS[EdgeType.DERIVED_FROM_SAME_EVENT],
                )
            )

        # Contradicts
        if self._detect_contradiction(content, str(getattr(self, "_content_cache", {}).get(other_id, ""))):
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.CONTRADICTS,
                    weight=EDGE_WEIGHTS[EdgeType.CONTRADICTS],
                )
            )

        # Supersedes
        if self._detect_supersedes(event_id, other_id, content, metadata):
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SUPERSEDES,
                    weight=EDGE_WEIGHTS[EdgeType.SUPERSEDES],
                )
            )

        # Same role
        event_role = str(metadata.get("role", ""))
        other_role = str(other_metadata.get("role", ""))
        if event_role and event_role == other_role:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SAME_ROLE,
                    weight=EDGE_WEIGHTS[EdgeType.SAME_ROLE],
                )
            )

        # Same route
        event_route = str(metadata.get("route", ""))
        other_route = str(other_metadata.get("route", ""))
        if event_route and event_route == other_route:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.SAME_ROUTE,
                    weight=EDGE_WEIGHTS[EdgeType.SAME_ROUTE],
                )
            )

        # Temporal adjacent (sequence numbers differ by 1)
        event_seq = int(metadata.get("sequence", -1))
        other_seq = int(other_metadata.get("sequence", -1))
        if event_seq >= 0 and other_seq >= 0 and abs(event_seq - other_seq) == 1:
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.TEMPORAL_ADJACENT,
                    weight=EDGE_WEIGHTS[EdgeType.TEMPORAL_ADJACENT],
                )
            )

        # Content similar (simple heuristic: share >50% words)
        if self._detect_content_similarity(content, str(other_metadata.get("content", ""))):
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.CONTENT_SIMILAR,
                    weight=EDGE_WEIGHTS[EdgeType.CONTENT_SIMILAR],
                )
            )

        # Error chain (error followed by error)
        if self._detect_error_chain(content, str(other_metadata.get("content", ""))):
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.ERROR_CHAIN,
                    weight=EDGE_WEIGHTS[EdgeType.ERROR_CHAIN],
                )
            )

        # Tool sequence (tool call followed by tool result)
        if self._detect_tool_sequence(metadata, other_metadata):
            edges.append(
                Edge(
                    source_id=event_id,
                    target_id=other_id,
                    edge_type=EdgeType.TOOL_SEQUENCE,
                    weight=EDGE_WEIGHTS[EdgeType.TOOL_SEQUENCE],
                )
            )

        return edges

    @staticmethod
    def _extract_file_paths(content: str) -> set[str]:
        """Extract file paths from content."""
        # Match common file path patterns
        patterns = [
            r"[A-Za-z]:\\[\w\\.-]+\.\w+",  # Windows paths
            r"/[\w/.-]+\.\w+",  # Unix paths
            r"[\w/\\.-]+\.(py|ts|js|jsx|tsx|java|go|rs|cpp|c|h|yaml|yml|json|md)\b",  # File extensions
        ]
        files: set[str] = set()
        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            files.update(matches)
        return files

    @staticmethod
    def _extract_symbols(content: str) -> set[str]:
        """Extract symbol names (function/class) from content."""
        symbols: set[str] = set()
        # Python function/class definitions
        func_pattern = r"def\s+(\w+)\s*\("
        class_pattern = r"class\s+(\w+)\s*[\(:]"
        symbols.update(re.findall(func_pattern, content))
        symbols.update(re.findall(class_pattern, content))
        return symbols

    @staticmethod
    def _extract_task_ids(content: str) -> set[str]:
        """Extract task IDs from content."""
        # Match common task ID patterns
        patterns = [
            r"task[_-]?(\d+)",
            r"T-(\d+)",
            r"#(\d+)",
        ]
        tasks: set[str] = set()
        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            tasks.update(matches)
        return tasks

    @staticmethod
    def _detect_contradiction(content1: str, content2: str) -> bool:
        """Detect if two contents contradict each other."""
        # Simple negation detection
        negation_patterns = [
            r"not\s+(\w+)",
            r"do\s+not\s+(\w+)",
            r"don\'t\s+(\w+)",
            r"never\s+(\w+)",
        ]
        # Extract negated words from content1
        negated_words: set[str] = set()
        for pattern in negation_patterns:
            matches = re.findall(pattern, content1, re.IGNORECASE)
            negated_words.update(matches)

        # Check if content2 contains those words (without negation)
        if not negated_words:
            return False

        # Simple check: if negated word appears in content2 without negation
        content2_lower = content2.lower()
        for word in negated_words:
            if word.lower() in content2_lower and f"not {word.lower()}" not in content2_lower:
                return True
        return False

    @staticmethod
    def _detect_supersedes(
        event_id: str,
        other_id: str,
        content: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Detect if event supersedes other event."""
        # Check if event explicitly references superseding
        supersedes_keywords = ["supersede", "replace", "override", "update", "fix"]
        content_lower = content.lower()
        return any(kw in content_lower for kw in supersedes_keywords) and other_id in content

    @staticmethod
    def _detect_content_similarity(content1: str, content2: str) -> bool:
        """Detect if two contents are similar (share >50% words)."""
        if not content1 or not content2:
            return False

        words1 = set(content1.lower().split())
        words2 = set(content2.lower().split())

        if not words1 or not words2:
            return False

        intersection = words1 & words2
        min_len = min(len(words1), len(words2))

        return len(intersection) / min_len > 0.5 if min_len > 0 else False

    @staticmethod
    def _detect_error_chain(content1: str, content2: str) -> bool:
        """Detect if both contents contain errors."""
        error_keywords = ["error", "exception", "traceback", "failed", "failure"]
        content1_lower = content1.lower()
        content2_lower = content2.lower()

        has_error1 = any(kw in content1_lower for kw in error_keywords)
        has_error2 = any(kw in content2_lower for kw in error_keywords)

        return has_error1 and has_error2

    @staticmethod
    def _detect_tool_sequence(metadata1: dict[str, Any], metadata2: dict[str, Any]) -> bool:
        """Detect if events form a tool call -> tool result sequence."""
        kind1 = str(metadata1.get("kind", "")).lower()
        kind2 = str(metadata2.get("kind", "")).lower()

        # tool_call followed by tool_result
        if "tool_call" in kind1 and "tool_result" in kind2:
            return True

        # Same tool used consecutively
        tool1 = str(metadata1.get("tool_name", ""))
        tool2 = str(metadata2.get("tool_name", ""))
        return bool(tool1 and tool1 == tool2)

    @property
    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        total_edges = sum(len(edges) for edges in self._adjacency.values())
        return {
            "total_events": len(self._adjacency),
            "total_edges": total_edges,
            "avg_edges_per_event": total_edges / len(self._adjacency) if self._adjacency else 0,
        }
