from __future__ import annotations

import ast
from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"

SCOPES = {
    "director.execution": BACKEND_ROOT / "polaris" / "cells" / "director" / "execution",
    "orchestration.workflow_runtime": BACKEND_ROOT / "polaris" / "cells" / "orchestration" / "workflow_runtime",
    "runtime.projection": BACKEND_ROOT / "polaris" / "cells" / "runtime" / "projection",
}


def _load_known_cells() -> set[str]:
    payload = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    cells = payload.get("cells") if isinstance(payload, dict) else []
    known_cells: set[str] = set()
    for item in cells:
        if not isinstance(item, dict):
            continue
        cell_id = str(item.get("id") or "").strip()
        if cell_id:
            known_cells.add(cell_id)
    return known_cells


def _cell_id_from_module(module: str, known_cells: set[str]) -> str | None:
    parts = str(module or "").strip().split(".")
    if len(parts) < 4:
        return None
    if parts[0] != "polaris" or parts[1] != "cells":
        return None
    candidate = f"{parts[2]}.{parts[3]}"
    return candidate if candidate in known_cells else None


def _collect_imported_cells(scope_dir: Path, *, known_cells: set[str], cell_id: str) -> set[str]:
    imported_cells: set[str] = set()
    for path in sorted(scope_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                target_cell = _cell_id_from_module(module, known_cells)
                if target_cell and target_cell != cell_id:
                    imported_cells.add(target_cell)
    return imported_cells


def _read_declared_depends_on(scope_dir: Path) -> set[str]:
    payload = yaml.safe_load((scope_dir / "cell.yaml").read_text(encoding="utf-8")) or {}
    depends_on = payload.get("depends_on") if isinstance(payload, dict) else []
    return {str(item).strip() for item in depends_on if str(item).strip()}


def test_scope_cells_cell_yaml_covers_current_code_imports() -> None:
    known_cells = _load_known_cells()

    for cell_id, scope_dir in SCOPES.items():
        imported_cells = _collect_imported_cells(scope_dir, known_cells=known_cells, cell_id=cell_id)
        declared_depends_on = _read_declared_depends_on(scope_dir)

        missing = imported_cells - declared_depends_on
        assert not missing, f"{cell_id} missing depends_on entries for: {sorted(missing)}"
