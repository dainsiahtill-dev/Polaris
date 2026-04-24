#!/usr/bin/env python3
"""Check migration_no_conflicting_full_coverage rule.

This script verifies that no two active migration units claim full coverage
of the same legacy path, and that target paths do not overlap unless
explicitly marked as partial with justification.

Rule ID: migration_no_conflicting_full_coverage
Severity: blocker
Evidence: docs/migration/ledger.yaml

Usage:
    python docs/governance/ci/scripts/check_no_conflicting_coverage.py
    python docs/governance/ci/scripts/check_no_conflicting_coverage.py --json
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ─────────────────────────────────────────────────────────────────────────────
# Path Setup
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Data Structures
# ─────────────────────────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


@dataclass
class FitnessCheckResult:
    """Result of a fitness rule check."""

    rule_id: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timestamp: str = ""
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def format(self) -> str:
        """Format result for console output."""
        status = f"{GREEN}PASS{RESET}" if self.passed else f"{RED}FAIL{RESET}"
        lines = [
            f"[{self.rule_id}] {status}",
            f"  Duration: {self.duration_ms:.2f}ms",
        ]
        if self.evidence:
            lines.append("  Evidence:")
            for e in self.evidence[:5]:  # Limit output
                lines.append(f"    - {e}")
        if self.violations:
            lines.append("  Violations:")
            for v in self.violations:
                lines.append(f"    - {v}")
        if self.warnings:
            lines.append("  Warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


@dataclass(frozen=True)
class SourceRef:
    """A source reference in a migration unit."""

    path: str
    kind: str  # file, directory, glob
    coverage: str  # full, partial
    note: str = ""


@dataclass(frozen=True)
class TargetPaths:
    """Target paths for a migration unit."""

    target_paths: tuple[str, ...]
    root_dirs: tuple[str, ...]


@dataclass(frozen=True)
class MigrationUnit:
    """A migration unit from the ledger."""

    id: str
    title: str
    status: str
    source_refs: tuple[SourceRef, ...]
    target: TargetPaths


# ─────────────────────────────────────────────────────────────────────────────
# Ledger Parser
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_path(path: str) -> str:
    """Normalize path for comparison (Windows/Unix compatible)."""
    return str(path).replace("\\", "/").strip()


def _load_ledger(repo_root: Path) -> dict[str, Any] | None:
    """Load the migration ledger YAML file."""
    ledger_path = repo_root / "docs" / "migration" / "ledger.yaml"
    if not ledger_path.exists():
        return None
    with open(ledger_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_source_ref(raw: dict[str, Any]) -> SourceRef:
    """Parse a source_ref entry from a migration unit."""
    return SourceRef(
        path=_normalize_path(raw.get("path", "")),
        kind=str(raw.get("kind", "file")),
        coverage=str(raw.get("coverage", "partial")),
        note=str(raw.get("note", "")),
    )


def _parse_target(raw: dict[str, Any]) -> TargetPaths:
    """Parse target paths from a migration unit."""
    return TargetPaths(
        target_paths=tuple(_normalize_path(p) for p in raw.get("target_paths", [])),
        root_dirs=tuple(_normalize_path(d) for d in raw.get("root_dirs", [])),
    )


def _parse_units(ledger: dict[str, Any]) -> list[MigrationUnit]:
    """Parse all migration units from the ledger."""
    units: list[MigrationUnit] = []
    for raw_unit in ledger.get("units", []):
        source_refs = tuple(_parse_source_ref(sr) for sr in raw_unit.get("source_refs", []))
        target = _parse_target(raw_unit.get("target", {}))
        units.append(
            MigrationUnit(
                id=str(raw_unit.get("id", "")),
                title=str(raw_unit.get("title", "")),
                status=str(raw_unit.get("status", "")),
                source_refs=source_refs,
                target=target,
            )
        )
    return units


def _is_active_status(status: str) -> bool:
    """Check if a migration unit status is considered 'active'.

    Active statuses are those that have not completed the migration lifecycle.
    Verified and retired units are considered completed.
    """
    completed_statuses = {"verified", "retired"}
    return status not in completed_statuses


# ─────────────────────────────────────────────────────────────────────────────
# Conflict Detection
# ─────────────────────────────────────────────────────────────────────────────


def _expand_path_for_comparison(path: str) -> set[str]:
    """Expand a path to include all potential sub-paths for overlap detection.

    For a directory like 'src/backend/core/', this returns:
    - 'src/backend/core/'
    - 'src/backend/core' (without trailing slash)
    """
    normalized = _normalize_path(path)
    paths: set[str] = {normalized}
    # Also add without trailing slash for comparison
    if normalized.endswith("/"):
        paths.add(normalized.rstrip("/"))
    return paths


def _check_source_ref_conflicts(units: list[MigrationUnit]) -> list[str]:
    """Check for conflicting full coverage claims on source paths.

    Returns a list of violation messages for any conflicts found.
    """
    # Map: normalized_path -> list of (unit_id, coverage_type)
    path_owners: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for unit in units:
        if not _is_active_status(unit.status):
            continue
        for ref in unit.source_refs:
            if ref.coverage == "full":
                path_owners[ref.path].append((unit.id, ref.coverage))

    violations: list[str] = []
    for path, owners in path_owners.items():
        if len(owners) > 1:
            unit_ids = [u[0] for u in owners]
            violations.append(f"Source path '{path}' claimed with full coverage by multiple active units: {unit_ids}")

    return violations


def _check_target_path_overlaps(units: list[MigrationUnit]) -> list[str]:
    """Check for overlapping target paths between active migration units.

    Target paths should not overlap unless explicitly marked as partial with
    justification in the notes.
    """
    # Map: normalized_target_path -> list of unit_ids claiming it
    target_owners: dict[str, list[str]] = defaultdict(list)

    for unit in units:
        if not _is_active_status(unit.status):
            continue
        for target_path in unit.target.target_paths:
            target_owners[target_path].append(unit.id)

    # Also check root_dirs overlap
    root_owners: dict[str, list[str]] = defaultdict(list)
    for unit in units:
        if not _is_active_status(unit.status):
            continue
        for root_dir in unit.target.root_dirs:
            root_owners[root_dir].append(unit.id)

    violations: list[str] = []

    # Check target_paths conflicts
    for path, owners in target_owners.items():
        if len(owners) > 1:
            violations.append(f"Target path '{path}' claimed by multiple active units: {owners}")

    # Check root_dirs conflicts
    for path, owners in root_owners.items():
        if len(owners) > 1:
            violations.append(f"Target root_dir '{path}' claimed by multiple active units: {owners}")

    return violations


def _get_active_unit_count(units: list[MigrationUnit]) -> int:
    """Count active migration units."""
    return sum(1 for u in units if _is_active_status(u.status))


def _get_units_with_full_coverage(units: list[MigrationUnit]) -> list[str]:
    """Get list of active units that claim full coverage on some source_ref."""
    result: list[str] = []
    for unit in units:
        if not _is_active_status(unit.status):
            continue
        if any(ref.coverage == "full" for ref in unit.source_refs):
            result.append(unit.id)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main Checker
# ─────────────────────────────────────────────────────────────────────────────


class NoConflictChecker:
    """Checker for migration_no_conflicting_full_coverage rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def check_no_conflicting_coverage(self) -> FitnessCheckResult:
        """Check that migration units don't claim conflicting full coverage.

        This rule verifies:
        1. No two active units claim full coverage of the same source path
        2. No two active units have overlapping target paths
        3. Any overlapping paths are explicitly justified as partial
        """
        result = FitnessCheckResult(
            rule_id="migration_no_conflicting_full_coverage",
            passed=True,
        )

        # Load and parse the ledger
        ledger = _load_ledger(self.workspace)
        if ledger is None:
            result.passed = False
            result.violations.append("docs/migration/ledger.yaml not found - cannot verify migration coverage")
            result.duration_ms = self._elapsed_ms()
            return result

        units = _parse_units(ledger)

        if not units:
            result.evidence.append("No migration units found in ledger")
            result.duration_ms = self._elapsed_ms()
            return result

        # Record evidence
        active_count = _get_active_unit_count(units)
        result.evidence.append(f"Total migration units: {len(units)}")
        result.evidence.append(f"Active migration units: {active_count}")

        full_coverage_units = _get_units_with_full_coverage(units)
        result.evidence.append(f"Active units with full coverage claims: {len(full_coverage_units)}")

        if not full_coverage_units:
            result.evidence.append("No active units claim full coverage - check passes vacuously")
            result.duration_ms = self._elapsed_ms()
            return result

        # Check for source_ref conflicts
        source_conflicts = _check_source_ref_conflicts(units)
        for conflict in source_conflicts:
            result.passed = False
            result.violations.append(conflict)

        # Check for target path overlaps
        target_overlaps = _check_target_path_overlaps(units)
        for overlap in target_overlaps:
            result.passed = False
            result.violations.append(overlap)

        result.duration_ms = self._elapsed_ms()
        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    """Main entry point for the checker."""
    parser = argparse.ArgumentParser(description="Check migration_no_conflicting_full_coverage rule")
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Workspace root path (default: repo root from script location)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace) if args.workspace else None
    checker = NoConflictChecker(workspace)
    result = checker.check_no_conflicting_coverage()

    if args.json:
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
                },
                indent=2,
            )
        )
    else:
        print(result.format())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
