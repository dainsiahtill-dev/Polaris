"""Tests for dangerous command pattern source policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_cell_kernelone_03 import CellKernelone03Checker


def _write_canonical_patterns(workspace: Path) -> None:
    """Write the canonical KernelOne dangerous pattern source fixture."""
    canonical_path = workspace / "polaris" / "kernelone" / "security" / "dangerous_patterns.py"
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_path.write_text(
        '_DANGEROUS_PATTERNS = ((r"rm\\\\s+-rf", "recursive delete"),)\n',
        encoding="utf-8",
    )


def _write_cell_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write one Cell source fixture."""
    source_path = workspace / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(content, encoding="utf-8")


def test_fitness_runner_uses_canonical_dangerous_pattern_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the CELL_KERNELONE_03 policy."""
    _write_canonical_patterns(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/roles/kernel/internal/policy.py",
        "from polaris.kernelone.security.dangerous_patterns import is_dangerous_command\n",
    )

    cell_gate = CellKernelone03Checker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_command_pattern_source()

    assert cell_gate.passed is True
    assert aggregate.passed is True
    assert cell_gate.rule_id == "CELL_KERNELONE_03"
    assert aggregate.rule_id == "canonical_dangerous_patterns"
    assert aggregate.violations == cell_gate.violations
    assert aggregate.warnings == cell_gate.warnings
    assert "No local dangerous pattern definitions found in cells/" in aggregate.evidence


def test_fitness_runner_reports_local_dangerous_pattern_definitions(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical local-pattern failures."""
    _write_canonical_patterns(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/roles/kernel/internal/policy.py",
        'DANGEROUS_PATTERNS = [(r"rm -rf", "duplicate local source")]\n',
    )

    cell_gate = CellKernelone03Checker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_command_pattern_source()

    assert cell_gate.passed is False
    assert aggregate.passed is False
    assert cell_gate.rule_id == "CELL_KERNELONE_03"
    assert aggregate.rule_id == "canonical_dangerous_patterns"
    assert aggregate.violations == cell_gate.violations
    assert any("Local pattern definition" in violation for violation in aggregate.violations)
