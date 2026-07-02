"""Pure policy for legacy coverage granularity governance checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

RULE_ID = "legacy_file_coverage_audit"


@dataclass(frozen=True)
class SourceRef:
    """A migration source reference used by the legacy coverage policy."""

    path: str
    kind: str
    coverage: str
    note: str = ""


@dataclass(frozen=True)
class MigrationUnit:
    """A normalized migration unit used by the legacy coverage policy."""

    id: str
    title: str
    status: str
    source_refs: tuple[SourceRef, ...]


@dataclass(frozen=True)
class LegacyCoveragePolicyResult:
    """Evaluation result for the legacy coverage governance policy."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


VAGUE_DIRECTORY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"entire\s+(legacy\s+)?(directory|directory\s+replaced)", re.IGNORECASE),
    re.compile(r"whole\s+directory", re.IGNORECASE),
    re.compile(r"all\s+files?\s+in\s+directory", re.IGNORECASE),
    re.compile(r"directory\s+fully\s+(covered|migrated|replaced)", re.IGNORECASE),
    re.compile(r"the\s+entire\s+", re.IGNORECASE),
    re.compile(r"all\s+\*\.py\s+files?", re.IGNORECASE),
    re.compile(r"\*.py\s+files?", re.IGNORECASE),
    re.compile(r"\.\.\.", re.IGNORECASE),
)

EXPLICIT_FILE_LIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d+\s+files?:\s*\w+", re.IGNORECASE),
    re.compile(r"(file|module)s?:\s*\w+", re.IGNORECASE),
    re.compile(r"\[[\w\s,]+]", re.IGNORECASE),
    re.compile(r"(\w+\.py\s*,\s*){2,}", re.IGNORECASE),
    re.compile(r"(service|storage|models|runtime|engine|config)\.py", re.IGNORECASE),
)

FILE_EXTENSIONS = frozenset({".py", ".yaml", ".yml", ".json", ".txt", ".md", ".rst"})
DIRECTORY_KINDS = frozenset({"directory", "file_family"})


def _normalize_path(path: object) -> str:
    """Normalize migration-ledger paths for cross-platform matching."""
    return str(path).replace("\\", "/").strip()


def _parse_source_ref(raw: object) -> SourceRef:
    """Parse one raw ledger source reference into a stable typed object."""
    if not isinstance(raw, Mapping):
        raw = {}
    return SourceRef(
        path=_normalize_path(raw.get("path", "")),
        kind=str(raw.get("kind", "file")),
        coverage=str(raw.get("coverage", "partial")),
        note=str(raw.get("note", "")),
    )


def _parse_units(raw_units: object) -> tuple[MigrationUnit, ...]:
    """Parse raw ledger units into the policy's normalized unit model."""
    if not isinstance(raw_units, list):
        return ()

    units: list[MigrationUnit] = []
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            continue
        source_refs = tuple(_parse_source_ref(source_ref) for source_ref in raw_unit.get("source_refs", []))
        units.append(
            MigrationUnit(
                id=str(raw_unit.get("id", "")),
                title=str(raw_unit.get("title", "")),
                status=str(raw_unit.get("status", "")),
                source_refs=source_refs,
            )
        )
    return tuple(units)


def _has_explicit_file_list(note: str) -> bool:
    """Return true when a directory coverage note names concrete files/modules."""
    if not note:
        return False

    if any(pattern.search(note) for pattern in EXPLICIT_FILE_LIST_PATTERNS):
        return True

    for extension in FILE_EXTENSIONS:
        if re.search(rf"\w+{re.escape(extension)}\b", note, re.IGNORECASE):
            return True

    module_pattern = r"\b(service|storage|models|runtime|engine|config|loader|manager|handler)\b"
    return len(re.findall(module_pattern, note, re.IGNORECASE)) >= 2


def _is_vague_directory_claim(note: str) -> bool:
    """Return true when a note claims broad directory coverage without detail."""
    if not note:
        return True
    if _has_explicit_file_list(note):
        return False
    return any(pattern.search(note) for pattern in VAGUE_DIRECTORY_PATTERNS)


def _get_directory_refs(units: Sequence[MigrationUnit]) -> tuple[tuple[str, str], ...]:
    """Return all migration units that declare directory-like source refs."""
    refs: list[tuple[str, str]] = []
    for unit in units:
        for source_ref in unit.source_refs:
            if source_ref.kind in DIRECTORY_KINDS:
                refs.append((unit.id, source_ref.path))
    return tuple(refs)


def _check_directory_coverage_granularity(units: Sequence[MigrationUnit]) -> tuple[str, ...]:
    """Return violations for directory refs that lack explicit file listings."""
    violations: list[str] = []
    for unit in units:
        for source_ref in unit.source_refs:
            if source_ref.kind not in DIRECTORY_KINDS:
                continue
            if not _is_vague_directory_claim(source_ref.note):
                continue
            note = source_ref.note
            note_snippet = f"{note[:80]}..." if len(note) > 80 else note
            violations.append(
                f"Unit '{unit.id}': Directory '{source_ref.path}' lacks explicit file list. Note: \"{note_snippet}\""
            )
    return tuple(violations)


def evaluate_legacy_coverage(workspace: Path) -> LegacyCoveragePolicyResult:
    """Evaluate migration ledger directory coverage at file granularity.

    Complexity:
        O(n + r) time for ``n`` migration units and ``r`` source references.
        O(n + r) space for normalized units and emitted evidence.
    """
    ledger_path = workspace / "docs" / "migration" / "ledger.yaml"
    if not ledger_path.exists():
        return LegacyCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml not found - cannot verify legacy coverage granularity",),
        )

    try:
        with ledger_path.open(encoding="utf-8") as stream:
            ledger = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return LegacyCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(f"Failed to parse ledger.yaml: {exc}",),
        )

    if not isinstance(ledger, Mapping):
        return LegacyCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("docs/migration/ledger.yaml must contain a mapping",),
        )

    units = _parse_units(ledger.get("units", []))
    if not units:
        return LegacyCoveragePolicyResult(
            rule_id=RULE_ID,
            passed=True,
            evidence=("No migration units found in ledger",),
        )

    evidence = [f"Total migration units: {len(units)}"]
    directory_refs = _get_directory_refs(units)
    evidence.append(f"Units with directory-level source refs: {len(directory_refs)}")

    if not directory_refs:
        evidence.append("No directory-level coverage claims found - check passes vacuously")
        return LegacyCoveragePolicyResult(rule_id=RULE_ID, passed=True, evidence=tuple(evidence))

    violations = _check_directory_coverage_granularity(units)
    warnings: tuple[str, ...] = ()
    if violations:
        warnings = (f"Found {len(violations)} directory coverage claims lacking explicit file lists",)
    else:
        evidence.append(f"All {len(directory_refs)} directory coverage claims have explicit file lists")

    return LegacyCoveragePolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=violations,
        warnings=warnings,
    )
