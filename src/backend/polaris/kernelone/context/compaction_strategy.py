"""Compaction Strategy — profile-driven context compaction decisions.

WS3: Shared Agent Foundation Convergence.
Converges budget-triggered compaction decisions into a unified strategy layer.

This module provides:
  - CompactionStrategy: implements CompactionStrategyPort; wraps ContextBudgetGate
    with profile-driven thresholds and delegates to RoleContextCompressor.

Key design decisions:
  - should_compact() uses profile override `trigger_at_budget_pct`
  - compact() delegates to RoleContextCompressor (no LLM dependency)
  - Deterministic fallback preserved

Note: CompactionStrategy delegates to RoleContextCompressor for actual compression.
These two should be unified in a future iteration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.context.budget_gate import ContextBudget

from polaris.kernelone.context.compaction import (
    RoleContextCompressor,
    RoleContextIdentity,
)

from .strategy_contracts import (
    CompactionDecision,
    CompactionResult,
)

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# CompactionStrategy
# ------------------------------------------------------------------


class CompactionStrategy:
    """Profile-driven compaction strategy.

    Implements CompactionStrategyPort.

    Decision logic:
      - should_compact: triggers when (used_tokens / max_tokens) >= trigger_pct
        (from profile overrides, default 0.80 for canonical_balanced)
      - compact: delegates to RoleContextCompressor (deterministic fallback preserved)

    Profile overrides used (from strategy_profiles.py):
      - compaction.trigger_at_budget_pct: float (default 0.80)
      - compaction.receipt_micro_compact: bool (default True)
      - compaction.receipt_compact_threshold: int (default 3)

    Usage::

        strategy = CompactionStrategy(profile_overrides=profile.overrides)
        decision = strategy.should_compact(budget=ctx_budget, history_size=20)
        if decision == CompactionDecision.TRIGGER:
            result = strategy.compact(history=messages)
    """

    def __init__(
        self,
        profile_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._profile_overrides = dict(profile_overrides or {})
        compaction_cfg = self._profile_overrides.get("compaction", {})

        # Budget percentage at which compaction is triggered
        self._trigger_pct: float = float(compaction_cfg.get("trigger_at_budget_pct", 0.80))

        # Micro-compact configuration (passed to RoleContextCompressor)
        self._micro_compact_keep: int = int(compaction_cfg.get("micro_compact_keep", 3))
        self._receipt_micro_compact: bool = bool(compaction_cfg.get("receipt_micro_compact", True))

        # Truncation fallback: max messages to keep when compact() is called
        self._truncate_to_messages: int = int(compaction_cfg.get("truncate_to_messages", 8))

        # Lazy compressor (requires workspace; set on first compact() call)
        self._compressor: RoleContextCompressor | None = None
        self._workspace: str = ""

    def _get_compressor(
        self,
        workspace: str,
        role_name: str = "StrategyCompaction",
    ) -> RoleContextCompressor:
        """Lazily create a RoleContextCompressor."""
        if self._compressor is None or self._workspace != workspace:
            self._workspace = workspace
            config = {
                "micro_compact_keep": self._micro_compact_keep,
            }
            self._compressor = RoleContextCompressor(
                workspace=workspace,
                role_name=role_name,
                config=config,
            )
        return self._compressor

    # ------------------------------------------------------------------
    # CompactionStrategyPort
    # ------------------------------------------------------------------

    def should_compact(
        self,
        budget: ContextBudget,
        history_size: int,
    ) -> CompactionDecision:
        """Decide whether compaction should be triggered.

        Implements CompactionStrategyPort.should_compact().

        Args:
            budget: Current budget snapshot (from ContextBudgetGate).
            history_size: Approximate number of history messages.

        Returns:
            CompactionDecision: TRIGGER / DEFER / NONE.
        """
        if budget.effective_limit <= 0:
            _logger.debug("should_compact: effective_limit=%d <= 0 → TRIGGER", budget.effective_limit)
            return CompactionDecision.TRIGGER

        current_pct = budget.current_tokens / budget.effective_limit

        if current_pct >= self._trigger_pct:
            _logger.debug(
                "should_compact: %.0f%% >= %.0f%% threshold → TRIGGER",
                current_pct * 100,
                self._trigger_pct * 100,
            )
            return CompactionDecision.TRIGGER

        # Micro-compact at 75% of main threshold if receipt micro-compact is enabled
        micro_threshold = self._trigger_pct * 0.75
        if current_pct >= micro_threshold and self._receipt_micro_compact:
            _logger.debug(
                "should_compact: %.0f%% >= %.0f%% micro-threshold → DEFER",
                current_pct * 100,
                micro_threshold * 100,
            )
            return CompactionDecision.DEFER

        _logger.debug(
            "should_compact: %.0f%% < %.0f%% (%.0f%% micro) → NONE",
            current_pct * 100,
            self._trigger_pct * 100,
            micro_threshold * 100,
        )
        return CompactionDecision.NONE

    def compact(
        self,
        history: list[dict[str, Any]],
        *,
        identity: RoleContextIdentity | None = None,
        focus: str = "",
        workspace: str = "",
    ) -> CompactionResult:
        """Apply compaction to the history.

        Implements CompactionStrategyPort.compact().

        Pipeline:
          1. Always apply micro-compact first (RoleContextCompressor.micro_compact)
          2. Estimate token count
          3. If still over budget, apply truncation fallback
          4. Return CompactionResult with counts

        Args:
            history: Message history to compact.
            identity: Optional RoleContextIdentity for compressor.
            focus: Optional focus string for summary.
            workspace: Workspace path (required for compressor initialization).

        Returns:
            CompactionResult with triggered flag, counts, and summary.
        """
        if not history:
            return CompactionResult(
                triggered=False,
                compacted_items=0,
                tokens_recovered=0,
                summary="No history to compact.",
            )

        original_count = len(history)
        original_tokens = _estimate_history_tokens(history)

        # Step 1: Micro-compact
        compressor: RoleContextCompressor | None = None
        if workspace:
            compressor = self._get_compressor(workspace)

        compacted = list(history)
        micro_compacted_count = 0

        if compressor is not None:
            before = len(compacted)
            compacted = compressor.micro_compact(compacted)
            micro_compacted_count = max(0, before - len(compacted))

        post_micro_tokens = _estimate_history_tokens(compacted)

        # Step 2: Truncation fallback if still large
        avg_per_msg = max(1, original_tokens / max(1, original_count))
        estimated_max = int(avg_per_msg * self._truncate_to_messages * 1.25)

        truncated_count = 0
        if post_micro_tokens > estimated_max and self._truncate_to_messages > 0:
            truncate_at = max(1, self._truncate_to_messages)
            if len(compacted) > truncate_at:
                compacted = compacted[-truncate_at:]
                truncated_count = max(0, len(history) - truncate_at)

        final_tokens = _estimate_history_tokens(compacted)
        tokens_recovered = max(0, original_tokens - final_tokens)
        compacted_items = micro_compacted_count + truncated_count

        # Step 3: Build summary
        if compacted_items == 0:
            method = "none"
        elif truncated_count > 0:
            method = "micro+truncate"
        else:
            method = "micro"

        summary_parts = [
            f"CompactionStrategy [{method}].",
            f"Original: {original_count} msgs / ~{original_tokens} tokens.",
            f"Final: {len(compacted)} msgs / ~{final_tokens} tokens.",
            f"Recovered: ~{tokens_recovered} tokens.",
        ]
        if micro_compacted_count > 0:
            summary_parts.append(f"Micro-compacted {micro_compacted_count} receipts.")
        if truncated_count > 0:
            summary_parts.append(f"Truncated {truncated_count} older messages.")

        _logger.debug("compact: %s", " ".join(summary_parts))

        return CompactionResult(
            triggered=compacted_items > 0,
            compacted_items=compacted_items,
            tokens_recovered=tokens_recovered,
            summary=" ".join(summary_parts),
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def trigger_pct(self) -> float:
        """Current trigger threshold (for diagnostics)."""
        return self._trigger_pct


# ------------------------------------------------------------------
# Token estimation helpers
# ------------------------------------------------------------------


def _estimate_history_tokens(history: list[dict[str, Any]]) -> int:
    """Rough deterministic token estimate for a message history.

    Mirrors the estimation in RoleContextGateway and history_materialization.
    """
    if not history:
        return 0
    total = 0
    for item in history:
        content = str(item.get("content") or item.get("message") or "")
        if not content:
            total += 4
            continue
        ascii_chars = sum(1 for c in content if ord(c) < 128)
        cjk_chars = len(content) - ascii_chars
        total += int(ascii_chars / 4) + int(cjk_chars * 1.5) + 4
    return max(1, total)


__all__ = [
    "CompactionStrategy",
]
