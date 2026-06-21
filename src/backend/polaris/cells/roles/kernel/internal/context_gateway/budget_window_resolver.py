"""Strategy/window/budget resolution helpers for :class:`RoleContextGateway`.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 6). The gateway keeps
delegating shims (``_compute_enforcement_budget`` /
``_extract_strategy_override`` / ``_effective_recent_window_messages`` /
``_context_budget_trigger_pct``) with identical signatures — the budget math and
window scaling are load-bearing (ADR-0090 I4.1, documented live incidents).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

from .gateway_helpers import _coerce_float, _deep_merge_strategy_payload

logger = logging.getLogger(__name__)


class BudgetWindowResolver:
    """Computes the enforcement budget and recent-window sizing for one gateway.

    Holds the gateway's ``policy`` plus a reference to its ``context_os`` (only
    consulted for the resolved model window). The static-style strategy parsing
    helpers are instance methods here so the gateway can route every call through
    a single collaborator.
    """

    _MODEL_WINDOW_SAFETY_RATIO = 0.85
    _MIN_ENFORCEMENT_BUDGET_TOKENS = 1024

    def __init__(self, *, policy: Any, context_os: Any) -> None:
        self._policy = policy
        self._context_os = context_os

    def compute_enforcement_budget(self) -> int:
        """ADR-0090 I4.1: clamp the role budget to the resolved model window."""
        policy_budget = int(self._policy.max_context_tokens)
        try:
            resolved_window = int(self._context_os.resolved_context_window)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "context window resolution failed; using role policy budget %d: %s",
                policy_budget,
                exc,
            )
            return policy_budget
        if resolved_window <= 0:
            return policy_budget
        clamped = min(policy_budget, int(resolved_window * self._MODEL_WINDOW_SAFETY_RATIO))
        # The floor protects against absurdly small windows but must never RAISE
        # the budget above the role policy.
        floor = min(self._MIN_ENFORCEMENT_BUDGET_TOKENS, policy_budget)
        return max(floor, clamped)

    def extract_strategy_override(self, request: ContextRequest) -> tuple[dict[str, Any], tuple[str, ...]]:
        merged: dict[str, Any] = {}
        sources: list[str] = []

        value = getattr(request, "strategy_override", None)
        if isinstance(value, Mapping):
            _deep_merge_strategy_payload(merged, value)
            sources.append("request.strategy_override")

        context_override = getattr(request, "context_override", None)
        if isinstance(context_override, Mapping):
            for key in ("strategy_override", "cognitive_strategy_override"):
                nested = context_override.get(key)
                if isinstance(nested, Mapping):
                    _deep_merge_strategy_payload(merged, nested)
                    sources.append(f"context_override.{key}")

            metadata = context_override.get("metadata")
            if isinstance(metadata, Mapping):
                for key in ("strategy_override", "cognitive_strategy_override"):
                    nested = metadata.get(key)
                    if isinstance(nested, Mapping):
                        _deep_merge_strategy_payload(merged, nested)
                        sources.append(f"context_override.metadata.{key}")

        return merged, tuple(sources)

    def effective_recent_window_messages(self, strategy_override: Mapping[str, Any]) -> int:
        base_window = max(1, int(self._policy.max_history_turns or 1))
        if not strategy_override:
            return base_window

        exploration = strategy_override.get("exploration")
        exploration_payload = exploration if isinstance(exploration, Mapping) else {}
        depth = _coerce_float(exploration_payload.get("max_expansion_depth"))
        aggressive = bool(exploration_payload.get("neighbor_expansion_aggressive"))

        read_escalation = strategy_override.get("read_escalation")
        read_payload = read_escalation if isinstance(read_escalation, Mapping) else {}
        full_read_allowed = bool(read_payload.get("full_read_allowed"))

        cognitive_runtime = strategy_override.get("cognitive_runtime")
        cognitive_payload = cognitive_runtime if isinstance(cognitive_runtime, Mapping) else {}
        cognitive_applied = bool(cognitive_payload.get("applied"))

        requested = base_window
        if depth is not None and depth >= 4:
            requested = max(requested, base_window * 2)
        elif depth is not None and depth >= 3:
            requested = max(requested, base_window + max(2, base_window // 2))
        if aggressive:
            requested = max(requested, base_window + 4)
        if full_read_allowed:
            requested = max(requested, base_window + 2)
        if cognitive_applied:
            requested = max(requested, base_window + 2)

        return min(max(base_window, requested), max(base_window, 32))

    @staticmethod
    def context_budget_trigger_pct(strategy_override: Mapping[str, Any]) -> float:
        compaction = strategy_override.get("compaction") if strategy_override else None
        compaction_payload = compaction if isinstance(compaction, Mapping) else {}
        ratio = _coerce_float(compaction_payload.get("trigger_at_budget_pct"))
        if ratio is None:
            return 1.0
        return min(1.0, max(0.1, ratio))
