"""Tests for catalog presence policy wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_catalog_presence import CatalogPresenceChecker


def _write_catalog(workspace: Path, cell_ids: list[str]) -> None:
    """Write a minimal Cell catalog fixture."""
    catalog_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog = {"cells": [{"id": cell_id} for cell_id in cell_ids]}
    catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8")


def _write_ledger(workspace: Path, units: list[dict[str, Any]]) -> None:
    """Write a migration ledger fixture."""
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(yaml.safe_dump({"units": units}, sort_keys=False), encoding="utf-8")


def test_fitness_runner_uses_canonical_catalog_presence_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the canonical catalog policy."""
    _write_catalog(tmp_path, ["runtime.execution", "runtime.ledger"])
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-CATALOG-OK",
                "status": "in_progress",
                "target": {
                    "cell": "runtime.execution",
                    "cells": ["runtime.ledger"],
                    "catalog_status": "actual",
                },
            }
        ],
    )

    canonical = CatalogPresenceChecker(tmp_path).check_catalog_presence()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_catalog_presence()

    assert canonical.passed is True
    assert aggregate.passed is True
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert aggregate.warnings == canonical.warnings
    assert "Found 1 migration units in ledger" in aggregate.evidence


def test_fitness_runner_blocks_verified_missing_catalog_target(tmp_path: Path) -> None:
    """Verified/retired units cannot target missing catalog cells."""
    _write_catalog(tmp_path, ["runtime.execution"])
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-CATALOG-MISSING",
                "status": "verified",
                "target": {
                    "cell": "runtime.missing",
                    "catalog_status": "missing",
                },
            }
        ],
    )

    canonical = CatalogPresenceChecker(tmp_path).check_catalog_presence()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_catalog_presence()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("advanced to verified/retired" in violation for violation in aggregate.violations)


def test_fitness_runner_checks_multi_cell_actual_targets(tmp_path: Path) -> None:
    """The aggregate runner must check every target.cells entry, not only target.cell."""
    _write_catalog(tmp_path, ["runtime.execution"])
    _write_ledger(
        tmp_path,
        [
            {
                "id": "MIG-CATALOG-MULTI",
                "status": "in_progress",
                "target": {
                    "cell": "runtime.execution",
                    "cells": ["runtime.missing"],
                    "catalog_status": "actual",
                },
            }
        ],
    )

    canonical = CatalogPresenceChecker(tmp_path).check_catalog_presence()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_catalog_presence()

    assert canonical.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == canonical.rule_id
    assert aggregate.violations == canonical.violations
    assert any("runtime.missing" in violation for violation in aggregate.violations)
