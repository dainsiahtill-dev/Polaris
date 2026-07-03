"""Architecture fence for prompt-budget compressor fallback terminology."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROMPT_BUDGET_SOURCE = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "engine" / "prompt_budget.py"


def test_prompt_budget_fallback_is_not_described_as_backward_compatibility() -> None:
    """The lazy compressor import is an active DI fallback, not a legacy alias path."""
    source = PROMPT_BUDGET_SOURCE.read_text(encoding="utf-8")
    forbidden = " ".join(("fallback", "for", "backward", "compatibility"))
    assert forbidden not in source.lower()
