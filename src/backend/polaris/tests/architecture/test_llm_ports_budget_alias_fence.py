"""Architecture fence for retired LLM budget observer aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.ports as llm_ports

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LLM_PORTS_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "ports.py"
RETIRED_LLM_BUDGET_ALIAS = "".join(("LLM", "Budget", "Observer", "Port"))
RETIRED_DEFAULT_BUDGET_ALIAS = "".join(("Default", "Context", "Budget", "Port"))
RETIRED_DOC_PHRASE = " and ".join(("ContextBudgetPort", RETIRED_DEFAULT_BUDGET_ALIAS))


def test_llm_budget_observer_aliases_are_retired() -> None:
    """The LLM port boundary exposes only TokenBudgetObserverPort names."""
    assert hasattr(llm_ports, "TokenBudgetObserverPort")
    assert hasattr(llm_ports, "DefaultTokenBudgetObserverPort")
    assert not hasattr(llm_ports, RETIRED_LLM_BUDGET_ALIAS)
    assert not hasattr(llm_ports, RETIRED_DEFAULT_BUDGET_ALIAS)
    assert RETIRED_LLM_BUDGET_ALIAS not in llm_ports.__all__
    assert RETIRED_DEFAULT_BUDGET_ALIAS not in llm_ports.__all__


def test_llm_ports_source_does_not_reintroduce_budget_aliases() -> None:
    """Block second budget observer names in the LLM port module."""
    source = LLM_PORTS_SOURCE.read_text(encoding="utf-8")
    assert RETIRED_LLM_BUDGET_ALIAS not in source
    assert RETIRED_DEFAULT_BUDGET_ALIAS not in source
    assert RETIRED_DOC_PHRASE not in source
