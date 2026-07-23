"""Import-boundary regressions for the runtime artifact-store public API."""

from __future__ import annotations

import ast
from pathlib import Path


def test_artifact_store_public_boundary_does_not_import_projection_cell() -> None:
    """Path helpers must not eagerly re-export the Projection Cell's models."""

    public_root = Path(__file__).resolve().parents[1] / "public"
    source_files = (
        public_root / "service.py",
        public_root / "__init__.py",
    )
    imported_modules: set[str] = set()
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        imported_modules.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )

    assert not any(module.startswith("polaris.cells.runtime.projection") for module in imported_modules)
