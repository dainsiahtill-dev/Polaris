"""Tests for migration coverage conflict policy wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_no_conflicting_coverage import NoConflictChecker


def _write_ledger(workspace: Path, units: list[dict[str, Any]]) -> None:
    """Write a migration ledger fixture into the temporary workspace."""
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(yaml.safe_dump({"units": units}, sort_keys=False), encoding="utf-8")


def test_fitness_runner_uses_canonical_no_conflict_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the canonical conflict policy."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-CONFLICT-OK-1",
                "status": "in_progress",
                "source_refs": [{"path": "legacy/a.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/a/public.py"], "root_dirs": ["polaris/cells/a"]},
            },
            {
                "id": "MIG-CONFLICT-OK-2",
                "status": "in_progress",
                "source_refs": [{"path": "legacy/b.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/b/public.py"], "root_dirs": ["polaris/cells/b"]},
            },
        ],
    )

    canonical = NoConflictChecker(tmp_path).check_no_conflicting_coverage()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_no_conflicting_coverage()

    assert canonical.passed is True
    assert aggregate.passed is True
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert aggregate.warnings == canonical.warnings
    assert "Active units with full coverage claims: 2" in aggregate.evidence


def test_fitness_runner_reports_full_source_coverage_conflict(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical source coverage conflicts."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-CONFLICT-A",
                "status": "in_progress",
                "source_refs": [{"path": "legacy/shared.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/a/public.py"]},
            },
            {
                "id": "MIG-CONFLICT-B",
                "status": "blocked",
                "source_refs": [{"path": "legacy/shared.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/b/public.py"]},
            },
        ],
    )

    canonical = NoConflictChecker(tmp_path).check_no_conflicting_coverage()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_no_conflicting_coverage()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("full coverage by multiple active units" in violation for violation in aggregate.violations)


def test_fitness_runner_reports_target_path_conflict(tmp_path: Path) -> None:
    """The aggregate runner must preserve canonical target path conflicts."""
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-TARGET-A",
                "status": "in_progress",
                "source_refs": [{"path": "legacy/a.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/shared/public.py"]},
            },
            {
                "id": "MIG-TARGET-B",
                "status": "in_progress",
                "source_refs": [{"path": "legacy/b.py", "kind": "file", "coverage": "full"}],
                "target": {"target_paths": ["polaris/cells/shared/public.py"]},
            },
        ],
    )

    canonical = NoConflictChecker(tmp_path).check_no_conflicting_coverage()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_no_conflicting_coverage()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("Target path" in violation for violation in aggregate.violations)
