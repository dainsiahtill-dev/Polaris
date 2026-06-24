"""Deterministic Rust repairs used by Director materialization quality gates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tomllib

_RUST_UNRESOLVED_CRATE_RE = re.compile(
    r"cannot find (?:module or )?crate [`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
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
    del task_id
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    repairs = repair_rust_dependencies(workspace, artifact_quality_errors)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_rust_dependency_repair",
                "file": "Cargo.toml",
                "packages": record["packages"],
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
    for missing_crate in missing_crates:
        if missing_crate == canonical_crate:
            continue
        if missing_crate in declared_dependencies:
            continue
        if not _rust_crate_names_look_related(missing_crate, canonical_crate):
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
    repaired = re.sub(
        rf"(?m)^\s*pub\s+use\s+[A-Za-z_][A-Za-z0-9_:]*::{re.escape(symbol)}\s*;\s*\n?",
        "",
        text,
    )
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

    return group_pattern.sub(replace_group, repaired)


def _rust_crate_names_look_related(missing: str, canonical: str) -> bool:
    missing_tokens = _crate_name_tokens(missing)
    canonical_tokens = _crate_name_tokens(canonical)
    if len(missing_tokens) < 2 or not canonical_tokens:
        return False
    return missing_tokens.issubset(canonical_tokens)


def _crate_name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[_\\W]+", name.lower()) if token}


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
