"""LLM ports - interfaces for LLM module dependencies.

This module defines port interfaces that allow the LLM module to
interact with context capabilities without creating circular dependencies.

Architecture:
    - Context modules depend on these interfaces (not implementations)
    - Concrete implementations are injected at runtime via DI
    - All text uses UTF-8 encoding.

Ports defined:
    - TokenBudgetObserverPort: Interface for context budget observation (renamed from ContextBudgetPort)
    - RoleContextCompressorPort: Interface for context compression

Usage::

    from polaris.kernelone.llm.ports import TokenBudgetObserverPort, RoleContextCompressorPort

    # In a service constructor
    def __init__(self, context_port: TokenBudgetObserverPort | None = None):
        self._context_port = context_port or DefaultTokenBudgetObserverPort()

Backward Compatibility:
    ContextBudgetPort and DefaultContextBudgetPort are available as aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TokenBudgetObserverPort(Protocol):
    """Interface for observing context budget state.

    P0-009 Unified Interface:
        This interface is distinct from ContextBudgetAllocatorPort (context/contracts.py):
        - TokenBudgetObserverPort: Observe/query budget state (read-only operations)
        - ContextBudgetAllocatorPort: Allocate/manage budgets (write operations)

        Both ports serve different roles per ACGA 2.0 separation of concerns.
        Use this interface when you need to check or observe token budget
        without allocating or managing it.

    This port allows LLM modules to query context budget information
    without importing from the context package directly.
    """

    def get_remaining_tokens(self) -> int:
        """Get remaining token budget in context.

        Returns:
            Number of tokens remaining in the context budget.
        """
        ...

    def get_effective_limit(self) -> int:
        """Get effective token limit after safety margin.

        Returns:
            Effective token limit.
        """
        ...

    def observe_usage(self, tokens: int) -> None:
        """Record token usage in context.

        Args:
            tokens: Number of tokens consumed.
        """
        ...

    def can_add(self, estimated_tokens: int) -> tuple[bool, str]:
        """Check if adding estimated tokens would stay within budget.

        Args:
            estimated_tokens: Tokens to add.

        Returns:
            (True, "") if it fits, (False, reason) if it exceeds.
        """
        ...


# Backward compatibility alias - use unique name to avoid conflict with context.contracts.ContextBudgetPort
LLMBudgetObserverPort = TokenBudgetObserverPort


@runtime_checkable
class RoleContextCompressorPort(Protocol):
    """Interface for context compression capabilities.

    This port allows LLM modules to request context compression
    without importing from the context package directly.
    """

    def compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        identity: ContextIdentity,
        force_compact: bool = False,
        focus: str = "",
    ) -> tuple[list[dict[str, Any]], CompressionSnapshot | None]:
        """Apply compression if over threshold or forced.

        Args:
            messages: Message list to compress.
            identity: Context identity for compression.
            force_compact: Force compression regardless of threshold.
            focus: Focus hint for compression.

        Returns:
            Tuple of (compressed_messages, snapshot or None).
        """
        ...


@dataclass(frozen=True)
class ContextIdentity:
    """Identity for context compression operations."""

    role_id: str
    role_type: str
    goal: str
    acceptance_criteria: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    current_phase: str = "unknown"
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "role_id": self.role_id,
            "role_type": self.role_type,
            "goal": self.goal,
            "acceptance_criteria": list(self.acceptance_criteria),
            "scope": list(self.scope),
            "current_phase": self.current_phase,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class CompressionSnapshot:
    """Snapshot of a compression operation."""

    original_tokens: int
    compressed_tokens: int
    method: str  # "micro" | "truncate" | "llm" | "deterministic"
    transcript_path: str | None = None

    @property
    def reduction_ratio(self) -> float:
        """Calculate token reduction ratio."""
        if self.original_tokens <= 0:
            return 0.0
        return 1.0 - (self.compressed_tokens / self.original_tokens)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "method": self.method,
            "transcript_path": self.transcript_path,
            "reduction_ratio": self.reduction_ratio,
        }


# ----------------------------------------------------------------------
# Default implementations (fallback when no DI container is available)
# ----------------------------------------------------------------------


class DefaultTokenBudgetObserverPort:
    """Default implementation of TokenBudgetObserverPort.

    This is a no-op implementation that reports unlimited budget.
    Use when no real budget management is needed or available.

    Renamed from DefaultContextBudgetPort.
    """

    _remaining: int = 200_000  # Conservative default: 200k tokens
    _limit: int = 200_000

    def get_remaining_tokens(self) -> int:
        """Get remaining tokens (returns full budget as no-op)."""
        return self._remaining

    def get_effective_limit(self) -> int:
        """Get effective limit (returns full budget as no-op)."""
        return self._limit

    def observe_usage(self, tokens: int) -> None:
        """Record usage (no-op)."""
        self._remaining = max(0, self._remaining - tokens)

    def can_add(self, estimated_tokens: int) -> tuple[bool, str]:
        """Always returns True as no-op."""
        return True, ""


# Backward compatibility alias
DefaultContextBudgetPort = DefaultTokenBudgetObserverPort


class DefaultRoleContextCompressorPort:
    """Default implementation of RoleContextCompressorPort.

    This implementation uses the actual RoleContextCompressor from context.compaction.
    It's provided as a reference implementation that can be replaced via DI.
    """

    def __init__(self) -> None:
        self._compressor = self._load_compressor()

    def _load_compressor(self) -> Any:
        """Lazy load the actual RoleContextCompressor from context.compaction."""
        try:
            from polaris.kernelone.context.compaction import RoleContextCompressor

            return RoleContextCompressor
        except ImportError:
            return _MinimalCompressor

    def compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        identity: ContextIdentity,
        force_compact: bool = False,
        focus: str = "",
    ) -> tuple[list[dict[str, Any]], CompressionSnapshot | None]:
        """Attempt compression using the loaded compressor."""
        if self._compressor is _MinimalCompressor:
            # Return messages unchanged with minimal snapshot
            return messages, CompressionSnapshot(
                original_tokens=len(str(messages)),
                compressed_tokens=len(str(messages)),
                method="noop",
            )

        # Convert identity to RoleContextIdentity
        from polaris.kernelone.context.compaction import RoleContextIdentity

        role_identity = RoleContextIdentity(
            role_id=identity.role_id,
            role_type=identity.role_type,
            goal=identity.goal,
            acceptance_criteria=list(identity.acceptance_criteria),
            scope=list(identity.scope),
            current_phase=identity.current_phase,
            metadata=identity.metadata if identity.metadata is not None else {},  # type: ignore[arg-type]
        )

        compressor = self._compressor(
            workspace=".",
            role_name=identity.role_type,
        )

        compressed, snapshot = compressor.compact_if_needed(
            messages,
            role_identity,
            force_compact=force_compact,
            focus=focus,
        )

        if snapshot is None:
            return compressed, None

        return compressed, CompressionSnapshot(
            original_tokens=snapshot.original_tokens,
            compressed_tokens=snapshot.compressed_tokens,
            method=snapshot.method,
            transcript_path=snapshot.transcript_path,
        )


class _MinimalCompressor:
    """Minimal fallback compressor when context.compaction is not available."""

    def compact_if_needed(
        self,
        messages: list[dict[str, Any]],
        identity: Any,
        force_compact: bool = False,
        focus: str = "",
    ) -> tuple[list[dict[str, Any]], None]:
        """Return messages unchanged."""
        return messages, None


__all__ = [
    "CompressionSnapshot",
    "ContextIdentity",
    "DefaultRoleContextCompressorPort",
    "DefaultTokenBudgetObserverPort",
    "LLMBudgetObserverPort",  # Backward compat alias (unique name)
    "RoleContextCompressorPort",
    "TokenBudgetObserverPort",
]
