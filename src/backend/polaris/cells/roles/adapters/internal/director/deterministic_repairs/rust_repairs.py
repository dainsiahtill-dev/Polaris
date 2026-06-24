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
