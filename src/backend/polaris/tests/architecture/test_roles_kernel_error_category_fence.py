from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_ERROR_CATEGORY_FILE = (
    POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "error_category.py"
)
RETIRED_ERROR_CATEGORY_IMPORT = "polaris.cells.roles.kernel.internal.error_category"


def _imports_retired_error_category(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == RETIRED_ERROR_CATEGORY_IMPORT for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == RETIRED_ERROR_CATEGORY_IMPORT:
            return True
    return False


def test_roles_kernel_error_category_module_is_retired() -> None:
    assert not RETIRED_ERROR_CATEGORY_FILE.exists()


def test_production_code_does_not_import_retired_error_category_module() -> None:
    offenders: list[str] = []
    for path in sorted(POLARIS_ROOT.rglob("*.py")):
        if any(part in {"tests", "generated", "__pycache__"} for part in path.parts):
            continue
        if _imports_retired_error_category(path):
            offenders.append(path.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
