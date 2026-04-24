#!/usr/bin/env python3
"""Check legacy_file_coverage_audit rule.

This script verifies that migration units claiming legacy path coverage
specify that coverage at file granularity, not just directory-level.
Directories should have explicit file lists in the note field.

Rule ID: legacy_file_coverage_audit
Severity: blocker
Evidence: docs/migration/ledger.yaml

Usage:
    python docs/governance/ci/scripts/check_legacy_coverage.py
    python docs/governance/ci/scripts/check_legacy_coverage.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Sequence
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
    kind: str  # file, directory, glob, file_family, module_slice
    coverage: str  # full, partial
    note: str = ""


@dataclass(frozen=True)
class MigrationUnit:
    """A migration unit from the ledger."""

    id: str
    title: str
    status: str
    source_refs: tuple[SourceRef, ...]


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


def _parse_units(ledger: dict[str, Any]) -> list[MigrationUnit]:
    """Parse all migration units from the ledger."""
    units: list[MigrationUnit] = []
    for raw_unit in ledger.get("units", []):
        source_refs = tuple(
            _parse_source_ref(sr) for sr in raw_unit.get("source_refs", [])
        )
        units.append(
            MigrationUnit(
                id=str(raw_unit.get("id", "")),
                title=str(raw_unit.get("title", "")),
                status=str(raw_unit.get("status", "")),
                source_refs=source_refs,
            )
        )
    return units


# ─────────────────────────────────────────────────────────────────────────────
# File Granularity Analysis
# ─────────────────────────────────────────────────────────────────────────────

# Patterns that indicate vague directory coverage without explicit file lists
VAGUE_DIRECTORY_PATTERNS: list[re.Pattern[str]] = [
    # Generic "entire directory" statements
    re.compile(r"entire\s+(legacy\s+)?(directory|directory\s+replaced)", re.IGNORECASE),
    re.compile(r"whole\s+directory", re.IGNORECASE),
    re.compile(r"all\s+files?\s+in\s+directory", re.IGNORECASE),
    re.compile(r"directory\s+fully\s+(covered|migrated|replaced)", re.IGNORECASE),
    re.compile(r"the\s+entire\s+", re.IGNORECASE),
    # Wildcard-only patterns without explicit file listing
    re.compile(r"all\s+\*\.py\s+files?", re.IGNORECASE),
    re.compile(r"\*.py\s+files?", re.IGNORECASE),
    re.compile(r"\.\.\.", re.IGNORECASE),  # Ellipsis often used for "etc"
]

# Patterns that indicate explicit file listing is present
EXPLICIT_FILE_LIST_PATTERNS: list[re.Pattern[str]] = [
    # Lists of actual file/module names
    re.compile(r"\d+\s+files?:\s*\w+", re.IGNORECASE),  # "15 files: service, storage..."
    re.compile(r"(file|module)s?:\s*\w+", re.IGNORECASE),  # "files: service, storage..."
    re.compile(r"\[[\w\s,]+\]", re.IGNORECASE),  # "[service, storage, models]"
    # Named file patterns
    re.compile(r"(\w+\.py\s*,\s*){2,}", re.IGNORECASE),  # At least 2 .py files listed
    re.compile(r"(service|storage|models|runtime|engine|config)\.py", re.IGNORECASE),
]

# File extension patterns for explicit file detection
FILE_EXTENSIONS = {".py", ".yaml", ".yml", ".json", ".txt", ".md", ".rst"}


def _has_explicit_file_list(note: str) -> bool:
    """Check if the note contains an explicit list of files.

    Returns True if the note contains explicit file listings rather than
    just vague directory-level coverage statements.
    """
    if not note:
        return False

    # Check for explicit file list patterns
    for pattern in EXPLICIT_FILE_LIST_PATTERNS:
        if pattern.search(note):
            return True

    # Check for actual file names with extensions
    for ext in FILE_EXTENSIONS:
        # Look for file patterns like "filename.py" or "filename.yaml"
        if re.search(rf"\w+\{ext}\b", note, re.IGNORECASE):
            return True

    # Check for module-style names (without extension but explicit)
    module_patterns = [
        r"\b(service|storage|models|runtime|engine|config|loader|manager|handler)\b",
    ]
    explicit_module_count = sum(
        1 for p in module_patterns if re.search(p, note, re.IGNORECASE)
    )
    # If we have multiple explicit module names, consider it explicit
    if explicit_module_count >= 2:
        return True

    return False


def _is_vague_directory_claim(note: str) -> bool:
    """Check if the note contains vague directory-level coverage claims.

    Returns True if the note only describes directory-level coverage
    without explicit file listings.
    """
    if not note:
        return True  # Empty note is vague

    # If it has explicit file list, it's not vague
    if _has_explicit_file_list(note):
        return False

    # Check for vague patterns
    for pattern in VAGUE_DIRECTORY_PATTERNS:
        if pattern.search(note):
            return True

    return False


def _check_directory_coverage_granularity(
    units: Sequence[MigrationUnit],
) -> list[str]:
    """Check that directory coverage is specified at file granularity.

    Returns a list of violation messages for any directory entries
    that lack explicit file listings.
    """
    violations: list[str] = []

    # Kinds that represent directory-level claims
    directory_kinds = {"directory", "file_family"}

    for unit in units:
        for ref in unit.source_refs:
            if ref.kind in directory_kinds:
                if _is_vague_directory_claim(ref.note):
                    violations.append(
                        f"Unit '{unit.id}': Directory '{ref.path}' lacks explicit file list. "
                        f"Note: \"{ref.note[:80]}...\"" if len(ref.note) > 80
                        else f"Unit '{unit.id}': Directory '{ref.path}' lacks explicit file list. "
                        f"Note: \"{ref.note}\""
                    )

    return violations


def _get_legacy_units_with_directory_refs(
    units: Sequence[MigrationUnit],
) -> list[tuple[str, str]]:
    """Get list of (unit_id, directory_path) for units with directory refs."""
    directory_kinds = {"directory", "file_family"}
    result: list[tuple[str, str]] = []

    for unit in units:
        for ref in unit.source_refs:
            if ref.kind in directory_kinds:
                result.append((unit.id, ref.path))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main Checker
# ─────────────────────────────────────────────────────────────────────────────


class LegacyCoverageChecker:
    """Checker for legacy_file_coverage_audit rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def check_legacy_coverage(self) -> FitnessCheckResult:
        """Check that legacy path coverage is audited at file granularity.

        This rule verifies that migration units claiming directory-level
        coverage provide explicit file lists in their note field,
        rather than vague statements like "entire directory".
        """
        result = FitnessCheckResult(
            rule_id="legacy_file_coverage_audit",
            passed=True,
        )

        # Load and parse the ledger
        ledger = _load_ledger(self.workspace)
        if ledger is None:
            result.passed = False
            result.violations.append(
                "docs/migration/ledger.yaml not found - cannot verify legacy coverage granularity"
            )
            result.duration_ms = self._elapsed_ms()
            return result

        units = _parse_units(ledger)

        if not units:
            result.evidence.append("No migration units found in ledger")
            result.duration_ms = self._elapsed_ms()
            return result

        # Record evidence
        result.evidence.append(f"Total migration units: {len(units)}")

        # Get units with directory refs
        dir_refs = _get_legacy_units_with_directory_refs(units)
        result.evidence.append(f"Units with directory-level source refs: {len(dir_refs)}")

        if not dir_refs:
            result.evidence.append(
                "No directory-level coverage claims found - check passes vacuously"
            )
            result.duration_ms = self._elapsed_ms()
            return result

        # Check for file granularity violations
        violations = _check_directory_coverage_granularity(units)
        for violation in violations:
            result.passed = False
            result.violations.append(violation)

        # Summary
        if result.passed:
            result.evidence.append(
                f"All {len(dir_refs)} directory coverage claims have explicit file lists"
            )
        else:
            result.warnings.append(
                f"Found {len(violations)} directory coverage claims lacking explicit file lists"
            )

        result.duration_ms = self._elapsed_ms()
        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    """Main entry point for the checker."""
    parser = argparse.ArgumentParser(
        description="Check legacy_file_coverage_audit rule"
    )
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
    checker = LegacyCoverageChecker(workspace)
    result = checker.check_legacy_coverage()

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
