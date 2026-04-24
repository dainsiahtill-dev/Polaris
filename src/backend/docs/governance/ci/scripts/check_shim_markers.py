#!/usr/bin/env python3
"""Check shim_markers rule.

Verifies that all files in shim_only migration units contain proper migration
markers (deprecation notices, migration dates, shim notices).

Rule ID: shim_only_units_require_markers
Severity: blocker
Evidence: docs/migration/ledger.yaml

Usage:
    python docs/governance/ci/scripts/check_shim_markers.py
    python docs/governance/ci/scripts/check_shim_markers.py --json
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Path Setup
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent.parent.parent.parent

# Terminal Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# Migration marker patterns - case-insensitive for matching, stored as-is for evidence
MIGRATION_MARKER_PATTERNS: list[re.Pattern[str]] = [
    # Deprecation notices
    re.compile(r"#\s*DEPRECATED", re.IGNORECASE),
    re.compile(r"..\s*deprecated::", re.IGNORECASE),
    re.compile(r"warnings\.warn\([^)]*DeprecationWarning", re.IGNORECASE),
    # Migration notices
    re.compile(r"#\s*TODO[:\s]+migrate", re.IGNORECASE),
    re.compile(r"#\s*MIGRATED", re.IGNORECASE),
    re.compile(r"#\s*LEGACY", re.IGNORECASE),
    # Shim notices
    re.compile(r"#\s*SHIM", re.IGNORECASE),
    re.compile(r"#\s*COMPATIBILITY", re.IGNORECASE),
    re.compile(r"#\s*BACKWARD\s*COMPAT", re.IGNORECASE),
    re.compile(r"#\s*MOVED\s*TO", re.IGNORECASE),
    # Date-based migration markers
    re.compile(r"#\s*\d{4}-\d{2}-\d{2}.*migration", re.IGNORECASE),
    re.compile(r"migrated?\s+(?:on|from|to)\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    re.compile(r"deprecated.*\d{4}-\d{2}-\d{2}", re.IGNORECASE),
]


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
class ShimOnlyUnit:
    """A shim_only migration unit."""

    id: str
    title: str
    source_refs: tuple[SourceRef, ...]


# Ledger Parser


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


def _find_shim_only_units(ledger: dict[str, Any]) -> list[ShimOnlyUnit]:
    """Find all migration units with shim_only status."""
    units: list[ShimOnlyUnit] = []
    for raw_unit in ledger.get("units", []):
        status = str(raw_unit.get("status", ""))
        if status == "shim_only":
            source_refs = tuple(_parse_source_ref(sr) for sr in raw_unit.get("source_refs", []))
            units.append(
                ShimOnlyUnit(
                    id=str(raw_unit.get("id", "")),
                    title=str(raw_unit.get("title", "")),
                    source_refs=source_refs,
                )
            )
    return units


# File Content Analysis


def _find_migration_markers(content: str) -> list[str]:
    """Find all migration marker patterns in file content.

    Returns a list of matched patterns for evidence.
    """
    matches: list[str] = []
    for pattern in MIGRATION_MARKER_PATTERNS:
        match = pattern.search(content)
        if match:
            # Extract a snippet around the match for evidence
            start = max(0, match.start() - 20)
            end = min(len(content), match.end() + 40)
            snippet = content[start:end].replace("\n", " ").strip()
            matches.append(snippet)
    return matches


def _file_has_markers(file_path: Path) -> tuple[bool, list[str]]:
    """Check if a file contains migration markers.

    Returns:
        Tuple of (has_markers, matched_snippets)
    """
    if not file_path.exists():
        return False, []

    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return False, []

    snippets = _find_migration_markers(content)
    return len(snippets) > 0, snippets


def _resolve_file_path(repo_root: Path, source_ref: SourceRef) -> Path | None:
    """Resolve a source_ref path to an actual file path.

    Handles both legacy root paths and polaris/ paths.
    """
    path_str = source_ref.path

    # Check various possible locations
    possible_paths = [
        repo_root / path_str,  # Direct path
        repo_root / "src" / "backend" / path_str,  # With src/backend prefix
        repo_root / "polaris" / path_str,  # polaris/ prefix
    ]

    for candidate in possible_paths:
        if candidate.exists() and candidate.is_file():
            return candidate

    # For directories, we check if any file exists
    if source_ref.kind == "directory":
        for candidate in possible_paths:
            if candidate.exists() and candidate.is_dir():
                return candidate

    return None


def _check_directory_for_markers(repo_root: Path, source_ref: SourceRef) -> list[tuple[Path, bool, list[str]]]:
    """Check all files in a directory for migration markers.

    Returns a list of (file_path, has_markers, snippets) for each file.
    """
    results: list[tuple[Path, bool, list[str]]] = []
    path_str = source_ref.path

    # Try different base paths
    base_paths = [
        repo_root / path_str,
        repo_root / "src" / "backend" / path_str,
        repo_root / "polaris" / path_str,
    ]

    dir_path = None
    for candidate in base_paths:
        if candidate.exists() and candidate.is_dir():
            dir_path = candidate
            break

    if dir_path is None:
        return results

    # Find all Python files in the directory
    for py_file in dir_path.rglob("*.py"):
        has_markers, snippets = _file_has_markers(py_file)
        results.append((py_file, has_markers, snippets))

    return results


# Main Checker


class ShimMarkersChecker:
    """Checker for shim_only_units_require_markers rule."""

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or REPO_ROOT
        self.start_time = time.time()

    def _elapsed_ms(self) -> float:
        """Get elapsed time in milliseconds."""
        return (time.time() - self.start_time) * 1000

    def check_shim_markers(self) -> FitnessCheckResult:
        """Check that shim_only files have migration markers.

        This rule verifies:
        1. Each shim_only migration unit has files with migration markers
        2. Files contain deprecation notices, migration dates, or shim notices
        """
        result = FitnessCheckResult(
            rule_id="shim_only_units_require_markers",
            passed=True,
        )

        # Load the ledger
        ledger = _load_ledger(self.workspace)
        if ledger is None:
            result.passed = False
            result.violations.append("docs/migration/ledger.yaml not found - cannot verify shim markers")
            result.duration_ms = self._elapsed_ms()
            return result

        # Find all shim_only units
        shim_units = _find_shim_only_units(ledger)

        if not shim_units:
            result.evidence.append("No shim_only migration units found in ledger - check passes vacuously")
            result.duration_ms = self._elapsed_ms()
            return result

        result.evidence.append(f"Found {len(shim_units)} shim_only migration unit(s)")

        # Track overall statistics
        total_files_checked = 0
        files_with_markers = 0
        files_without_markers: list[str] = []

        for unit in shim_units:
            result.evidence.append(f"Checking unit: {unit.id} ({unit.title})")

            for source_ref in unit.source_refs:
                total_files_checked += 1

                if source_ref.kind == "directory":
                    # Check all files in directory
                    dir_results = _check_directory_for_markers(self.workspace, source_ref)

                    if not dir_results:
                        # Directory might not exist yet or be empty
                        result.warnings.append(f"Directory not found or empty: {source_ref.path} (unit: {unit.id})")
                        continue

                    for file_path, has_markers, _ in dir_results:
                        total_files_checked += 1
                        if has_markers:
                            files_with_markers += 1
                        else:
                            files_without_markers.append(str(file_path))
                            result.violations.append(
                                f"No migration markers in: {file_path} (unit: {unit.id}, source_ref: {source_ref.path})"
                            )

                else:
                    # Check single file
                    file_path = _resolve_file_path(self.workspace, source_ref)

                    if file_path is None:
                        result.warnings.append(f"Source file not found: {source_ref.path} (unit: {unit.id})")
                        continue

                    has_markers, snippets = _file_has_markers(file_path)

                    if has_markers:
                        files_with_markers += 1
                        result.evidence.append(f"Migration markers found in {file_path.name}: {snippets[0][:60]}...")
                    else:
                        files_without_markers.append(str(file_path))
                        result.violations.append(
                            f"No migration markers in: {file_path} (unit: {unit.id}, source_ref: {source_ref.path})"
                        )

        # Update result based on findings
        result.evidence.append(
            f"Files checked: {total_files_checked}, "
            f"with markers: {files_with_markers}, "
            f"without markers: {len(files_without_markers)}"
        )

        if files_without_markers:
            result.passed = False
            result.violations.append(f"FAILED: {len(files_without_markers)} file(s) missing migration markers")

        result.duration_ms = self._elapsed_ms()
        return result


# CLI Entry Point


def main() -> int:
    """Main entry point for the checker."""
    parser = argparse.ArgumentParser(description="Check shim_only_units_require_markers rule")
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
    checker = ShimMarkersChecker(workspace)
    result = checker.check_shim_markers()

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
