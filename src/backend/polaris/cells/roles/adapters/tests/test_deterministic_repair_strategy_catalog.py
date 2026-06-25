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
from polaris.cells.director.runtime.public.service import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs import cpp_repairs, rust_repairs
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

    assert implementation_tokens
    assert implementation_tokens <= KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS


def test_catalog_describes_language_phase_and_concern() -> None:
    profile = describe_deterministic_repair_strategy("deterministic_typescript_missing_export_repair")

    assert profile.source_tool == "deterministic_typescript_missing_export_repair"
    assert profile.language == "typescript"
    assert profile.phase == "quality_repair"
    assert profile.concern == "missing_symbol_or_file"
    assert profile.risk_level == "low"


def test_unknown_source_tool_is_fail_closed_high_risk() -> None:
    profile = describe_deterministic_repair_strategy("deterministic_future_repair")

    assert deterministic_repair_source_tool_known(profile.source_tool) is False
    assert profile.language == "unknown"
    assert profile.phase == "unknown"
    assert profile.concern == "unregistered"
    assert profile.risk_level == "high"


def test_summary_dedupes_profiles_and_marks_registration() -> None:
    profiles = summarize_deterministic_repair_source_tools(
        [
            "deterministic_patch_residue_cleanup",
            "deterministic_patch_residue_cleanup",
            "deterministic_future_repair",
        ]
    )

    assert profiles == [
        {
            "source_tool": "deterministic_patch_residue_cleanup",
            "language": "generic",
            "phase": "cleanup",
            "concern": "generated_residue",
            "risk_level": "low",
            "registered": True,
        },
        {
            "source_tool": "deterministic_future_repair",
            "language": "unknown",
            "phase": "unknown",
            "concern": "unregistered",
            "risk_level": "high",
            "registered": False,
        },
    ]


def test_catalog_is_stable_sorted_and_machine_readable() -> None:
    catalog = deterministic_repair_strategy_catalog()
    source_tools = [item["source_tool"] for item in catalog]

    assert source_tools == sorted(source_tools)
    assert len(source_tools) == len(set(source_tools))
    assert {"source_tool", "language", "phase", "concern", "risk_level"} <= set(catalog[0])


def test_director_runtime_public_catalog_mirrors_authoritative_catalog() -> None:
    catalog = deterministic_repair_strategy_catalog()
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
    assert records[0]["round_number"] == 1
    assert records[0]["revalidation"] == {
        "command": ["cargo", "check", "--quiet"],
        "exit_code": 0,
        "errors_before": 1,
        "errors_after": 0,
        "net_error_reduction": 1,
        "max_rounds": 3,
    }


def test_cpp_post_repairs_public_wrapper_uses_catalog_source_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    def fake_cpp_repairs(workspace: Path) -> list[dict[str, str]]:
        assert workspace == tmp_path
        return [{"file": "src/main.cpp", "action": "fix_include"}]

    monkeypatch.setattr(cpp_repairs, "run_all_cpp_post_repairs", fake_cpp_repairs)

    results = apply_deterministic_cpp_post_repairs(tmp_path)

    assert results == [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_cpp_post_repair",
                "file": "src/main.cpp",
                "action": "fix_include",
                "operation": "modify",
            },
        }
    ]
    assert deterministic_repair_source_tool_known(results[0]["result"]["source_tool"])
