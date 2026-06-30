"""Architecture fences for Director repair-kernel ownership.

These checks lock the deterministic-repair convergence boundary:
``roles.adapters`` may keep legacy deterministic repair functions during
migration, but it must not own the repair kernel, import the Director Runtime
internal repair-kernel package, or restore the old strategy catalog fact source.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import yaml
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import runtime_repair_bindings
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_coverage,
    query_director_repair_language_slots,
    query_director_repair_materialization_quality_schedule,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
    run_director_materialization_quality_repair_schedule_result,
    run_director_post_execution_repair_schedule_result,
    validate_director_repair_advisory,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
ROLES_DIRECTOR_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "director"
ROLES_ADAPTERS_PUBLIC_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "public"
ROLES_ADAPTERS_PUBLIC_SERVICE_PATH = ROLES_ADAPTERS_PUBLIC_ROOT / "service.py"
ROLES_ADAPTERS_PUBLIC_INIT_PATH = ROLES_ADAPTERS_PUBLIC_ROOT / "__init__.py"
ROLES_ADAPTERS_TESTS_ROOT = BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "tests"
ROLES_ADAPTERS_STRATEGY_CATALOG_TEST_PATH = ROLES_ADAPTERS_TESTS_ROOT / "test_deterministic_repair_strategy_catalog.py"
ROLES_ADAPTERS_DESCRIPTOR_PACK_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "generated" / "descriptor.pack.json"
)
QA_ROOT = BACKEND_ROOT / "polaris" / "cells" / "qa"
EXECUTE_METHOD_PATH = ROLES_DIRECTOR_ROOT / "execute_method.py"
EXECUTE_METHOD_REPAIR_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "execute_method_repair_bridge.py"
EXECUTE_METHOD_REPAIR_BRIDGE_MODULE = "polaris.cells.roles.adapters.internal.director.execute_method_repair_bridge"
POST_EXECUTION_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "post_execution_repair_bridge.py"
MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH = ROLES_DIRECTOR_ROOT / "materialization_quality_callback_ports.py"
MATERIALIZATION_QUALITY_EVIDENCE_PORTS_PATH = ROLES_DIRECTOR_ROOT / "materialization_quality_evidence_ports.py"
MATERIALIZATION_QUALITY_RUNTIME_PORTS_PATH = ROLES_DIRECTOR_ROOT / "materialization_quality_runtime_ports.py"
DETERMINISTIC_REPAIRS_INIT_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "__init__.py"
DETERMINISTIC_REPAIRS_ROOT = ROLES_DIRECTOR_ROOT / "deterministic_repairs"
GENERIC_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "generic_repairs.py"
RUNTIME_REPAIR_BRIDGE_PATH = ROLES_DIRECTOR_ROOT / "runtime_repair_tool_adapter.py"
RUST_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "rust_repairs.py"
GO_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "go_repairs.py"
CPP_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "cpp_repairs.py"
JAVA_REPAIRS_PATH = ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "java_repairs.py"
QUALITY_GATE_PATH = ROLES_DIRECTOR_ROOT / "quality_gate.py"
DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "service.py"
)
DIRECTOR_RUNTIME_PUBLIC_CONTRACTS_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "contracts.py"
)
DIRECTOR_RUNTIME_PUBLIC_INIT_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "public" / "__init__.py"
)
DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_ROOT = (
    BACKEND_ROOT / "polaris" / "cells" / "director" / "runtime" / "internal" / "repair_kernel"
)
FACTORY_STAGE_EXECUTOR_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "factory" / "pipeline" / "internal" / "factory_stage_executor.py"
)
FACTORY_WORKSPACE_QUALITY_PATH = (
    BACKEND_ROOT / "polaris" / "cells" / "factory" / "pipeline" / "internal" / "factory_workspace_quality.py"
)
REPAIR_BOUNDARY_FAILURE_HINT = (
    "Director repair boundary violation: use polaris.cells.director.runtime.public "
    "or the controlled roles.adapters bridge; do not restore legacy deterministic "
    "repair helper imports/calls."
)

FORBIDDEN_IMPORT_PREFIXES = (
    "polaris.cells.director.runtime.internal.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.deterministic_repairs.strategy_catalog",
)
ALLOWED_EXECUTE_METHOD_DIRECTOR_RUNTIME_IMPORTS = {
    "polaris.cells.director.runtime.public.contracts",
    "polaris.cells.director.runtime.public.service",
}
CONCRETE_LEGACY_REPAIR_NAME_PREFIXES = ("_apply_deterministic_",)
CONCRETE_LEGACY_REPAIR_EXPORT_PREFIXES = (
    "_apply_deterministic_",
    "_repair_",
    "repair_",
)
LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST_REASONS: dict[Path, str] = {}
LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST = set(LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST_REASONS)
SCHEDULE_RUNNER_BINDING_BRIDGES = {
    MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH,
    POST_EXECUTION_BRIDGE_PATH,
}
DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_IMPORT_ALLOWLIST = {
    DIRECTOR_RUNTIME_PUBLIC_CONTRACTS_PATH,
    DIRECTOR_RUNTIME_PUBLIC_SERVICE_PATH,
}
PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS: dict[str, str] = {}
PUBLIC_MIGRATION_SHIM_TEST_REFERENCE_ALLOWLIST = {
    (
        ROLES_ADAPTERS_STRATEGY_CATALOG_TEST_PATH,
        "test_legacy_public_repair_wrappers_are_migration_only_compatibility_shims",
    ),
    (
        Path(__file__).resolve(),
        "test_roles_public_old_named_repair_wrappers_are_migration_only",
    ),
    (
        Path(__file__).resolve(),
        "test_public_migration_repair_shims_are_not_new_cross_cell_fact_sources",
    ),
    (
        Path(__file__).resolve(),
        "test_roles_adapters_public_legacy_repair_wrappers_are_migration_only_shims",
    ),
}
MIGRATED_RUNTIME_REPAIR_EXPORTS_FORBIDDEN_IN_EXECUTE_METHOD = {
    "_apply_deterministic_typescript_return_object_semicolon_repair",
    "_parse_typescript_return_object_semicolon_paths",
    "_repair_typescript_return_object_semicolon_lines",
}
MIGRATED_TYPESCRIPT_SOURCE_TOOL_PREFIXES = (
    "deterministic_typescript",
    "deterministic_html_typescript",
    "deterministic_typeorm",
)
MIGRATED_TYPESCRIPT_SOURCE_TOOL_NAMES = {
    "deterministic_javascript_typescript_annotation_repair",
}
EXPECTED_EXECUTE_METHOD_REPAIR_BRIDGE_COMPAT_ALLOWLIST: set[str] = set()
MIGRATED_EXECUTE_METHOD_COMPAT_HELPERS_FORBIDDEN = {
    "_apply_deterministic_javascript_test_missing_target_repair",
    "_apply_deterministic_javascript_esm_commonjs_entrypoint_repair",
    "_apply_deterministic_javascript_missing_export_repair",
    "_apply_deterministic_javascript_missing_method_runtime_repair",
    "_apply_deterministic_node_test_script_contract_repair",
    "_apply_deterministic_html_typescript_module_script_repair",
    "_apply_deterministic_scaffold_marker_cleanup",
    "_apply_deterministic_scaffold_marker_error_cleanup",
    "_apply_deterministic_pre_materialization_declared_target_repairs",
    "_apply_deterministic_declared_target_contract_repairs",
    "_apply_deterministic_missing_declared_target_repair",
    "_apply_deterministic_javascript_typescript_annotation_repair",
    "_apply_deterministic_npm_test_script_repair",
    "_apply_deterministic_python_package_shadow_bridge_repair",
    "_apply_deterministic_python_runtime_smoke",
    "_apply_deterministic_python_static_smoke",
    "_apply_deterministic_python_unittest_runtime_failure_repair",
    "_apply_deterministic_runtime_dependency_repair",
    "_apply_deterministic_rust_crate_import_repair",
    "_apply_deterministic_rust_derive_repair",
    "_apply_deterministic_rust_line_suggestion_repair",
    "_apply_deterministic_rust_missing_lib_target_repair",
    "_apply_deterministic_rust_lib_root_facade_repair",
    "_apply_deterministic_rust_trait_import_repair",
    "_apply_deterministic_rust_unresolved_pub_use_repair",
    "_apply_deterministic_typeorm_model_normalization_repair",
    "_apply_deterministic_typescript_canvas_scale_return_type_repair",
    "_apply_deterministic_typescript_entrypoint_repair",
    "_apply_deterministic_typescript_escaped_newline_repair",
    "_apply_deterministic_typescript_member_alias_repair",
    "_apply_deterministic_typescript_missing_closing_brace_repair",
    "_apply_deterministic_typescript_missing_export_repair",
    "_apply_deterministic_typescript_missing_member_repair",
    "_apply_deterministic_typescript_number_to_string_argument_repair",
    "_apply_deterministic_typescript_relative_import_case_repair",
    "_apply_deterministic_typescript_reexport_repair",
    "_apply_deterministic_typescript_reexported_type_binding_repair",
    "_apply_deterministic_typescript_sourcefile_diagnostics_repair",
    "_apply_deterministic_typescript_too_few_arguments_repair",
    "_apply_deterministic_typescript_scaffold_repair",
    "_apply_deterministic_typescript_tsconfig_lib_repair",
    "_apply_deterministic_typescript_uninitialized_property_repair",
    "_apply_deterministic_typescript_unresolved_identifier_repair",
    "_apply_deterministic_typescript_vitest_globals_repair",
    "_apply_deterministic_typescript_zod_type_class_collision_repair",
    "_add_vitest_import_to_typescript_test",
    "_build_javascript_missing_method_alias",
    "_parse_javascript_constructor_string_contract_errors",
    "_parse_javascript_missing_method_runtime_errors",
    "_parse_typescript_missing_test_global_errors",
    "_parse_typescript_escaped_newline_paths",
    "_parse_typescript_sourcefile_diagnostics_errors",
    "_looks_like_typescript_reexport_failure",
    "_repair_typescript_escaped_newline_in_line_comments",
    "_repair_javascript_class_missing_methods",
    "_repair_javascript_constructor_object_contracts",
    "_add_typescript_reexported_type_binding",
    "_typescript_missing_identifier_usage_is_type_position",
    "_typescript_reexport_module_for_symbol",
    "_typescript_errors_require_dom_lib",
    "_typescript_errors_require_import_meta_module",
    "_typescript_module_allows_import_meta",
    "_parse_html_typescript_module_script_errors",
    "_html_javascript_entrypoint_for_typescript_source",
    "_HTML_TS_MODULE_SCRIPT_ERROR_RE",
    "_parse_typescript_cannot_find_name_errors",
    "_repair_typescript_unresolved_identifier_lines",
    "_select_typescript_unresolved_identifier_replacement",
    "_typescript_identifier_alias_matches",
    "_TS_CANNOT_FIND_NAME_ERROR_RE",
    "_repair_typescript_sourcefile_diagnostics_usage",
    "_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE",
    "_typescript_vitest_manifest_repair_content",
    "_apply_deterministic_unresolved_import_symbol_repair",
}
ALLOWED_EXECUTE_METHOD_LEGACY_DETERMINISTIC_REPAIR_CALLS: set[str] = set()
FACTORY_WORKSPACE_QUALITY_DIRECT_WRITE_MIGRATION_DEBT: set[str] = set()


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


def _production_python_source_files(root: Path) -> list[Path]:
    return [path for path in _python_source_files(root) if "tests" not in path.parts and "generated" not in path.parts]


def _test_python_source_files() -> list[Path]:
    return [
        path
        for path in _python_source_files(BACKEND_ROOT)
        if "generated" not in path.parts and ("tests" in path.parts or path.name.startswith("test_"))
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


def _import_references(path: Path) -> list[str]:
    tree = ast.parse(_read_text(path))
    references: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _resolve_import_from_module(path, node)
        if module:
            references.append(module)
        for alias in node.names:
            if alias.name == "*":
                references.append(f"{module}.*" if module else "*")
            elif module:
                references.append(f"{module}.{alias.name}")
            else:
                references.append(alias.name)
    return references


def _called_function_names(path: Path) -> set[str]:
    tree = ast.parse(_read_text(path))
    return _called_function_names_in_node(tree)


def _called_function_names_in_node(root: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(root):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node)
        if call_name:
            names.add(call_name)
    return names


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _function_definitions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(_read_text(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]


def _test_function_public_migration_shim_references(path: Path) -> list[tuple[str, int, list[str]]]:
    tree = ast.parse(_read_text(path))
    shim_names = set(PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS)
    references: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith("test_"):
            continue
        observed: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in shim_names:
                observed.add(child.id)
            elif isinstance(child, ast.Attribute) and child.attr in shim_names:
                observed.add(child.attr)
            elif isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in shim_names:
                observed.add(child.value)
        if observed:
            references.append((node.name, node.lineno, sorted(observed)))
    return references


def _called_function_names_in_function(path: Path, function_name: str) -> set[str]:
    for node in _function_definitions(path):
        if node.name == function_name:
            return _called_function_names_in_node(node)
    raise AssertionError(f"function not found: {function_name}")


def _is_concrete_legacy_repair_name(name: str) -> bool:
    return name.startswith(CONCRETE_LEGACY_REPAIR_NAME_PREFIXES) or (
        name.startswith("deterministic_") and name.endswith("_repair")
    )


def _is_public_old_named_repair_wrapper(name: str) -> bool:
    return name.startswith("apply_deterministic_")


def _called_deterministic_repair_names(path: Path) -> set[str]:
    tree = ast.parse(_read_text(path))
    imports_legacy_repair_host = any(
        reference == "polaris.cells.roles.adapters.internal.director.deterministic_repairs"
        or reference.startswith("polaris.cells.roles.adapters.internal.director.deterministic_repairs.")
        for reference in _import_references(path)
    )
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and _is_concrete_legacy_repair_name(node.func.id):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute) and _is_concrete_legacy_repair_name(node.func.attr):
            names.add(node.func.attr)
        elif (
            imports_legacy_repair_host
            and isinstance(node.func, ast.Name)
            and str(node.func.id or "").startswith("repair_")
        ):
            names.add(node.func.id)
        elif (
            imports_legacy_repair_host
            and isinstance(node.func, ast.Attribute)
            and str(node.func.attr or "").startswith("repair_")
        ):
            names.add(node.func.attr)
    return names


def _concrete_legacy_repair_imports(path: Path) -> list[str]:
    tree = ast.parse(_read_text(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _resolve_import_from_module(path, node)
        if ".deterministic_repairs" not in module:
            continue
        for alias in node.names:
            if _is_concrete_legacy_repair_name(alias.name):
                imports.append(f"{module}.{alias.name}")
    return imports


def _repair_boundary_source_files() -> list[Path]:
    roots = [
        BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal",
        ROLES_ADAPTERS_PUBLIC_ROOT,
        BACKEND_ROOT / "polaris" / "cells" / "factory",
        QA_ROOT,
        BACKEND_ROOT / "scripts" / "factory_bench",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in _python_source_files(root):
            if "deterministic_repairs" in path.parts:
                continue
            files.append(path)
    return sorted(set(files))


def _factory_qa_bench_source_files() -> list[Path]:
    roots = [
        BACKEND_ROOT / "polaris" / "cells" / "factory",
        QA_ROOT,
        BACKEND_ROOT / "scripts" / "factory_bench",
    ]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(_production_python_source_files(root))
    return sorted(set(files))


def _repair_boundary_import_source_files() -> list[Path]:
    roots = [
        BACKEND_ROOT / "polaris",
        BACKEND_ROOT / "scripts",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in _production_python_source_files(root):
            if "deterministic_repairs" in path.parts:
                continue
            files.append(path)
    return sorted(set(files))


def _product_python_source_files() -> list[Path]:
    roots = [
        BACKEND_ROOT / "polaris",
        BACKEND_ROOT / "scripts",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        files.extend(_production_python_source_files(root))
    return sorted(set(files))


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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


def _module_literal_string_list(path: Path, variable_name: str) -> list[str]:
    tree = ast.parse(_read_text(path))
    for node in tree.body:
        value = _assignment_value_for_name(node, variable_name)
        if value is None:
            continue
        if not isinstance(value, ast.List):
            raise AssertionError(f"{variable_name} is not a literal list")
        values: list[str] = []
        for item in value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                raise AssertionError(f"{variable_name} contains non-string literal item")
            values.append(item.value)
        return values
    raise AssertionError(f"module-level list not found: {variable_name}")


def _module_literal_string_frozenset(path: Path, variable_name: str) -> list[str]:
    tree = ast.parse(_read_text(path))
    for node in tree.body:
        value = _assignment_value_for_name(node, variable_name)
        if value is None:
            continue
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            if len(value.args) == 0:
                return []
            if len(value.args) != 1:
                raise AssertionError(f"{variable_name} frozenset must have one literal set arg")
            value = value.args[0]
        if not isinstance(value, ast.Set):
            raise AssertionError(f"{variable_name} is not a literal frozenset")
        values: list[str] = []
        for item in value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                raise AssertionError(f"{variable_name} contains non-string literal item")
            values.append(item.value)
        return values
    raise AssertionError(f"module-level frozenset not found: {variable_name}")


def _forbidden_import(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORT_PREFIXES)


def _catalog_strings(cell: dict[str, Any], key: str) -> set[str]:
    values = cell.get(key, [])
    assert isinstance(values, list)
    return {str(value) for value in values}


def _repair_named_helper_write_primitives(path: Path) -> list[str]:
    write_call_names = {
        "edit_file",
        "rmdir",
        "rmtree",
        "touch",
        "unlink",
        "write_bytes",
        "write_file",
        "write_text",
    }
    violations: list[str] = []
    for function in _function_definitions(path):
        if "_repair_" not in function.name:
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            if call_name in write_call_names or _is_write_mode_open_call(node):
                rel_path = path.relative_to(BACKEND_ROOT).as_posix()
                violations.append(f"{rel_path}:{function.name}:{call_name or 'open(write-mode)'}")
    return violations


def _legacy_strategy_host_write_primitives() -> list[str]:
    write_call_names = {
        "open",
        "write_bytes",
        "write_file",
        "write_text",
    }
    allowed = {
        DETERMINISTIC_REPAIRS_ROOT / "_common.py": {"controlled_legacy_write_text"},
    }
    violations: list[str] = []
    for path in _production_python_source_files(DETERMINISTIC_REPAIRS_ROOT):
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        allowed_functions = allowed.get(path, set())
        for function in _function_definitions(path):
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                call_name = _call_name(node)
                if call_name not in write_call_names and not _is_write_mode_open_call(node):
                    continue
                if function.name in allowed_functions:
                    continue
                violations.append(f"{rel_path}:{function.name}:{call_name or 'open(write-mode)'}")
    return violations


def _factory_workspace_quality_repair_write_primitives() -> dict[str, list[str]]:
    write_call_names = {
        "open",
        "write_bytes",
        "write_file",
        "write_text",
    }
    violations: dict[str, list[str]] = {}
    for function in _function_definitions(FACTORY_WORKSPACE_QUALITY_PATH):
        if not function.name.startswith("repair"):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node)
            if call_name in write_call_names or _is_write_mode_open_call(node):
                violations.setdefault(function.name, []).append(call_name or "open(write-mode)")
    return violations


def _is_write_mode_open_call(node: ast.Call) -> bool:
    call_name = _call_name(node)
    if call_name != "open":
        return False
    mode: str | None = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
        mode = node.args[1].value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            mode = keyword.value.value
    return mode is not None and any(flag in mode for flag in ("w", "a", "x", "+"))


def test_roles_adapters_does_not_own_director_repair_kernel_package() -> None:
    payload_files = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in sorted((ROLES_DIRECTOR_ROOT / "repair_kernel").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]

    assert payload_files == []
    assert not (ROLES_DIRECTOR_ROOT / "deterministic_repairs" / "strategy_catalog.py").exists()


def test_factory_workspace_quality_repair_helpers_do_not_direct_write_files() -> None:
    direct_write_repairs = _factory_workspace_quality_repair_write_primitives()

    assert set(direct_write_repairs) <= FACTORY_WORKSPACE_QUALITY_DIRECT_WRITE_MIGRATION_DEBT
    assert set(direct_write_repairs) == FACTORY_WORKSPACE_QUALITY_DIRECT_WRITE_MIGRATION_DEBT


def test_director_runtime_repair_public_contract_exports_are_in_graph_catalog() -> None:
    cells = _load_catalog_cells()
    director_runtime = cells["director.runtime"]
    contracts = director_runtime.get("public_contracts")
    assert isinstance(contracts, dict)
    catalog_names = {
        str(item) for key in ("commands", "queries", "results", "errors") for item in contracts.get(key, [])
    }
    public_repair_contracts = {
        name
        for name in _module_literal_string_list(DIRECTOR_RUNTIME_PUBLIC_INIT_PATH, "__all__")
        if name and name[0].isupper() and ("Repair" in name or name.startswith("PlanDirectorRepair"))
    }

    assert public_repair_contracts <= catalog_names


def test_roles_adapters_graph_catalog_exposes_only_generic_repair_schedule_boundary() -> None:
    cells = _load_catalog_cells()
    roles_adapters = cells["roles.adapters"]
    contracts = roles_adapters.get("public_contracts")
    assert isinstance(contracts, dict)
    catalog_names = {
        str(item) for key in ("commands", "queries", "results", "events", "errors") for item in contracts.get(key, [])
    }

    assert "run_director_materialization_quality_repair_schedule" in catalog_names
    assert "run_director_post_execution_repair_schedule" in catalog_names
    assert "run_director_cpp_post_execution_repairs" not in catalog_names
    assert "apply_deterministic_cpp_post_repairs" not in catalog_names


def test_roles_adapters_never_imports_director_runtime_internal_repair_kernel() -> None:
    violations: list[str] = []
    for path in _python_source_files(ROLES_DIRECTOR_ROOT):
        for module in _imported_modules(path):
            if _forbidden_import(module):
                violations.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {module}")

    assert violations == [], f"{REPAIR_BOUNDARY_FAILURE_HINT} Violations: {violations}"


def test_product_code_imports_director_repair_kernel_only_through_runtime_public_boundary() -> None:
    violations: list[str] = []
    for path in _product_python_source_files():
        if _path_is_under(path, DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_ROOT):
            continue
        if path in DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_IMPORT_ALLOWLIST:
            continue
        for module in _imported_modules(path):
            if module == "polaris.cells.director.runtime.internal.repair_kernel" or module.startswith(
                "polaris.cells.director.runtime.internal.repair_kernel."
            ):
                rel_path = path.relative_to(BACKEND_ROOT).as_posix()
                violations.append(f"{rel_path}: {module}")

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} product code must use director.runtime.public; "
        f"only the runtime internal package and public facade may import repair_kernel internals. "
        f"Violations: {violations}"
    )


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
            reference
            for reference in _import_references(EXECUTE_METHOD_PATH)
            if reference == "polaris.cells.roles.adapters.internal.director.deterministic_repairs"
            or reference.startswith("polaris.cells.roles.adapters.internal.director.deterministic_repairs.")
        }
    )

    assert deterministic_repair_imports == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} execute_method.py must import controlled bridge/public service, "
        f"not deterministic_repairs directly. Violations: {deterministic_repair_imports}"
    )


def test_legacy_strategy_host_has_single_controlled_write_surface() -> None:
    imports = {
        reference
        for path in _production_python_source_files(DETERMINISTIC_REPAIRS_ROOT)
        for reference in _import_references(path)
    }
    assert "polaris.cells.roles.adapters.internal.director.execution_tools.DirectorToolExecutor" not in imports
    assert not any(reference.endswith(".execution_tools") for reference in imports)
    assert _legacy_strategy_host_write_primitives() == []


def test_execute_method_does_not_own_repair_tool_execution() -> None:
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    forbidden_calls = {
        "DirectorToolExecutor",
        "execute_tool",
        "plan_director_repair",
        "run_director_repair",
        "run_director_repair_convergence",
    }

    assert execute_calls.isdisjoint(forbidden_calls), (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} execute_method.py may orchestrate phases, but repair planning, "
        f"tool execution, convergence, and revalidation must stay behind director.runtime.public and "
        f"the controlled repair bridge. Violations: {sorted(execute_calls & forbidden_calls)}"
    )


def test_execute_factory_qa_and_bench_never_direct_call_legacy_deterministic_repairs() -> None:
    disallowed_paths = [EXECUTE_METHOD_PATH, *_factory_qa_bench_source_files()]
    violations: list[str] = []
    for path in sorted(set(disallowed_paths)):
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        direct_imports = sorted(
            reference
            for reference in _import_references(path)
            if reference == "polaris.cells.roles.adapters.internal.director.deterministic_repairs"
            or reference.startswith("polaris.cells.roles.adapters.internal.director.deterministic_repairs.")
        )
        if direct_imports:
            violations.extend(f"{rel_path}: import {reference}" for reference in direct_imports)
        direct_calls = sorted(_called_deterministic_repair_names(path))
        if direct_calls:
            violations.extend(f"{rel_path}: call {call_name}" for call_name in direct_calls)

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} execute_method.py, Factory, QA, and factory_bench must go through "
        f"director.runtime.public or controlled bridge entrypoints. Violations: {violations}"
    )


def test_only_director_repair_bridges_import_concrete_legacy_repair_functions() -> None:
    violations: list[str] = []
    for path in _repair_boundary_import_source_files():
        concrete_imports = _concrete_legacy_repair_imports(path)
        if not concrete_imports or path in LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST:
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        violations.extend(f"{rel_path}: {item}" for item in concrete_imports)

    assert violations == [], f"{REPAIR_BOUNDARY_FAILURE_HINT} Violations: {violations}"


def test_legacy_repair_bridge_allowlist_is_explicit_and_schedule_limited() -> None:
    expected_bridge_import_allowlist: set[Path] = set()
    assert set(LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST_REASONS) == LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST
    assert expected_bridge_import_allowlist == LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST
    assert len(LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST) == 0
    assert {
        MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH,
        POST_EXECUTION_BRIDGE_PATH,
    } == SCHEDULE_RUNNER_BINDING_BRIDGES
    for path in SCHEDULE_RUNNER_BINDING_BRIDGES:
        source = _read_text(path)
        assert "query_director_repair_" in source
        assert "_REPAIR_RUNNERS" in source
        assert "_REPAIR_STEPS" not in source
        assert "deterministic_repairs" not in source
        assert "_ordered_post_execution_steps" not in source
        assert "_ordered_materialization_quality_steps" not in source
        assert "class PostExecutionRepairStep" not in source
        assert "class MaterializationQualityRepairStep" not in source
    assert "run_director_" in _read_text(POST_EXECUTION_BRIDGE_PATH)
    assert "run_director_materialization_quality_repair_schedule_result" not in _read_text(
        MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH
    )


def test_execute_method_repair_bridge_is_migration_only_and_not_a_public_fact_source() -> None:
    violations: list[str] = []
    allowed_production_importers = {EXECUTE_METHOD_PATH}
    for path in _repair_boundary_import_source_files():
        if path == EXECUTE_METHOD_REPAIR_BRIDGE_PATH or path in allowed_production_importers:
            continue
        bridge_references = sorted(
            {
                reference
                for reference in _import_references(path)
                if reference == EXECUTE_METHOD_REPAIR_BRIDGE_MODULE
                or reference.startswith(f"{EXECUTE_METHOD_REPAIR_BRIDGE_MODULE}.")
            }
        )
        if not bridge_references:
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        violations.append(f"{rel_path}: {', '.join(bridge_references)}")

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} execute_method_repair_bridge.py is a migration-only wrapper; "
        "Factory, QA, bench, public wrappers, and other production code must use director.runtime.public "
        f"or their controlled bridge instead. Violations: {violations}"
    )


def test_execute_method_repair_bridge_compat_allowlist_is_narrow_and_blocks_runtime_tools() -> None:
    bridge_source = _read_text(EXECUTE_METHOD_REPAIR_BRIDGE_PATH)
    runtime_source_tools = {
        str(binding.get("source_tool") or "").strip()
        for binding in runtime_repair_bindings()
        if str(binding.get("source_tool") or "").strip()
    }
    runtime_execute_method_compat_names = {f"_apply_{source_tool}" for source_tool in runtime_source_tools}

    assert "_LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST" not in bridge_source
    assert "__getattr__" not in bridge_source
    assert not EXPECTED_EXECUTE_METHOD_REPAIR_BRIDGE_COMPAT_ALLOWLIST
    assert not any(name in bridge_source for name in MIGRATED_EXECUTE_METHOD_COMPAT_HELPERS_FORBIDDEN)
    assert not any(name in bridge_source for name in runtime_execute_method_compat_names), (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} migrated runtime source_tools must not be exposed through "
        "execute_method.__getattr__ compat."
    )


def test_roles_public_old_named_repair_wrappers_are_migration_only() -> None:
    service_function_names = {node.name for node in _function_definitions(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH)}
    old_named_wrappers = {name for name in service_function_names if _is_public_old_named_repair_wrapper(name)}
    service_exports = set(_module_literal_string_list(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH, "__all__"))
    package_exports = set(_module_literal_string_list(ROLES_ADAPTERS_PUBLIC_INIT_PATH, "__all__"))
    service_source = _read_text(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH)

    assert old_named_wrappers == set(PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS)
    assert old_named_wrappers <= service_exports
    assert old_named_wrappers.isdisjoint(package_exports)
    for shim_name, preferred_entrypoint in PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS.items():
        shim_source = _function_source(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH, shim_name)
        shim_calls = _called_function_names_in_function(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH, shim_name)
        assert "Deprecated migration-only shim" in shim_source
        assert preferred_entrypoint in shim_calls
        assert f"{shim_name}.__deprecated__" in service_source
        assert f"{shim_name}.__migration_only__ = True" in service_source
        assert f"{shim_name}.__preferred_entrypoint__" in service_source
        assert "run_materialization_quality_repairs" not in shim_source
        assert "run_cpp_post_repairs_as_tool_results" not in shim_source


def test_public_migration_repair_shims_are_not_new_cross_cell_fact_sources() -> None:
    violations: list[str] = []
    shim_names = set(PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS)
    for path in _repair_boundary_import_source_files():
        if path == ROLES_ADAPTERS_PUBLIC_SERVICE_PATH:
            continue
        references = {
            reference.rsplit(".", 1)[-1]
            for reference in _import_references(path)
            if reference.rsplit(".", 1)[-1] in shim_names
        }
        calls = _called_function_names(path) & shim_names
        observed = references | calls
        if not observed:
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        violations.append(f"{rel_path}: {', '.join(sorted(observed))}")

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} old-named roles public repair wrappers are migration-only; "
        f"new callers must use preferred runtime-named entrypoints. Violations: {violations}"
    )


def test_public_migration_repair_shim_test_references_are_dedicated_shim_tests() -> None:
    violations: list[str] = []
    for path in _test_python_source_files():
        for function_name, lineno, observed in _test_function_public_migration_shim_references(path):
            if (path, function_name) in PUBLIC_MIGRATION_SHIM_TEST_REFERENCE_ALLOWLIST:
                continue
            rel_path = path.relative_to(BACKEND_ROOT).as_posix()
            violations.append(f"{rel_path}:{lineno} {function_name}: {', '.join(observed)}")

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} old-named roles public repair wrappers may only be "
        f"referenced by dedicated compatibility-shim tests. Violations: {violations}"
    )


def test_factory_qa_and_bench_do_not_import_or_call_direct_legacy_repair_helpers() -> None:
    violations: list[str] = []
    for path in _factory_qa_bench_source_files():
        concrete_imports = _concrete_legacy_repair_imports(path)
        concrete_calls = sorted(_called_deterministic_repair_names(path))
        if not concrete_imports and not concrete_calls:
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        if concrete_imports:
            violations.append(f"{rel_path}: imports {', '.join(concrete_imports)}")
        if concrete_calls:
            violations.append(f"{rel_path}: calls {', '.join(concrete_calls)}")

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} Factory, QA, and bench must not call/import concrete "
        f"legacy repair helpers directly. Violations: {violations}"
    )


def test_public_factory_qa_and_bench_do_not_call_concrete_legacy_repair_functions() -> None:
    violations: list[str] = []
    allowed_direct_call_paths = set(LEGACY_REPAIR_BRIDGE_IMPORT_ALLOWLIST)
    for path in _repair_boundary_source_files():
        if path in allowed_direct_call_paths:
            continue
        concrete_calls = sorted(_called_deterministic_repair_names(path))
        if not concrete_calls:
            continue
        rel_path = path.relative_to(BACKEND_ROOT).as_posix()
        violations.append(f"{rel_path}: {', '.join(concrete_calls)}")

    assert violations == [], f"{REPAIR_BOUNDARY_FAILURE_HINT} Violations: {violations}"


def test_repair_named_helpers_outside_runtime_kernel_remain_read_only() -> None:
    violations: list[str] = []
    for path in _repair_boundary_source_files():
        violations.extend(_repair_named_helper_write_primitives(path))

    assert violations == [], (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} helpers named *_repair_* outside the runtime kernel must "
        f"remain read-only measurement/selection helpers or move behind the controlled bridge. "
        f"Violations: {violations}"
    )


def test_deterministic_repairs_package_all_does_not_export_concrete_repair_functions() -> None:
    from polaris.cells.roles.adapters.internal.director import deterministic_repairs

    concrete_exports = [
        name
        for name in getattr(deterministic_repairs, "__all__", ())
        if str(name).startswith(CONCRETE_LEGACY_REPAIR_EXPORT_PREFIXES)
    ]

    assert concrete_exports == []


def test_roles_adapters_descriptor_pack_does_not_publish_concrete_repair_mutators() -> None:
    payload = json.loads(_read_text(ROLES_ADAPTERS_DESCRIPTOR_PACK_PATH))
    capabilities = payload.get("capabilities")
    assert isinstance(capabilities, list)
    concrete_entries = [
        {
            "name": str(item.get("name") or ""),
            "defined_in": str(item.get("defined_in") or ""),
        }
        for item in capabilities
        if isinstance(item, dict)
        and (
            str(item.get("name") or "").startswith("_apply_deterministic_")
            or str(item.get("name") or "").startswith("repair_rust_")
            or (
                str(item.get("name") or "").startswith("run_all_")
                and str(item.get("name") or "").endswith("_post_repairs")
            )
        )
    ]

    assert concrete_entries == []


def test_execute_method_does_not_reexport_migrated_runtime_repairs() -> None:
    execute_method_source = _read_text(EXECUTE_METHOD_PATH)

    assert not any(
        symbol in execute_method_source for symbol in MIGRATED_RUNTIME_REPAIR_EXPORTS_FORBIDDEN_IN_EXECUTE_METHOD
    )


def test_execute_method_legacy_deterministic_repair_calls_are_explicitly_bounded() -> None:
    deterministic_repair_calls = _called_deterministic_repair_names(EXECUTE_METHOD_PATH)
    execute_method_source = _read_text(EXECUTE_METHOD_PATH)

    assert deterministic_repair_calls == ALLOWED_EXECUTE_METHOD_LEGACY_DETERMINISTIC_REPAIR_CALLS, (
        f"{REPAIR_BOUNDARY_FAILURE_HINT} execute_method.py must delegate legacy helper calls through "
        f"execute_method_repair_bridge.py. Direct calls: {sorted(deterministic_repair_calls)}"
    )
    assert "_legacy_deterministic_repairs" not in execute_method_source
    assert "deterministic_repairs" not in execute_method_source


def test_execute_method_legacy_repairs_delegate_to_controlled_bridge() -> None:
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    bridge_calls = _called_deterministic_repair_names(EXECUTE_METHOD_REPAIR_BRIDGE_PATH)
    bridge_source = _read_text(EXECUTE_METHOD_REPAIR_BRIDGE_PATH)

    assert "run_declared_target_contract_repairs" in execute_calls
    assert "run_node_test_script_contract_repair" in execute_calls
    assert "run_patch_residue_cleanup" in execute_calls
    assert "run_pre_materialization_declared_target_repairs" in execute_calls
    assert "run_python_runtime_smoke" in execute_calls
    assert "run_python_static_smoke" in execute_calls
    assert "run_python_unittest_missing_target_repair" in execute_calls
    assert "run_scaffold_marker_cleanup" in execute_calls
    assert "run_typescript_reexport_repair" in execute_calls
    assert bridge_calls == set()
    assert "deterministic_repairs" not in bridge_source
    assert "File-mutating deterministic repairs must execute through" in bridge_source
    assert "run_runtime_repair_with_director_tools" in bridge_source


def test_roles_adapter_public_boundary_blocks_internal_kernel_and_direct_legacy_helpers() -> None:
    internal_kernel_imports: list[str] = []
    for path in _python_source_files(ROLES_DIRECTOR_ROOT):
        for module in _imported_modules(path):
            if module == "polaris.cells.director.runtime.internal.repair_kernel" or module.startswith(
                "polaris.cells.director.runtime.internal.repair_kernel."
            ):
                rel_path = path.relative_to(BACKEND_ROOT).as_posix()
                internal_kernel_imports.append(f"{rel_path}: {module}")

    execute_imports = sorted(
        reference
        for reference in _import_references(EXECUTE_METHOD_PATH)
        if "polaris.cells.roles.adapters.internal.director.deterministic_repairs" in reference
    )
    execute_direct_helper_calls = sorted(_called_deterministic_repair_names(EXECUTE_METHOD_PATH))
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    execute_source = _read_text(EXECUTE_METHOD_PATH)

    assert internal_kernel_imports == []
    assert execute_imports == []
    assert execute_direct_helper_calls == []
    assert "run_post_execution_language_repairs" in execute_calls
    assert "run_director_materialization_quality_repair_schedule" in execute_source


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
        "deterministic_rust_dependency_repair",
    }
    bridge_runtime_tokens = {
        "deterministic_go_bare_import_string_repair",
        "deterministic_go_nested_import_repair",
        "deterministic_go_module_import_repair",
        "deterministic_go_bare_import_repair",
        "deterministic_go_subpath_repair",
        "deterministic_go_dedup_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_lib_root_facade_repair",
    }
    public_schedule = query_director_repair_post_execution_schedule(
        QueryDirectorRepairPostExecutionScheduleV1(include_items=True)
    )
    expected_runtime_step_ids = [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    public_runtime_step_ids = [step.step_id for step in public_schedule.items]

    assert "run_post_execution_language_repairs" in execute_method_source
    assert not any(token in execute_method_source for token in language_repair_tokens)
    assert all(token in bridge_source for token in bridge_runtime_tokens)
    assert "_apply_deterministic_go_module_import_repair" not in bridge_source
    assert "_GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS" in bridge_source
    assert "run_all_rust_post_repairs" not in bridge_source
    assert "_apply_deterministic_rust_dependency_repair" not in bridge_source
    assert public_runtime_step_ids == expected_runtime_step_ids
    assert runner_step_ids == expected_runtime_step_ids
    assert runner_step_ids == public_runtime_step_ids
    assert not GO_REPAIRS_PATH.exists()
    assert not CPP_REPAIRS_PATH.exists()
    assert "run_all_cpp_post_repairs" not in bridge_source
    assert "cpp_repairs" not in bridge_source
    assert "go_repairs" not in bridge_source
    assert "repair_cpp_failing_smoke_translation_units" not in bridge_source
    assert "repair_cpp_invalid_placeholder_declarations" not in bridge_source
    assert "repair_cpp_missing_private_members" not in bridge_source
    assert "repair_cpp_missing_standard_includes" not in bridge_source
    assert "repair_cpp_struct_getter_field_access" not in bridge_source
    assert "deterministic_cpp_include_path_repair" in bridge_source
    assert "deterministic_cpp_post_repair" in bridge_source
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


def test_post_execution_bridge_does_not_call_legacy_java_test_dependency_tail() -> None:
    bridge_source = _read_text(POST_EXECUTION_BRIDGE_PATH)
    java_runner_source = _function_source(POST_EXECUTION_BRIDGE_PATH, "_run_java_post_repairs")

    assert not JAVA_REPAIRS_PATH.exists()
    assert "java_repairs" not in bridge_source
    assert "run_all_java_post_repairs" not in bridge_source
    assert "repair_java_test_dependencies" not in bridge_source
    assert "deterministic_java_test_dependency_repair" in bridge_source
    assert "_run_java_test_dependency_runtime_repair" in bridge_source
    assert "repair_java_test_dependencies" not in java_runner_source


def test_rust_post_execution_legacy_aggregate_callback_is_retired() -> None:
    rust_source = _read_text(RUST_REPAIRS_PATH)

    assert "def run_all_rust_post_repairs" not in rust_source
    assert "def _run_rust_post_repair_round" not in rust_source
    assert "def _annotate_rust_post_repair_records" not in rust_source


def test_rust_aggregate_post_repair_is_not_executable_runtime_binding() -> None:
    source_tools = {binding["source_tool"] for binding in runtime_repair_bindings()}

    assert "deterministic_rust_post_repair" not in source_tools
    assert "deterministic_rust_method_self_signature_repair" in source_tools
    assert "deterministic_rust_missing_module_file_repair" in source_tools


def test_materialization_quality_runtime_ports_consume_runtime_owned_schedule() -> None:
    runtime_ports_source = _read_text(MATERIALIZATION_QUALITY_RUNTIME_PORTS_PATH)
    callback_ports_source = _read_text(MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH)
    evidence_ports_source = _read_text(MATERIALIZATION_QUALITY_EVIDENCE_PORTS_PATH)
    runner_step_ids = _module_level_dict_keys(
        MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH, "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS"
    )
    public_schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    expected_runtime_step_ids = [
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.html_entrypoint",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    ]
    public_runtime_step_ids = [step.step_id for step in public_schedule.items]

    assert "run_director_materialization_quality_repair_facade" not in runtime_ports_source
    assert "run_director_materialization_quality_repair_schedule_result" not in runtime_ports_source
    assert "query_director_repair_materialization_quality_schedule" in callback_ports_source
    assert "DirectorRepairMaterializationQualityStepV1" in runtime_ports_source
    assert "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS" not in runtime_ports_source
    assert "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS" in callback_ports_source
    assert "_require_materialization_schedule_reconciliation" not in runtime_ports_source
    assert "_materialization_schedule_reconciliation" not in runtime_ports_source
    assert "runner_binding_reconciliation" not in runtime_ports_source
    assert "evidence_status" not in runtime_ports_source
    assert public_runtime_step_ids == expected_runtime_step_ids
    assert runner_step_ids == expected_runtime_step_ids
    assert runner_step_ids == public_runtime_step_ids
    assert "runtime_schedule_step_runner_adapter" in evidence_ports_source
    assert "runtime_schedule_step_runner_adapter" not in callback_ports_source
    assert "adapter_strategy_host_wrapper" not in callback_ports_source
    assert "materialization.quality_repair_host" not in callback_ports_source
    assert "materialization.typescript_compiler" in callback_ports_source
    assert "_apply_deterministic_materialization_quality_repairs" not in callback_ports_source


def test_bridge_runner_keys_match_runtime_schedule_run_items_exactly() -> None:
    post_runner_step_ids = _module_level_dict_keys(POST_EXECUTION_BRIDGE_PATH, "_POST_EXECUTION_REPAIR_RUNNERS")
    materialization_runner_step_ids = _module_level_dict_keys(
        MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH,
        "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS",
    )
    post_schedule = query_director_repair_post_execution_schedule(
        QueryDirectorRepairPostExecutionScheduleV1(include_items=True)
    )
    materialization_schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    post_runtime_step_ids = [step.step_id for step in post_schedule.items]
    materialization_runtime_step_ids = [step.step_id for step in materialization_schedule.items]

    post_run_result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=post_runner_step_ids,
        runner=lambda step: [],
        max_rounds=1,
    )
    materialization_run_result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=materialization_runner_step_ids,
        runner=lambda step: [],
        max_rounds=1,
    )
    post_result_step_ids = [step.step_id for step in post_run_result.ordered_steps]
    materialization_result_step_ids = [step.step_id for step in materialization_run_result.ordered_steps]

    assert post_runner_step_ids == post_runtime_step_ids
    assert post_result_step_ids == post_runtime_step_ids
    assert materialization_runner_step_ids == materialization_runtime_step_ids
    assert materialization_result_step_ids == materialization_runtime_step_ids


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


def test_typescript_source_tools_do_not_return_to_adapter_strategy_host() -> None:
    catalog_payload = query_director_repair_strategy_catalog(
        QueryDirectorRepairStrategyCatalogV1(include_items=True, max_items=10_000)
    ).to_dict()
    catalog_summary = catalog_payload["summary"]
    adapter_source_tools = [str(source_tool) for source_tool in catalog_summary["adapter_strategy_host_source_tools"]]
    adapter_typescript_source_tools = [
        source_tool
        for source_tool in adapter_source_tools
        if source_tool.startswith(MIGRATED_TYPESCRIPT_SOURCE_TOOL_PREFIXES)
        or source_tool in MIGRATED_TYPESCRIPT_SOURCE_TOOL_NAMES
    ]
    adapter_failure_message = (
        "expected public strategy catalog ledger to have adapter_strategy_host=0; "
        f"observed implementation_status_counts={catalog_summary['implementation_status_counts']}; "
        "adapter_strategy_host_source_tools:\n- " + "\n- ".join(adapter_source_tools)
    )
    adapter_typescript_failure_message = (
        "TypeScript migration source_tools must not be in adapter_strategy_host_source_tools:\n- "
        + "\n- ".join(adapter_typescript_source_tools)
    )
    executable_status_count = catalog_summary["implementation_status_counts"].get("executable_runtime", 0)
    metadata_status_count = catalog_summary["implementation_status_counts"].get("metadata_rule_registered", 0)

    assert adapter_typescript_source_tools == [], adapter_typescript_failure_message
    assert adapter_source_tools == [], adapter_failure_message
    assert catalog_summary["implementation_status_counts"].get("adapter_strategy_host", 0) == 0, adapter_failure_message
    assert catalog_summary["adapter_strategy_host_count"] == 0, adapter_failure_message
    assert catalog_summary["total"] == executable_status_count + metadata_status_count, adapter_failure_message


def test_runtime_public_discovery_and_advisory_surfaces_are_read_only() -> None:
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=()))
    slots = query_director_repair_language_slots(QueryDirectorRepairLanguageSlotsV1(include_items=True))
    advisory = validate_director_repair_advisory(
        QueryDirectorRepairAdvisoryValidationV1(
            advisor_source="architecture-boundary-test",
            message="suggest coverage gap only",
        )
    )
    advisory_summary = dict(advisory.summary)
    slot_status_counts = dict(slots.summary["implementation_status_counts"])
    non_executable_slot_statuses = {"metadata_rule_registered", "reserved_only"}
    non_executable_slots = [slot for slot in slots.items if slot.implementation_status in non_executable_slot_statuses]

    assert coverage.access == "read_only"
    assert coverage.total_diagnostics == 0
    assert slots.access == "read_only"
    assert slots.summary["bench_driven_rule_addition_required"] is True
    assert {"executable_runtime", "reserved_only"} <= set(slot_status_counts)
    assert "adapter_strategy_host" not in slot_status_counts
    assert non_executable_slots
    assert all(not slot.executable_runtime_source_tools for slot in non_executable_slots)
    assert advisory.ok is True
    assert advisory.access == "read_only"
    assert advisory.execution_boundary == "read_only_advisory_validation_no_writes_no_registration"
    assert advisory.agi_execution_authority is False
    assert advisory.writes_allowed is False
    assert advisory.registration_allowed is False
    assert advisory.authoritative_receipts_allowed is False
    assert advisory_summary["advisory_only"] is True
    assert advisory_summary["suggested_rules_are_advisory_only"] is True
    assert advisory_summary["director_runtime_remains_authoritative"] is True


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
    source = (
        _read_text(MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH)
        + "\n"
        + _function_source(MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH, "_run_materialization_go_import_repairs")
        + "\n"
        + _function_source(POST_EXECUTION_BRIDGE_PATH, "_run_go_post_repairs")
    )
    bridge_source = _read_text(RUNTIME_REPAIR_BRIDGE_PATH)

    assert "deterministic_go_bare_import_string_repair" in _read_text(
        DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_ROOT / "schedule_catalog.py"
    )
    assert "run_runtime_repair_with_director_tools" in source
    assert "executor_factory=DirectorToolExecutor" in source
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


def test_materialization_quality_repairs_stay_behind_public_boundary() -> None:
    execute_calls = _called_function_names(EXECUTE_METHOD_PATH)
    quality_calls = _called_function_names(QUALITY_GATE_PATH)
    factory_calls = _called_function_names(FACTORY_STAGE_EXECUTOR_PATH)
    execute_source = _read_text(EXECUTE_METHOD_PATH)
    quality_source = _read_text(QUALITY_GATE_PATH)
    factory_source = _read_text(FACTORY_STAGE_EXECUTOR_PATH)

    assert "_apply_deterministic_materialization_quality_repairs" not in execute_calls
    assert "run_materialization_quality_repairs" not in execute_calls
    assert "run_materialization_quality_repairs" not in quality_calls
    assert "run_director_materialization_quality_repair_schedule_result" in execute_calls
    assert "run_director_materialization_quality_repair_schedule_result" in quality_calls
    assert "_apply_deterministic_materialization_quality_repairs" not in execute_source
    assert (
        "from .materialization_quality_runtime_ports import run_materialization_quality_repairs" not in execute_source
    )
    assert (
        "from .materialization_quality_runtime_ports import run_materialization_quality_repairs" not in quality_source
    )
    assert "_apply_deterministic_materialization_quality_repairs" not in factory_calls
    assert "_apply_deterministic_materialization_quality_repairs" not in factory_source
    for shim_name in PUBLIC_MIGRATION_ONLY_REPAIR_SHIMS:
        assert shim_name not in factory_calls
        assert shim_name not in factory_source
    assert "run_director_materialization_quality_repair_schedule" in factory_calls
    assert "run_director_post_execution_repair_schedule" in factory_calls
    assert "run_director_cpp_post_execution_repairs" not in factory_calls
    assert "run_director_cpp_post_execution_repairs" not in factory_source


def test_roles_adapters_public_legacy_repair_wrappers_are_removed_after_hard_cut() -> None:
    public_source = _read_text(ROLES_ADAPTERS_PUBLIC_SERVICE_PATH)

    assert "def run_director_materialization_quality_repair_schedule(" in public_source
    assert "def run_director_post_execution_repair_schedule(" in public_source
    assert "def run_director_cpp_post_execution_repairs(" not in public_source
    assert "def apply_deterministic_materialization_quality_repairs(" not in public_source
    assert "def apply_deterministic_cpp_post_repairs(" not in public_source
    assert "Deprecated migration-only shim" not in public_source
    assert "migration_only_compatibility_shim" not in public_source
    assert "__migration_only__ = True" not in public_source
    assert "__preferred_entrypoint__" not in public_source
    assert ".__deprecated__" not in public_source


def test_quality_gate_semantic_repairs_use_runtime_materialization_schedule() -> None:
    quality_source = _read_text(QUALITY_GATE_PATH)
    runtime_ports_source = _read_text(MATERIALIZATION_QUALITY_RUNTIME_PORTS_PATH)
    callback_ports_source = _read_text(MATERIALIZATION_QUALITY_CALLBACK_PORTS_PATH)
    runtime_schedule_source = _read_text(DIRECTOR_RUNTIME_INTERNAL_REPAIR_KERNEL_ROOT / "schedule_catalog.py")

    assert "run_typescript_semantic_quality_repairs" not in quality_source
    assert "_apply_deterministic_typescript_missing_export_repair" not in quality_source
    assert "_apply_deterministic_typescript_canvas_scale_return_type_repair" not in quality_source
    assert "def run_typescript_semantic_quality_repairs(" not in runtime_ports_source
    assert "deterministic_typescript_missing_export_repair" not in runtime_ports_source
    assert "deterministic_typescript_missing_export_repair" in runtime_schedule_source
    assert "deterministic_typescript_hyphenated_identifier_repair" in runtime_schedule_source
    assert "deterministic_typescript_zod_type_class_collision_repair" in runtime_schedule_source
    assert "run_runtime_repair_with_director_tools" in callback_ports_source
    assert "_apply_deterministic_typescript_missing_export_repair" not in callback_ports_source
    assert "_apply_deterministic_typescript_canvas_scale_return_type_repair" not in callback_ports_source


def test_roles_adapter_repair_summaries_use_runtime_typed_projection_contract() -> None:
    facade_path = ROLES_DIRECTOR_ROOT / "repair_profile_projection.py"
    facade_source = _read_text(facade_path)
    adapter_projection_callers = [
        POST_EXECUTION_BRIDGE_PATH,
        MATERIALIZATION_QUALITY_EVIDENCE_PORTS_PATH,
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
