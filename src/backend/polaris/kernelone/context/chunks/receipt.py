"""Final Request Debug Receipt for KernelOne prompt assembly.

Architecture:
    FinalRequestReceipt captures the complete state of a prompt assembly pass
    for debugging, observability, and audit purposes. It is emitted once
    at the end of assembly, containing only final state (no intermediate states).

Design constraints:
    - All text uses UTF-8 encoding.
    - Receipt is immutable after construction.
    - Only contains final state, not intermediate states.
    - Includes all strategy/decision metadata for reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .taxonomy import PromptChunk


@dataclass(frozen=True)
class ChunkTokenStats:
    """Per-chunk-type token statistics."""

    chunk_type: str
    token_count: int
    char_count: int
    chunk_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_type": self.chunk_type,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "chunk_count": self.chunk_count,
        }


@dataclass(frozen=True)
class CompressionDecision:
    """Record of a compression/eviction decision."""

    chunk_type: str
    reason: str
    tokens_freed: int
    method: str  # "evicted" | "truncated" | "summarized"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_type": self.chunk_type,
            "reason": self.reason,
            "tokens_freed": self.tokens_freed,
            "method": self.method,
        }


@dataclass(frozen=True)
class ContinuityDecision:
    """Record of session continuity decisions."""

    enabled: bool
    summary_tokens: int
    summary_hash: str
    source_messages: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "summary_tokens": self.summary_tokens,
            "summary_hash": self.summary_hash,
            "source_messages": self.source_messages,
        }


@dataclass(frozen=True)
class StrategyMetadata:
    """Strategy decisions that affected assembly."""

    profile_id: str
    profile_hash: str
    strategy_bundle_hash: str
    continuity_policy_id: str
    compaction_policy_id: str
    domain: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "strategy_bundle_hash": self.strategy_bundle_hash,
            "continuity_policy_id": self.continuity_policy_id,
            "compaction_policy_id": self.compaction_policy_id,
            "domain": self.domain,
        }


@dataclass(frozen=True)
class ContextOSReceipt:
    """Compact receipt view of the final Context OS prompt projection."""

    adapter_id: str = ""
    current_goal: str = ""
    next_action_hint: str = ""
    pressure_level: str = ""
    hard_constraint_count: int = 0
    open_loop_count: int = 0
    active_entity_count: int = 0
    active_artifact_count: int = 0
    episode_count: int = 0
    included_count: int = 0
    excluded_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "current_goal": self.current_goal,
            "next_action_hint": self.next_action_hint,
            "pressure_level": self.pressure_level,
            "hard_constraint_count": self.hard_constraint_count,
            "open_loop_count": self.open_loop_count,
            "active_entity_count": self.active_entity_count,
            "active_artifact_count": self.active_artifact_count,
            "episode_count": self.episode_count,
            "included_count": self.included_count,
            "excluded_count": self.excluded_count,
        }


@dataclass(frozen=True)
class FinalRequestReceipt:
    """Immutable receipt of a completed prompt assembly pass.

    This is the canonical debug artifact for understanding exactly what
    was sent to the LLM. It contains only final state, not intermediate states.

    Usage::

        receipt = FinalRequestReceipt.build(
            chunks=admitted_chunks,
            model="claude-opus-4-5",
            provider="anthropic",
            strategy_metadata=StrategyMetadata(...),
        )
        print(receipt.to_human_readable())
    """

    # Identity
    receipt_id: str
    timestamp: str  # ISO 8601 UTC

    # Model info
    model: str
    provider: str
    model_window: int
    effective_limit: int

    # Content stats
    total_tokens: int
    total_chars: int
    chunk_count: int

    # Per-type breakdown
    token_breakdown: tuple[ChunkTokenStats, ...]
    eviction_summary: tuple[CompressionDecision, ...]

    # Continuity
    continuity: ContinuityDecision | None
    context_os: ContextOSReceipt | None

    # Strategy
    strategy: StrategyMetadata | None

    # Assembly metadata
    assembly_start: str  # ISO 8601 UTC
    assembly_duration_ms: int

    # Provenance
    role_id: str
    session_id: str
    turn_index: int

    # Cache control applied (T6-5 fix)
    cache_control_applied: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "receipt_id": self.receipt_id,
            "timestamp": self.timestamp,
            "model": {
                "model": self.model,
                "provider": self.provider,
                "model_window": self.model_window,
                "effective_limit": self.effective_limit,
            },
            "content": {
                "total_tokens": self.total_tokens,
                "total_chars": self.total_chars,
                "chunk_count": self.chunk_count,
            },
            "token_breakdown": [s.to_dict() for s in self.token_breakdown],
            "eviction_summary": [e.to_dict() for e in self.eviction_summary],
            "continuity": self.continuity.to_dict() if self.continuity else None,
            "context_os": self.context_os.to_dict() if self.context_os else None,
            "strategy": self.strategy.to_dict() if self.strategy else None,
            "assembly": {
                "start": self.assembly_start,
                "duration_ms": self.assembly_duration_ms,
                "cache_control_applied": list(self.cache_control_applied),
            },
            "provenance": {
                "role_id": self.role_id,
                "session_id": self.session_id,
                "turn_index": self.turn_index,
            },
        }

    def to_human_readable(self) -> str:
        """Format receipt as human-readable text."""
        lines = [
            "=" * 60,
            "FINAL REQUEST RECEIPT",
            "=" * 60,
            f"Receipt ID: {self.receipt_id}",
            f"Timestamp: {self.timestamp}",
            "",
            "--- Model ---",
            f"  Model: {self.model}",
            f"  Provider: {self.provider}",
            f"  Window: {self.model_window:,} tokens",
            f"  Effective Limit: {self.effective_limit:,} tokens",
            "",
            "--- Content Stats ---",
            f"  Total Tokens: {self.total_tokens:,}",
            f"  Total Chars: {self.total_chars:,}",
            f"  Chunk Count: {self.chunk_count}",
            f"  Usage Ratio: {self.total_tokens / self.effective_limit * 100:.1f}%",
            "",
            "--- Token Breakdown ---",
        ]

        for stat in self.token_breakdown:
            if stat.token_count > 0:
                pct = stat.token_count / self.total_tokens * 100 if self.total_tokens > 0 else 0
                lines.append(
                    f"  {stat.chunk_type:20} {stat.token_count:6,} tokens ({pct:5.1f}%) "
                    f"[{stat.chunk_count} chunks, {stat.char_count:,} chars]"
                )

        if self.eviction_summary:
            lines.extend(
                [
                    "",
                    "--- Evictions ---",
                ]
            )
            for ev in self.eviction_summary:
                lines.append(f"  {ev.chunk_type}: {ev.reason} ({ev.tokens_freed} tokens freed via {ev.method})")

        if self.continuity:
            lines.extend(
                [
                    "",
                    "--- Continuity ---",
                    f"  Enabled: {self.continuity.enabled}",
                    f"  Summary Tokens: {self.continuity.summary_tokens:,}",
                    f"  Source Messages: {self.continuity.source_messages}",
                ]
            )

        if self.context_os:
            lines.extend(
                [
                    "",
                    "--- Context OS ---",
                    f"  Adapter: {self.context_os.adapter_id or '(unset)'}",
                    f"  Goal: {self.context_os.current_goal or '(none)'}",
                    f"  Next Action: {self.context_os.next_action_hint or '(none)'}",
                    f"  Pressure: {self.context_os.pressure_level or '(none)'}",
                    (
                        "  Counts: "
                        f"constraints={self.context_os.hard_constraint_count}, "
                        f"loops={self.context_os.open_loop_count}, "
                        f"entities={self.context_os.active_entity_count}, "
                        f"artifacts={self.context_os.active_artifact_count}, "
                        f"episodes={self.context_os.episode_count}, "
                        f"included={self.context_os.included_count}, "
                        f"excluded={self.context_os.excluded_count}"
                    ),
                ]
            )

        if self.strategy:
            lines.extend(
                [
                    "",
                    "--- Strategy ---",
                    f"  Profile: {self.strategy.profile_id} ({self.strategy.profile_hash[:8]})",
                    f"  Bundle: {self.strategy.strategy_bundle_hash[:8]}",
                ]
            )

        lines.extend(
            [
                "",
                "--- Provenance ---",
                f"  Role: {self.role_id}",
                f"  Session: {self.session_id}",
                f"  Turn: {self.turn_index}",
                "",
                "--- Assembly ---",
                f"  Duration: {self.assembly_duration_ms}ms",
                "=" * 60,
            ]
        )

        return "\n".join(lines)

    @classmethod
    def build(
        cls,
        chunks: list[PromptChunk],
        model: str,
        provider: str,
        model_window: int,
        safety_margin: float,
        role_id: str = "",
        session_id: str = "",
        turn_index: int = 0,
        continuity: ContinuityDecision | None = None,
        context_os: ContextOSReceipt | None = None,
        strategy: StrategyMetadata | None = None,
        eviction_decisions: list[CompressionDecision] | None = None,
        assembly_start: str | None = None,
        assembly_duration_ms: int = 0,
        cache_control_applied: list[str] | None = None,
    ) -> FinalRequestReceipt:
        """Build a receipt from assembled chunks.

        Args:
            chunks: Admitted chunks (final state)
            model: Model name
            provider: Provider name
            model_window: Model context window
            safety_margin: Safety margin applied
            role_id: Role identifier
            session_id: Session identifier
            turn_index: Turn index
            continuity: Continuity decision (if any)
            strategy: Strategy metadata (if any)
            eviction_decisions: Eviction decisions made
            assembly_start: ISO timestamp when assembly started
            assembly_duration_ms: Assembly duration in milliseconds
            cache_control_applied: List of chunk types that had cache control applied

        Returns:
            Immutable FinalRequestReceipt
        """
        import hashlib

        # Compute stats
        total_tokens = sum(c.tokens for c in chunks)
        total_chars = sum(c.chars for c in chunks)
        effective_limit = int(model_window * safety_margin)

        # Per-type breakdown
        breakdown_map: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            ct = chunk.chunk_type.value
            if ct not in breakdown_map:
                breakdown_map[ct] = {"tokens": 0, "chars": 0, "count": 0}
            breakdown_map[ct]["tokens"] += chunk.tokens
            breakdown_map[ct]["chars"] += chunk.chars
            breakdown_map[ct]["count"] += 1

        token_breakdown = tuple(
            ChunkTokenStats(
                chunk_type=ct,
                token_count=data["tokens"],
                char_count=data["chars"],
                chunk_count=data["count"],
            )
            for ct, data in sorted(breakdown_map.items())
        )

        # Eviction summary
        eviction_summary = tuple(eviction_decisions or [])

        # Generate receipt ID
        receipt_content = f"{model}:{provider}:{total_tokens}:{len(chunks)}:{datetime.now(timezone.utc).isoformat()}"
        receipt_id = hashlib.sha256(receipt_content.encode("utf-8")).hexdigest()[:16]

        # Timestamps
        timestamp = datetime.now(timezone.utc).isoformat()
        assembly_start_ts = assembly_start or timestamp

        return cls(
            receipt_id=receipt_id,
            timestamp=timestamp,
            model=model,
            provider=provider,
            model_window=model_window,
            effective_limit=effective_limit,
            total_tokens=total_tokens,
            total_chars=total_chars,
            chunk_count=len(chunks),
            token_breakdown=token_breakdown,
            eviction_summary=eviction_summary,
            continuity=continuity,
            context_os=context_os,
            strategy=strategy,
            assembly_start=assembly_start_ts,
            assembly_duration_ms=assembly_duration_ms,
            cache_control_applied=tuple(cache_control_applied or []),
            role_id=role_id,
            session_id=session_id,
            turn_index=turn_index,
        )


__all__ = [
    "ChunkTokenStats",
    "CompressionDecision",
    "ContextOSReceipt",
    "ContinuityDecision",
    "FinalRequestReceipt",
    "StrategyMetadata",
]
