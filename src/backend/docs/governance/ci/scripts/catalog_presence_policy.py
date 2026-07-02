"""Pure policy for migration catalog-presence governance checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RULE_ID = "catalog_missing_units_cannot_advance"
NON_ADVANCEABLE_MISSING_CATALOG_STATUSES = frozenset({"verified", "retired"})


@dataclass(frozen=True)
class CatalogPresencePolicyResult:
    """Evaluation result for catalog presence policy checks."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _load_yaml_mapping(path: Path, label: str) -> tuple[Mapping[str, Any] | None, str | None]:
    """Load one YAML mapping, returning an error message instead of raising."""
    try:
        with path.open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        return None, f"Failed to load {label}: {exc}"
    if data is None:
        return {}, None
    if not isinstance(data, Mapping):
        return None, f"{label} must contain a mapping"
    return data, None


def _catalog_cells(catalog: Mapping[str, Any]) -> frozenset[str]:
    """Return all declared Cell ids from the catalog document."""
    raw_cells = catalog.get("cells", [])
    if not isinstance(raw_cells, list):
        return frozenset()

    cell_ids: set[str] = set()
    for raw_cell in raw_cells:
        if isinstance(raw_cell, Mapping) and raw_cell.get("id"):
            cell_ids.add(str(raw_cell["id"]))
    return frozenset(cell_ids)


def _migration_units(ledger: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """Return migration units from the ledger document."""
    raw_units = ledger.get("units", [])
    if not isinstance(raw_units, list):
        return ()
    return tuple(unit for unit in raw_units if isinstance(unit, Mapping))


def _target_block(unit: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the target block for a migration unit."""
    target = unit.get("target", {})
    return target if isinstance(target, Mapping) else {}


def _target_cells(unit: Mapping[str, Any]) -> tuple[str, ...]:
    """Return all catalog cells declared by a migration unit.

    ``target.cell`` is the historical single-owner field. ``target.cells`` is
    the newer multi-cell declaration and must be checked by the same authority.
    """
    target = _target_block(unit)
    cells: list[str] = []

    primary = str(target.get("cell", "") or "").strip()
    if primary:
        cells.append(primary)

    raw_cells = target.get("cells", []) or []
    if isinstance(raw_cells, list):
        for raw_cell in raw_cells:
            cell = str(raw_cell or "").strip()
            if cell and cell not in cells:
                cells.append(cell)

    return tuple(cells)


def evaluate_catalog_presence(workspace: Path) -> CatalogPresencePolicyResult:
    """Evaluate catalog presence constraints for migration ledger units.

    Complexity:
        O(c + u + t) time for catalog cells, migration units, and unit target
        cell references. O(c + u + t) space for normalized evidence.
    """
    cells_yaml_path = workspace / "docs" / "graph" / "catalog" / "cells.yaml"
    ledger_yaml_path = workspace / "docs" / "migration" / "ledger.yaml"

    catalog, catalog_error = _load_yaml_mapping(cells_yaml_path, "cells.yaml")
    if catalog_error is not None or catalog is None:
        return CatalogPresencePolicyResult(rule_id=RULE_ID, passed=False, violations=(catalog_error or "",))

    ledger, ledger_error = _load_yaml_mapping(ledger_yaml_path, "ledger.yaml")
    if ledger_error is not None or ledger is None:
        return CatalogPresencePolicyResult(rule_id=RULE_ID, passed=False, violations=(ledger_error or "",))

    catalog_cells = _catalog_cells(catalog)
    units = _migration_units(ledger)
    evidence = [
        f"Catalog contains {len(catalog_cells)} declared cells",
        f"Found {len(units)} migration units in ledger",
    ]

    missing_catalog_units: list[str] = []
    advanced_missing_units: list[str] = []
    undeclared_targets: list[str] = []

    for unit in units:
        unit_id = str(unit.get("id", "unknown"))
        target = _target_block(unit)
        target_cells = _target_cells(unit)
        target_cell_label = ", ".join(target_cells) if target_cells else "<none>"
        catalog_status = target.get("catalog_status", "unknown")
        current_status = unit.get("status", "")

        if catalog_status == "missing":
            missing_catalog_units.append(unit_id)
            evidence.append(f"Unit '{unit_id}' targets cell(s) '{target_cell_label}' with catalog_status=missing")
            if current_status in NON_ADVANCEABLE_MISSING_CATALOG_STATUSES:
                advanced_missing_units.append(f"{unit_id} (status={current_status}, cell={target_cell_label})")

        if catalog_status == "actual":
            for target_cell in target_cells:
                if target_cell and target_cell not in catalog_cells:
                    undeclared_targets.append(target_cell)

    warnings: list[str] = []
    if missing_catalog_units:
        warnings.append(f"{len(missing_catalog_units)} migration units target cells not yet in catalog")

    violations: list[str] = []
    violations.extend(
        f"Unit has advanced to verified/retired but target cell is missing from catalog: {unit_info}"
        for unit_info in advanced_missing_units
    )
    if undeclared_targets:
        target_list = ", ".join(sorted(set(undeclared_targets)))
        violations.append(
            f"{len(set(undeclared_targets))} target cell(s) declared as 'actual' but not found in catalog: "
            f"{target_list}"
        )

    return CatalogPresencePolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
