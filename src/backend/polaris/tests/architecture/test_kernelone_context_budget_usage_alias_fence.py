"""Architecture fence for retired KernelOne context budget usage aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.context as context_root
import polaris.kernelone.context.budget_gate as budget_gate

BACKEND_ROOT = Path(__file__).resolve().parents[3]
BUDGET_GATE_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "budget_gate.py"
CONTEXT_INIT = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "__init__.py"


def test_context_budget_usage_alias_is_retired() -> None:
    """budget_gate exports ContextBudgetUsage, not the old ContextBudget alias."""
    assert hasattr(budget_gate, "ContextBudgetUsage")
    assert not hasattr(budget_gate, "ContextBudget")
    assert "ContextBudget" not in budget_gate.__all__
    assert not hasattr(budget_gate, "DEFAULT_FALLBACK_WINDOW")
    assert "DEFAULT_FALLBACK_WINDOW" not in budget_gate.__all__

    assert hasattr(context_root, "ContextBudgetUsage")
    assert not hasattr(context_root, "ContextBudget")
    assert "ContextBudget" not in context_root.__all__
    assert not hasattr(context_root, "DEFAULT_FALLBACK_WINDOW")
    assert "DEFAULT_FALLBACK_WINDOW" not in context_root.__all__


def test_budget_gate_sources_do_not_reintroduce_context_budget_alias() -> None:
    """Source-level fence blocks the old runtime usage alias."""
    for path in (BUDGET_GATE_MODULE, CONTEXT_INIT):
        source = path.read_text(encoding="utf-8")
        assert "ContextBudget = ContextBudgetUsage" not in source
        assert "DEFAULT_FALLBACK_WINDOW" not in source
