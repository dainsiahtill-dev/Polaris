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
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    query_director_repair_materialization_quality_schedule,
    query_director_repair_post_execution_schedule,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
ROLES_DIRECTOR_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "director"
EXECUTE_METHOD_PATH = ROLES_DIRECTOR_ROOT / "execute_method.py"
POST_EXECUTION_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "post_execution_repair_bridge.py"
MATERIALIZATION_QUALITY_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "materialization_quality_repair_bridge.py"
GENERIC_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "generic_repairs.py"
RUNTIME_REPAIR_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "_runtime_bridge.py"
RUST_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "rust_repairs.py"
QUALITY_GATE_PATH = ROLES_DIRECTOR_ROOT / "quality_gate.py"
DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "service.py"
)
DIRECTOR_RUNTIME_PUBLIC_INIT_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "__init__.py"
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
MIGRATED_RUNTIME_REPAIR_EXPORTS_FORBIDDEN_IN_EXECUTE_METHOD = {
    "_apply_deterministic_typescript_return_object_semicolon_repair",
    "_parse_typescript_return_object_semicolon_paths",
    "_repair_typescript_return_object_semicolon_lines",
}
ALLOWED_EXECUTE_METHOD_LEGACY_DETERMINISTIC_REPAIR_CALLS = {
    "_apply_deterministic_declared_target_contract_repairs",
    "_apply_deterministic_node_test_script_contract_repair",
    "_apply_deterministic_patch_residue_cleanup",
    "_apply_deterministic_pre_materialization_declared_target_repairs",
    "_apply_deterministic_python_runtime_smoke",
    "_apply_deterministic_python_static_smoke",
    "_apply_deterministic_python_unittest_missing_target_repair",
    "_apply_deterministic_scaffold_marker_cleanup",
    "_apply_deterministic_typescript_reexport_repair",
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


def _called_deterministic_repair_names(path: Path) -> set[str]:
    return {name for name in _called_function_names(path) if name.startswith("_apply_deterministic_")}


def _function_source(path: Path, function_name: str) -> str:
    source = _read_text(path)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function not found: {function_name}")


def _module_level_dict_keys(path: Path, variable_name: str) -> list[str]:
    tree = ast.parse(_read_text(path))
    for node in tree.body:
        value = _assignment_value_for_name(node, variable_name)
        if value is None:
            continue
        if not isinstance(value, ast.Dict):
            raise AssertionError(f"{variable_name} is not a literal dict")
        return _literal_dict_string_keys(value, variable_name=variable_name)
    raise AssertionError(f"module-level dict not found: {variable_name}")


def _assignment_value_for_name(node: ast.stmt, variable_name: str) -> ast.expr | None:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == variable_name for target in node.targets
    ):
        return node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == variable_name:
        return node.value
    return None


def _literal_dict_string_keys(node: ast.Dict, *, variable_name: str) -> list[str]:
    keys: list[str] = []
    for key in node.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise AssertionError(f"{variable_name} contains non-string literal key")
        keys.append(key.value)
    return keys


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


def test_execute_method_does_not_reexport_migrated_runtime_repairs() -> None:
    execute_method_source = _read_text(EXECUTE_METHOD_PATH)

    assert not any(
        symbol in execute_method_source for symbol in MIGRATED_RUNTIME_REPAIR_EXPORTS_FORBIDDEN_IN_EXECUTE_METHOD
    )


def test_execute_method_legacy_deterministic_repair_calls_are_explicitly_bounded() -> None:
    deterministic_repair_calls = _called_deterministic_repair_names(EXECUTE_METHOD_PATH)

    assert deterministic_repair_calls == ALLOWED_EXECUTE_METHOD_LEGACY_DETERMINISTIC_REPAIR_CALLS


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
    runner_step_ids = _module_level_dict_keys(POST_EXECUTION_BRIDGE_PATH, "_POST_EXECUTION_REPAIR_RUNNERS")
    language_repair_tokens = {
        "_apply_deterministic_go_module_import_repair",
        "run_all_rust_post_repairs",
    }
    public_schedule = query_director_repair_post_execution_schedule(
        QueryDirectorRepairPostExecutionScheduleV1(include_items=True)
    )
    expected_runtime_step_ids = [
        "go.module_import",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    public_runtime_step_ids = [step.step_id for step in public_schedule.items]

    assert "run_post_execution_language_repairs" in execute_method_source
    assert not any(token in execute_method_source for token in language_repair_tokens)
    assert all(token in bridge_source for token in language_repair_tokens)
    assert public_runtime_step_ids == expected_runtime_step_ids
    assert runner_step_ids == expected_runtime_step_ids
    assert runner_step_ids == public_runtime_step_ids
    assert "run_all_cpp_post_repairs" not in bridge_source
    assert "repair_cpp_invalid_placeholder_declarations" not in bridge_source
    assert "repair_cpp_missing_private_members" not in bridge_source
    assert "repair_cpp_missing_standard_includes" not in bridge_source
    assert "repair_cpp_struct_getter_field_access" not in bridge_source
    assert "deterministic_cpp_include_path_repair" in bridge_source
    assert "deterministic_cpp_missing_private_members_repair" in bridge_source
    assert "deterministic_cpp_placeholder_declaration_repair" in bridge_source
    assert "deterministic_cpp_standard_include_repair" in bridge_source
    assert "deterministic_cpp_struct_getter_field_access_repair" in bridge_source
    assert "run_all_java_post_repairs" not in bridge_source
    assert "deterministic_java_accessor_alias_repair" in bridge_source
    assert "run_director_repair" in bridge_source
    assert "run_director_post_execution_repair_schedule" in bridge_source
    assert "query_director_repair_post_execution_schedule" in bridge_source
    assert "_POST_EXECUTION_REPAIR_RUNNERS" in bridge_source
    assert "_POST_EXECUTION_REPAIR_STEPS" not in bridge_source
    assert "class PostExecutionRepairStep" not in bridge_source
    assert "_ordered_post_execution_steps" not in bridge_source
    assert "_annotate_bridge_step" not in bridge_source


def test_rust_post_execution_callback_does_not_own_convergence_loop() -> None:
    rust_runner_source = _function_source(RUST_REPAIRS_PATH, "run_all_rust_post_repairs")
    rust_annotation_source = _function_source(RUST_REPAIRS_PATH, "_annotate_rust_post_repair_records")

    assert "for round_number in range" not in rust_runner_source
    assert "seen_stderr" not in rust_runner_source
    assert "max_rounds =" not in rust_runner_source
    assert '"round_number"' not in rust_annotation_source
    assert '"max_rounds"' not in rust_annotation_source
    assert '"revalidation"' in rust_annotation_source


def test_materialization_quality_bridge_consumes_runtime_owned_schedule() -> None:
    bridge_source = _read_text(MATERIALIZATION_QUALITY_BRIDGE_PATH)
    runner_step_ids = _module_level_dict_keys(
        MATERIALIZATION_QUALITY_BRIDGE_PATH, "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS"
    )
    public_schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    expected_runtime_step_ids = [
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    ]
    public_runtime_step_ids = [step.step_id for step in public_schedule.items]

    assert "run_director_materialization_quality_repair_schedule" in bridge_source
    assert "DirectorRepairMaterializationQualityStepV1" in bridge_source
    assert "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS" in bridge_source
    assert public_runtime_step_ids == expected_runtime_step_ids
    assert runner_step_ids == expected_runtime_step_ids
    assert runner_step_ids == public_runtime_step_ids
    assert "runtime_schedule_step_runner_adapter" in bridge_source
    assert "legacy_strategy_host_wrapper" not in bridge_source
    assert "materialization.quality_repair_host" not in bridge_source
    assert "materialization.typescript_compiler" in bridge_source
    assert "_apply_deterministic_materialization_quality_repairs" not in bridge_source


def test_post_execution_schedule_catalog_stays_inside_runtime_internal_kernel() -> None:
    public_service_source = _read_text(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH)

    assert "post_execution_repair_schedule" in public_service_source
    assert "_POST_EXECUTION_REPAIR_SCHEDULE" not in public_service_source
    assert "_ordered_post_execution_schedule_steps" not in public_service_source


def test_runtime_public_service_does_not_own_language_repair_execution_flow() -> None:
    modules = set(_imported_modules(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH))
    calls = _called_function_names(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH)
    source = _read_text(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH)
    public_init_source = _read_text(DIRECTOR_RUNTIME_PUBLIC_INIT_PATH)

    assert "polaris.cells.director.runtime.internal.repair_kernel.composer" not in modules
    assert "polaris.cells.director.runtime.internal.repair_kernel.executor" not in modules
    assert "polaris.cells.director.runtime.internal.repair_kernel.policy_gate" not in modules
    assert "polaris.cells.director.runtime.internal.repair_kernel.typescript_syntax" not in modules
    assert "polaris.cells.director.runtime.internal.repair_kernel.typescript_runtime" not in modules
    assert "PatchComposer" not in calls
    assert "RepairPolicyGate" not in calls
    assert "RepairPolicyContext" not in calls
    assert "TransactionalRepairExecutor" not in calls
    assert "build_typescript_object_literal_comma_plan" not in source
    assert "plan_director_repair" in source
    assert "run_director_repair" in source
    assert "plan_director_typescript" not in source
    assert "run_director_typescript" not in source
    assert "TypeScriptObjectLiteral" not in source
    assert "plan_director_typescript" not in public_init_source
    assert "run_director_typescript" not in public_init_source


def test_runtime_public_service_exposes_strategy_catalog_only_as_query_projection() -> None:
    public_service_source = _read_text(DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH)
    public_init_source = _read_text(DIRECTOR_RUNTIME_PUBLIC_INIT_PATH)
    forbidden_public_helpers = {
        '"KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS"',
        '"DeterministicRepairStrategy"',
        '"describe_deterministic_repair_strategy"',
        '"deterministic_repair_source_tool_known"',
        '"deterministic_repair_strategy_catalog"',
        '"summarize_deterministic_repair_source_tools"',
    }

    assert "query_director_repair_strategy_catalog" in public_service_source
    assert "query_director_repair_strategy_catalog" in public_init_source
    assert all(token not in public_service_source for token in forbidden_public_helpers)
    assert all(token not in public_init_source for token in forbidden_public_helpers)


def test_roles_adapters_consumes_repair_strategy_profiles_through_projection_helper() -> None:
    violations: list[str] = []
    forbidden_names = {
        "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
        "DeterministicRepairStrategy",
        "describe_deterministic_repair_strategy",
        "deterministic_repair_source_tool_known",
        "deterministic_repair_strategy_catalog",
    }
    for path in _python_source_files(ROLES_DIRECTOR_ROOT):
        if path.name == "repair_profile_projection.py":
            continue
        source = _read_text(path)
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        for name in forbidden_names:
            if name in source:
                violations.append(f"{rel_path}: {name}")

    assert violations == []


def test_go_bare_import_string_repair_runs_through_director_runtime_kernel() -> None:
    source = _read_text(GENERIC_REPAIRS_PATH)
    bridge_source = _read_text(RUNTIME_REPAIR_BRIDGE_PATH)

    assert "deterministic_go_bare_import_string_repair" in source
    assert "run_runtime_repair_with_director_tools" in source
    assert "executor_factory=DirectorToolExecutor" in source
    assert "use_editor=False" in source
    assert "run_director_repair" not in source
    assert "RunDirectorRepairCommandV1" not in source
    assert "plan_director_repair" in bridge_source
    assert "PlanDirectorRepairCommandV1" in bridge_source
    assert "run_director_repair" in bridge_source
    assert "RunDirectorRepairCommandV1" in bridge_source
    assert "planning_preflight" in bridge_source
    assert '"write_file"' in bridge_source
    assert '"edit_file"' in bridge_source
    assert "repair_go_bare_import_strings(" not in source


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


def test_quality_gate_semantic_repairs_stay_behind_bridge() -> None:
    quality_source = _read_text(QUALITY_GATE_PATH)
    bridge_source = _read_text(MATERIALIZATION_QUALITY_BRIDGE_PATH)

    assert "run_typescript_semantic_quality_repairs" in quality_source
    assert "_apply_deterministic_typescript_missing_export_repair" not in quality_source
    assert "_apply_deterministic_typescript_canvas_scale_return_type_repair" not in quality_source
    assert "def run_typescript_semantic_quality_repairs(" in bridge_source
    assert "_apply_deterministic_typescript_missing_export_repair" in bridge_source
    assert "_apply_deterministic_typescript_canvas_scale_return_type_repair" in bridge_source


def test_roles_adapter_repair_summaries_use_runtime_typed_projection_contract() -> None:
    facade_path = ROLES_DIRECTOR_ROOT / "repair_profile_projection.py"
    facade_source = _read_text(facade_path)
    adapter_projection_callers = [
        POST_EXECUTION_BRIDGE_PATH,
        MATERIALIZATION_QUALITY_BRIDGE_PATH,
        GENERIC_REPAIRS_PATH,
        QUALITY_GATE_PATH,
    ]
    violations: list[str] = []
    if "ProjectDirectorRepairKernelSummaryV1" not in facade_source:
        violations.append("repair_profile_projection.py: missing typed summary projection command")
    if "project_director_repair_kernel_summary" not in facade_source:
        violations.append("repair_profile_projection.py: missing typed summary projection service")
    if "def project_repair_kernel_summary(" not in facade_source:
        violations.append("repair_profile_projection.py: missing adapter summary projection facade")
    for path in adapter_projection_callers:
        source = _read_text(path)
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        if "build_director_repair_kernel_summary" in source:
            violations.append(f"{rel_path}: uses legacy summary helper")
        if "ProjectDirectorRepairKernelSummaryV1" in source:
            violations.append(f"{rel_path}: bypasses adapter summary projection facade command")
        if "project_director_repair_kernel_summary" in source:
            violations.append(f"{rel_path}: bypasses adapter summary projection facade service")
        if "project_repair_kernel_summary" not in source:
            violations.append(f"{rel_path}: missing adapter summary projection facade")

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
