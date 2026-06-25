"""Architecture fences for Director repair-kernel ownership.

These checks lock the deterministic-repair convergence boundary:
``roles.adapters`` may keep legacy deterministic repair functions during
migration, but it must not own the repair kernel, import the Director Runtime
internal repair-kernel package, or restore the old strategy catalog fact source.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
ROLES_DIRECTOR_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "director"
EXECUTE_METHOD_PATH = ROLES_DIRECTOR_ROOT / "execute_method.py"
POST_EXECUTION_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "post_execution_repair_bridge.py"

FORBIDDEN_IMPORT_PREFIXES = (
    "polaris.cells.director.runtime.internal.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.deterministic_repairs.strategy_catalog",
)
ALLOWED_EXECUTE_METHOD_DIRECTOR_RUNTIME_IMPORTS = {
    "polaris.cells.director.runtime.public.service",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_catalog_cells() -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(_read_text(CATALOG_PATH))
    assert isinstance(payload, dict)
    cells = payload.get("cells")
    assert isinstance(cells, list)
    return {str(item["id"]): item for item in cells if isinstance(item, dict) and item.get("id")}


def _python_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts and not path.name.startswith("test_")
    ]


def _module_name_for_path(path: Path) -> str:
    rel_path = path.with_suffix("").relative_to(BACKEND_ROOT)
    return ".".join(rel_path.parts)


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level <= 0:
        return node.module or ""

    current_module_parts = _module_name_for_path(path).split(".")
    package_parts = current_module_parts[: -node.level]
    if node.module:
        package_parts.append(node.module)
    return ".".join(package_parts)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(_read_text(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(_resolve_import_from_module(path, node))
    return modules


def _forbidden_import(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _catalog_strings(cell: dict[str, Any], key: str) -> set[str]:
    values = cell.get(key, [])
    assert isinstance(values, list)
    return {str(value) for value in values}


def test_roles_adapters_does_not_own_director_repair_kernel_package() -> None:
    payload_files = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in sorted((ROLES_DIRECTOR_ROOT / "repair_kernel").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]

    assert payload_files == []
    assert not (ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "strategy_catalog.py").exists()


def test_roles_adapters_never_imports_director_runtime_internal_repair_kernel() -> None:
    violations: list[str] = []
    for path in _python_source_files(ROLES_DIRECTOR_ROOT):
        for module in _imported_modules(path):
            if _forbidden_import(module):
                violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {module}")

    assert violations == []


def test_execute_method_uses_director_runtime_repair_kernel_only_via_public_service() -> None:
    director_runtime_imports = sorted(
        {
            module
            for module in _imported_modules(EXECUTE_METHOD_PATH)
            if module == "polaris.cells.director.runtime" or module.startswith("polaris.cells.director.runtime.")
        }
    )

    assert set(director_runtime_imports) <= ALLOWED_EXECUTE_METHOD_DIRECTOR_RUNTIME_IMPORTS
    assert "polaris.cells.director.runtime.public.service" in director_runtime_imports


def test_execute_method_delegates_post_execution_language_repairs_to_bridge() -> None:
    execute_method_source = _read_text(EXECUTE_METHOD_PATH)
    bridge_source = _read_text(POST_EXECUTION_BRIDGE_PATH)
    language_repair_tokens = {
        "_apply_deterministic_go_module_import_repair",
        "run_all_rust_post_repairs",
        "run_all_cpp_post_repairs",
        "run_all_java_post_repairs",
    }

    assert "run_post_execution_language_repairs" in execute_method_source
    assert not any(token in execute_method_source for token in language_repair_tokens)
    assert all(token in bridge_source for token in language_repair_tokens)


def test_graph_catalog_keeps_repair_kernel_owned_by_director_runtime() -> None:
    cells = _load_catalog_cells()
    roles_adapters = cells["roles.adapters"]
    director_runtime = cells["director.runtime"]

    roles_modules = _catalog_strings(roles_adapters, "current_modules")
    roles_owned_paths = _catalog_strings(roles_adapters, "owned_paths")
    director_modules = _catalog_strings(director_runtime, "current_modules")

    assert "polaris.cells.director.runtime.internal.repair_kernel" in director_modules
    assert "polaris.cells.director.runtime.public.service" in director_modules
    assert not any(module.endswith(".repair_kernel") for module in roles_modules)
    assert not any(module.endswith(".deterministic_repairs.strategy_catalog") for module in roles_modules)
    assert "polaris/cells/roles/adapters/internal/director/repair_kernel/**" not in roles_owned_paths
    assert (
        "polaris/cells/roles/adapters/internal/director/deterministic_repairs/strategy_catalog.py"
        not in roles_owned_paths
    )
