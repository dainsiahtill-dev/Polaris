"""Deterministic Rust repairs used by Director materialization quality gates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tomllib

from ..execution_tools import DirectorToolExecutor
from ._runtime_bridge import run_runtime_repair_with_director_tools

_RUST_UNRESOLVED_CRATE_RE = re.compile(
    r"(?:cannot find (?:module or )?crate|use of unresolved module or unlinked crate) "
    r"[`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_MISSING_LIB_PATH_RE = re.compile(
    r"can't find lib [`'\"][^`'\"]+[`'\"] at path [`'\"](?P<path>[^`'\"]+\.rs)[`'\"]",
    re.IGNORECASE,
)
_RUST_UNRESOLVED_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<import>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_NO_SYMBOL_RE = re.compile(
    r"no [`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] in [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_TRAIT_IMPORT_SUGGESTION_RE = re.compile(
    r"error\[E0599\]:\s+no method named [`'\"][A-Za-z_][A-Za-z0-9_]*[`'\"].*?"
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):\d+:\d+.*?"
    r"help:\s+trait [`'\"][A-Za-z_][A-Za-z0-9_]*[`'\"].*?"
    r"perhaps you want to import it.*?"
    r"^\s*(?:\d+\s*)?\+\s*(?P<import>use\s+[^;\n]+;)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_FIELD_METHOD_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+one of the expressions' fields has a method of the same name.*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_FULL_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+(?:consider borrowing here|try dereferencing|consider removing the borrow).*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_RUST_LIB_ROOT_EXPORT_HINT_RE = re.compile(
    r"lib\.rs must expose [`'\"]?(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"]?(?:\s+API)?",
    re.IGNORECASE,
)
_RUST_ROOT_UNRESOLVED_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"]"
    r".*?no [`'\"](?P=symbol)[`'\"] in the root",
    re.IGNORECASE | re.DOTALL,
)
_RUST_ROOT_TYPE_FIELD_MISMATCH_RE = re.compile(
    r"no field [`'\"][A-Za-z_][A-Za-z0-9_]*[`'\"] on type [`'\"]&?(?P<crate>[A-Za-z_][A-Za-z0-9_]*)::"
    r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)
_RUST_ROOT_STRUCT_FIELD_MISMATCH_RE = re.compile(
    r"struct [`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] "
    r"has no field named",
    re.IGNORECASE,
)
_RUST_SERDE_DERIVE_SUGGESTION_RE = re.compile(
    r"consider adding [`'\"]#\[derive\(serde::(?P<trait>Serialize|Deserialize)\)\][`'\"] "
    r"to your [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] type",
    re.IGNORECASE,
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_KNOWN_RUST_DEPENDENCIES: dict[str, str] = {
    "serde": 'serde = { version = "1.0", features = ["derive"] }',
    "serde_json": 'serde_json = "1.0"',
}


def _apply_deterministic_rust_crate_import_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_crate_imports(workspace, artifact_quality_errors)
    results: list[dict[str, Any]] = []
    for record in repairs:
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_rust_crate_import_repair",
                    "file": record["file"],
                    "before": record["before"],
                    "after": record["after"],
                    "replacements": record["replacements"],
                },
            }
        )
    return results


def _apply_deterministic_rust_dependency_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace.is_dir():
        return []
    cargo_path = workspace / "Cargo.toml"
    if not cargo_path.is_file():
        return []

    base_files: dict[str, str] = {}
    try:
        base_files["Cargo.toml"] = cargo_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    for rust_file in sorted(workspace.rglob("*.rs")):
        try:
            relative_path = rust_file.relative_to(workspace)
        except ValueError:
            continue
        if "target" in relative_path.parts:
            continue
        try:
            base_files[relative_path.as_posix()] = rust_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace,
        task_id=task_id,
        source_tool="deterministic_rust_dependency_repair",
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        use_editor=False,
    )


def _apply_deterministic_rust_derive_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_derives(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_derive_repair",
                "file": record["file"],
                "serde_derives": record["serde_derives"],
                "eq_derives_removed": record["eq_derives_removed"],
            },
        }
        for record in repairs
    ]


def _apply_deterministic_rust_missing_lib_target_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_missing_lib_targets(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_missing_lib_target_repair",
                "file": record["file"],
                "modules": record["modules"],
            },
        }
        for record in repairs
    ]


def _apply_deterministic_rust_lib_root_facade_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_lib_root_facade(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_lib_root_facade_repair",
                "file": record["file"],
                "path_rewrites": record["path_rewrites"],
                "module_exports": record["module_exports"],
            },
        }
        for record in repairs
    ]


def _apply_deterministic_rust_line_suggestion_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_line_suggestions(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_line_suggestion_repair",
                "file": record["file"],
                "line": record["line"],
            },
        }
        for record in repairs
    ]


def _apply_deterministic_rust_unresolved_pub_use_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_unresolved_pub_uses(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_unresolved_pub_use_repair",
                "file": record["file"],
                "symbols": record["symbols"],
            },
        }
        for record in repairs
    ]


def _apply_deterministic_rust_trait_import_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_trait_imports(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_trait_import_repair",
                "file": record["file"],
                "import": record["import"],
            },
        }
        for record in repairs
    ]


def repair_rust_crate_imports(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    cargo_path = workspace / "Cargo.toml"
    if not cargo_path.is_file():
        return []
    cargo = _read_cargo_manifest(cargo_path)
    if not cargo:
        return []
    canonical_crate = _canonical_rust_crate_name(cargo)
    if not canonical_crate:
        return []

    missing_crates = _parse_unresolved_rust_crates(artifact_quality_errors)
    if not missing_crates:
        return []

    repairs: list[dict[str, Any]] = []
    declared_dependencies = _declared_rust_dependencies(cargo)
    has_local_lib = _cargo_declares_local_rust_lib(workspace, cargo)
    for missing_crate in missing_crates:
        if missing_crate == canonical_crate:
            continue
        if missing_crate in declared_dependencies:
            continue
        if not _rust_crate_names_look_related(
            missing_crate,
            canonical_crate,
        ) and not (has_local_lib and _rust_crate_prefix_used_in_binary_entrypoint(workspace, missing_crate)):
            continue
        repairs.extend(_replace_rust_crate_prefix(workspace, missing_crate, canonical_crate))
    return repairs


def repair_rust_dependencies(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    cargo_path = workspace / "Cargo.toml"
    if not cargo_path.is_file():
        return []
    packages = _rust_dependency_packages_to_add(workspace, artifact_quality_errors)
    if not packages:
        return []
    try:
        original = cargo_path.read_text(encoding="utf-8")
    except OSError:
        return []
    missing = [package for package in packages if not _cargo_dependency_declared(original, package)]
    if not missing:
        return []
    repaired = original
    for package in missing:
        repaired = _insert_cargo_dependency(repaired, _KNOWN_RUST_DEPENDENCIES[package])
    if repaired == original:
        return []
    cargo_path.write_text(repaired, encoding="utf-8")
    return [{"packages": missing}]


def repair_rust_derives(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []

    serde_targets = _parse_rust_serde_derive_targets(artifact_quality_errors)
    repairs_by_path: dict[Path, dict[str, int]] = {}
    for module, symbol, traits in serde_targets:
        target_path = _find_rust_file_for_module_symbol(workspace, module, symbol)
        if target_path is None:
            continue
        try:
            original = target_path.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired, derive_count = _ensure_rust_file_serde_derives(original, traits)
        if repaired == original:
            continue
        target_path.write_text(repaired, encoding="utf-8")
        record = repairs_by_path.setdefault(target_path, {"serde_derives": 0, "eq_derives_removed": 0})
        record["serde_derives"] += derive_count

    if _artifact_errors_include_float_eq_failure(artifact_quality_errors):
        for rust_file in sorted(workspace.rglob("*.rs")):
            if "target" in rust_file.relative_to(workspace).parts:
                continue
            try:
                original = rust_file.read_text(encoding="utf-8")
            except OSError:
                continue
            repaired, removed = _remove_rust_eq_derives_for_float_fields(original)
            if repaired == original:
                continue
            rust_file.write_text(repaired, encoding="utf-8")
            record = repairs_by_path.setdefault(rust_file, {"serde_derives": 0, "eq_derives_removed": 0})
            record["eq_derives_removed"] += removed

    repairs: list[dict[str, Any]] = []
    for path, counts in sorted(repairs_by_path.items(), key=lambda item: item[0].as_posix()):
        repairs.append(
            {
                "file": str(path.relative_to(workspace)),
                "serde_derives": counts["serde_derives"],
                "eq_derives_removed": counts["eq_derives_removed"],
            }
        )
    return repairs


def repair_rust_missing_lib_targets(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    cargo_path = workspace / "Cargo.toml"
    if not cargo_path.is_file():
        return []
    cargo = _read_cargo_manifest(cargo_path)
    if not cargo:
        return []
    lib_targets = _missing_rust_lib_target_paths(workspace, cargo, artifact_quality_errors)
    repairs: list[dict[str, Any]] = []
    for target_path in lib_targets:
        if target_path.exists() or target_path.suffix.lower() != ".rs":
            continue
        modules = _rust_lib_modules_for_directory(target_path.parent)
        if not modules:
            continue
        content = "".join(f"pub mod {module};\n" for module in modules)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        repairs.append({"file": str(target_path.relative_to(workspace)), "modules": modules})
    return repairs


def repair_rust_lib_root_facade(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    cargo_path = workspace / "Cargo.toml"
    lib_path = workspace / "src" / "lib.rs"
    if not cargo_path.is_file() or not lib_path.is_file():
        return []
    cargo = _read_cargo_manifest(cargo_path)
    if not cargo:
        return []
    canonical_crate = _canonical_rust_crate_name(cargo)
    if not canonical_crate:
        return []

    requested_symbols = _parse_rust_lib_root_export_symbols(artifact_quality_errors, canonical_crate)
    requested_symbols = _dedupe_rust_symbols(
        [
            *requested_symbols,
            *_expand_rust_root_import_group_symbols(workspace, canonical_crate, requested_symbols),
        ]
    )
    source_had_lib_root_paths = _rust_workspace_uses_lib_root_path(workspace, canonical_crate)
    requested_modules = _parse_rust_external_module_imports(artifact_quality_errors, canonical_crate)
    requested_modules = [module for module in requested_modules if _rust_external_module_exists(workspace, module)]
    if not requested_symbols and not source_had_lib_root_paths and not requested_modules:
        return []

    repairs: list[dict[str, Any]] = []
    path_rewrites = _rewrite_rust_lib_root_paths(workspace, canonical_crate)
    external_modules = _ensure_external_rust_module_declarations(lib_path, requested_modules)
    module_exports = _ensure_rust_lib_root_exports(lib_path, workspace, requested_symbols)
    if path_rewrites or external_modules or module_exports:
        repairs.append(
            {
                "file": "src/lib.rs",
                "path_rewrites": path_rewrites,
                "module_exports": [*external_modules, *module_exports],
            }
        )
    return repairs


def repair_rust_unresolved_pub_uses(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    missing_symbols = _parse_missing_rust_symbols(artifact_quality_errors)
    if not missing_symbols:
        return []
    repairs: list[dict[str, Any]] = []
    for rust_file in sorted(workspace.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        try:
            original = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired = original
        removed: list[str] = []
        for symbol in missing_symbols:
            next_repaired = _remove_unresolved_pub_use_symbol(repaired, symbol)
            if next_repaired != repaired:
                repaired = next_repaired
                removed.append(symbol)
        if not removed or repaired == original:
            continue
        rust_file.write_text(repaired, encoding="utf-8")
        repairs.append({"file": str(rust_file.relative_to(workspace)), "symbols": removed})
    return repairs


def repair_rust_trait_imports(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    suggestions = _parse_rust_trait_import_suggestions(artifact_quality_errors)
    if not suggestions:
        return []
    repairs: list[dict[str, Any]] = []
    for relative_path, import_line in suggestions:
        target_path = (workspace / relative_path).resolve()
        try:
            relative = target_path.relative_to(workspace)
        except ValueError:
            continue
        if target_path.suffix.lower() != ".rs" or not target_path.is_file():
            continue
        try:
            original = target_path.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired = _insert_rust_use_import(original, import_line)
        if repaired == original:
            continue
        target_path.write_text(repaired, encoding="utf-8")
        repairs.append({"file": relative.as_posix(), "import": import_line})
    return repairs


def repair_rust_line_suggestions(workspace: Path, artifact_quality_errors: list[str]) -> list[dict[str, Any]]:
    if not workspace.is_dir():
        return []
    suggestions = _parse_rust_line_suggestions(artifact_quality_errors)
    if not suggestions:
        return []
    repairs: list[dict[str, Any]] = []
    for relative_path, line_number, code in suggestions:
        target_path = (workspace / relative_path).resolve()
        if not _path_inside_workspace(target_path, workspace) or target_path.suffix.lower() != ".rs":
            continue
        try:
            lines = target_path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError:
            continue
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        newline = "\n" if lines[line_index].endswith("\n") else ""
        replacement = f"{code.rstrip()}{newline}"
        if lines[line_index] == replacement:
            continue
        lines[line_index] = replacement
        target_path.write_text("".join(lines), encoding="utf-8")
        repairs.append({"file": str(target_path.relative_to(workspace)), "line": line_number})
    return repairs


def _read_cargo_manifest(cargo_path: Path) -> dict[str, Any]:
    try:
        payload = tomllib.loads(cargo_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_rust_crate_name(cargo: dict[str, Any]) -> str:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        lib_name = str(lib.get("name") or "").strip()
        if lib_name:
            return _rust_identifier_from_manifest_name(lib_name)
    package = cargo.get("package")
    if not isinstance(package, dict):
        return ""
    package_name = str(package.get("name") or "").strip()
    return _rust_identifier_from_manifest_name(package_name)


def _rust_identifier_from_manifest_name(name: str) -> str:
    normalized = name.replace("-", "_")
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        return ""
    return normalized


def _declared_rust_dependencies(cargo: dict[str, Any]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        value = cargo.get(key)
        if isinstance(value, dict):
            dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    target = cargo.get("target")
    if isinstance(target, dict):
        for target_payload in target.values():
            if not isinstance(target_payload, dict):
                continue
            for key in ("dependencies", "dev-dependencies", "build-dependencies"):
                value = target_payload.get(key)
                if isinstance(value, dict):
                    dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    dependency_names.discard("")
    return dependency_names


def _missing_rust_lib_target_paths(
    workspace: Path,
    cargo: dict[str, Any],
    artifact_quality_errors: list[str],
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for relative_path in _parse_missing_rust_lib_paths(artifact_quality_errors):
        target = (workspace / relative_path).resolve()
        if _path_inside_workspace(target, workspace) and target not in seen:
            seen.add(target)
            candidates.append(target)
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        configured = str(lib.get("path") or "").strip()
        if configured:
            target = (workspace / configured).resolve()
            if _path_inside_workspace(target, workspace) and not target.exists() and target not in seen:
                seen.add(target)
                candidates.append(target)
    return candidates


def _parse_missing_rust_lib_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for error in artifact_quality_errors:
        for match in _RUST_MISSING_LIB_PATH_RE.finditer(str(error or "")):
            relative_path = str(match.group("path") or "").strip().replace("\\", "/")
            if not relative_path or relative_path.startswith("/"):
                continue
            if relative_path not in seen:
                seen.add(relative_path)
                paths.append(relative_path)
    return paths


def _path_inside_workspace(path: Path, workspace: Path) -> bool:
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return False
    return True


def _rust_lib_modules_for_directory(directory: Path) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()
    if not directory.is_dir():
        return modules
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        module = ""
        if child.is_file() and child.suffix == ".rs" and child.name not in {"lib.rs", "main.rs"}:
            module = child.stem
        elif child.is_dir() and (child / "mod.rs").is_file():
            module = child.name
        if not module or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module) or module in seen:
            continue
        seen.add(module)
        modules.append(module)
    return modules


def _parse_unresolved_rust_crates(artifact_quality_errors: list[str]) -> list[str]:
    seen: set[str] = set()
    crates: list[str] = []
    for error in artifact_quality_errors:
        for match in _RUST_UNRESOLVED_CRATE_RE.finditer(str(error or "")):
            crate = match.group("crate")
            if crate not in seen:
                seen.add(crate)
                crates.append(crate)
    return crates


def _rust_dependency_packages_to_add(workspace: Path, artifact_quality_errors: list[str]) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for error in artifact_quality_errors:
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(str(error or "")):
            root = match.group("import").split("::", 1)[0]
            if root in _KNOWN_RUST_DEPENDENCIES and root not in seen:
                seen.add(root)
                packages.append(root)
    source_text = _rust_workspace_source_text(workspace)
    if "serde_json::" in source_text and "serde_json" not in seen:
        seen.add("serde_json")
        packages.append("serde_json")
    return packages


def _rust_workspace_source_text(workspace: Path) -> str:
    chunks: list[str] = []
    for rust_file in sorted(workspace.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        try:
            chunks.append(rust_file.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


def _parse_rust_serde_derive_targets(
    artifact_quality_errors: list[str],
) -> list[tuple[str, str, set[str]]]:
    targets: dict[tuple[str, str], set[str]] = {}
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors))
    for match in _RUST_SERDE_DERIVE_SUGGESTION_RE.finditer(text):
        module = str(match.group("module") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        trait = str(match.group("trait") or "").strip()
        if not module or not symbol or trait not in {"Serialize", "Deserialize"}:
            continue
        targets.setdefault((module, symbol), set()).add(f"serde::{trait}")
    return [(module, symbol, traits) for (module, symbol), traits in targets.items()]


def _find_rust_file_for_module_symbol(workspace: Path, module: str, symbol: str) -> Path | None:
    src = workspace / "src"
    if not src.is_dir():
        return None
    symbol_pattern = re.compile(
        rf"(?m)^\s*pub\s+(?:struct|enum|trait|type)\s+{re.escape(symbol)}\b"
        rf"|^\s*(?:struct|enum|trait|type)\s+{re.escape(symbol)}\b"
    )
    candidates: list[Path] = []
    for rust_file in sorted(src.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        if rust_file.stem == module:
            candidates.insert(0, rust_file)
        else:
            candidates.append(rust_file)
    for rust_file in candidates:
        try:
            text = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if symbol_pattern.search(text):
            return rust_file
    return None


def _ensure_rust_file_serde_derives(text: str, traits: set[str]) -> tuple[str, int]:
    if not traits:
        return text, 0
    lines = text.splitlines(keepends=True)
    derive_count = 0
    index = 0
    while index < len(lines):
        if not re.match(r"^\s*(?:pub\s+)?(?:struct|enum)\s+[A-Za-z_][A-Za-z0-9_]*\b", lines[index]):
            index += 1
            continue
        derive_index = _rust_existing_derive_line_index(lines, index)
        if derive_index is None:
            indent_match = re.match(r"^(\s*)", lines[index])
            indent = indent_match.group(1) if indent_match else ""
            lines.insert(index, f"{indent}#[derive({', '.join(sorted(traits))})]\n")
            derive_count += len(traits)
            index += 2
            continue
        repaired, added = _add_rust_derive_traits_to_line(lines[derive_index], traits)
        if added:
            lines[derive_index] = repaired
            derive_count += added
        index += 1
    return "".join(lines), derive_count


def _rust_existing_derive_line_index(lines: list[str], item_index: int) -> int | None:
    index = item_index - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index >= 0 and re.match(r"^\s*#\[derive\([^)]*\)\]\s*$", lines[index]):
        return index
    return None


def _add_rust_derive_traits_to_line(line: str, traits: set[str]) -> tuple[str, int]:
    match = re.match(r"^(?P<indent>\s*)#\[derive\((?P<body>[^)]*)\)\](?P<newline>\n?)$", line)
    if not match:
        return line, 0
    items = [item.strip() for item in str(match.group("body") or "").split(",") if item.strip()]
    added = 0
    for trait in sorted(traits):
        short = trait.rsplit("::", 1)[-1]
        if any(item in (trait, short) or item.endswith(f"::{short}") for item in items):
            continue
        items.append(trait)
        added += 1
    if not added:
        return line, 0
    return f"{match.group('indent')}#[derive({', '.join(items)})]{match.group('newline')}", added


def _artifact_errors_include_float_eq_failure(artifact_quality_errors: list[str]) -> bool:
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors)).lower()
    return "the trait bound `f32: eq` is not satisfied" in text or "the trait bound `f64: eq` is not satisfied" in text


def _remove_rust_eq_derives_for_float_fields(text: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    removed = 0
    for index, line in enumerate(lines):
        if "Eq" not in line or not re.match(r"^\s*#\[derive\([^)]*\)\]", line):
            continue
        block = _rust_item_block_after_derive(lines, index)
        if "f32" not in block and "f64" not in block:
            continue
        repaired, did_remove = _remove_rust_derive_trait_from_line(line, "Eq")
        if did_remove:
            lines[index] = repaired
            removed += 1
    return "".join(lines), removed


def _rust_item_block_after_derive(lines: list[str], derive_index: int) -> str:
    item_index = derive_index + 1
    while item_index < len(lines) and (not lines[item_index].strip() or lines[item_index].lstrip().startswith("///")):
        item_index += 1
    end_index = item_index + 1
    while end_index < len(lines):
        if re.match(r"^\s*(?:#\[derive\(|pub\s+(?:struct|enum)|(?:struct|enum)\s+)", lines[end_index]):
            break
        end_index += 1
    return "".join(lines[item_index:end_index])


def _remove_rust_derive_trait_from_line(line: str, trait: str) -> tuple[str, bool]:
    match = re.match(r"^(?P<indent>\s*)#\[derive\((?P<body>[^)]*)\)\](?P<newline>\n?)$", line)
    if not match:
        return line, False
    items = [item.strip() for item in str(match.group("body") or "").split(",") if item.strip()]
    filtered = [item for item in items if item != trait]
    if len(filtered) == len(items):
        return line, False
    return f"{match.group('indent')}#[derive({', '.join(filtered)})]{match.group('newline')}", True


def _cargo_dependency_declared(cargo_text: str, package: str) -> bool:
    return bool(re.search(rf"(?m)^\s*{re.escape(package)}\s*=", cargo_text))


def _insert_cargo_dependency(cargo_text: str, dependency_line: str) -> str:
    dependency_header = re.search(r"(?m)^\[dependencies\]\s*$", cargo_text)
    if not dependency_header:
        suffix = "" if cargo_text.endswith("\n") else "\n"
        return f"{cargo_text}{suffix}\n[dependencies]\n{dependency_line}\n"
    insert_at = dependency_header.end()
    return f"{cargo_text[:insert_at]}\n{dependency_line}{cargo_text[insert_at:]}"


def _parse_missing_rust_symbols(artifact_quality_errors: list[str]) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for error in artifact_quality_errors:
        for match in _RUST_NO_SYMBOL_RE.finditer(str(error or "")):
            symbol = match.group("symbol")
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    return symbols


def _parse_rust_trait_import_suggestions(artifact_quality_errors: list[str]) -> list[tuple[str, str]]:
    suggestions: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors))
    for match in _RUST_TRAIT_IMPORT_SUGGESTION_RE.finditer(text):
        relative_path = str(match.group("path") or "").strip().replace("\\", "/")
        import_line = str(match.group("import") or "").strip()
        if not relative_path or not import_line.startswith("use ") or not import_line.endswith(";"):
            continue
        key = (relative_path, import_line)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(key)
    return suggestions


def _parse_rust_line_suggestions(artifact_quality_errors: list[str]) -> list[tuple[str, int, str]]:
    suggestions: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors))
    for pattern in (_RUST_FIELD_METHOD_LINE_SUGGESTION_RE, _RUST_FULL_LINE_SUGGESTION_RE):
        for match in pattern.finditer(text):
            relative_path = str(match.group("path") or "").strip().replace("\\", "/")
            code = str(match.group("code") or "").rstrip()
            try:
                line_number = int(match.group("line"))
            except (TypeError, ValueError):
                continue
            if not relative_path or relative_path.startswith("/") or not code.strip():
                continue
            key = (relative_path, line_number, code)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(key)
    return suggestions


def _parse_rust_lib_root_export_symbols(artifact_quality_errors: list[str], canonical_crate: str) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors))
    for match in _RUST_LIB_ROOT_EXPORT_HINT_RE.finditer(text):
        symbol = str(match.group("symbol") or "").strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for match in _RUST_ROOT_UNRESOLVED_IMPORT_RE.finditer(text):
        crate = str(match.group("crate") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        if crate == canonical_crate and symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for match in re.finditer(
        rf"[`'\"]{re.escape(canonical_crate)}::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
        text,
    ):
        symbol = str(match.group("symbol") or "").strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for match in _RUST_ROOT_TYPE_FIELD_MISMATCH_RE.finditer(text):
        crate = str(match.group("crate") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        if crate == canonical_crate and symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    for match in _RUST_ROOT_STRUCT_FIELD_MISMATCH_RE.finditer(text):
        crate = str(match.group("crate") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        if crate == canonical_crate and symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _dedupe_rust_symbols(symbols: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        normalized = str(symbol or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _expand_rust_root_import_group_symbols(
    workspace: Path,
    canonical_crate: str,
    requested_symbols: list[str],
) -> list[str]:
    requested_modules = {
        module for symbol in requested_symbols if (module := _find_rust_module_exporting_symbol(workspace, symbol))
    }
    if not requested_modules:
        return []

    grouped_root_import_re = re.compile(
        rf"use\s+{re.escape(canonical_crate)}::\{{(?P<body>.*?)\}}\s*;",
        re.DOTALL,
    )
    requested_set = set(requested_symbols)
    companions: list[str] = []
    seen: set[str] = set()
    for rust_file in sorted(workspace.rglob("*.rs")):
        try:
            relative_parts = rust_file.relative_to(workspace).parts
        except ValueError:
            continue
        if "target" in relative_parts:
            continue
        try:
            text = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in grouped_root_import_re.finditer(text):
            import_symbols = _parse_rust_use_group_symbols(str(match.group("body") or ""))
            if not requested_set.intersection(import_symbols):
                continue
            for symbol in import_symbols:
                module = _find_rust_module_exporting_symbol(workspace, symbol)
                if module not in requested_modules or symbol in seen:
                    continue
                seen.add(symbol)
                companions.append(symbol)
    return companions


def _parse_rust_use_group_symbols(body: str) -> list[str]:
    symbols: list[str] = []
    for item in body.replace("\n", " ").split(","):
        token = item.strip()
        if not token:
            continue
        token = token.split(" as ", 1)[0].strip()
        if "::" in token:
            token = token.rsplit("::", 1)[-1].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            symbols.append(token)
    return symbols


def _parse_rust_external_module_imports(artifact_quality_errors: list[str], canonical_crate: str) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()
    text = _ANSI_ESCAPE_RE.sub("", "\n".join(str(error or "") for error in artifact_quality_errors))
    for match in re.finditer(
        rf"[`'\"]{re.escape(canonical_crate)}::(?P<module>[A-Za-z_][A-Za-z0-9_]*)::"
        r"[A-Za-z_][A-Za-z0-9_]*[`'\"]",
        text,
    ):
        module = str(match.group("module") or "").strip()
        if module and module not in seen:
            seen.add(module)
            modules.append(module)
    return modules


def _rust_external_module_exists(workspace: Path, module: str) -> bool:
    normalized = str(module or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        return False
    return (workspace / "src" / f"{normalized}.rs").is_file() or (workspace / "src" / normalized / "mod.rs").is_file()


def _ensure_external_rust_module_declarations(lib_path: Path, requested_modules: list[str]) -> list[str]:
    if not requested_modules:
        return []
    try:
        original = lib_path.read_text(encoding="utf-8")
    except OSError:
        return []
    repaired = original
    declarations: list[str] = []
    for module in requested_modules:
        decl = f"pub mod {module};"
        if re.search(rf"(?m)^\s*pub\s+mod\s+{re.escape(module)}\s*;", repaired):
            continue
        next_repaired, replaced = _replace_inline_rust_pub_module_with_external(repaired, module)
        if replaced:
            repaired = next_repaired
            declarations.append(decl)
    if repaired != original:
        lib_path.write_text(repaired, encoding="utf-8")
    return declarations


def _replace_inline_rust_pub_module_with_external(text: str, module: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?m)^(?P<indent>\s*)pub\s+mod\s+{re.escape(module)}\s*\{{")
    match = pattern.search(text)
    if not match:
        return text, False
    brace_index = text.find("{", match.start(), match.end())
    if brace_index < 0:
        return text, False
    depth = 0
    end_index = -1
    for index in range(brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end_index = index + 1
                break
    if end_index < 0:
        return text, False
    if end_index < len(text) and text[end_index : end_index + 1] == "\n":
        end_index += 1
    declaration = f"{match.group('indent')}pub mod {module};\n"
    return text[: match.start()] + declaration + text[end_index:], True


def _rust_workspace_uses_lib_root_path(workspace: Path, canonical_crate: str) -> bool:
    crate_lib_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(canonical_crate)}::lib::")
    crate_relative_pattern = re.compile(r"(?<![A-Za-z0-9_])crate::lib::")
    for rust_file in sorted(workspace.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        try:
            text = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if crate_lib_pattern.search(text) or crate_relative_pattern.search(text):
            return True
    return False


def _rewrite_rust_lib_root_paths(workspace: Path, canonical_crate: str) -> int:
    crate_lib_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(canonical_crate)}::lib::")
    crate_relative_pattern = re.compile(r"(?<![A-Za-z0-9_])crate::lib::")
    rewrites = 0
    for rust_file in sorted(workspace.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        try:
            original = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired, crate_count = crate_lib_pattern.subn(f"{canonical_crate}::", original)
        repaired, relative_count = crate_relative_pattern.subn("crate::", repaired)
        if repaired == original:
            continue
        rust_file.write_text(repaired, encoding="utf-8")
        rewrites += crate_count + relative_count
    return rewrites


def _ensure_rust_lib_root_exports(lib_path: Path, workspace: Path, requested_symbols: list[str]) -> list[str]:
    if not requested_symbols:
        return []
    try:
        original = lib_path.read_text(encoding="utf-8")
    except OSError:
        return []

    repaired = original
    exports: list[str] = []
    for symbol in requested_symbols:
        module = _find_rust_module_exporting_symbol(workspace, symbol)
        if not module:
            continue
        repaired = _remove_conflicting_rust_root_symbol_exports(repaired, preferred_module=module, symbol=symbol)
        module_decl = f"pub mod {module};"
        export_decl = f"pub use {module}::{symbol};"
        if not re.search(rf"(?m)^\s*pub\s+mod\s+{re.escape(module)}\s*;", repaired):
            repaired = _append_rust_root_decl(repaired, module_decl)
            exports.append(module_decl)
        if not re.search(rf"(?m)^\s*pub\s+use\s+{re.escape(module)}::{re.escape(symbol)}\s*;", repaired):
            repaired = _append_rust_root_decl(repaired, export_decl)
            exports.append(export_decl)
    if repaired != original:
        lib_path.write_text(repaired, encoding="utf-8")
    return exports


def _remove_conflicting_rust_root_symbol_exports(text: str, *, preferred_module: str, symbol: str) -> str:
    lines = text.splitlines(keepends=True)
    repaired: list[str] = []
    for line in lines:
        next_line = _remove_conflicting_simple_rust_pub_use(line, preferred_module=preferred_module, symbol=symbol)
        if next_line is None:
            continue
        next_line = _remove_conflicting_grouped_rust_pub_use(
            next_line,
            preferred_module=preferred_module,
            symbol=symbol,
        )
        if next_line:
            repaired.append(next_line)
    return "".join(repaired)


def _remove_conflicting_simple_rust_pub_use(line: str, *, preferred_module: str, symbol: str) -> str | None:
    match = re.match(
        r"^(?P<indent>\s*)pub\s+use\s+(?P<path>[A-Za-z_][A-Za-z0-9_:]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\s*;\s*(?P<newline>\n?)$",
        line,
    )
    if not match or str(match.group("symbol") or "") != symbol:
        return line
    path = str(match.group("path") or "")
    if path == preferred_module or path.startswith(f"{preferred_module}::"):
        return line
    return None


def _remove_conflicting_grouped_rust_pub_use(line: str, *, preferred_module: str, symbol: str) -> str:
    newline = "\n" if line.endswith("\n") else ""
    match = re.match(
        r"^(?P<indent>\s*)pub\s+use\s+(?P<path>[A-Za-z_][A-Za-z0-9_:]*)::\{(?P<body>[^}]+)\}\s*;",
        line,
    )
    if not match:
        return line
    path = str(match.group("path") or "")
    if path == preferred_module or path.startswith(f"{preferred_module}::"):
        return line
    items = [item.strip() for item in str(match.group("body") or "").split(",") if item.strip()]
    filtered = [item for item in items if item.split(" as ", 1)[0].strip() != symbol]
    if len(filtered) == len(items):
        return line
    if not filtered:
        return ""
    return f"{match.group('indent')}pub use {path}::{{{', '.join(filtered)}}};{newline}"


def _find_rust_module_exporting_symbol(workspace: Path, symbol: str) -> str:
    src = workspace / "src"
    if not src.is_dir():
        return ""
    symbol_pattern = re.compile(
        rf"(?m)^\s*pub\s+(?:async\s+)?(?:fn|struct|enum|trait|type|const|static)\s+{re.escape(symbol)}\b"
        rf"|^\s*pub\s+use\s+[^;]*\b{re.escape(symbol)}\b",
    )
    candidates: list[tuple[str, Path]] = []
    for child in sorted(src.iterdir(), key=lambda item: item.name):
        if child.name in {"lib.rs", "main.rs", "bin"}:
            continue
        if child.is_file() and child.suffix == ".rs":
            candidates.append((child.stem, child))
        elif child.is_dir() and (child / "mod.rs").is_file():
            candidates.append((child.name, child / "mod.rs"))
    for module, path in candidates:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", module):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if symbol_pattern.search(text):
            return module
    return ""


def _append_rust_root_decl(text: str, declaration: str) -> str:
    suffix = "" if text.endswith("\n") else "\n"
    return f"{text}{suffix}{declaration}\n"


def _insert_rust_use_import(text: str, import_line: str) -> str:
    if re.search(rf"(?m)^\s*{re.escape(import_line)}\s*$", text):
        return text
    lines = text.splitlines(keepends=True)
    insert_index = _rust_use_insert_index(lines)
    lines.insert(insert_index, f"{import_line}\n")
    return "".join(lines)


def _rust_use_insert_index(lines: list[str]) -> int:
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or stripped.startswith("//!") or stripped.startswith("#!["):
            index += 1
            continue
        break

    insert_index = index
    seen_use = False
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("use ") and stripped.endswith(";"):
            seen_use = True
            insert_index = index + 1
            index += 1
            continue
        if seen_use and stripped == "":
            insert_index = index + 1
            index += 1
            continue
        break
    return insert_index


def _remove_unresolved_pub_use_symbol(text: str, symbol: str) -> str:
    """Remove a single symbol from pub use statements (flat or nested groups)."""
    repaired = re.sub(
        rf"(?m)^\s*pub\s+use\s+[A-Za-z_][A-Za-z0-9_:]*::{re.escape(symbol)}\s*;\s*\n?",
        "",
        text,
    )
    # Flat group: pub use path::{A, B, Symbol};
    group_pattern = re.compile(
        r"(?m)^(?P<prefix>\s*pub\s+use\s+[A-Za-z_][A-Za-z0-9_:]*::\{)(?P<body>[^}]+)(?P<suffix>\}\s*;\s*)$"
    )

    def replace_group(match: re.Match[str]) -> str:
        items = [item.strip() for item in match.group("body").split(",") if item.strip()]
        filtered = [item for item in items if item.split(" as ", 1)[0].strip() != symbol]
        if len(filtered) == len(items):
            return match.group(0)
        if not filtered:
            return ""
        return f"{match.group('prefix')}{', '.join(filtered)}{match.group('suffix')}"

    repaired = group_pattern.sub(replace_group, repaired)

    # Nested group: pub use path::{sub::{A, Symbol}, sub2::{B}};
    # Remove the symbol from inner groups, then clean up empty inner groups.
    # Step 1: Remove ", Symbol" or "Symbol, " or "Symbol" inside braces
    repaired = re.sub(rf",\s*{re.escape(symbol)}\b", "", repaired)
    repaired = re.sub(rf"\b{re.escape(symbol)}\s*,\s*", "", repaired)
    repaired = re.sub(rf"\b{re.escape(symbol)}\b(?=[,\s}}])", "", repaired)
    # Step 2: Remove empty sub-groups like "ingredient::{}, " or "ingredient::{}"
    repaired = re.sub(r",\s*\w+::\{\s*\}", "", repaired)
    repaired = re.sub(r"\w+::\{\s*\}\s*,\s*", "", repaired)
    repaired = re.sub(r"\w+::\{\s*\}", "", repaired)
    # Step 3: Remove entirely empty pub use statements
    repaired = re.sub(r"(?m)^\s*pub\s+use\s+[A-Za-z_][A-Za-z0-9_:]*::\{\s*\}\s*;\s*\n?", "", repaired)
    # Step 4: Remove pub use with only empty braces left (multi-line)
    repaired = re.sub(r"pub\s+use\s+[A-Za-z_][A-Za-z0-9_:]*::\{[\s,]*\}\s*;\s*", "", repaired)

    return repaired


def _rust_crate_names_look_related(missing: str, canonical: str) -> bool:
    missing_tokens = _crate_name_tokens(missing)
    canonical_tokens = _crate_name_tokens(canonical)
    if len(missing_tokens) < 2 or not canonical_tokens:
        return False
    overlap = missing_tokens & canonical_tokens
    return missing_tokens.issubset(canonical_tokens) or len(overlap) >= 2


def _cargo_declares_local_rust_lib(workspace: Path, cargo: dict[str, Any]) -> bool:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        configured = str(lib.get("path") or "src/lib.rs").strip() or "src/lib.rs"
        return (workspace / configured).is_file()
    return (workspace / "src" / "lib.rs").is_file()


def _rust_crate_prefix_used_in_binary_entrypoint(workspace: Path, missing_crate: str) -> bool:
    candidates = [workspace / "src" / "main.rs"]
    src_bin = workspace / "src" / "bin"
    if src_bin.is_dir():
        candidates.extend(sorted(src_bin.glob("*.rs")))
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        if pattern.search(text):
            return True
    return False


def _crate_name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[_\\W]+", name.lower()) if token}


def repair_rust_unused_imports(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Remove unused imports flagged by cargo check.

    Parses ``warning: unused import: `X``` and comments out or removes the
    specific import from the use statement on the indicated line.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    unused_re = re.compile(
        r"warning:\s*unused\s+import:\s*[`'\"](?P<name>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
        r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    repairs: list[dict[str, Any]] = []
    grouped: dict[str, list[tuple[str, int]]] = {}
    for m in unused_re.finditer(stderr):
        path = m.group("path").strip()
        name = m.group("name")
        line = int(m.group("line"))
        grouped.setdefault(path, []).append((name, line))

    for rel_path, items in grouped.items():
        target = (workspace / rel_path).resolve()
        if not _path_inside_workspace(target, workspace) or not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        modified = False
        for name, line_num in items:
            idx = line_num - 1
            if idx < 0 or idx >= len(lines):
                continue
            line_text = lines[idx]
            # Try removing just the name from a multi-import use statement
            new_line = re.sub(rf",\s*{re.escape(name)}\b", "", line_text)
            new_line = re.sub(rf"\b{re.escape(name)}\s*,\s*", "", new_line)
            # If the use statement is now empty or trivial, comment out the line
            remaining = re.search(r"use\s+(.+?)\s*;", new_line)
            if remaining and remaining.group(1).strip().strip("{}"):
                lines[idx] = new_line
            else:
                lines[idx] = f"// [repair-unused] {line_text.strip()}"
            modified = True
        if modified:
            target.write_text("\n".join(lines), encoding="utf-8")
            repairs.append(
                {
                    "file": rel_path,
                    "symbols": [name for name, _ in items],
                }
            )
    if repairs:
        import logging as _log

        _log.getLogger(__name__).info("Rust unused imports repair: %d file(s)", len(repairs))
    return repairs


def repair_rust_missing_fields(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Add missing struct fields referenced in code.

    When code references ``ingredient.intensity`` but the Ingredient struct
    does not declare an ``intensity`` field, add it with an inferred type.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    field_error_re = re.compile(
        r"error\[E0609\]:\s*no field [`'\"](?P<field>[A-Za-z_][A-Za-z0-9_]*)[`'\"]"
        r"\s+on type [`'\"]&?(?P<struct>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
        re.IGNORECASE,
    )
    seen: set[tuple[str, str]] = set()
    repairs: list[dict[str, Any]] = []

    for m in field_error_re.finditer(stderr):
        field_name = m.group("field")
        struct_name = m.group("struct")
        key = (struct_name, field_name)
        if key in seen:
            continue
        seen.add(key)

        # Find struct definition
        struct_def_re = re.compile(
            rf"(pub\s+)?struct\s+{re.escape(struct_name)}\s*\{{(?P<body>[^}}]*)\}}",
            re.DOTALL,
        )
        for rs_file in sorted(workspace.rglob("*.rs")):
            if "target" in rs_file.relative_to(workspace).parts:
                continue
            try:
                content = rs_file.read_text(encoding="utf-8")
            except OSError:
                continue
            sm = struct_def_re.search(content)
            if sm and field_name not in sm.group("body"):
                # Try to infer type from usage context first
                field_type = _infer_rust_field_type_from_usage(workspace, field_name) or _infer_rust_field_type(
                    field_name
                )
                new_field = f"    pub {field_name}: {field_type},\n"
                # Insert before closing brace
                end = sm.end() - 1  # position of '}'
                new_content = content[:end] + new_field + content[end:]
                rs_file.write_text(new_content, encoding="utf-8")
                repairs.append(
                    {
                        "file": str(rs_file.relative_to(workspace)),
                        "struct": struct_name,
                        "field": field_name,
                        "type": field_type,
                    }
                )
                break
    return repairs


def _infer_rust_field_type_from_usage(workspace: Path, field_name: str) -> str | None:
    """Infer field type from how it's used in function calls.

    If ``x.field_name`` is passed to a function that expects ``f32``,
    return ``"f32"``. This catches the common case of numeric type mismatches.
    """
    # Look for function definitions: fn func_name(..., field_name: TYPE, ...)
    fn_param_re = re.compile(
        rf"fn\s+\w+\s*\([^)]*{re.escape(field_name)}\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
    )
    for rs_file in sorted(workspace.rglob("*.rs")):
        if "target" in rs_file.relative_to(workspace).parts:
            continue
        try:
            content = rs_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Check if field_name appears as a function parameter with explicit type
        m = fn_param_re.search(content)
        if m:
            return m.group(1)
    return None


def _infer_rust_field_type(field_name: str) -> str:
    """Infer a Rust type for a field name based on naming conventions."""
    lower = field_name.lower()
    if lower.endswith("_count") or lower in ("count", "num", "index"):
        return "usize"
    if lower in ("intensity", "strength", "level", "score", "weight", "ratio", "temperature"):
        return "f64"
    if lower.startswith("is_") or lower.startswith("has_") or lower in ("active", "enabled", "visible", "done"):
        return "bool"
    if lower.endswith("_id") or lower == "id":
        return "u64"
    if lower.endswith("_list") or lower.endswith("_items") or lower.endswith("_vec"):
        return "Vec<String>"
    if lower in ("color", "colour", "hex", "palette", "name", "label", "description", "title"):
        return "String"
    return "String"


def _run_cargo_check_stderr(workspace: Path) -> str:
    """Run cargo check and return stderr."""
    if not (workspace / "Cargo.toml").is_file():
        return ""
    import shutil
    import subprocess

    cargo = shutil.which("cargo")
    if not cargo:
        return ""
    try:
        result = subprocess.run(
            [cargo, "check", "--quiet"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stderr or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def repair_rust_field_rename_suggestions(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Apply cargo's field rename suggestions (e.g., ingredients → ingredient).

    When cargo says ``no field `ingredients` on type `&Recipe``` and suggests
    ``a field with a similar name exists``, extract the wrong and correct field
    names from the error message and do a targeted rename on the specific line.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    # Extract: error[E0609]: no field `wrong` on type `&Struct`
    # + --> file.rs:line
    field_error_re = re.compile(
        r"error\[E0609\]:\s*no field [`'\"](?P<wrong>[A-Za-z_][A-Za-z0-9_]*)[`'\"]"
        r"\s+on type [`'\"]&?(?P<struct>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
        r"-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+)",
        re.DOTALL,
    )
    # Extract the suggested field name from the diff line:
    # 72 +         for ingredient in &recipe.ingredient {
    suggestion_re = re.compile(r"\d+\s*\+\s*.+\.(?P<correct>[A-Za-z_][A-Za-z0-9_]*)\b")

    repairs: list[dict[str, Any]] = []
    error_blocks = re.split(r"(?=error\[E\d+\])", stderr)

    for block in error_blocks:
        err_match = field_error_re.search(block)
        if not err_match:
            continue
        wrong_field = err_match.group("wrong")
        rel_path = err_match.group("path").strip()
        line_num = int(err_match.group("line"))

        # Find the suggested correct field name
        sug_match = suggestion_re.search(block)
        if not sug_match:
            continue
        correct_field = sug_match.group("correct")
        if wrong_field == correct_field:
            continue

        target = (workspace / rel_path).resolve()
        if not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        idx = line_num - 1
        if idx < 0 or idx >= len(lines):
            continue
        old_line = lines[idx]
        # Replace .wrong with .correct on this specific line
        new_line = old_line.replace(f".{wrong_field}", f".{correct_field}")
        if new_line != old_line:
            lines[idx] = new_line
            target.write_text("\n".join(lines), encoding="utf-8")
            repairs.append(
                {
                    "file": rel_path,
                    "action": f"field_rename_{wrong_field}_to_{correct_field}",
                }
            )
    return repairs


def repair_rust_wrong_crate_paths(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Fix wrong ``crate::X`` import paths using cargo's suggestions.

    When code uses ``use crate::recipe::Recipe`` but the module is at
    ``crate::models::recipe``, cargo helpfully suggests the correct path.
    Parse the suggestion and apply it.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    repairs: list[dict[str, Any]] = []
    error_blocks = re.split(r"(?=error\[E\d+\])", stderr)

    for block in error_blocks:
        # Also try the simpler format: just the replacement line
        path_match = re.search(r"--> (?P<path>[^:\n]+\.rs):(?P<line>\d+)", block)
        if not path_match:
            continue
        rel_path = path_match.group("path").strip()
        line_num = int(path_match.group("line"))

        # Look for cargo's inline suggestion
        sug_match = re.search(
            r"help:.*?a similar path exists.*?\n\s*\|\s*\n\s*\d+\s*\|\s*(?P<sug>use\s+[^;]+;)", block, re.DOTALL
        )
        if not sug_match:
            continue
        suggestion = sug_match.group("sug").strip()

        target = (workspace / rel_path).resolve()
        if not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        idx = line_num - 1
        if idx < 0 or idx >= len(lines):
            continue
        old_line = lines[idx].strip()
        # Only replace if it's a use statement
        if not old_line.startswith("use "):
            continue
        lines[idx] = suggestion
        target.write_text("\n".join(lines), encoding="utf-8")
        repairs.append(
            {
                "file": rel_path,
                "action": f"fixed_crate_path_line_{line_num}",
            }
        )
    return repairs


def repair_rust_incompatible_copy_derives(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Remove ``Copy`` derive from structs containing non-Copy fields (String, Vec, etc.)."""
    if not stderr or "the trait `Copy` cannot be implemented" not in stderr:
        return []

    copy_error_re = re.compile(
        r"the trait `Copy` cannot be implemented.*?-->.*?(?P<path>[^:\n]+\.rs):(?P<line>\d+)",
        re.DOTALL,
    )
    repairs: list[dict[str, Any]] = []

    for m in copy_error_re.finditer(stderr):
        rel_path = m.group("path").strip()
        line_num = int(m.group("line"))
        target = (workspace / rel_path).resolve()
        if not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        # Find the derive line (could be above the struct line)
        for offset in range(0, 5):
            idx = line_num - 1 - offset
            if idx < 0 or idx >= len(lines):
                continue
            line = lines[idx]
            if "#[derive(" in line and "Copy" in line:
                # Remove Copy from the derive list
                new_line = re.sub(r",\s*Copy\b", "", line)
                new_line = re.sub(r"\bCopy\s*,\s*", "", new_line)
                new_line = re.sub(r"\bCopy\b", "", new_line)
                if new_line != line:
                    lines[idx] = new_line
                    target.write_text("\n".join(lines), encoding="utf-8")
                    repairs.append({"file": rel_path, "action": f"removed_copy_derive_line_{idx + 1}"})
                break
    return repairs


def repair_rust_method_self_signatures(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Fix method signatures where ``&self`` or ``&mut self`` is missing.

    Common LLM mistake: ``pub fn foo(&) -> ...`` instead of ``pub fn foo(&self) -> ...``
    or ``pub fn bar(&mut) -> ...`` instead of ``pub fn bar(&mut self) -> ...``.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    # Pattern: error: expected parameter name, found `)`
    # --> file.rs:line:col
    missing_self_re = re.compile(
        r"error:\s+expected parameter name.*?-->.*?(?P<path>[^:\n]+\.rs):(?P<line>\d+)",
        re.DOTALL,
    )

    repairs: list[dict[str, Any]] = []
    for m in missing_self_re.finditer(stderr):
        rel_path = m.group("path").strip()
        line_num = int(m.group("line"))
        target = (workspace / rel_path).resolve()
        if not target.is_file():
            continue
        try:
            lines = target.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        idx = line_num - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        fixed = False
        # Fix (&) → (&self)
        if re.search(r"\(\s*&\s*\)", line) and "fn " in line:
            lines[idx] = re.sub(r"\(\s*&\s*\)", "(&self)", line)
            fixed = True
        # Fix (&mut) → (&mut self)
        elif re.search(r"\(\s*&mut\s*\)", line) and "fn " in line:
            lines[idx] = re.sub(r"\(\s*&mut\s*\)", "(&mut self)", line)
            fixed = True
        if fixed:
            target.write_text("\n".join(lines), encoding="utf-8")
            repairs.append(
                {
                    "file": rel_path,
                    "action": f"fixed_method_self_signature_line_{line_num}",
                }
            )
    return repairs


def run_all_rust_post_repairs(workspace: Path) -> list[dict[str, Any]]:
    """Run one Rust post-execution repair pass from a fresh compiler snapshot.

    This migration bridge keeps legacy strategy callbacks in this module, but
    convergence ownership belongs to director.runtime's scheduler. This callback
    must not run its own retry loop or stamp authoritative round/max-round
    metadata; the runtime schedule injects those fields around each callback run.
    """
    if not (workspace / "Cargo.toml").is_file():
        return []

    stderr = _run_cargo_check_stderr(workspace)
    if not stderr or ("error" not in stderr and "warning" not in stderr):
        return []

    errors_before = _count_rust_cargo_diagnostics(stderr)
    round_batches = _run_rust_post_repair_round(workspace, stderr)
    if not round_batches:
        return []

    stderr_after = _run_cargo_check_stderr(workspace)
    errors_after = _count_rust_cargo_diagnostics(stderr_after)
    annotated: list[dict[str, Any]] = []
    for records, source_tool, phase, priority in round_batches:
        annotated.extend(
            _annotate_rust_post_repair_records(
                records,
                source_tool=source_tool,
                phase=phase,
                priority=priority,
                errors_before=errors_before,
                errors_after=errors_after,
            )
        )
    return annotated


def _run_rust_post_repair_round(
    workspace: Path,
    stderr: str,
) -> list[tuple[list[dict[str, Any]], str, str, int]]:
    errors = [stderr]
    batches: list[tuple[list[dict[str, Any]], str, str, int]] = []
    for records, source_tool, phase, priority in (
        (
            repair_rust_dependencies(workspace, errors),
            "deterministic_rust_dependency_repair",
            "dependency_resolution",
            0,
        ),
        (
            repair_rust_crate_imports(workspace, errors),
            "deterministic_rust_crate_import_repair",
            "dependency_resolution",
            0,
        ),
        (
            repair_rust_wrong_crate_paths(workspace, stderr),
            "deterministic_rust_post_repair",
            "dependency_resolution",
            0,
        ),
        (
            repair_rust_method_self_signatures(workspace, stderr),
            "deterministic_rust_post_repair",
            "code_repair",
            1,
        ),
        (
            repair_rust_incompatible_copy_derives(workspace, stderr),
            "deterministic_rust_derive_repair",
            "code_repair",
            1,
        ),
        (
            repair_rust_duplicate_module_files(workspace),
            "deterministic_rust_post_repair",
            "structural_repair",
            1,
        ),
        (
            repair_rust_missing_module_files(workspace),
            "deterministic_rust_post_repair",
            "structural_repair",
            1,
        ),
        (
            repair_rust_missing_binary_entrypoint(workspace),
            "deterministic_rust_post_repair",
            "structural_repair",
            1,
        ),
        (
            repair_rust_missing_derives(workspace, stderr),
            "deterministic_rust_derive_repair",
            "code_repair",
            2,
        ),
        (
            repair_rust_unused_imports(workspace, stderr),
            "deterministic_rust_post_repair",
            "code_repair",
            2,
        ),
        (
            repair_rust_missing_fields(workspace, stderr),
            "deterministic_rust_post_repair",
            "code_repair",
            2,
        ),
        (
            repair_rust_field_rename_suggestions(workspace, stderr),
            "deterministic_rust_post_repair",
            "code_repair",
            2,
        ),
        (
            repair_rust_lib_root_facade(workspace, errors),
            "deterministic_rust_lib_root_facade_repair",
            "export_resolution",
            3,
        ),
        (
            repair_rust_unresolved_pub_uses(workspace, errors),
            "deterministic_rust_unresolved_pub_use_repair",
            "export_resolution",
            3,
        ),
        (
            repair_rust_trait_imports(workspace, errors),
            "deterministic_rust_trait_import_repair",
            "export_resolution",
            3,
        ),
        (
            repair_rust_line_suggestions(workspace, errors),
            "deterministic_rust_line_suggestion_repair",
            "export_resolution",
            3,
        ),
    ):
        if records:
            batches.append((records, source_tool, phase, priority))
    return batches


def _annotate_rust_post_repair_records(
    records: list[dict[str, Any]],
    *,
    source_tool: str,
    phase: str,
    priority: int,
    errors_before: int,
    errors_after: int,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for record in records:
        payload = dict(record)
        payload.setdefault("source_tool", source_tool)
        payload["phase"] = phase
        payload["priority"] = priority
        payload["revalidation"] = {
            "command": ["cargo", "check", "--quiet"],
            "exit_code": 0 if errors_after == 0 else 1,
            "errors_before": errors_before,
            "errors_after": errors_after,
            "net_error_reduction": errors_before - errors_after,
        }
        annotated.append(payload)
    return annotated


def _count_rust_cargo_diagnostics(stderr: str) -> int:
    text = str(stderr or "")
    errors = len(re.findall(r"(?m)^error(?:\[[A-Z]\d+\])?:", text))
    warnings = len(re.findall(r"(?m)^warning:", text))
    return errors + warnings


def repair_rust_duplicate_module_files(workspace: Path) -> list[dict[str, Any]]:
    """Remove duplicate module files (E0761).

    When both ``src/engine.rs`` and ``src/engine/mod.rs`` exist, Rust rejects
    the ambiguity. Keep the ``mod.rs`` version (more common for multi-file
    modules) and remove the flat file.
    """
    repairs: list[dict[str, Any]] = []
    src = workspace / "src"
    if not src.is_dir():
        return []

    # Check every directory for a matching .rs file at the parent level
    for subdir in sorted(src.iterdir()):
        if not subdir.is_dir() or subdir.name in ("target", "build"):
            continue
        mod_rs = subdir / "mod.rs"
        flat_rs = src / f"{subdir.name}.rs"
        if mod_rs.is_file() and flat_rs.is_file():
            # Keep mod.rs (directory module), remove flat file
            flat_rs.unlink()
            repairs.append(
                {
                    "file": str(flat_rs.relative_to(workspace)),
                    "action": f"removed_duplicate_module_{subdir.name}",
                }
            )
    return repairs


def repair_rust_missing_derives(workspace: Path, stderr: str = "") -> list[dict[str, Any]]:
    """Add missing trait derives when cargo reports trait bound errors.

    When code requires ``Eq``, ``Hash``, ``PartialOrd`` etc. on a struct but
    the struct only derives ``Debug, Clone``, add the missing traits.
    """
    if not stderr:
        stderr = _run_cargo_check_stderr(workspace)
    if not stderr:
        return []

    # Pattern: the trait bound `Type: Trait` is not satisfied
    trait_bound_re = re.compile(r"the trait bound [`'](?:\w+::)*(\w+):\s*(\w+)[`'] is not satisfied")
    # Collect (struct_name, trait_name) pairs
    missing: set[tuple[str, str]] = set()
    for m in trait_bound_re.finditer(stderr):
        struct_name = m.group(1)
        trait_name = m.group(2)
        missing.add((struct_name, trait_name))

    if not missing:
        return []

    repairs: list[dict[str, Any]] = []
    derive_re = re.compile(r"#\[derive\(([^)]+)\)\]")

    for rs_file in sorted(workspace.rglob("*.rs")):
        if "target" in rs_file.relative_to(workspace).parts:
            continue
        try:
            content = rs_file.read_text(encoding="utf-8")
        except OSError:
            continue

        modified = False
        for struct_name, trait_name in missing:
            # Find the struct definition
            struct_re = re.compile(rf"(#\[derive\([^)]+\)\]\s*\n\s*(?:pub\s+)?struct\s+{re.escape(struct_name)}\b)")
            sm = struct_re.search(content)
            if not sm:
                continue
            # Check if the trait is already derived
            derive_match = derive_re.search(sm.group(0))
            if derive_match:
                existing = derive_match.group(1)
                if trait_name not in existing:
                    new_derive = f"{existing}, {trait_name}"
                    content = content.replace(
                        derive_match.group(0),
                        derive_match.group(0).replace(existing, new_derive),
                        1,
                    )
                    modified = True

        if modified:
            rs_file.write_text(content, encoding="utf-8")
            repairs.append(
                {
                    "file": str(rs_file.relative_to(workspace)),
                    "action": "added_missing_derives",
                }
            )

    return repairs


def repair_rust_missing_binary_entrypoint(workspace: Path) -> list[dict[str, Any]]:
    """Create stub main.rs when Cargo.toml declares a [[bin]] target but the file is missing."""
    repairs: list[dict[str, Any]] = []
    cargo_toml = workspace / "Cargo.toml"
    if not cargo_toml.is_file():
        return []
    try:
        content = cargo_toml.read_text(encoding="utf-8")
    except OSError:
        return []
    # Find [[bin]] sections with path declarations
    bin_path_re = re.compile(r'\[\[bin\]\].*?path\s*=\s*"([^"]+)"', re.DOTALL)
    for match in bin_path_re.finditer(content):
        bin_path = match.group(1)
        full_path = workspace / bin_path
        if not full_path.is_file():
            # Find the lib crate name to use in the stub
            lib_name_match = re.search(r'name\s*=\s*"([^"]+)"', content)
            lib_name = lib_name_match.group(1) if lib_name_match else "app"
            full_path.parent.mkdir(parents=True, exist_ok=True)
            stub = (
                f"// Auto-generated binary entry point for {lib_name}\n"
                f"fn main() {{\n"
                f'    println!("{lib_name} binary entry point");\n'
                f"}}\n"
            )
            full_path.write_text(stub, encoding="utf-8")
            repairs.append(
                {
                    "file": bin_path,
                    "action": f"created_missing_binary_{bin_path}",
                }
            )
    return repairs


def repair_rust_missing_module_files(workspace: Path) -> list[dict[str, Any]]:
    """Create missing module files when ``mod xxx;`` is declared but the file doesn't exist.

    Handles E0583: file not found for module. Also scans the workspace for
    ``use`` statements that import from the new module and creates minimal
    type stubs for those symbols (struct, enum, type alias, function).
    """
    repairs: list[dict[str, Any]] = []
    mod_decl_re = re.compile(r"^\s*(pub\s+)?mod\s+(\w+)\s*;", re.MULTILINE)

    # Collect all missing modules first (including stub-only files)
    missing_modules: dict[str, Path] = {}  # module_name → module_file_path
    for rs_file in sorted(workspace.rglob("*.rs")):
        if "target" in rs_file.relative_to(workspace).parts:
            continue
        try:
            content = rs_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Rust 2018+ module resolution: mod foo; in bar.rs resolves to
        # bar/foo.rs (sibling directory), NOT the parent of bar.rs.
        # Exception: mod.rs and main.rs/lib.rs use their own directory.
        if rs_file.name in ("mod.rs", "main.rs", "lib.rs"):
            resolve_dir = rs_file.parent
        else:
            resolve_dir = rs_file.parent / rs_file.stem
        for match in mod_decl_re.finditer(content):
            module_name = match.group(2)
            # Try flat file first: resolve_dir/module_name.rs
            module_file = resolve_dir / f"{module_name}.rs"
            # Also check sibling file: parent/module_name.rs (legacy/fallback)
            sibling_file = rs_file.parent / f"{module_name}.rs"
            module_dir = resolve_dir / module_name / "mod.rs"
            if module_dir.is_file():
                continue  # Directory module exists
            # Check both possible locations
            target_file = module_file
            if not module_file.is_file() and sibling_file.is_file():
                target_file = sibling_file
            elif module_file.is_file():
                target_file = module_file
            if target_file.is_file():
                # Check if it's just a stub (only comments, no real code)
                try:
                    existing = target_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                code_lines = [
                    ln
                    for ln in existing.split("\n")
                    if ln.strip() and not ln.strip().startswith("//") and not ln.strip().startswith("#")
                ]
                if code_lines:
                    continue  # File has real code, skip
                # Stub file — will update with type definitions
            missing_modules[module_name] = target_file

    if not missing_modules:
        return []

    # For each missing module, scan workspace for use statements that import from it
    use_grouped_re = re.compile(r"use\s+(?:crate|super)::(?:\w+::)*(\w+)::\{([^}]+)\}")
    use_single_re = re.compile(r"use\s+(?:crate|super)::(?:\w+::)*(\w+)::(\w+)")
    # Grouped import with sub-paths: use crate::models::{element::Element, flavor::Flavor}
    use_nested_grouped_re = re.compile(r"use\s+(?:crate|super)::(\w+)::\{([^}]+)\}")

    module_symbols: dict[str, set[str]] = {name: set() for name in missing_modules}

    for rs_file in sorted(workspace.rglob("*.rs")):
        if "target" in rs_file.relative_to(workspace).parts:
            continue
        try:
            content = rs_file.read_text(encoding="utf-8")
        except OSError:
            continue
        # Match flat grouped imports: use crate::models::element::{Element, ElementKind};
        for m in use_grouped_re.finditer(content):
            mod_name = m.group(1)
            if mod_name in module_symbols:
                imported_names = [s.strip() for s in m.group(2).split(",") if s.strip()]
                # Filter out sub-path items (module::Symbol) — those are handled below
                module_symbols[mod_name].update(s for s in imported_names if "::" not in s)
        # Match single imports: use crate::models::element::Element;
        for m in use_single_re.finditer(content):
            mod_name = m.group(1)
            if mod_name in module_symbols:
                module_symbols[mod_name].add(m.group(2))
        # Match nested grouped imports: use crate::models::{element::Element, flavor::Flavor}
        for m in use_nested_grouped_re.finditer(content):
            _parent_mod = m.group(1)
            body = m.group(2)
            for item in body.split(","):
                item = item.strip()
                if "::" not in item:
                    continue
                parts = item.split("::")
                sub_mod = parts[0].strip()
                sub_sym = parts[-1].strip()
                if sub_mod in module_symbols and sub_sym:
                    module_symbols[sub_mod].add(sub_sym)

    # Collect all symbols needed across all missing modules
    all_needed_symbols: set[str] = set()
    for syms in module_symbols.values():
        all_needed_symbols.update(syms)

    # Detect enum usage: scan workspace for Symbol::Variant patterns
    enum_variants: dict[str, set[str]] = {}
    enum_path_re = re.compile(r"\b([A-Z][A-Za-z0-9_]*)::([A-Z][A-Za-z0-9_]*)\b")
    # Tokens that are method calls, not enum variants
    _non_enum_rhs = {
        "new",
        "from",
        "default",
        "into",
        "try_from",
        "try_into",
        "as_ref",
        "as_mut",
        "clone",
        "copy",
        "len",
        "is_empty",
    }
    for rs_file in sorted(workspace.rglob("*.rs")):
        if "target" in rs_file.relative_to(workspace).parts:
            continue
        try:
            content = rs_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for em in enum_path_re.finditer(content):
            type_name = em.group(1)
            variant_name = em.group(2)
            if variant_name.lower() in _non_enum_rhs:
                continue
            # Only track symbols we need stubs for
            if type_name in all_needed_symbols:
                enum_variants.setdefault(type_name, set()).add(variant_name)

    # Create stub files with type definitions
    for module_name, module_file in missing_modules.items():
        module_file.parent.mkdir(parents=True, exist_ok=True)
        symbols = module_symbols.get(module_name, set())
        stub_lines: list[str] = [f"// Auto-generated stub module: {module_name}\n"]
        for sym in sorted(symbols):
            if sym[0].isupper():
                if enum_variants.get(sym):
                    # Generate enum with discovered variants
                    variants = ", ".join(sorted(enum_variants[sym]))
                    stub_lines.append("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]")
                    stub_lines.append(f"pub enum {sym} {{ {variants} }}\n")
                else:
                    # Likely a struct/enum/type — create a minimal struct
                    stub_lines.append("#[derive(Debug, Clone, PartialEq)]")
                    stub_lines.append(f"pub struct {sym} {{}}\n")
        if not symbols:
            stub_lines.append("// No symbols imported from this module yet.\n")
        new_content = "\n".join(stub_lines)
        try:
            existing = module_file.read_text(encoding="utf-8") if module_file.is_file() else ""
        except OSError:
            existing = ""
        if new_content == existing:
            continue
        module_file.write_text(new_content, encoding="utf-8")
        action = f"updated_stub_module_{module_name}" if existing else f"created_missing_module_{module_name}"
        repairs.append(
            {
                "file": str(module_file.relative_to(workspace)),
                "action": action,
            }
        )
    return repairs


def _replace_rust_crate_prefix(workspace: Path, missing_crate: str, canonical_crate: str) -> list[dict[str, Any]]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    extern_pattern = re.compile(rf"\bextern\s+crate\s+{re.escape(missing_crate)}\b")
    repairs: list[dict[str, Any]] = []
    for rust_file in sorted(workspace.rglob("*.rs")):
        if "target" in rust_file.relative_to(workspace).parts:
            continue
        try:
            original = rust_file.read_text(encoding="utf-8")
        except OSError:
            continue
        repaired, prefix_count = pattern.subn(canonical_crate, original)
        repaired, extern_count = extern_pattern.subn(f"extern crate {canonical_crate}", repaired)
        replacements = prefix_count + extern_count
        if repaired == original or replacements <= 0:
            continue
        rust_file.write_text(repaired, encoding="utf-8")
        repairs.append(
            {
                "file": str(rust_file.relative_to(workspace)),
                "before": missing_crate,
                "after": canonical_crate,
                "replacements": replacements,
            }
        )
    return repairs
