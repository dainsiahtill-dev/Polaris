"""Context subsystem contracts for KernelOne.

This module defines the stable port surface for context management:
role-scoped context compression, token budgeting, and prompt construction.

Architecture:
    - ContextCompressorPort: role-aware context window compression
    - ContextBudgetAllocatorPort: token/char budget allocation (renamed from ContextBudgetPort)
    - ContextBuilderPort: high-level prompt construction from budgeted sources

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - Budget is per-role and per-mode (budget governs what gets in/out)
    - Sources are policy-driven (not hard-coded to specific file types)
    - Explicit UTF-8: all text I/O uses encoding="utf-8"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextBudget:
    """Immutable token and character budget for a context window."""

    max_tokens: int
    max_chars: int
    cost_class: str = "medium"  # small | medium | large


@dataclass(frozen=True)
class ContextSource:
    """A single source of context content (file, memory, events, etc.)."""

    source_type: str  # e.g. "memory", "events", "task", "reflection"
    source_id: str  # stable identifier within that source type
    role: str  # which role this source serves
    text: str
    tokens: int
    importance: float  # 0.0–1.0
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompactSnapshot:
    """Immutable snapshot of a compressed context window."""

    role: str
    mode: str
    run_id: str
    step: int
    budget: ContextBudget
    sources: list[ContextSource]
    total_tokens: int
    total_chars: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_within_budget(self) -> bool:
        return self.total_tokens <= self.budget.max_tokens and self.total_chars <= self.budget.max_chars


@dataclass(frozen=True)
class ContextRequest:
    """Immutable request describing what context is needed."""

    run_id: str
    step: int
    role: str
    mode: str
    query: str
    budget: ContextBudget
    sources_enabled: list[str]
    policy: dict[str, Any] = field(default_factory=dict)
    events_path: str = ""


@dataclass(frozen=True)
class ContextPack:
    """Immutable pack of all sources bundled for injection into an LLM."""

    role: str
    mode: str
    run_id: str
    step: int
    content: str  # assembled text for injection
    sources: list[ContextSource]
    total_tokens: int
    total_chars: int
    budget: ContextBudget


# -----------------------------------------------------------------------------


class ContextCompressorPort(Protocol):
    """Abstract interface for role-aware context compression.

    Implementations: RoleContextCompressor (in-process), RemoteCompressorAdapter.
    """

    def compress(
        self,
        request: ContextRequest,
    ) -> CompactSnapshot:
        """Build a compressed context snapshot within the budget."""
        ...

    def build_pack(
        self,
        snapshot: CompactSnapshot,
    ) -> ContextPack:
        """Assemble a ContextPack from a CompactSnapshot for LLM injection."""
        ...


class ContextBudgetAllocatorPort(Protocol):
    """Abstract interface for token/char budget allocation.

    P0-009 Unified Interface:
        This interface is distinct from TokenBudgetObserverPort (llm/ports.py):
        - ContextBudgetAllocatorPort: Allocate/manage budgets (write operations)
        - TokenBudgetObserverPort: Observe/query budget state (read-only)

        Both ports serve different roles per ACGA 2.0 separation of concerns.
        Use this interface when you need to allocate and manage budgets
        per role and mode.

    Implementations: InMemoryBudgetCalculator.
    """

    def allocate(
        self,
        role: str,
        mode: str,
        *,
        cost_class: str | None = None,
    ) -> ContextBudget:
        """Allocate a budget for a role/mode combination."""
        ...

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a text string."""
        ...

    def truncate_to_budget(
        self,
        sources: list[ContextSource],
        budget: ContextBudget,
    ) -> list[ContextSource]:
        """Select and order sources to fit within the budget."""
        ...


# Backward compatibility alias - use unique name to avoid conflict with llm.ports.ContextBudgetPort
ContextAllocatorBudgetPort = ContextBudgetAllocatorPort


class RoleContextIdentityPort(Protocol):
    """Abstract interface for role identity and persona resolution.

    Implementations: RoleContextIdentity (in-process).
    """

    def get_persona(self, role: str) -> str:
        """Get the persona text for a role (e.g. 'director', 'pm')."""
        ...

    def get_context_window(
        self,
        workspace: str,
        role: str,
        query: str,
        *,
        step: int = 0,
        run_id: str = "",
        mode: str = "default",
    ) -> ContextPack:
        """Build a complete context window for a role."""
        ...


# -----------------------------------------------------------------------------
# TurnEngine Context Types (Phase 2: Unified ContextRequest)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnEngineContextRequest:
    """Immutable context request for TurnEngine LLM calls.

    Replaces the local ContextRequest in cells/roles/kernel/internal/context_gateway.py.
    Used by RoleContextGateway.build_context() to build LLM messages.

    Phase 3: context_os_snapshot carries the ContextOSProjection from session
    for direct consumption by RoleContextGateway (eliminating indirect strategy_receipt path).
    """

    message: str = ""
    history: tuple[tuple[str, str], ...] = ()
    task_id: str | None = None
    strategy_receipt: Any = None  # StrategyReceipt | None
    # Phase 3: ContextOS snapshot from session (dict form of ContextOSSnapshot)
    context_os_snapshot: dict[str, Any] | None = None
    # Optional override dict for domain/hints passed through to LLM caller
    context_override: dict[str, Any] | None = None


@dataclass(frozen=True)
class TurnEngineContextResult:
    """Immutable context result from TurnEngine LLM call."""

    messages: tuple[dict[str, str], ...] = ()
    token_estimate: int = 0
    context_sources: tuple[str, ...] = ()
    # Compression tracking for ContextOS event emission
    compression_applied: bool = False
    compression_strategy: str | None = None
    # ContextOS routing audit telemetry
    metadata: dict[str, Any] = field(default_factory=dict)


class ContextBuilderPort(Protocol):
    """Port for building LLM context from role-aware state.

    Infrastructure providers receive this port via dependency injection.
    The concrete implementation lives in cells/roles/kernel/internal/ but
    infrastructure never imports it directly — this port breaks the
    infrastructure → Cell.internal dependency violation.

    Implementations: RoleContextGateway.
    """

    async def build_context(
        self,
        request: TurnEngineContextRequest,
    ) -> TurnEngineContextResult:
        """Build LLM messages from role context, history, and budget."""
        ...


__all__ = [
    "CompactSnapshot",
    # Types
    "ContextBudget",
    "ContextBuilderPort",
    "ContextBudgetAllocatorPort",
    "ContextAllocatorBudgetPort",  # Backward compat alias (unique name)
    # Ports
    "ContextCompressorPort",
    "ContextPack",
    "ContextRequest",
    "ContextSource",
    "RoleContextIdentityPort",
    "TurnEngineContextRequest",
    "TurnEngineContextResult",
]
