from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ROLES_RUNTIME_PUBLIC = BACKEND_ROOT / "polaris" / "cells" / "roles" / "runtime" / "public"
BASELINE_VIOLATIONS: frozenset[str] = frozenset(
    {
        "polaris/cells/roles/runtime/public/persistence.py -> "
        "polaris.cells.roles.session.internal.role_session_service",
        "polaris/cells/roles/runtime/public/service.py -> polaris.cells.context.engine.internal.search_gateway",
        "polaris/cells/roles/runtime/public/tests/test_uep_stream_parity.py -> "
        "polaris.cells.archive.run_archive.internal.archive_sink",
    }
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _is_foreign_cell_internal_import(module: str) -> bool:
    parts = module.split(".")
    if len(parts) < 6:
        return False
    if parts[0:2] != ["polaris", "cells"]:
        return False
    if "internal" not in parts:
        return False
    return parts[2:4] != ["roles", "runtime"]


def test_roles_runtime_public_does_not_import_foreign_cell_internal_modules() -> None:
    violations: list[str] = []
    for path in sorted(ROLES_RUNTIME_PUBLIC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for module in _imported_modules(path):
            if _is_foreign_cell_internal_import(module):
                rel = path.relative_to(BACKEND_ROOT).as_posix()
                violations.append(f"{rel} -> {module}")

    new_violations = sorted(set(violations) - BASELINE_VIOLATIONS)
    assert not new_violations, "roles.runtime.public must use foreign Cell public contracts:\n" + "\n".join(
        new_violations
    )
