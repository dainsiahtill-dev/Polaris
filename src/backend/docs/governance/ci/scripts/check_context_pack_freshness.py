#!/usr/bin/env python3
"""Check context_pack_freshness rule.

Verifies that each Cell in cells.yaml has a context.pack.json file
and that the file was recently modified (within 7 days).

Usage:
    python docs/governance/ci/scripts/check_context_pack_freshness.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Determine script directory and set up path for imports
_SCRIPT_DIR = Path(__file__).parent.resolve()
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

from fitness_rule_checker import (
    FitnessCheckResult,
    FitnessRuleChecker,
)

# Freshness threshold: 7 days in seconds
FRESHNESS_THRESHOLD_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds


class ContextPackFreshnessChecker(FitnessRuleChecker):
    """Check that each Cell has a fresh context.pack.json file."""

    def __init__(self, workspace: Path | None = None) -> None:
        super().__init__(workspace)
        self.current_time = time.time()
        self.freshness_cutoff = self.current_time - FRESHNESS_THRESHOLD_SECONDS

    def _find_context_pack_path(self, cell_id: str) -> Path | None:
        """Find the context.pack.json file for a given cell ID.

        Checks multiple possible locations:
        - polaris/cells/{cell_id}/generated/context.pack.json
        - polaris/cells/{cell_id}/context.pack.json
        """
        cell_path = self.workspace / "polaris" / "cells" / cell_id.replace(".", "/")

        # Try generated/ subdirectory first
        generated_path = cell_path / "generated" / "context.pack.json"
        if generated_path.exists():
            return generated_path

        # Fall back to root of cell directory
        root_path = cell_path / "context.pack.json"
        if root_path.exists():
            return root_path

        return None

    def _get_pack_timestamp(self, pack_path: Path) -> float | None:
        """Get the modification timestamp of the pack file.

        Returns None if the file doesn't exist or can't be read.
        """
        if not pack_path.exists():
            return None
        try:
            return pack_path.stat().st_mtime
        except OSError:
            return None

    def _validate_pack_structure(self, pack_path: Path) -> list[str]:
        """Validate that the pack file has expected structure.

        Returns list of validation issues (empty if valid).
        """
        issues = []
        try:
            with open(pack_path, encoding="utf-8") as f:
                pack_data = json.load(f)

            # Check for required fields
            if "cell_id" not in pack_data and "id" not in pack_data:
                issues.append(f"Missing 'cell_id' or 'id' field in {pack_path}")

        except json.JSONDecodeError as e:
            issues.append(f"Invalid JSON in {pack_path}: {e}")
        except OSError as e:
            issues.append(f"Cannot read {pack_path}: {e}")

        return issues

    def check_context_pack_freshness(self) -> FitnessCheckResult:
        """Check that each Cell has a context pack with fresh timestamp.

        The rule verifies:
        1. Each cell in cells.yaml has a context.pack.json file
        2. The file was modified within the last 7 days
        """
        result = FitnessCheckResult(
            rule_id="context_pack_is_primary_ai_entry",
            passed=True,
        )

        # Load cells.yaml
        cells_yaml_path = self.workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        if not cells_yaml_path.exists():
            result.passed = False
            result.violations.append(f"cells.yaml not found at {cells_yaml_path}")
            return result

        try:
            import yaml

            with open(cells_yaml_path, encoding="utf-8") as f:
                catalog_data = yaml.safe_load(f)
        except Exception as e:
            result.passed = False
            result.violations.append(f"Failed to parse cells.yaml: {e}")
            return result

        cells = catalog_data.get("cells", [])
        if not cells:
            result.warnings.append("No cells found in cells.yaml")
            return result

        # Track statistics
        total_cells = len(cells)
        cells_with_pack = 0
        fresh_packs = 0
        missing_packs: list[str] = []
        stale_packs: list[str] = []
        invalid_packs: list[str] = []

        for cell in cells:
            cell_id = cell.get("id")
            if not cell_id:
                continue

            pack_path = self._find_context_pack_path(cell_id)

            if pack_path is None:
                missing_packs.append(cell_id)
                continue

            cells_with_pack += 1

            # Validate pack structure
            validation_issues = self._validate_pack_structure(pack_path)
            if validation_issues:
                invalid_packs.append(f"{cell_id}: {', '.join(validation_issues)}")
                continue

            # Check freshness using file modification time
            mtime = self._get_pack_timestamp(pack_path)
            if mtime is None:
                invalid_packs.append(f"{cell_id}: cannot read modification time")
                continue

            if mtime >= self.freshness_cutoff:
                fresh_packs += 1
                result.evidence.append(
                    f"{cell_id}: context.pack.json is fresh (modified {self._format_age(mtime)})"
                )
            else:
                stale_packs.append(
                    f"{cell_id}: context.pack.json is stale (modified {self._format_age(mtime)})"
                )

        # Build result
        result.evidence.append(
            f"Summary: {fresh_packs}/{cells_with_pack} packs fresh, "
            f"{len(stale_packs)} stale, {len(missing_packs)} missing out of {total_cells} cells"
        )

        if missing_packs:
            result.violations.extend(
                f"Missing context.pack.json: {cell_id}" for cell_id in missing_packs
            )
            result.passed = False

        if stale_packs:
            result.violations.extend(stale_packs)
            result.passed = False

        if invalid_packs:
            result.violations.extend(invalid_packs)
            result.passed = False

        return result

    def _format_age(self, mtime: float) -> str:
        """Format the age of a file based on its modification time."""
        age_seconds = self.current_time - mtime
        if age_seconds < 60:
            return f"{age_seconds:.0f}s ago"
        elif age_seconds < 3600:
            return f"{age_seconds / 60:.0f}m ago"
        elif age_seconds < 86400:
            return f"{age_seconds / 3600:.1f}h ago"
        else:
            return f"{age_seconds / 86400:.1f}d ago"


def main() -> int:
    """Main entry point for running the check."""
    checker = ContextPackFreshnessChecker()
    result = checker.check_context_pack_freshness()
    print(result.format())

    # Also print in JSON format if requested via environment variable
    import os

    if os.environ.get("CHECK_CONTEXT_PACK_JSON_OUTPUT"):
        import json

        print(
            json.dumps(
                {
                    "rule_id": result.rule_id,
                    "passed": result.passed,
                    "evidence": result.evidence,
                    "violations": result.violations,
                    "warnings": result.warnings,
                    "timestamp": result.timestamp,
                    "duration_ms": result.duration_ms,
                }
            )
        )

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
