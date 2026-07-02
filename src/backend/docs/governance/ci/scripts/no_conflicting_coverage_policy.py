"""Pure policy for migration coverage conflict governance checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

RULE_ID = "migration_no_conflicting_full_coverage"
COMPLETED_STATUSES = frozenset({"verified", "retired"})


@dataclass(frozen=True)
class SourceRef:
    """A migration source reference used by the coverage conflict policy."""

    path: str
    kind: str
    coverage: str
    note: str = ""


@dataclass(frozen=True)
class TargetPaths:
    """Target paths declared by a migration unit."""

    target_paths: tuple[str, ...]
    root_dirs: tuple[str, ...]


@dataclass(frozen=True)
class MigrationUnit:
    """A normalized migration unit used by the coverage conflict policy."""

    id: str
    title: str
    status: str
    source_refs: tuple[SourceRef, ...]
    target: TargetPaths


@dataclass(frozen=True)
class NoConflictingCoveragePolicyResult:
    """Evaluation result for the no-conflicting-coverage policy."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _normalize_path(path: object) -> str:
    """Normalize path separators and trim whitespace for stable comparison."""
    return str(path).replace("\\", "/").strip()


def _parse_source_ref(raw: object) -> SourceRef:
    """Parse one raw ledger source reference into a typed object."""
    if not isinstance(raw, Mapping):
        raw = {}
    return SourceRef(
        path=_normalize_path(raw.get("path", "")),
        kind=str(raw.get("kind", "file")),
        coverage=str(raw.get("coverage", "partial")),
        note=str(raw.get("note", "")),
    )


def _parse_target(raw: object) -> TargetPaths:
    """Parse target paths from a raw migration unit target block."""
    if not isinstance(raw, Mapping):
        raw = {}
    return TargetPaths(
        target_paths=tuple(_normalize_path(path) for path in raw.get("target_paths", [])),
        root_dirs=tuple(_normalize_path(path) for path in raw.get("root_dirs", [])),
    )


def _parse_units(raw_units: object) -> tuple[MigrationUnit, ...]:
    """Parse raw migration ledger units into normalized policy units."""
    if not isinstance(raw_units, list):
        return ()

    units: list[MigrationUnit] = []
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            continue
        units.append(
            MigrationUnit(
                id=str(raw_unit.get("id", "")),
                title=str(raw_unit.get("title", "")),
                status=str(raw_unit.get("status", "")),
                source_refs=tuple(_parse_source_ref(source_ref) for source_ref in raw_unit.get("source_refs", [])),
                target=_parse_target(raw_unit.get("target", {})),
            )
        )
    return tuple(units)


def _is_active_status(status: str) -> bool:
    """Return true when a migration unit can still conflict with other units."""
    return status not in COMPLETED_STATUSES


def _check_source_ref_conflicts(units: tuple[MigrationUnit, ...]) -> tuple[str, ...]:
    """Return full-source-coverage conflicts across active migration units."""
    path_owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for unit in units:
        if not _is_active_status(unit.status):
            continue
        for source_ref in unit.source_refs:
            if source_ref.coverage == "full" and source_ref.path:
                path_owners[source_ref.path].append((unit.id, source_ref.coverage))

    violations: list[str] = []
    for path, owners in path_owners.items():
        if len(owners) > 1:
            unit_ids = [owner[0] for owner in owners]
            violations.append(f"Source path '{path}' claimed with full coverage by multiple active units: {unit_ids}")
    return tuple(violations)


def _check_target_path_overlaps(units: tuple[MigrationUnit, ...]) -> tuple[str, ...]:
    """Return target path/root directory conflicts across active migration units."""
    target_owners: dict[str, list[str]] = defaultdict(list)
    root_owners: dict[str, list[str]] = defaultdict(list)

    for unit in units:
        if not _is_active_status(unit.status):
            continue
        for target_path in unit.target.target_paths:
            if target_path:
                target_owners[target_path].append(unit.id)
        for root_dir in unit.target.root_dirs:
            if root_dir:
                root_owners[root_dir].append(unit.id)

    violations: list[str] = []
    for path, owners in target_owners.items():
        if len(owners) > 1:
            violations.append(f"Target path '{path}' claimed by multiple active units: {owners}")
    for path, owners in root_owners.items():
        if len(owners) > 1:
            violations.append(f"Target root_dir '{path}' claimed by multiple active units: {owners}")
    return tuple(violations)


def _get_active_unit_count(units: tuple[MigrationUnit, ...]) -> int:
    """Count active migration units."""
    return sum(1 for unit in units if _is_active_status(unit.status))


def _get_units_with_full_coverage(units: tuple[MigrationUnit, ...]) -> tuple[str, ...]:
    """Return active unit ids that claim full source coverage."""
    return tuple(
        unit.id
        for unit in units
        if _is_active_status(unit.status) and any(ref.coverage == "full" for ref in unit.source_refs)
    )


def evaluate_no_conflicting_coverage(workspace: Path) -> NoConflictingCoveragePolicyResult:
    """Evaluate migration units for source and target coverage conflicts.

    Complexity:
        O(n + r + t) time for migration units, source refs, and target refs.
        O(n + r + t) space for normalized units and ownership indexes.
    """
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    if not ledger_path.exists():
        return NoConflictingCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml not found - cannot verify migration coverage",),
        )

    try:
        with ledger_path.open(encoding="utf-8") as stream:
            ledger = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return NoConflictingCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"Failed to parse ledger.yaml: {exc}",),
        )

    if not isinstance(ledger, Mapping):
        return NoConflictingCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml must contain a mapping",),
        )

    units = _parse_units(ledger.get("units", []))
    if not units:
        return NoConflictingCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=True,
            evidence=("No migration units found in ledger",),
        )

    active_count = _get_active_unit_count(units)
    full_coverage_units = _get_units_with_full_coverage(units)
    evidence = [
        f"Total migration units: {len(units)}",
        f"Active migration units: {active_count}",
        f"Active units with full coverage claims: {len(full_coverage_units)}",
    ]

    if not full_coverage_units:
        evidence.append("No active units claim full coverage - check passes vacuously")
        return NoConflictingCoveragePolicyResult(rule_id=RULE_ID, passed=True, evidence=tuple(evidence))

    violations = _check_source_ref_conflicts(units) + _check_target_path_overlaps(units)
    return NoConflictingCoveragePolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=violations,
    )
