"""Pure policy for shim-only migration marker governance checks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULE_ID = "shim_only_units_require_markers"

MIGRATION_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"#\s*DEPRECATED", re.IGNORECASE),
    re.compile(r"..\s*deprecated::", re.IGNORECASE),
    re.compile(r"warnings\.warn\([^)]*DeprecationWarning", re.IGNORECASE),
    re.compile(r"#\s*TODO[:\s]+migrate", re.IGNORECASE),
    re.compile(r"#\s*MIGRATED", re.IGNORECASE),
    re.compile(r"#\s*LEGACY", re.IGNORECASE),
    re.compile(r"#\s*SHIM", re.IGNORECASE),
    re.compile(r"#\s*COMPATIBILITY", re.IGNORECASE),
    re.compile(r"#\s*BACKWARD\s*COMPAT", re.IGNORECASE),
    re.compile(r"#\s*MOVED\s*TO", re.IGNORECASE),
    re.compile(r"#\s*\d{4}-\d{2}-\d{2}.*migration", re.IGNORECASE),
    re.compile(r"migrated?\s+(?:on|from|to)\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE),
    re.compile(r"deprecated.*\d{4}-\d{2}-\d{2}", re.IGNORECASE),
)


@dataclass(frozen=True)
class ShimMarkerPolicyResult:
    """Evaluation result for the shim marker governance policy."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRef:
    """A source reference in a migration unit."""

    path: str
    kind: str
    coverage: str
    note: str = ""


@dataclass(frozen=True)
class ShimOnlyUnit:
    """A shim_only migration unit."""

    id: str
    title: str
    source_refs: tuple[SourceRef, ...]


def _normalize_path(path: str) -> str:
    """Normalize path separators for platform-independent comparisons."""
    return str(path).replace("\\", "/").strip()


def _load_ledger(repo_root: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    """Load the migration ledger as a mapping, returning an error instead of raising."""
    ledger_path = repo_root / "docs" / "migration" / "ledger.yaml"
    if not ledger_path.exists():
        return None, "docs/migration/ledger.yaml not found - cannot verify shim markers"

    try:
        with ledger_path.open(encoding="utf-8") as stream:
            ledger = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Failed to parse docs/migration/ledger.yaml: {exc}"

    if not isinstance(ledger, Mapping):
        return None, "docs/migration/ledger.yaml must contain a mapping"
    return ledger, None


def _parse_source_ref(raw: Mapping[str, Any]) -> SourceRef:
    """Parse a source_ref entry from a migration unit."""
    return SourceRef(
        path=_normalize_path(str(raw.get("path", ""))),
        kind=str(raw.get("kind", "file")),
        coverage=str(raw.get("coverage", "partial")),
        note=str(raw.get("note", "")),
    )


def _find_shim_only_units(ledger: Mapping[str, Any]) -> tuple[ShimOnlyUnit, ...]:
    """Return all migration units with shim_only status."""
    raw_units = ledger.get("units", [])
    if not isinstance(raw_units, list):
        return ()

    units: list[ShimOnlyUnit] = []
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            continue

        status = str(raw_unit.get("status", ""))
        if status != "shim_only":
            continue

        raw_source_refs = raw_unit.get("source_refs", [])
        if not isinstance(raw_source_refs, list):
            raw_source_refs = []
        source_refs = tuple(
            _parse_source_ref(raw_source_ref)
            for raw_source_ref in raw_source_refs
            if isinstance(raw_source_ref, Mapping)
        )
        units.append(
            ShimOnlyUnit(
                id=str(raw_unit.get("id", "")),
                title=str(raw_unit.get("title", "")),
                source_refs=source_refs,
            )
        )
    return tuple(units)


def _find_migration_markers(content: str) -> tuple[str, ...]:
    """Return matched migration marker snippets from file content."""
    matches: list[str] = []
    for pattern in MIGRATION_MARKER_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue

        start = max(0, match.start() - 20)
        end = min(len(content), match.end() + 40)
        matches.append(content[start:end].replace("\n", " ").strip())
    return tuple(matches)


def _file_has_markers(file_path: Path) -> tuple[bool, tuple[str, ...]]:
    """Return whether a file contains migration markers and matched snippets."""
    if not file_path.exists():
        return False, ()

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, ()

    snippets = _find_migration_markers(content)
    return bool(snippets), snippets


def _candidate_paths(repo_root: Path, path: str) -> tuple[Path, ...]:
    """Return supported migration-source path interpretations."""
    return (
        repo_root / path,
        repo_root / "src" / "backend" / path,
        repo_root / "polaris" / path,
    )


def _resolve_file_path(repo_root: Path, source_ref: SourceRef) -> Path | None:
    """Resolve a source reference to an existing file or directory."""
    for candidate in _candidate_paths(repo_root, source_ref.path):
        if candidate.exists() and candidate.is_file():
            return candidate

    if source_ref.kind == "directory":
        for candidate in _candidate_paths(repo_root, source_ref.path):
            if candidate.exists() and candidate.is_dir():
                return candidate
    return None


def _check_directory_for_markers(
    repo_root: Path, source_ref: SourceRef
) -> tuple[tuple[Path, bool, tuple[str, ...]], ...]:
    """Check all Python files in a source directory for migration markers."""
    directory = next(
        (
            candidate
            for candidate in _candidate_paths(repo_root, source_ref.path)
            if candidate.exists() and candidate.is_dir()
        ),
        None,
    )
    if directory is None:
        return ()

    return tuple((py_file, *_file_has_markers(py_file)) for py_file in directory.rglob("*.py"))


def evaluate_shim_markers(workspace: Path) -> ShimMarkerPolicyResult:
    """Evaluate shim-only migration units for explicit migration markers.

    The policy has one authoritative responsibility: every file represented by
    a ``shim_only`` migration source must contain an explicit marker explaining
    its transitional nature. CLI formatters and aggregate fitness runners must
    adapt this result instead of reimplementing the rule.

    Complexity:
        O(u + r + f * p) time for migration units, source refs, scanned files,
        and marker patterns. O(f + v + e) space for scan tuples and emitted
        evidence/violations. The dominant cost is reading source files.
    """
    ledger, ledger_error = _load_ledger(workspace)
    if ledger_error is not None or ledger is None:
        return ShimMarkerPolicyResult(rule_id=RULE_ID, passed=False, violations=(ledger_error or "",))

    shim_units = _find_shim_only_units(ledger)
    if not shim_units:
        return ShimMarkerPolicyResult(
            rule_id=RULE_ID,
            passed=True,
            evidence=("No shim_only migration units found in ledger - check passes vacuously",),
        )

    evidence: list[str] = [f"Found {len(shim_units)} shim_only migration unit(s)"]
    warnings: list[str] = []
    violations: list[str] = []
    files_without_markers: list[str] = []
    total_files_checked = 0
    files_with_markers = 0

    for unit in shim_units:
        evidence.append(f"Checking unit: {unit.id} ({unit.title})")

        for source_ref in unit.source_refs:
            total_files_checked += 1

            if source_ref.kind == "directory":
                directory_results = _check_directory_for_markers(workspace, source_ref)
                if not directory_results:
                    warnings.append(f"Directory not found or empty: {source_ref.path} (unit: {unit.id})")
                    continue

                for checked_path, has_markers, _ in directory_results:
                    total_files_checked += 1
                    if has_markers:
                        files_with_markers += 1
                    else:
                        files_without_markers.append(str(checked_path))
                        violations.append(
                            f"No migration markers in: {checked_path} (unit: {unit.id}, source_ref: {source_ref.path})"
                        )
                continue

            resolved_file_path = _resolve_file_path(workspace, source_ref)
            if resolved_file_path is None:
                warnings.append(f"Source file not found: {source_ref.path} (unit: {unit.id})")
                continue

            has_markers, snippets = _file_has_markers(resolved_file_path)
            if has_markers:
                files_with_markers += 1
                evidence.append(f"Migration markers found in {resolved_file_path.name}: {snippets[0][:60]}...")
            else:
                files_without_markers.append(str(resolved_file_path))
                violations.append(
                    f"No migration markers in: {resolved_file_path} (unit: {unit.id}, source_ref: {source_ref.path})"
                )

    evidence.append(
        f"Files checked: {total_files_checked}, "
        f"with markers: {files_with_markers}, "
        f"without markers: {len(files_without_markers)}"
    )

    if files_without_markers:
        violations.append(f"FAILED: {len(files_without_markers)} file(s) missing migration markers")

    return ShimMarkerPolicyResult(
        rule_id=RULE_ID,
        passed=not files_without_markers,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
