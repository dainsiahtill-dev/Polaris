#!/usr/bin/env python3
"""Check catalog_presence rule.

Rule: catalog_missing_units_cannot_advance
Enforces that migration units targeting cells with catalog_status=missing
cannot advance to verified/retired states until they are added to the catalog.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker


class CatalogPresenceChecker(FitnessRuleChecker):
    """Checker for catalog_missing_units_cannot_advance rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        self.ledger_yaml_path = self.workspace / "docs" / "migration" / "ledger.yaml"

    def _load_catalog_cells(self) -> set[str]:
        """Load all cell IDs declared in cells.yaml."""
        cell_ids: set[str] = set()
        try:
            with self.cells_yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "cells" in data:
                for cell in data["cells"]:
                    if "id" in cell:
                        cell_ids.add(cell["id"])
        except (OSError, yaml.YAMLError) as e:
            result = FitnessCheckResult(
                rule_id="catalog_missing_units_cannot_advance",
                passed=False,
                violations=[f"Failed to load cells.yaml: {e}"],
            )
            print(result.format())
            sys.exit(1)
        return cell_ids

    def _load_migration_units(self) -> list[dict]:
        """Load all migration units from ledger.yaml."""
        try:
            with self.ledger_yaml_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data and "units" in data:
                return data["units"]
        except (OSError, yaml.YAMLError) as e:
            result = FitnessCheckResult(
                rule_id="catalog_missing_units_cannot_advance",
                passed=False,
                violations=[f"Failed to load ledger.yaml: {e}"],
            )
            print(result.format())
            sys.exit(1)
        return []

    def check_catalog_presence(self) -> FitnessCheckResult:
        """Check that all migration targets are present in catalog.

        Migration units with catalog_status=missing should not be able to
        advance to verified/retired states until their target cell is
        declared in cells.yaml.
        """
        result = FitnessCheckResult(
            rule_id="catalog_missing_units_cannot_advance",
            passed=True,
        )

        # Load catalog cells
        catalog_cells = self._load_catalog_cells()
        result.evidence.append(f"Catalog contains {len(catalog_cells)} declared cells")

        # Load migration units
        units = self._load_migration_units()
        result.evidence.append(f"Found {len(units)} migration units in ledger")

        # Track units with missing catalog status
        missing_catalog_units: list[str] = []
        advanced_missing_units: list[str] = []

        # Non-terminal states that cannot advance for missing-catalog units
        non_advanceable_states = {
            "verified",
            "retired",
        }

        for unit in units:
            unit_id = unit.get("id", "unknown")
            target = unit.get("target", {})
            target_cell = target.get("cell", "")
            catalog_status = target.get("catalog_status", "unknown")
            current_status = unit.get("status", "")

            # Check if target cell is in catalog
            if catalog_status == "missing":
                missing_catalog_units.append(unit_id)
                result.evidence.append(f"Unit '{unit_id}' targets cell '{target_cell}' with catalog_status=missing")

                # Check if this unit has advanced to verified/retired
                if current_status in non_advanceable_states:
                    advanced_missing_units.append(f"{unit_id} (status={current_status}, cell={target_cell})")

        # Report violations
        if missing_catalog_units:
            result.warnings.append(f"{len(missing_catalog_units)} migration units target cells not yet in catalog")

        if advanced_missing_units:
            result.passed = False
            for unit_info in advanced_missing_units:
                result.violations.append(
                    f"Unit has advanced to verified/retired but target cell is missing from catalog: {unit_info}"
                )

        # Additional check: verify all catalog targets are actually declared
        undeclared_targets: list[str] = []
        for unit in units:
            target = unit.get("target", {})
            target_cell = target.get("cell", "")
            if target_cell and target_cell not in catalog_cells:
                # Only flag if it's a planned target with actual catalog_status
                catalog_status = target.get("catalog_status", "unknown")
                if catalog_status == "actual":
                    undeclared_targets.append(target_cell)

        if undeclared_targets:
            result.warnings.append(
                f"{len(undeclared_targets)} target cells declared as 'actual' but not found in catalog"
            )

        return result


if __name__ == "__main__":
    checker = CatalogPresenceChecker()
    result = checker.check_catalog_presence()
    print(result.format())
    sys.exit(0 if result.passed else 1)
