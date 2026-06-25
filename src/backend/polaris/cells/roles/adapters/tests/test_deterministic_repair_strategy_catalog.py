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
from polaris.cells.roles.adapters.internal.director import materialization_quality_repair_bridge
from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
    generic_repairs,
    rust_repairs,
)
from polaris.cells.roles.adapters.internal.director.repair_profile_projection import (
    summarize_deterministic_repair_source_tools,
)
from polaris.cells.roles.adapters.public import service as role_adapter_service
from polaris.cells.roles.adapters.public.service import apply_deterministic_cpp_post_repairs

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


def test_rust_post_repairs_emit_rule_metadata_and_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    stderr_before = "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `serde`\n"
    stderr_after = ""
    cargo_outputs = iter((stderr_before, stderr_after))

    def fake_cargo_check(workspace: Path) -> str:
        assert workspace == tmp_path
        return next(cargo_outputs)

    def fake_dependencies(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
        assert workspace == tmp_path
        assert artifact_quality_errors == [stderr_before]
        return [{"file": "Cargo.toml", "packages": ["serde"]}]

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

    assert len(records) == 1
    assert records[0]["source_tool"] == "deterministic_rust_dependency_repair"
    assert records[0]["phase"] == "dependency_resolution"
    assert records[0]["priority"] == 0
    assert "round_number" not in records[0]
    assert records[0]["revalidation"] == {
        "command": ["cargo", "check", "--quiet"],
        "exit_code": 0,
        "errors_before": 1,
        "errors_after": 0,
        "net_error_reduction": 1,
    }


def test_cpp_post_repairs_public_wrapper_uses_catalog_source_tool(
    tmp_path: Path,
) -> None:
    header = tmp_path / "src" / "models" / "postcard.hpp"
    target = tmp_path / "src" / "engine" / "generator.cpp"
    header.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    header.write_text("#pragma once\n", encoding="utf-8")
    target.write_text('#include "src/models/postcard.hpp"\n', encoding="utf-8")

    results = apply_deterministic_cpp_post_repairs(tmp_path)

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
    ) -> list[dict[str, Any]]:
        observed_step_ids.append(step_id)
        assert adapter == {"workspace": "/tmp/demo"}
        assert task_id == "task-1"
        assert task == {"target_files": ["src/app.ts"]}
        assert artifact_quality_errors == ["error TS1005"]
        return []

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "_run_legacy_materialization_quality_repair_step",
        fake_materialization_repair_step,
    )

    results, summary = role_adapter_service.apply_deterministic_materialization_quality_repairs(
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
    assert role_adapter_service.apply_deterministic_materialization_quality_repairs.__module__.endswith(
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
    assert summary["materialization_quality_bridge"] == {
        "schema_version": "director.materialization_quality_repair_bridge.v1",
        "mode": "runtime_schedule_step_runner_adapter",
        "bridge_file": "roles.adapters.internal.director.materialization_quality_repair_bridge",
        "legacy_strategy_host": "roles.adapters.internal.director.deterministic_repairs",
        "runtime_schedule_owner": "director.runtime",
        "runner_binding_owner": "roles.adapters",
        "ordered_step_ids": expected_step_ids,
        "runner_step_ids": expected_step_ids,
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
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
    assert summary["public_boundary"] == {
        "schema_version": "roles.adapters.materialization_quality_repair_boundary.v1",
        "mode": "legacy_strategy_host_wrapper",
        "internal_function_exported": False,
        "repair_kernel_owner": "director.runtime",
        "director_runtime_public_summary_required": True,
    }


def test_legacy_materialization_quality_function_facades_runtime_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_bridge(
        adapter: Any,
        *,
        task: dict[str, Any],
        task_id: str,
        artifact_quality_errors: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        observed["adapter"] = adapter
        observed["task"] = task
        observed["task_id"] = task_id
        observed["artifact_quality_errors"] = artifact_quality_errors
        return [], {"stage": "deterministic_quality_repair", "via": "runtime_schedule"}

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_materialization_quality_repairs",
        fake_bridge,
    )

    results, summary = generic_repairs._apply_deterministic_materialization_quality_repairs(
        {"workspace": "/tmp/demo"},
        task={"target_files": ["src/app.ts"]},
        task_id="task-1",
        artifact_quality_errors=["error TS1005"],
    )

    assert results == []
    assert summary == {"stage": "deterministic_quality_repair", "via": "runtime_schedule"}
    assert observed == {
        "adapter": {"workspace": "/tmp/demo"},
        "task": {"target_files": ["src/app.ts"]},
        "task_id": "task-1",
        "artifact_quality_errors": ["error TS1005"],
    }
