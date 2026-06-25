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
MATERIALIZATION_QUALITY_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "materialization_quality_repair_bridge.py"
GENERIC_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "generic_repairs.py"
QUALITY_GATE_PATH = ROLES_DIRECTOR_ROOT / "quality_gate.py"
DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "service.py"
)
FACTORY_STAGE_EXECUTOR_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "factory" / "pipeline" / "internal" / "factory_stage_executor.py"
)

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


def _called_function_names(path: Path) -> set[str]:
    tree = ast.parse(_read_text(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


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


def test_execute_method_does_not_import_specific_deterministic_repair_modules() -> None:
    deterministic_repair_imports = sorted(
        {
            module
            for module in _imported_modules(EXECUTE_METHOD_PATH)
            if module.startswith("polaris.cells.roles.adapters.internal.director.deterministic_repairs.")
        }
    )

    assert deterministic_repair_imports == []


def test_execute_method_projects_revalidation_evidence_through_runtime_public_contract() -> None:
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    execute_source = _read_text(EXECUTE_METHOD_PATH)

    assert "AttachDirectorRepairRevalidationEvidenceV1" in execute_source
    assert "project_director_repair_revalidation_evidence" in execute_calls
    assert "authority_hash" not in execute_source
    assert "projection_hash" not in execute_source


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
    assert "query_director_repair_post_execution_schedule" in bridge_source
    assert "_POST_EXECUTION_REPAIR_RUNNERS" in bridge_source
    assert "_POST_EXECUTION_REPAIR_STEPS" not in bridge_source
    assert "class PostExecutionRepairStep" not in bridge_source


def test_post_execution_schedule_catalog_stays_inside_runtime_internal_kernel() -> None:
    public_service_source = _read_text(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH)

    assert "post_execution_repair_schedule" in public_service_source
    assert "_POST_EXECUTION_REPAIR_SCHEDULE" not in public_service_source
    assert "_ordered_post_execution_schedule_steps" not in public_service_source


def test_materialization_quality_repairs_stay_behind_bridge_and_public_boundary() -> None:
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    factory_calls = _called_function_names(FACTORY_STAGE_EXECUTOR_PATH)
    execute_source = _read_text(EXECUTE_METHOD_PATH)
    factory_source = _read_text(FACTORY_STAGE_EXECUTOR_PATH)

    assert "_apply_deterministic_materialization_quality_repairs" not in execute_calls
    assert "run_materialization_quality_repairs" in execute_calls
    assert "_apply_deterministic_materialization_quality_repairs" not in execute_source
    assert "_apply_deterministic_materialization_quality_repairs" not in factory_calls
    assert "_apply_deterministic_materialization_quality_repairs" not in factory_source
    assert "apply_deterministic_materialization_quality_repairs" in factory_calls


def test_roles_adapter_repair_summaries_use_runtime_typed_projection_contract() -> None:
    paths = [
        POST_EXECUTION_BRIDGE_PATH,
        MATERIALIZATION_QUALITY_BRIDGE_PATH,
        GENERIC_REPAIRS_PATH,
        QUALITY_GATE_PATH,
    ]
    violations: list[str] = []
    for path in paths:
        source = _read_text(path)
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        if "build_director_repair_kernel_summary" in source:
            violations.append(f"{rel_path}: uses legacy summary helper")
        if "ProjectDirectorRepairKernelSummaryV1" not in source:
            violations.append(f"{rel_path}: missing typed summary projection command")
        if "project_director_repair_kernel_summary" not in source:
            violations.append(f"{rel_path}: missing typed summary projection service")

    assert violations == []


def test_graph_catalog_keeps_repair_kernel_owned_by_director_runtime() -> None:
    cells = _load_catalog_cells()
    roles_adapters = cells["roles.adapters"]
    director_runtime = cells["director.runtime"]

    roles_modules = _catalog_strings(roles_adapters, "current_modules")
    roles_owned_paths = _catalog_strings(roles_adapters, "owned_paths")
    director_modules = _catalog_strings(director_runtime, "current_modules")
    director_commands = set(director_runtime.get("public_contracts", {}).get("commands", []))
    director_queries = set(director_runtime.get("public_contracts", {}).get("queries", []))
    director_results = set(director_runtime.get("public_contracts", {}).get("results", []))

    assert "polaris.cells.director.runtime.internal.repair_kernel" in director_modules
    assert "polaris.cells.director.runtime.public.service" in director_modules
    assert "AttachDirectorRepairRevalidationEvidenceV1" in director_commands
    assert "ProjectDirectorRepairKernelSummaryV1" in director_commands
    assert "DirectorRepairKernelSummaryProjectionResultV1" in director_results
    assert "QueryDirectorRepairPostExecutionScheduleV1" in director_queries
    assert "DirectorRepairPostExecutionScheduleResultV1" in director_results
    assert "DirectorRepairPostExecutionStepV1" in director_results
    assert "DirectorRepairRevalidationProjectionResultV1" in director_results
    assert not any(module.endswith(".repair_kernel") for module in roles_modules)
    assert not any(module.endswith(".deterministic_repairs.strategy_catalog") for module in roles_modules)
    assert "polaris/cells/roles/adapters/internal/director/repair_kernel/**" not in roles_owned_paths
    assert (
        "polaris/cells/roles/adapters/internal/director/deterministic_repairs/strategy_catalog.py"
        not in roles_owned_paths
    )
