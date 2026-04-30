"""Tests for CELL_KERNELONE_06 governance rule.

Verifies that budget infrastructure uses kernelone.context.budget_gate as canonical source.

Rule ID: CELL_KERNELONE_06
Severity: high
Description:
    Budget infrastructure must use kernelone.context.budget_gate as canonical source.
    TokenBudget in roles.kernel must delegate to ContextBudgetGate.

Evidence:
    - docs/blueprints/CELLS_KERNELONE_INTEGRATION_BLUEPRINT_20260403.md
    - polaris/kernelone/context/budget_gate.py
    - polaris/kernelone/context/__init__.py

Compliance:
    1. ContextBudgetGate must be used for token budget decisions
    2. No independent TokenBudget implementation in cells/ that doesn't delegate
    3. Budget policy integration should use ContextBudgetGate

Violations:
    - Cells defining their own budget calculation logic
    - Cells with TokenBudget that doesn't delegate to ContextBudgetGate
    - Local budget enforcement that bypasses kernelone.context.budget_gate
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[4]
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
CANONICAL_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "context" / "budget_gate.py"


def _build_utf8_env() -> dict[str, str]:
    """Build environment dict with UTF-8 settings."""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


# =============================================================================
# Test: Rule Declaration
# =============================================================================


def test_rule_declared_in_fitness_rules() -> None:
    """Test that CELL_KERNELONE_06 rule is declared in fitness-rules.yaml."""
    assert FITNESS_RULES_FILE.is_file(), f"missing fitness rules file: {FITNESS_RULES_FILE}"

    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])
    rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
    assert "CELL_KERNELONE_06" in rule_ids


def test_rule_has_correct_severity() -> None:
    """Test that CELL_KERNELONE_06 has severity 'high'."""
    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules", [])

    for rule in rules:
        if isinstance(rule, dict) and rule.get("id") == "CELL_KERNELONE_06":
            assert rule.get("severity") == "high", "CELL_KERNELONE_06 severity must be 'high'"
            return

    pytest.fail("CELL_KERNELONE_06 rule not found in fitness-rules.yaml")


# =============================================================================
# Test: Canonical Module Existence
# =============================================================================


def test_canonical_module_exists() -> None:
    """Test that the canonical budget_gate module exists."""
    assert CANONICAL_MODULE.is_file(), (
        f"Canonical module not found: {CANONICAL_MODULE}. "
        "Budget infrastructure must be defined in kernelone.context.budget_gate."
    )


def test_canonical_module_exports_public_api() -> None:
    """Test that the canonical module exports required public classes."""
    from polaris.kernelone.context.budget_gate import (
        ContextBudgetGate,
        ContextBudgetUsage,
        SectionAllocation,
    )

    assert callable(ContextBudgetGate), "ContextBudgetGate must be a class"
    assert callable(ContextBudgetUsage), "ContextBudgetUsage must be a dataclass"
    assert callable(SectionAllocation), "SectionAllocation must be a dataclass"


def test_canonical_module_has_defaults() -> None:
    """Test that canonical module defines default constants."""
    from polaris.kernelone.context.budget_gate import DEFAULT_SAFETY_MARGIN, MIN_BUDGET_TOKENS

    assert DEFAULT_SAFETY_MARGIN == 0.85, "DEFAULT_SAFETY_MARGIN must be 0.85"
    assert MIN_BUDGET_TOKENS == 30000, "MIN_BUDGET_TOKENS must be 30000"


# =============================================================================
# Test: ContextBudgetGate Functionality
# =============================================================================


class TestContextBudgetGate:
    """Test the ContextBudgetGate class."""

    def test_gate_can_be_instantiated(self) -> None:
        """Test that ContextBudgetGate can be instantiated."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000)
        assert gate is not None

    def test_gate_has_required_methods(self) -> None:
        """Test that ContextBudgetGate has required public methods."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000)

        assert hasattr(gate, "can_add"), "ContextBudgetGate must have 'can_add' method"
        assert hasattr(gate, "get_current_budget"), "ContextBudgetGate must have 'get_current_budget' method"
        assert hasattr(gate, "record_usage"), "ContextBudgetGate must have 'record_usage' method"
        assert hasattr(gate, "reset"), "ContextBudgetGate must have 'reset' method"
        assert hasattr(gate, "suggest_compaction"), "ContextBudgetGate must have 'suggest_compaction' method"
        assert hasattr(gate, "allocate_section"), "ContextBudgetGate must have 'allocate_section' method"
        assert hasattr(gate, "get_section_breakdown"), "ContextBudgetGate must have 'get_section_breakdown' method"

    def test_gate_validates_model_window(self) -> None:
        """Test that ContextBudgetGate validates model_window."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        with pytest.raises(ValueError, match="model_window must be a positive int"):
            ContextBudgetGate(model_window=0)

        with pytest.raises(ValueError, match="model_window must be a positive int"):
            ContextBudgetGate(model_window=-1000)

    def test_gate_validates_safety_margin(self) -> None:
        """Test that ContextBudgetGate validates safety_margin."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        with pytest.raises(ValueError, match="safety_margin must be in"):
            ContextBudgetGate(model_window=128_000, safety_margin=0.0)

        with pytest.raises(ValueError, match="safety_margin must be in"):
            ContextBudgetGate(model_window=128_000, safety_margin=1.5)


class TestBudgetUsage:
    """Test the ContextBudgetUsage dataclass."""

    def test_usage_computes_effective_limit(self) -> None:
        """Test that ContextBudgetUsage computes effective_limit correctly."""
        from polaris.kernelone.context.budget_gate import ContextBudgetUsage

        usage = ContextBudgetUsage(model_window=100_000, safety_margin=0.85, current_tokens=0)
        assert usage.effective_limit == 85_000

    def test_usage_computes_headroom(self) -> None:
        """Test that ContextBudgetUsage computes headroom correctly."""
        from polaris.kernelone.context.budget_gate import ContextBudgetUsage

        usage = ContextBudgetUsage(model_window=100_000, safety_margin=0.85, current_tokens=50_000)
        assert usage.headroom == 35_000

    def test_usage_computes_usage_ratio(self) -> None:
        """Test that ContextBudgetUsage computes usage_ratio correctly."""
        from polaris.kernelone.context.budget_gate import ContextBudgetUsage

        usage = ContextBudgetUsage(model_window=100_000, safety_margin=0.85, current_tokens=42_500)
        assert abs(usage.usage_ratio - 0.5) < 0.001


class TestCanAdd:
    """Test the can_add method."""

    def test_can_add_returns_true_when_under_budget(self) -> None:
        """Test that can_add returns True when under budget."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.85)
        ok, reason = gate.can_add(1000)

        assert ok is True
        assert reason == ""

    def test_can_add_returns_false_when_over_budget(self) -> None:
        """Test that can_add returns False when over budget."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000, safety_margin=0.85)
        gate.record_usage(110_000)  # Over the 85% safety margin

        ok, reason = gate.can_add(1000)
        assert ok is False
        assert "exceed budget" in reason.lower() or "exhausted" in reason.lower()

    def test_can_add_rejects_negative_tokens(self) -> None:
        """Test that can_add rejects negative tokens."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000)
        ok, reason = gate.can_add(-100)

        assert ok is False
        assert "negative" in reason.lower()


class TestSectionAllocation:
    """Test section-based budget allocation."""

    def test_allocate_section_returns_allocation(self) -> None:
        """Test that allocate_section returns a SectionAllocation."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000)
        allocation = gate.allocate_section("system", allocated=4000, actual=3500)

        assert allocation.section == "system"
        assert allocation.allocated == 4000
        assert allocation.actual == 3500
        assert allocation.compressed is True

    def test_get_section_breakdown_returns_dict(self) -> None:
        """Test that get_section_breakdown returns a dictionary."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate(model_window=128_000)
        gate.allocate_section("system", allocated=4000, actual=3500)

        breakdown = gate.get_section_breakdown()
        assert isinstance(breakdown, dict)
        assert "system" in breakdown


class TestFactoryMethods:
    """Test ContextBudgetGate factory methods."""

    def test_from_model_window(self) -> None:
        """Test the from_model_window factory method."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate.from_model_window(128_000)
        assert gate.model_window == 128_000

    def test_from_role_policy(self) -> None:
        """Test the from_role_policy factory method."""
        from polaris.kernelone.context.budget_gate import ContextBudgetGate

        gate = ContextBudgetGate.from_role_policy(max_context_tokens=100_000)
        assert gate.model_window == 100_000

    def test_from_role_policy_uses_min_budget_for_zero(self) -> None:
        """Test that from_role_policy uses MIN_BUDGET_TOKENS for zero input."""
        from polaris.kernelone.context.budget_gate import MIN_BUDGET_TOKENS, ContextBudgetGate

        gate = ContextBudgetGate.from_role_policy(max_context_tokens=0)
        assert gate.model_window == MIN_BUDGET_TOKENS


# =============================================================================
# Test: No Independent Budget Implementation in Cells
# =============================================================================


def test_cells_import_context_budget_gate() -> None:
    """Test that cells that need budget functionality import from kernelone."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    importing_cells: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        if "ContextBudgetGate" in content or "budget_gate" in content:
            if "polaris.kernelone.context.budget_gate" in content:
                importing_cells.append(str(py_file.relative_to(BACKEND_ROOT)))

    # At least some cells should be importing from the canonical source
    assert len(importing_cells) > 0, (
        "No cells appear to import ContextBudgetGate from kernelone.context. The integration may not be complete."
    )


def test_no_local_context_budget_gate_in_cells() -> None:
    """Test that no cells define local ContextBudgetGate class."""
    cells_dir = BACKEND_ROOT / "polaris" / "cells"

    if not cells_dir.exists():
        pytest.skip("cells directory not found")

    violations: list[str] = []

    for py_file in cells_dir.rglob("*.py"):
        if "test" in py_file.parts or "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for i, line in enumerate(content.splitlines(), 1):
            if "class ContextBudgetGate" in line:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    # Allow if it's importing from kernelone
                    if "from polaris.kernelone.context.budget_gate import" not in content:
                        violations.append(f"{py_file.relative_to(BACKEND_ROOT)}:{i}: {stripped}")

    assert len(violations) == 0, (
        f"Found {len(violations)} local ContextBudgetGate class definitions in cells:\n" + "\n".join(violations[:10])
    )


# =============================================================================
# Test: Known Locations
# =============================================================================


def test_known_locations_import_correctly() -> None:
    """Test that known locations with budget logic now import correctly."""
    # These files should import from kernelone.context.budget_gate
    known_locations = [
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "token_budget.py",
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "kernel" / "internal" / "policy" / "budget_policy.py",
    ]

    for file_path in known_locations:
        if not file_path.exists():
            continue

        content = file_path.read_text(encoding="utf-8")

        # Should NOT have local ContextBudgetGate definition
        # Should import from kernelone.context.budget_gate
        has_local_def = "class ContextBudgetGate" in content and not any(
            line for line in content.splitlines() if "from polaris.kernelone.context.budget_gate import" in line
        )

        if has_local_def:
            pytest.fail(f"{file_path.relative_to(BACKEND_ROOT)} still has local ContextBudgetGate definition")


# =============================================================================
# Test: Integration
# =============================================================================


def test_kernelone_context_exports_budget_gate() -> None:
    """Test that kernelone.context exports budget gate classes."""
    from polaris.kernelone.context import ContextBudgetGate, ContextBudgetUsage

    assert callable(ContextBudgetGate)
    assert callable(ContextBudgetUsage)


def test_kernelone_context_exports_section_allocation() -> None:
    """Test that kernelone.context exports SectionAllocation."""
    from polaris.kernelone.context import SectionAllocation

    assert callable(SectionAllocation)


def test_budget_gate_module_exports_required_items() -> None:
    """Test that the budget_gate module exports required items."""
    from polaris.kernelone.context import budget_gate as module

    required_exports = [
        "ContextBudgetGate",
        "ContextBudgetUsage",
        "SectionAllocation",
        "DEFAULT_SAFETY_MARGIN",
        "MIN_BUDGET_TOKENS",
    ]

    for export in required_exports:
        assert hasattr(module, export), f"Missing export: {export}"


def test_context_budget_usage_is_frozen_dataclass() -> None:
    """Test that ContextBudgetUsage is a frozen dataclass."""
    from polaris.kernelone.context.budget_gate import ContextBudgetUsage

    usage = ContextBudgetUsage(model_window=128_000, safety_margin=0.85)

    # Should be immutable
    with pytest.raises(AttributeError):
        usage.model_window = 256_000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
