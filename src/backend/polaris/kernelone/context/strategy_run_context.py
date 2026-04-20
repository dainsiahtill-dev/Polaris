"""Strategy run context for one resolved strategy turn.

This module owns the immutable run identity used by the strategy framework
and the per-turn accumulators that later become a strategy receipt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

from .strategy_contracts import (
    BudgetDecision,
    ExplorationPhase,
    ReadEscalation,
    ResolvedStrategy,
    StrategyReceipt,
)

if TYPE_CHECKING:
    from .budget_gate import ContextBudget


@dataclass(frozen=True, kw_only=True)
class StrategyRunContext:
    """Immutable per-turn strategy run context."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_index: int = 0
    bundle_id: str = "kernelone.default.v1"
    bundle_version: str = "1.0.0"
    profile_id: str = "canonical_balanced"
    profile_hash: str = "none"
    domain: str = "code"
    role: str | None = None
    session_id: str | None = None
    workspace: str = ""
    started_at: str = field(default_factory=_utc_now_iso)
    ended_at: str = ""
    budget: ContextBudget | None = None
    resolved_overrides: dict[str, Any] = field(default_factory=dict)

    _budget_decisions: list[BudgetDecision] = field(default_factory=list, repr=False)
    _read_escalations: list[ReadEscalation] = field(default_factory=list, repr=False)
    _tool_sequence: list[str] = field(default_factory=list, repr=False)
    _cache_hits: list[str] = field(default_factory=list, repr=False)
    _cache_misses: list[str] = field(default_factory=list, repr=False)
    _compaction_triggered: bool = False
    _compaction_result: str = ""
    _exploration_phase_reached: str = ExplorationPhase.MAP.value

    @classmethod
    def from_resolved(
        cls,
        resolved: ResolvedStrategy,
        *,
        turn_index: int = 0,
        session_id: str | None = None,
        workspace: str = "",
        role: str | None = None,
        domain: str = "code",
        budget: ContextBudget | None = None,
    ) -> StrategyRunContext:
        """Build a run context from a resolved strategy."""

        return cls(
            turn_index=int(turn_index),
            bundle_id=str(resolved.bundle.bundle_id),
            bundle_version=str(resolved.bundle.bundle_version),
            profile_id=str(resolved.profile.profile_id),
            profile_hash=str(resolved.profile_hash),
            domain=str(domain or "code"),
            role=str(role or "").strip() or None,
            session_id=str(session_id or "").strip() or None,
            workspace=str(workspace or ""),
            budget=budget,
            resolved_overrides=dict(resolved.overrides_applied or resolved.profile.overrides or {}),
        )

    def record_budget_decision(self, decision: BudgetDecision) -> None:
        decisions = list(self._budget_decisions)
        decisions.append(decision)
        object.__setattr__(self, "_budget_decisions", decisions)

    def record_read_escalation(self, escalation: ReadEscalation) -> None:
        escalations = list(self._read_escalations)
        escalations.append(escalation)
        object.__setattr__(self, "_read_escalations", escalations)

    def record_tool(self, tool_name: str) -> None:
        tools = list(self._tool_sequence)
        tools.append(str(tool_name or ""))
        object.__setattr__(self, "_tool_sequence", tools)

    def record_cache_hit(self, asset_key: str) -> None:
        hits = list(self._cache_hits)
        hits.append(str(asset_key or ""))
        object.__setattr__(self, "_cache_hits", hits)

    def record_cache_miss(self, asset_key: str) -> None:
        misses = list(self._cache_misses)
        misses.append(str(asset_key or ""))
        object.__setattr__(self, "_cache_misses", misses)

    def record_compaction(self, triggered: bool, result: str = "") -> None:
        object.__setattr__(self, "_compaction_triggered", bool(triggered))
        object.__setattr__(self, "_compaction_result", str(result or ""))

    def record_exploration_phase(self, phase: ExplorationPhase) -> None:
        object.__setattr__(self, "_exploration_phase_reached", phase.value)

    def with_tool_call(self, tool_name: str) -> StrategyRunContext:
        return replace(
            self,
            _tool_sequence=[*self._tool_sequence, str(tool_name or "")],
        )

    def with_cache_hit(self, asset_key: str) -> StrategyRunContext:
        return replace(
            self,
            _cache_hits=[*self._cache_hits, str(asset_key or "")],
        )

    def with_cache_miss(self, asset_key: str) -> StrategyRunContext:
        return replace(
            self,
            _cache_misses=[*self._cache_misses, str(asset_key or "")],
        )

    def with_compaction_triggered(
        self,
        triggered: bool,
        result: str = "",
    ) -> StrategyRunContext:
        return replace(
            self,
            _compaction_triggered=bool(triggered),
            _compaction_result=str(result or ""),
        )

    def with_phase(self, phase: ExplorationPhase) -> StrategyRunContext:
        return replace(self, _exploration_phase_reached=phase.value)

    def mark_ended(self) -> StrategyRunContext:
        return replace(self, ended_at=_utc_now_iso())

    @property
    def tool_sequence(self) -> tuple[str, ...]:
        return tuple(self._tool_sequence)

    @property
    def cache_hits(self) -> tuple[str, ...]:
        return tuple(self._cache_hits)

    @property
    def cache_misses(self) -> tuple[str, ...]:
        return tuple(self._cache_misses)

    @property
    def exploration_phase_reached(self) -> str:
        return self._exploration_phase_reached

    @property
    def strategy_identity(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "turn_index": str(self.turn_index),
            "domain": self.domain,
            "role": self.role or "",
        }

    def emit_receipt(self, emitter: Any | None = None) -> StrategyReceipt:
        # Token estimate: prefer budget.current_tokens, fallback to ContextOS BudgetPlan
        prompt_tokens_estimate = 0
        if self.budget is not None and self.budget.current_tokens > 0:
            prompt_tokens_estimate = int(self.budget.current_tokens)
        else:
            # Fallback: extract from ContextOS BudgetPlan in resolved_overrides
            ctx_os = self.resolved_overrides.get("context_os_snapshot")
            if isinstance(ctx_os, dict):
                budget_plan = ctx_os.get("budget_plan")
                if isinstance(budget_plan, dict):
                    cit = budget_plan.get("current_input_tokens")
                    if isinstance(cit, (int, float)) and int(cit) > 0:
                        prompt_tokens_estimate = int(cit)

        receipt = StrategyReceipt(
            bundle_id=self.bundle_id,
            bundle_version=self.bundle_version,
            profile_id=self.profile_id,
            profile_hash=self.profile_hash,
            turn_index=self.turn_index,
            timestamp=_utc_now_iso(),
            budget_decisions=tuple(self._budget_decisions),
            read_escalations=tuple(self._read_escalations),
            compaction_triggered=bool(self._compaction_triggered),
            tool_sequence=tuple(self._tool_sequence),
            prompt_tokens_estimate=prompt_tokens_estimate,
            exploration_phase_reached=self._exploration_phase_reached,
            cache_hits=tuple(self._cache_hits),
            cache_misses=tuple(self._cache_misses),
            run_id=self.run_id,
            session_id=self.session_id or "",
            workspace=self.workspace,
        )
        if emitter is not None:
            emitter.write_receipt(receipt)
        return receipt

    def to_dict(self) -> dict[str, Any]:
        budget_dict: dict[str, Any] | None = None
        if self.budget is not None:
            budget_dict = {
                "model_window": int(self.budget.model_window),
                "safety_margin": float(self.budget.safety_margin),
                "current_tokens": int(self.budget.current_tokens),
                "effective_limit": int(self.budget.effective_limit),
                "headroom": int(self.budget.headroom),
                "usage_ratio": float(self.budget.usage_ratio),
            }
        return {
            "run_id": self.run_id,
            "turn_index": self.turn_index,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "profile_id": self.profile_id,
            "profile_hash": self.profile_hash,
            "domain": self.domain,
            "role": self.role or "",
            "session_id": self.session_id or "",
            "workspace": self.workspace,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "budget": budget_dict,
            "resolved_overrides": dict(self.resolved_overrides),
            "tool_sequence": list(self._tool_sequence),
            "cache_hits": list(self._cache_hits),
            "cache_misses": list(self._cache_misses),
            "compaction_triggered": bool(self._compaction_triggered),
            "compaction_result": self._compaction_result,
            "exploration_phase_reached": self._exploration_phase_reached,
        }

    def __repr__(self) -> str:
        return (
            f"StrategyRunContext(run_id={self.run_id[:8]}, "
            f"profile={self.profile_id}, "
            f"turn={self.turn_index}, "
            f"phase={self._exploration_phase_reached})"
        )


__all__ = ["StrategyRunContext"]
