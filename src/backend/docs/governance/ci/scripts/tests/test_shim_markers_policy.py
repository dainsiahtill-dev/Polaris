"""Tests for the shim marker governance checker wiring."""

from __future__ import annotations

from pathlib import Path

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_shim_markers import ShimMarkersChecker


def _write_shim_ledger(workspace: Path, source_path: str) -> None:
    """Write a minimal migration ledger with one shim-only unit."""
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = {
        "units": [
            {
                "id": "MIG-TEST-001",
                "title": "temporary shim marker fixture",
                "status": "shim_only",
                "source_refs": [
                    {
                        "path": source_path,
                        "kind": "file",
                        "coverage": "partial",
                        "note": "test fixture",
                    }
                ],
            }
        ]
    }
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a source fixture under the temporary workspace."""
    source_path = workspace / relative_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(content, encoding="utf-8")


def test_fitness_runner_uses_canonical_shim_marker_checker(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the canonical shim checker."""
    source_path = "legacy/shim.py"
    _write_shim_ledger(tmp_path, source_path)
    _write_source(
        tmp_path,
        source_path,
        "# SHIM: temporary compatibility bridge until the target Cell owns this behavior.\n",
    )

    canonical = ShimMarkersChecker(tmp_path).check_shim_markers()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_shim_markers()

    assert canonical.passed is True
    assert aggregate.passed is True
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert aggregate.warnings == canonical.warnings
    assert "with markers: 1" in "\n".join(aggregate.evidence)


def test_fitness_runner_reports_missing_shim_markers(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical shim-marker failures."""
    source_path = "legacy/missing_marker.py"
    _write_shim_ledger(tmp_path, source_path)
    _write_source(
        tmp_path,
        source_path,
        "def bridge() -> str:\n    return 'still routed through old behavior'\n",
    )

    canonical = ShimMarkersChecker(tmp_path).check_shim_markers()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_shim_markers()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("No migration markers" in violation for violation in aggregate.violations)
