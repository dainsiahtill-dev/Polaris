"""Tests for legacy coverage granularity policy wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_legacy_coverage import LegacyCoverageChecker


def _write_ledger(workspace: Path, units: list[dict[str, Any]]) -> None:
    """Write a migration ledger fixture into the temporary workspace."""
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(yaml.safe_dump({"units": units}, sort_keys=False), encoding="utf-8")


def test_fitness_runner_uses_canonical_legacy_coverage_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the canonical coverage policy."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-LEGACY-001",
                "title": "explicit directory coverage",
                "status": "in_progress",
                "source_refs": [
                    {
                        "path": "legacy/runtime",
                        "kind": "directory",
                        "coverage": "partial",
                        "note": "files: service.py, storage.py, config.py",
                    }
                ],
            }
        ],
    )

    canonical = LegacyCoverageChecker(tmp_path).check_legacy_coverage()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_legacy_coverage()

    assert canonical.passed is True
    assert aggregate.passed is True
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert aggregate.warnings == canonical.warnings
    assert "All 1 directory coverage claims have explicit file lists" in aggregate.evidence


def test_fitness_runner_reports_vague_legacy_directory_coverage(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical vague-directory failures."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-LEGACY-002",
                "title": "vague directory coverage",
                "status": "in_progress",
                "source_refs": [
                    {
                        "path": "legacy/runtime",
                        "kind": "directory",
                        "coverage": "partial",
                        "note": "the entire legacy directory is covered",
                    }
                ],
            }
        ],
    )

    canonical = LegacyCoverageChecker(tmp_path).check_legacy_coverage()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_legacy_coverage()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("lacks explicit file list" in violation for violation in aggregate.violations)
