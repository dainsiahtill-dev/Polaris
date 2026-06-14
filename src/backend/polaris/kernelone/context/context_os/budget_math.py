"""Canonical active-window token-budget math (ContextOS single source of truth).

ONE implementation of the small-window active-window budget, shared by the live
pipeline ``WindowCollector``, the ``AttentionAwareWindowCollector``, and the
introspection ``_ContextOSSchedulerMixin``. Before this module the math was
duplicated inline in three places and the small-window protection (I3-r16/r17)
was patched into only one of them (the introspection path), so the live
projection silently kept the large-window formula. Centralising it here makes
that divergence structurally impossible — every active-window consumer routes
through ``active_window_token_budget``.

See docs/blueprints/REASONING_BUDGET_SSOT_ARCHITECTURE_BLUEPRINT_20260613.md (DEFECT 1).
"""

from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default


# Below this context window a model is treated as "small/local": its active
# window (the mandatory root task event) must not be starved by the default
# active-window ratio tuned for large cloud windows (live I3-r16). Overridable.
SMALL_CONTEXT_WINDOW_TOKENS = _env_int("KERNELONE_CONTEXT_OS_SMALL_WINDOW_TOKENS", 32_000)
SMALL_WINDOW_ACTIVE_RATIO = _env_float("KERNELONE_CONTEXT_OS_SMALL_WINDOW_ACTIVE_RATIO", 0.75)


def active_window_token_budget(
    *,
    model_context_window: int,
    input_budget: int,
    soft_limit: int,
    hard_limit: int,
    base_ratio: float,
) -> int:
    """Token budget for the active window (root task event + recent turns).

    On a small/local window the active window IS the work, so the mandatory root
    event must not be starved by the large-window default ratio — give it the
    higher ratio and the looser hard-limit cap (live I3-r16: a 16k qwen got
    ~4262 < a ~4330-token task prompt → root truncated → empty output). Large
    windows keep the conservative ratio + soft-limit cap unchanged.

    ``model_context_window`` MUST be the resolved provider window (carried on
    ``BudgetPlan.model_context_window``), not the policy default (DEFECT 1).
    """
    ratio = base_ratio
    cap = soft_limit
    if model_context_window <= SMALL_CONTEXT_WINDOW_TOKENS:
        ratio = max(base_ratio, SMALL_WINDOW_ACTIVE_RATIO)
        cap = hard_limit
    return max(512, min(cap, int(input_budget * ratio)))
