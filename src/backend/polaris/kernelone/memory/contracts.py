"""Memory subsystem contracts for KernelOne.

This module defines the stable port surface for the memory subsystem:
anthropomorphic thinking, semantic memory storage, and project profiling.

Architecture:
    - MemoryPort: async CRUD interface for MemoryItem storage
    - ReflectionPort: insight extraction and storage interface
    - ProjectProfilePort: project-scoped profile engine interface
    - ThinkingPort: transparent thinking / self-reflection interface

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All text I/O must use encoding="utf-8"
    - Deduplication via content hash (SHA-1)
    - MemoryItem importance is 1-10, assigned by rules or LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from polaris.kernelone.constants import RoleId

# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryItemSnapshot:
    """Immutable snapshot of a memory item.

    Represents a cognitive observation derived from a run event.
    """

    id: str
    source_event_id: str
    step: int
    timestamp: datetime
    role: str  # PM / Director / QA / Architect / etc.
    type: str  # observation / plan / reflection_summary
    kind: str  # error | info | success | warning | debug
    text: str
    importance: int  # 1-10
    keywords: list[str]
    hash: str  # SHA-1(content + type + role + context) for deduplication
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReflectionSnapshot:
    """Immutable snapshot of a derived insight from multiple memories."""

    id: str
    created_step: int
    expiry_steps: int  # Decay: steps until this insight expires
    type: str  # heuristic | summary | preference
    scope: list[str]  # e.g. ["npm", "network"] — applicability limits
    confidence: float  # 0.0–1.0
    text: str
    evidence_mem_ids: list[str]  # Back-links to memories that formed this
    importance: int  # 1-10


@dataclass(frozen=True)
class ProjectProfileSnapshot:
    """Immutable snapshot of a project's technology and collaboration profile."""

    workspace_key: str
    tech_stack: dict[str, Any] = field(default_factory=dict)
    collaboration: dict[str, Any] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)


# -----------------------------------------------------------------------------


class MemoryPort(Protocol):
    """Abstract interface for memory item CRUD operations.

    Implementations: MemoryStore (JSONL), LanceDBMemoryAdapter, etc.
    """

    async def add(self, item: MemoryItemSnapshot) -> str:
        """Store a memory item. Returns its ID. Deduplicates by hash."""
        ...

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
        role: str | None = None,
        kind: str | None = None,
        min_importance: int = 1,
    ) -> list[MemoryItemSnapshot]:
        """Retrieve memories matching a semantic query."""
        ...

    async def get_by_id(self, id: str) -> MemoryItemSnapshot | None:
        """Retrieve a specific memory item by ID."""
        ...

    async def get_recent(self, *, limit: int = 50) -> list[MemoryItemSnapshot]:
        """Retrieve the most recent memories by timestamp."""
        ...

    async def list_ids(self) -> list[str]:
        """List all memory item IDs."""
        ...

    async def delete(self, id: str) -> bool:
        """Delete a memory item. Returns True if it existed."""
        ...

    async def search_by_hash(self, hash: str) -> MemoryItemSnapshot | None:
        """Find a memory by its content hash (deduplication check)."""
        ...


class ReflectionPort(Protocol):
    """Abstract interface for derived insight storage.

    Implementations: ReflectionStore (JSONL), etc.
    """

    async def add(self, reflection: ReflectionSnapshot) -> str:
        """Store a reflection. Returns its ID."""
        ...

    async def retrieve(
        self,
        scope: list[str] | None = None,
        *,
        min_confidence: float = 0.0,
    ) -> list[ReflectionSnapshot]:
        """Retrieve reflections, optionally filtered by scope and confidence."""
        ...

    async def expire(self, current_step: int) -> int:
        """Remove reflections past their expiry step. Returns count deleted."""
        ...

    async def get_by_id(self, id: str) -> ReflectionSnapshot | None:
        """Retrieve a specific reflection by ID."""
        ...


class ProjectProfilePort(Protocol):
    """Abstract interface for project profile management.

    Implementations: ProjectProfileEngine (JSONL), etc.
    """

    async def get_profile(
        self,
        workspace: str,
    ) -> ProjectProfileSnapshot | None:
        """Load the cached profile for a workspace."""
        ...

    async def save_profile(
        self,
        workspace: str,
        profile: ProjectProfileSnapshot,
    ) -> None:
        """Persist a profile snapshot."""
        ...

    async def analyze(self, workspace: str) -> ProjectProfileSnapshot:
        """Re-analyze a workspace and return a fresh profile."""
        ...


# -----------------------------------------------------------------------------


class ThinkingPort(Protocol):
    """Abstract interface for transparent thinking / self-reflection.

    Implementations: ThinkingEngine (in-process).
    """

    async def think(
        self,
        prompt: str,
        *,
        role: RoleId | str = "director",
        mode: str = "default",
    ) -> str:
        """Generate transparent thinking output for a prompt."""
        ...

    async def reflect(
        self,
        events: list[MemoryItemSnapshot],
        *,
        role: RoleId | str = "director",
    ) -> ReflectionSnapshot:
        """Derive a new reflection from recent memory items."""
        ...


__all__ = [
    # Types
    "MemoryItemSnapshot",
    # Ports
    "MemoryPort",
    "ProjectProfilePort",
    "ProjectProfileSnapshot",
    "ReflectionPort",
    "ReflectionSnapshot",
    "ThinkingPort",
]
