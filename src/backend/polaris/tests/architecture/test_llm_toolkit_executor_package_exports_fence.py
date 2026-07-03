"""Architecture fence for LLM toolkit executor package-root exports."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.llm.toolkit.executor as executor_root

BACKEND_ROOT = Path(__file__).resolve().parents[3]
EXECUTOR_INIT_SOURCE = (
    BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "toolkit" / "executor" / "__init__.py"
)


def test_executor_package_root_keeps_current_public_exports() -> None:
    """The executor package root is the current public import surface."""
    expected_exports = {
        "CODE_INTELLIGENCE_AVAILABLE",
        "AgentAccelToolExecutor",
        "BudgetExceededError",
        "KernelToolCallingRuntime",
        "build_tool_feedback",
        "execute_tool_call",
        "execute_tool_calls",
    }

    assert set(executor_root.__all__) == expected_exports
    for name in expected_exports:
        assert hasattr(executor_root, name), name


def test_executor_package_root_source_does_not_claim_compat_shim_status() -> None:
    """Current package-root exports must not be documented as a retired shim."""
    source = EXECUTOR_INIT_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    assert "re-exports from core" not in source
