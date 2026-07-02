"""Pure policy for Context Pack freshness governance checks."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULE_ID = "context_pack_is_primary_ai_entry"
FRESHNESS_THRESHOLD_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ContextPackFreshnessPolicyResult:
    """Evaluation result for Context Pack freshness governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _load_catalog(workspace: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    """Load cells.yaml, returning an error message instead of raising."""
    cells_yaml_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    if not cells_yaml_path.exists():
        return None, f"cells.yaml not found at {cells_yaml_path}"

    try:
        with cells_yaml_path.open(encoding="utf-8") as stream:
            catalog_data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Failed to parse cells.yaml: {exc}"

    if not isinstance(catalog_data, Mapping):
        return None, "cells.yaml must contain a mapping"
    return catalog_data, None


def _catalog_cells(catalog: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return normalized catalog cell mappings."""
    raw_cells = catalog.get("cells", [])
    if not isinstance(raw_cells, list):
        return ()
    return tuple(raw_cell for raw_cell in raw_cells if isinstance(raw_cell, Mapping))


def _find_context_pack_path(workspace: Path, cell_id: str) -> Path | None:
    """Find the preferred context.pack.json path for a catalog Cell."""
    cell_path = workspace / "polaris" / "cells" / cell_id.replace(".", "/")
    generated_path = cell_path / "generated" / "context.pack.json"
    if generated_path.exists():
        return generated_path

    root_path = cell_path / "context.pack.json"
    if root_path.exists():
        return root_path
    return None


def _validate_pack_structure(pack_path: Path) -> tuple[str, ...]:
    """Return structure validation issues for one context pack."""
    try:
        with pack_path.open(encoding="utf-8") as stream:
            pack_data = json.load(stream)
    except json.JSONDecodeError as exc:
        return (f"Invalid JSON in {pack_path}: {exc}",)
    except OSError as exc:
        return (f"Cannot read {pack_path}: {exc}",)

    if not isinstance(pack_data, Mapping):
        return (f"context pack must contain a mapping in {pack_path}",)
    if "cell_id" not in pack_data and "id" not in pack_data:
        return (f"Missing 'cell_id' or 'id' field in {pack_path}",)
    return ()


def _pack_mtime(pack_path: Path) -> tuple[float | None, str | None]:
    """Return a context pack modification time or an error message."""
    try:
        return pack_path.stat().st_mtime, None
    except OSError as exc:
        return None, f"cannot read modification time: {exc}"


def _format_age(now: float, mtime: float) -> str:
    """Format file age based on a reference timestamp."""
    age_seconds = now - mtime
    if age_seconds < 60:
        return f"{age_seconds:.0f}s ago"
    if age_seconds < 3600:
        return f"{age_seconds / 60:.0f}m ago"
    if age_seconds < 86400:
        return f"{age_seconds / 3600:.1f}h ago"
    return f"{age_seconds / 86400:.1f}d ago"


def evaluate_context_pack_freshness(
    workspace: Path,
    *,
    now: float | None = None,
    freshness_threshold_seconds: int = FRESHNESS_THRESHOLD_SECONDS,
) -> ContextPackFreshnessPolicyResult:
    """Evaluate context pack presence, structure, and freshness for catalog Cells.

    Args:
        workspace: Backend workspace root.
        now: Optional reference timestamp, injectable for deterministic tests.
        freshness_threshold_seconds: Maximum allowed pack age in seconds.

    Complexity:
        O(c) time for catalog cells plus O(p) JSON parsing for discovered packs.
        O(c) space for emitted evidence and violations.
    """
    reference_time = time.time() if now is None else now
    freshness_cutoff = reference_time - freshness_threshold_seconds

    catalog, catalog_error = _load_catalog(workspace)
    if catalog_error is not None or catalog is None:
        return ContextPackFreshnessPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=(catalog_error or "",),
        )

    cells = _catalog_cells(catalog)
    if not cells:
        return ContextPackFreshnessPolicyResult(
            rule_id=RULE_ID,
            passed=True,
            warnings=("No cells found in cells.yaml",),
        )

    evidence: list[str] = []
    missing_packs: list[str] = []
    stale_packs: list[str] = []
    invalid_packs: list[str] = []
    cells_with_pack = 0
    fresh_packs = 0

    for cell in cells:
        cell_id = str(cell.get("id", "") or "").strip()
        if not cell_id:
            continue

        pack_path = _find_context_pack_path(workspace, cell_id)
        if pack_path is None:
            missing_packs.append(cell_id)
            continue

        cells_with_pack += 1

        validation_issues = _validate_pack_structure(pack_path)
        if validation_issues:
            invalid_packs.append(f"{cell_id}: {', '.join(validation_issues)}")
            continue

        mtime, mtime_error = _pack_mtime(pack_path)
        if mtime is None:
            invalid_packs.append(f"{cell_id}: {mtime_error or 'cannot read modification time'}")
            continue

        age = _format_age(reference_time, mtime)
        if mtime >= freshness_cutoff:
            fresh_packs += 1
            evidence.append(f"{cell_id}: context.pack.json is fresh (modified {age})")
        else:
            stale_packs.append(f"{cell_id}: context.pack.json is stale (modified {age})")

    violations: list[str] = []
    violations.extend(f"Missing context.pack.json: {cell_id}" for cell_id in missing_packs)
    violations.extend(stale_packs)
    violations.extend(invalid_packs)

    evidence.append(
        f"Summary: {fresh_packs}/{cells_with_pack} packs fresh, "
        f"{len(stale_packs)} stale, {len(missing_packs)} missing out of {len(cells)} cells"
    )

    return ContextPackFreshnessPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
    )
