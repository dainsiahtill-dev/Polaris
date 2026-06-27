"""Tests for the Director deterministic-repair strategy catalog."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_strategy_catalog,
)
from polaris.cells.roles.adapters.internal.director import (
    materialization_quality_repair_bridge,
    post_execution_repair_bridge,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
    generic_repairs,
    rust_repairs,
)
from polaris.cells.roles.adapters.internal.director.repair_profile_projection import (
    summarize_deterministic_repair_source_tools,
)
from polaris.cells.roles.adapters.public import service as role_adapter_service
from polaris.cells.roles.adapters.public.service import run_director_cpp_post_execution_repairs

_SOURCE_TOOL_RE = re.compile(r"[\"'](?P<tool>deterministic_[A-Za-z0-9_]+)[\"']")
_NON_STRATEGY_TOKENS = {"deterministic_repair_profiles"}
_FORBIDDEN_REPAIR_IMPORT_PREFIXES = (
    "polaris.cells.director.runtime.internal.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.repair_kernel",
    "polaris.cells.roles.adapters.internal.director.deterministic_repairs.strategy_catalog",
)
_ALLOWED_EXECUTE_METHOD_DIRECTOR_RUNTIME_IMPORTS = {
    "polaris.cells.director.runtime.public.service",
}
_EXECUTE_METHOD_FILE_MUTATING_REPAIR_WRAPPERS = frozenset(
    {
        "run_declared_target_contract_repairs",
        "run_node_test_script_contract_repair",
        "run_patch_residue_cleanup",
        "run_pre_materialization_declared_target_repairs",
        "run_python_unittest_missing_target_repair",
        "run_scaffold_marker_cleanup",
        "run_typescript_reexport_repair",
    }
)


def _implementation_files() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "internal" / "director"
    repair_root = root / "deterministic_repairs"
    files = [path for path in repair_root.glob("*.py") if path.name not in {"strategy_catalog.py", "__init__.py"}]
    files.append(root / "execute_method.py")
    return files


def _director_internal_root() -> Path:
    return Path(__file__).resolve().parents[1] / "internal" / "director"


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _python_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts and not path.name.startswith("test_")
    ]


def _module_name_for_path(path: Path) -> str:
    rel_path = path.with_suffix("").relative_to(_backend_root())
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
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(_resolve_import_from_module(path, node))
    return modules


def _function_definitions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _attribute_chain(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _matches_forbidden_import(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in _FORBIDDEN_REPAIR_IMPORT_PREFIXES)


def _deterministic_tokens_from_implementation() -> set[str]:
    tokens: set[str] = set()
    for path in _implementation_files():
        text = path.read_text(encoding="utf-8")
        tokens.update(
            match.group("tool")
            for match in _SOURCE_TOOL_RE.finditer(text)
            if match.group("tool") not in _NON_STRATEGY_TOKENS
        )
    return tokens


def _catalog_items() -> list[dict[str, Any]]:
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1(include_items=True))
    return [dict(item) for item in result.items]


def test_roles_adapter_no_longer_owns_repair_kernel_source_or_strategy_catalog() -> None:
    root = _director_internal_root()
    repair_kernel_payload = [
        path
        for path in sorted((root / "repair_kernel").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]

    assert repair_kernel_payload == []
    assert not (root / "deterministic_repairs" / "strategy_catalog.py").exists()


def test_roles_adapter_production_code_never_imports_repair_kernel_internals() -> None:
    violations: list[str] = []
    for path in _python_source_files(_director_internal_root()):
        for module in _imported_modules(path):
            if _matches_forbidden_import(module):
                rel_path = path.relative_to(_backend_root())
                violations.append(f"{rel_path}: {module}")

    assert violations == []


def test_execute_method_uses_director_runtime_repair_kernel_only_via_public_service() -> None:
    execute_method_path = _director_internal_root() / "execute_method.py"
    director_runtime_imports = sorted(
        {
            module
            for module in _imported_modules(execute_method_path)
            if module == "polaris.cells.director.runtime" or module.startswith("polaris.cells.director.runtime.")
        }
    )

    assert set(director_runtime_imports) <= _ALLOWED_EXECUTE_METHOD_DIRECTOR_RUNTIME_IMPORTS
    assert "polaris.cells.director.runtime.public.service" in director_runtime_imports


def test_execute_method_file_mutating_repair_wrappers_are_runtime_hard_cut() -> None:
    bridge_path = _director_internal_root() / "execute_method_repair_bridge.py"
    definitions = _function_definitions(bridge_path)
    missing = sorted(_EXECUTE_METHOD_FILE_MUTATING_REPAIR_WRAPPERS - set(definitions))

    assert missing == []

    violations: list[str] = []
    for wrapper_name in sorted(_EXECUTE_METHOD_FILE_MUTATING_REPAIR_WRAPPERS):
        wrapper = definitions[wrapper_name]
        calls_runtime_bridge = False
        for node in ast.walk(wrapper):
            if isinstance(node, ast.Call):
                callee = _attribute_chain(node.func)
                if callee in {"_runtime_repair_tool_results", "run_runtime_repair_with_director_tools"} or callee.endswith(
                    "run_runtime_repair_with_director_tools"
                ):
                    calls_runtime_bridge = True
                if callee.startswith("_legacy_deterministic_repairs._apply_deterministic_"):
                    violations.append(f"{wrapper_name}: {callee}")
        assert calls_runtime_bridge, f"{wrapper_name} must execute through director.runtime repair bridge"

    assert violations == []


def test_materialization_python_import_uses_runtime_bridge_not_legacy_python_repairs() -> None:
    bridge_path = _director_internal_root() / "materialization_quality_repair_bridge.py"
    definitions = _function_definitions(bridge_path)

    python_step = definitions["_run_materialization_python_import"]
    calls_runtime_bridge = False
    violations: list[str] = []
    for node in ast.walk(python_step):
        if not isinstance(node, ast.Call):
            continue
        callee = _attribute_chain(node.func)
        if callee == "run_runtime_repair_with_director_tools" or callee.endswith(
            "run_runtime_repair_with_director_tools"
        ):
            calls_runtime_bridge = True
        if callee.startswith("_legacy_deterministic_repairs._apply_deterministic_") or (
            callee.startswith("_apply_deterministic_") and "python" in callee
        ):
            violations.append(callee)

    assert calls_runtime_bridge is True
    assert violations == []


def test_execute_method_legacy_repair_helper_allowlist_stays_empty() -> None:
    bridge_path = _director_internal_root() / "execute_method_repair_bridge.py"
    tree = ast.parse(bridge_path.read_text(encoding="utf-8"))
    allowlist_values: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST"
            for target in node.targets
        ):
            allowlist_values.append(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST"
            and node.value is not None
        ):
            allowlist_values.append(node.value)

    assert len(allowlist_values) == 1
    allowlist_value = allowlist_values[0]
    assert isinstance(allowlist_value, ast.Call)
    assert _attribute_chain(allowlist_value.func) == "frozenset"
    assert allowlist_value.args == []


def test_catalog_registers_all_hardcoded_deterministic_tokens() -> None:
    implementation_tokens = _deterministic_tokens_from_implementation()
    known_source_tools = {str(item.get("source_tool") or "") for item in _catalog_items()}

    assert implementation_tokens
    assert implementation_tokens <= known_source_tools


def test_catalog_describes_language_phase_and_concern() -> None:
    profile = {str(item.get("source_tool") or ""): item for item in _catalog_items()}[
        "deterministic_typescript_missing_export_repair"
    ]

    assert profile["source_tool"] == "deterministic_typescript_missing_export_repair"
    assert profile["language"] == "typescript"
    assert profile["phase"] == "quality_repair"
    assert profile["concern"] == "missing_symbol_or_file"
    assert profile["risk_level"] == "low"


def test_rust_catalog_drift_tokens_are_registered_without_executable_drift() -> None:
    profiles = {
        str(item.get("source_tool") or ""): item
        for item in summarize_deterministic_repair_source_tools(
            [
                "deterministic_rust_missing_fields_repair",
                "deterministic_rust_struct_literal_missing_field_repair",
            ]
        )
    }

    missing_fields = profiles["deterministic_rust_missing_fields_repair"]
    assert missing_fields["registered"] is True
    assert missing_fields["language"] == "rust"
    assert missing_fields["phase"] == "code_repair"
    assert missing_fields["concern"] == "missing_symbol_or_file"
    assert missing_fields["risk_level"] == "low"
    assert missing_fields["implementation_status"] == "executable_runtime"
    assert missing_fields["execution_owner"] == "director.runtime"
    assert missing_fields["bench_driven_migration_required"] is False

    struct_literal = profiles["deterministic_rust_struct_literal_missing_field_repair"]
    assert struct_literal["registered"] is True
    assert struct_literal["language"] == "rust"
    assert struct_literal["phase"] == "code_repair"
    assert struct_literal["concern"] == "missing_symbol_or_file"
    assert struct_literal["risk_level"] == "low"
    assert struct_literal["implementation_status"] == "executable_runtime"
    assert struct_literal["execution_owner"] == "director.runtime"
    assert struct_literal["bench_driven_migration_required"] is False


def test_unknown_source_tool_is_fail_closed_high_risk() -> None:
    profile = summarize_deterministic_repair_source_tools(["deterministic_future_repair"])[0]

    assert profile["registered"] is False
    assert profile["source_tool"] == "deterministic_future_repair"
    assert profile["language"] == "unknown"
    assert profile["phase"] == "unknown"
    assert profile["concern"] == "unregistered"
    assert profile["risk_level"] == "high"


def test_summary_dedupes_profiles_and_marks_registration() -> None:
    profiles = summarize_deterministic_repair_source_tools(
        [
            "deterministic_patch_residue_cleanup",
            "deterministic_patch_residue_cleanup",
            "deterministic_future_repair",
        ]
    )

    assert len(profiles) == 2
    assert profiles[0]["source_tool"] == "deterministic_patch_residue_cleanup"
    assert profiles[0]["language"] == "generic"
    assert profiles[0]["phase"] == "cleanup"
    assert profiles[0]["concern"] == "generated_residue"
    assert profiles[0]["risk_level"] == "low"
    assert profiles[0]["registered"] is True
    assert profiles[0]["implementation_status"] == "executable_runtime"
    assert profiles[0]["execution_owner"] == "director.runtime"
    assert profiles[0]["bench_driven_migration_required"] is False
    assert profiles[1] == {
        "source_tool": "deterministic_future_repair",
        "language": "unknown",
        "phase": "unknown",
        "concern": "unregistered",
        "risk_level": "high",
        "registered": False,
    }


def test_catalog_is_stable_sorted_and_machine_readable() -> None:
    catalog = _catalog_items()
    source_tools = [item["source_tool"] for item in catalog]

    assert source_tools == sorted(source_tools)
    assert len(source_tools) == len(set(source_tools))
    assert {"source_tool", "language", "phase", "concern", "risk_level"} <= set(catalog[0])


def test_director_runtime_public_catalog_mirrors_authoritative_catalog() -> None:
    catalog = _catalog_items()
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1())
    payload = result.to_dict()

    assert payload["schema_version"] == "director.deterministic_repair_strategy_catalog.v1"
    assert payload["source"] == "director.runtime.repair_kernel.strategy_catalog"
    assert payload["access"] == "read_only"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "director_authorized_tools_only"
    assert payload["chain"] == "PM → Chief Engineer → Director"
    assert payload["unknown_source_tool_policy"] == "fail_closed_high_risk"
    assert payload["items"] == catalog
    assert payload["summary"]["total"] == len(catalog)
    assert payload["summary"]["returned"] == len(catalog)
    assert payload["summary"]["by_concern"]


def test_rust_post_repairs_no_longer_emit_dependency_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    stderr_before = "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `serde`\n"

    def fake_cargo_check(workspace: Path) -> str:
        assert workspace == tmp_path
        return stderr_before

    def fake_dependencies(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
        assert workspace == tmp_path
        assert artifact_quality_errors == [stderr_before]
        raise AssertionError("dependency repair belongs to rust.dependency_resolution runtime bridge")

    monkeypatch.setattr(rust_repairs, "_run_cargo_check_stderr", fake_cargo_check)
    monkeypatch.setattr(rust_repairs, "repair_rust_dependencies", fake_dependencies)

    for name in (
        "repair_rust_crate_imports",
        "repair_rust_wrong_crate_paths",
        "repair_rust_method_self_signatures",
        "repair_rust_incompatible_copy_derives",
        "repair_rust_duplicate_module_files",
        "repair_rust_missing_module_files",
        "repair_rust_missing_binary_entrypoint",
        "repair_rust_missing_derives",
        "repair_rust_unused_imports",
        "repair_rust_missing_fields",
        "repair_rust_field_rename_suggestions",
        "repair_rust_lib_root_facade",
        "repair_rust_unresolved_pub_uses",
        "repair_rust_trait_imports",
        "repair_rust_line_suggestions",
    ):
        monkeypatch.setattr(rust_repairs, name, lambda *args, **kwargs: [])

    records = rust_repairs.run_all_rust_post_repairs(tmp_path)

    assert records == []


def test_rust_dependency_resolution_bridge_routes_runtime_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("use serde::Serialize;\n", encoding="utf-8")
    artifact_errors = ("error[E0433]: failed to resolve: use of unresolved module or unlinked crate `serde`",)
    calls: list[dict[str, Any]] = []

    class Adapter:
        workspace = tmp_path
        artifact_quality_errors = artifact_errors

    def fake_runtime_repair(adapter: Any, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append({"adapter": adapter, **kwargs})
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": kwargs["source_tool"],
                    "file": "Cargo.toml",
                },
            }
        ]

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_runtime_repair_with_director_tools",
        fake_runtime_repair,
    )

    result = post_execution_repair_bridge._POST_EXECUTION_REPAIR_RUNNERS["rust.dependency_resolution"](
        Adapter(),
        tmp_path,
        "task-rust-deps",
    )

    assert result[0]["result"]["source_tool"] == "deterministic_rust_dependency_repair"
    assert len(calls) == 1
    assert calls[0]["workspace_path"] == tmp_path
    assert calls[0]["task_id"] == "task-rust-deps"
    assert calls[0]["source_tool"] == "deterministic_rust_dependency_repair"
    assert calls[0]["executor_factory"] is post_execution_repair_bridge.DirectorToolExecutor
    assert calls[0]["base_files"] == {
        "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
        "src/lib.rs": "use serde::Serialize;\n",
    }
    assert calls[0]["artifact_quality_errors"] == artifact_errors
    assert calls[0]["allowed_paths"] == ("Cargo.toml", "src/lib.rs")
    assert calls[0]["use_editor"] is False


def test_cpp_post_repairs_public_wrapper_uses_catalog_source_tool(
    tmp_path: Path,
) -> None:
    header = tmp_path / "src" / "models" / "postcard.hpp"
    target = tmp_path / "src" / "engine" / "generator.cpp"
    header.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    header.write_text("#pragma once\n", encoding="utf-8")
    target.write_text('#include "src/models/postcard.hpp"\n', encoding="utf-8")

    results = run_director_cpp_post_execution_repairs(tmp_path)

    assert len(results) == 1
    assert results[0]["tool"] == "write_file"
    assert results[0]["tool_name"] == "write_file"
    assert results[0]["success"] is True
    assert results[0]["result"]["ok"] is True
    assert results[0]["result"]["source_tool"] == "deterministic_cpp_include_path_repair"
    assert results[0]["result"]["file"] == "src/engine/generator.cpp"
    assert results[0]["result"]["repair_kernel"]["owner_cell"] == "director.runtime"
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    assert results[0]["result"]["source_tool"] in {str(item.get("source_tool") or "") for item in _catalog_items()}


def test_materialization_quality_public_wrapper_is_not_internal_function_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_step_ids: list[str] = []

    def fake_materialization_repair_step(
        step_id: str,
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        observed_step_ids.append(step_id)
        assert adapter == {"workspace": "/tmp/demo"}
        assert task_id == "task-1"
        assert task == {"target_files": ["src/app.ts"]}
        assert artifact_quality_errors == ["error TS1005"]
        assert convergence_verifier is None
        return []

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "_run_legacy_materialization_quality_repair_step",
        fake_materialization_repair_step,
    )

    results, summary = role_adapter_service.run_director_materialization_quality_repair_schedule(
        {"workspace": "/tmp/demo"},
        task={"target_files": ["src/app.ts"]},
        task_id="task-1",
        artifact_quality_errors=["error TS1005"],
    )

    assert results == []
    expected_step_ids = [
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    ]
    assert observed_step_ids == expected_step_ids
    assert role_adapter_service.run_director_materialization_quality_repair_schedule.__module__.endswith(
        ".public.service"
    )
    assert summary["repair_kernel"]["stage"] == "materialization_quality_repairs"
    assert summary["repair_kernel"]["receipt_count"] == 0
    assert summary["repair_kernel"]["coverage_report"]["total_diagnostics"] == 1
    assert summary["coverage_preaudit"]["total_diagnostics"] == 1
    assert summary["coverage_preaudit"]["uncovered_diagnostic_count"] == 1
    assert summary["coverage_preaudit"]["rule_discovery_required"] is True
    assert summary["dark_launch_comparison"]["comparison_mode"] == "legacy_projection_self_check"
    assert summary["dark_launch_comparison"]["cutover_ready"] is False
    assert summary["dark_launch_comparison"]["independent_shadow_required"] is True
    assert summary["dark_launch_comparison"]["independent_shadow_satisfied"] is False
    expected_reconciliation = {
        "schema_version": "director.materialization_quality_schedule_reconciliation.v1",
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "runtime_step_ids": expected_step_ids,
        "runner_step_ids": expected_step_ids,
        "schedule_result_step_ids": expected_step_ids,
        "runtime_step_count": len(expected_step_ids),
        "runner_step_count": len(expected_step_ids),
        "schedule_result_step_count": len(expected_step_ids),
        "runtime_has_unique_steps": True,
        "runner_has_unique_steps": True,
        "runner_key_set_matches_runtime": True,
        "runner_order_matches_runtime": True,
        "schedule_result_matches_runtime": True,
        "missing_runner_step_ids": [],
        "extra_runner_step_ids": [],
        "missing_schedule_result_step_ids": [],
        "extra_schedule_result_step_ids": [],
        "exact_match": True,
    }
    bridge = summary["materialization_quality_bridge"]
    reconciliation = bridge["runner_binding_reconciliation"]
    assert reconciliation["exact_match"] is True
    assert reconciliation["runtime_step_ids"] == expected_step_ids
    assert reconciliation["runner_step_ids"] == reconciliation["runtime_step_ids"]
    assert reconciliation["schedule_result_step_ids"] == reconciliation["runtime_step_ids"]
    assert reconciliation == expected_reconciliation
    assert bridge == {
        "schema_version": "director.materialization_quality_repair_bridge.v1",
        "mode": "runtime_schedule_step_runner_adapter",
        "bridge_file": "roles.adapters.internal.director.materialization_quality_repair_bridge",
        "retired_strategy_host_removed": True,
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "ordered_step_ids": expected_step_ids,
        "runner_step_ids": expected_step_ids,
        "runner_binding_reconciliation": expected_reconciliation,
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
        "convergence_verifier_present": False,
        "receipt_count": 0,
        "coverage_preaudit_uncovered_diagnostic_count": 1,
        "coverage_preaudit_rule_discovery_required": True,
        "dark_launch_cutover_ready": False,
        "dark_launch_cutover_blockers": [
            "independent_shadow_required",
            "missing_before_after_hash_evidence",
            "missing_revalidation_evidence",
            "non_authoritative_kernel_receipt",
        ],
        "coverage_uncovered_diagnostic_count": 1,
    }
    debt = summary["repair_kernel_migration_debt"]
    assert debt["schema_version"] == "director.materialization_quality_repair_migration_debt.v1"
    assert debt["adapter_projection_bridge"] is True
    assert debt["legacy_callback_bridge"] is False
    assert debt["convergence_verifier_present"] is False
    assert debt["cutover_ready"] is False
    assert debt["step_count"] == len(expected_step_ids)
    assert debt["blocked_step_count"] == len(expected_step_ids)
    assert debt["authoritative_receipts_allowed"] is False
    assert debt["native_receipt_present_step_count"] == 0
    assert debt["adapter_projection_present_step_count"] == 0
    assert debt["callback_projection_present_step_count"] == 0
    assert debt["adapter_projection_only_step_count"] == 0
    assert debt["callback_only_step_count"] == 0
    assert debt["native_receipt_step_ids"] == []
    assert debt["adapter_projection_step_ids"] == []
    assert debt["callback_projection_step_ids"] == []
    assert debt["remaining_adapter_projection_only_step_ids"] == []
    assert debt["remaining_callback_only_step_ids"] == []
    assert summary["adapter_projection_debt"] == debt["adapter_projection_debt"]
    assert summary["legacy_callback_debt"] == debt["legacy_callback_debt"]
    assert debt["adapter_projection_debt"] == debt["legacy_callback_debt"]
    assert [item["step_id"] for item in debt["adapter_projection_debt"]] == expected_step_ids
    for item in debt["adapter_projection_debt"]:
        assert {
            "step_id",
            "language",
            "phase",
            "priority",
            "declared_source_tool",
            "actual_source_tools",
            "runtime_executable_source_tools",
            "legacy_only_source_tools",
            "native_receipt_present",
            "native_repair_kernel_receipt_count",
            "adapter_projection_present",
            "callback_projection_present",
            "adapter_receipt_projection_count",
            "callback_receipt_projection_count",
            "adapter_projection_only",
            "callback_only",
            "projection_only",
            "authoritative_receipts_allowed",
            "write_tool_evidence",
            "convergence_path_available",
            "convergence_verifier_present",
            "verifier_evidence_required",
            "verifier_evidence_present",
            "native_verifier_evidence_present",
            "adapter_verifier_evidence_present",
            "callback_verifier_evidence_present",
            "cutover_ready",
            "cutover_blockers",
            "blockers",
        } <= set(item)
        assert item["actual_source_tools"] == []
        assert item["runtime_executable_source_tools"] == []
        assert item["legacy_only_source_tools"] == []
        assert item["native_receipt_present"] is False
        assert item["native_repair_kernel_receipt_count"] == 0
        assert item["adapter_projection_present"] is False
        assert item["callback_projection_present"] is False
        assert item["adapter_receipt_projection_count"] == 0
        assert item["callback_receipt_projection_count"] == 0
        assert item["adapter_projection_only"] is False
        assert item["callback_only"] is False
        assert item["projection_only"] is False
        assert item["authoritative_receipts_allowed"] is False
        assert item["convergence_verifier_present"] is False
        assert item["verifier_evidence_required"] is True
        assert item["verifier_evidence_present"] is False
        assert item["native_verifier_evidence_present"] is False
        assert item["adapter_verifier_evidence_present"] is False
        assert item["callback_verifier_evidence_present"] is False
        assert item["cutover_ready"] is False
        assert item["cutover_blockers"] == item["blockers"]
        assert "adapter_schedule_runner" in item["blockers"]
        assert "missing_revalidation_evidence" in item["blockers"]
        assert "independent_shadow_required" in item["blockers"]
    assert summary["public_boundary"] == {
        "schema_version": "roles.adapters.materialization_quality_repair_boundary.v1",
        "mode": "runtime_owned_schedule_public_boundary",
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
    }


def test_legacy_public_repair_wrappers_are_hard_cut() -> None:
    assert not hasattr(role_adapter_service, "apply_deterministic_materialization_quality_repairs")
    assert not hasattr(role_adapter_service, "apply_deterministic_cpp_post_repairs")
    assert "apply_deterministic_materialization_quality_repairs" not in role_adapter_service.__all__
    assert "apply_deterministic_cpp_post_repairs" not in role_adapter_service.__all__
    assert "run_director_materialization_quality_repair_schedule" in role_adapter_service.__all__
    assert "run_director_cpp_post_execution_repairs" in role_adapter_service.__all__


def test_legacy_materialization_quality_facade_is_hard_cut() -> None:
    assert not hasattr(generic_repairs, "_apply_deterministic_materialization_quality_repairs")


def test_materialization_quality_migration_debt_marks_legacy_only_step_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_materialization_repair_step(
        step_id: str,
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        assert adapter == {"workspace": "/tmp/demo"}
        assert task == {"target_files": ["package.json"]}
        assert task_id == "task-1"
        assert artifact_quality_errors == ["missing npm test script"]
        assert convergence_verifier is sentinel_verifier
        if step_id != "materialization.node_manifest":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_runtime_dependency_repair",
                    "file": "package.json",
                },
            }
        ]

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "_run_legacy_materialization_quality_repair_step",
        fake_materialization_repair_step,
    )

    results, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        {"workspace": "/tmp/demo"},
        task={"target_files": ["package.json"]},
        task_id="task-1",
        artifact_quality_errors=["missing npm test script"],
        convergence_verifier=sentinel_verifier,
    )

    assert len(results) == 1
    assert summary["convergence_verifier_present"] is True
    assert summary["materialization_quality_bridge"]["convergence_verifier_present"] is True
    assert summary["repair_kernel_migration_debt"]["convergence_verifier_present"] is True
    debt_by_step = {item["step_id"]: item for item in summary["repair_kernel_migration_debt"]["legacy_callback_debt"]}
    assert len(debt_by_step) == 8
    node_manifest_debt = debt_by_step["materialization.node_manifest"]
    assert node_manifest_debt["declared_source_tool"] == "deterministic_node_manifest_materialization_repair"
    assert node_manifest_debt["actual_source_tools"] == ["deterministic_runtime_dependency_repair"]
    assert node_manifest_debt["runtime_executable_source_tools"] == ["deterministic_runtime_dependency_repair"]
    assert node_manifest_debt["legacy_only_source_tools"] == []
    assert node_manifest_debt["write_tool_evidence"] is True
    assert node_manifest_debt["convergence_path_available"] is True
    assert node_manifest_debt["convergence_verifier_present"] is True
    assert node_manifest_debt["verifier_evidence_required"] is True
    assert node_manifest_debt["verifier_evidence_present"] is False
    assert node_manifest_debt["cutover_ready"] is False
    assert "adapter_schedule_runner" in node_manifest_debt["blockers"]
    assert "legacy_only_source_tools" not in node_manifest_debt["blockers"]
    assert "missing_revalidation_evidence" in node_manifest_debt["blockers"]
    assert "independent_shadow_required" in node_manifest_debt["blockers"]
    scheduler_bridge = summary["scheduler_bridge"]
    node_manifest_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.node_manifest"]
    assert scheduler_bridge["authoritative_receipts_allowed"] is False
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert node_manifest_lifecycle["authoritative_receipts_allowed"] is False
    assert node_manifest_lifecycle["typed_receipt_path_available"] is False
    assert node_manifest_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "resolved_evidence" not in node_manifest_lifecycle["receipt_lifecycle_evidence_status_counts"]


def test_materialization_quality_migration_debt_lists_remaining_callback_only_steps() -> None:
    step = materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
        step_id="materialization.go_import",
        language="go",
        phase="materialization_quality",
        priority=8,
        source_tool="deterministic_go_bare_import_string_repair",
    )
    tool_results = [
        {
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_go_bare_import_string_repair",
                "bridge_step_id": "materialization.go_import",
                "file": "main.go",
            },
        }
    ]
    callback_projection = {
        "projection_id": "callback-only-projection",
        "receipt_authority": "authoritative",
        "step_id": "materialization.go_import",
        "source_tool": "deterministic_go_bare_import_string_repair",
        "projection_only": False,
        "authoritative": True,
        "revalidation_evidence": {
            "command": ["rtk", "go", "test", "./..."],
            "exit_code": 0,
            "raw_output_ref": "runtime/verifier/materialization-go.log",
        },
    }
    normalized_projection = (
        materialization_quality_repair_bridge._materialization_callback_receipt_projections_from_schedule_result(
            [callback_projection]
        )
    )[0]

    debt = materialization_quality_repair_bridge._project_materialization_quality_migration_debt(
        ordered_steps=(step,),
        tool_results=tool_results,
        callback_receipt_projections=[normalized_projection],
        native_receipts_by_step={"materialization.go_import": []},
        convergence_verifier_present=True,
    )

    assert debt["cutover_ready"] is False
    assert debt["authoritative_receipts_allowed"] is False
    assert debt["native_receipt_step_ids"] == []
    assert debt["adapter_projection_step_ids"] == ["materialization.go_import"]
    assert debt["callback_projection_step_ids"] == ["materialization.go_import"]
    assert debt["remaining_adapter_projection_only_step_ids"] == ["materialization.go_import"]
    assert debt["remaining_callback_only_step_ids"] == ["materialization.go_import"]
    assert debt["adapter_projection_only_step_count"] == 1
    assert debt["callback_only_step_count"] == 1
    step_debt = debt["adapter_projection_debt"][0]
    assert step_debt["native_receipt_present"] is False
    assert step_debt["adapter_projection_present"] is True
    assert step_debt["callback_projection_present"] is True
    assert step_debt["adapter_projection_only"] is True
    assert step_debt["callback_only"] is True
    assert step_debt["projection_only"] is True
    assert step_debt["authoritative_receipts_allowed"] is False
    assert step_debt["verifier_evidence_present"] is True
    assert step_debt["native_verifier_evidence_present"] is False
    assert step_debt["adapter_verifier_evidence_present"] is True
    assert step_debt["callback_verifier_evidence_present"] is True
    assert step_debt["cutover_ready"] is False
    assert "adapter_projection_only" in step_debt["cutover_blockers"]
    assert "missing_native_repair_receipt" in step_debt["cutover_blockers"]


def test_materialization_hygiene_native_cutover_evidence_requires_all_selected_step_evidence() -> None:
    step = materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
        step_id="materialization.hygiene_scaffold",
        language="multi",
        phase="hygiene",
        priority=0,
        source_tool="deterministic_materialization_hygiene_repair",
    )
    native_receipt = {
        "receipt_id": "native-hygiene-ready",
        "source_tool": "deterministic_materialization_hygiene_repair",
        "revalidation_evidence": {
            "command": ["rtk", "pytest", "tests/test_hygiene.py"],
            "exit_code": 0,
            "errors_after": 0,
        },
    }
    callback_projection = {
        "projection_id": "callback-hygiene-projection",
        "step_id": "materialization.hygiene_scaffold",
        "source_tool": "deterministic_materialization_hygiene_repair",
        "evidence_status": "resolved_evidence",
    }

    missing_native_lifecycle = materialization_quality_repair_bridge._materialization_receipt_lifecycle_by_step(
        ordered_steps=(step,),
        tool_results=[],
        callback_receipt_projections=[],
        native_receipts_by_step={"materialization.hygiene_scaffold": []},
        migration_debt={"legacy_callback_debt": [{"step_id": "materialization.hygiene_scaffold"}]},
    )["materialization.hygiene_scaffold"]
    missing_native_evidence = missing_native_lifecycle["native_cutover_evidence"]
    assert missing_native_evidence["native_path_available"] is False
    assert missing_native_evidence["cutover_ready"] is False
    assert "native_repair_kernel.receipts" in missing_native_evidence["missing_required_evidence"]
    assert "missing_native_repair_receipt" in missing_native_evidence["cutover_blockers"]

    callback_blocked_lifecycle = materialization_quality_repair_bridge._materialization_receipt_lifecycle_by_step(
        ordered_steps=(step,),
        tool_results=[],
        callback_receipt_projections=[callback_projection],
        native_receipts_by_step={"materialization.hygiene_scaffold": [native_receipt]},
        migration_debt={"legacy_callback_debt": [{"step_id": "materialization.hygiene_scaffold"}]},
    )["materialization.hygiene_scaffold"]
    callback_blocked_evidence = callback_blocked_lifecycle["native_cutover_evidence"]
    assert callback_blocked_evidence["native_path_available"] is True
    assert callback_blocked_evidence["native_verifier_evidence_present"] is True
    assert callback_blocked_evidence["native_evidence_resolved"] is True
    assert callback_blocked_evidence["cutover_ready"] is False
    assert "adapter_projection_absent" in callback_blocked_evidence["missing_required_evidence"]
    assert "adapter_projection_still_present" in callback_blocked_evidence["cutover_blockers"]

    ready_lifecycle = materialization_quality_repair_bridge._materialization_receipt_lifecycle_by_step(
        ordered_steps=(step,),
        tool_results=[],
        callback_receipt_projections=[],
        native_receipts_by_step={"materialization.hygiene_scaffold": [native_receipt]},
        migration_debt={"legacy_callback_debt": [{"step_id": "materialization.hygiene_scaffold"}]},
    )["materialization.hygiene_scaffold"]
    ready_evidence = ready_lifecycle["native_cutover_evidence"]
    assert ready_evidence["native_path_available"] is True
    assert ready_evidence["native_verifier_evidence_present"] is True
    assert ready_evidence["native_evidence_resolved"] is True
    assert ready_evidence["missing_required_evidence"] == []
    assert ready_evidence["cutover_blockers"] == []
    assert ready_evidence["cutover_ready"] is True
