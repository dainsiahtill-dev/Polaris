"""Architecture fence for the retired KernelOne context budget alias."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.context.contracts as context_contracts

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONTEXT_CONTRACTS = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "contracts.py"
RETIRED_ALIAS = "ContextAllocatorBudgetPort"
CANONICAL_ALIAS = "ContextBudgetAllocatorPort"


def test_context_allocator_budget_port_alias_is_retired() -> None:
    """The public context contract must expose only the canonical budget port."""
    assert not hasattr(context_contracts, RETIRED_ALIAS)
    assert RETIRED_ALIAS not in context_contracts.__all__
    assert CANONICAL_ALIAS in context_contracts.__all__


def test_context_contract_source_uses_canonical_budget_allocator_name() -> None:
    """Source-level fence blocks reintroducing the retired alias."""
    source = CONTEXT_CONTRACTS.read_text(encoding="utf-8")
    assert RETIRED_ALIAS not in source
    assert "class ContextBudgetAllocatorPort" in source
