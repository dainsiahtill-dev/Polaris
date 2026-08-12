"""_shared domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic
from .constants import (
    _JS_IDENTIFIER_RE,
    _JS_RUNTIME_FILE_RE,
    _MISSING_NAMED_EXPORT_RE,
    _MISSING_NPM_SCRIPT_ENTRYPOINT_GATE_RE,
    _MISSING_NPM_SCRIPT_ENTRYPOINT_RE,
    _NODE_CANNOT_FIND_MODULE_DIST_RE,
    _UNRESOLVED_IMPORT_SYMBOL_RE,
)


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        normalized: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized := _normalize_repair_path(str(path or "")))
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _parse_package_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _diagnostic_script_name(diagnostic: RepairDiagnostic) -> str:
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    return str(metadata.get("script_name") or metadata.get("script") or "").strip()


def _missing_entrypoints_from_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> dict[str, str]:
    entrypoints: dict[str, str] = {}
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_issue = str(metadata.get("script_issue") or "").strip()
        script_name = _diagnostic_script_name(diagnostic)
        entrypoint = str(metadata.get("entrypoint") or "").strip().replace("\\", "/")
        if script_issue == "missing_local_entrypoint" and script_name and entrypoint:
            entrypoints[script_name] = entrypoint
    return entrypoints


def _has_typescript_context(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> bool:
    if "tsconfig.json" in base_files:
        return True
    for dependency_key in ("dependencies", "devDependencies"):
        dependencies = package_payload.get(dependency_key)
        if isinstance(dependencies, dict) and "typescript" in dependencies:
            return True
    return any(path.endswith(".ts") or path.endswith(".tsx") for path in base_files)


def _missing_entrypoint(errors: Sequence[str], *, script_name: str) -> str:
    for script, entrypoint in _missing_entrypoints(errors).items():
        if script == script_name:
            return entrypoint
    return ""


def _missing_entrypoints(errors: Sequence[str]) -> dict[str, str]:
    entrypoints: dict[str, str] = {}
    for error in errors:
        raw_error = str(error or "")
        match = _MISSING_NPM_SCRIPT_ENTRYPOINT_RE.search(raw_error)
        if not match:
            match = _MISSING_NPM_SCRIPT_ENTRYPOINT_GATE_RE.search(raw_error)
        if not match:
            continue
        script_name = str(match.group(1) or "").strip()
        entrypoint = str(match.group(2) or "").strip()
        if script_name and entrypoint:
            entrypoints[script_name] = entrypoint
    return entrypoints


def _missing_node_dist_entrypoints(errors: Sequence[str]) -> tuple[str, ...]:
    entrypoints: list[str] = []
    for error in errors:
        for match in _NODE_CANNOT_FIND_MODULE_DIST_RE.finditer(str(error or "")):
            raw_path = str(match.group("path") or "").replace("\\", "/")
            dist_index = raw_path.rfind("/dist/")
            if dist_index >= 0:
                entrypoints.append(raw_path[dist_index + 1 :])
            elif raw_path.startswith("dist/"):
                entrypoints.append(raw_path)
    return tuple(dict.fromkeys(entrypoints))


def _missing_node_dist_entrypoints_from_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    entrypoints: list[str] = []
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_issue = str(metadata.get("script_issue") or "").strip()
        entrypoint = str(metadata.get("entrypoint") or "").strip().replace("\\", "/")
        if script_issue == "missing_compiled_entrypoint" and entrypoint:
            entrypoints.append(entrypoint)
    return tuple(dict.fromkeys(entrypoints))


def _compiled_typescript_entrypoint(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> str:
    entrypoint = str(package_payload.get("main") or "").strip().replace("\\", "/")
    if entrypoint.startswith("src/") and entrypoint.endswith(".ts"):
        return _compiled_typescript_output_path(base_files, entrypoint)
    if entrypoint.startswith("dist/") and entrypoint.endswith((".js", ".mjs", ".cjs")):
        return entrypoint
    for source_entry in ("src/main.ts", "src/index.ts", "src/verify.ts"):
        if source_entry in base_files:
            return _compiled_typescript_output_path(base_files, source_entry)
    return "dist/index.js"


def _compiled_typescript_entrypoint_for_missing(
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    missing_entrypoint: str,
) -> str:
    missing_path = PurePosixPath(str(missing_entrypoint or "").replace("\\", "/"))
    stem = missing_path.stem
    candidates = [
        f"src/{stem}.ts",
        f"src/{stem}.tsx",
        f"{stem}.ts",
        f"{stem}.tsx",
    ]
    for candidate in candidates:
        if candidate in base_files:
            return _compiled_typescript_output_path(base_files, candidate)
    return _compiled_typescript_entrypoint(base_files, package_payload)


def _compiled_typescript_output_path(base_files: Mapping[str, str], source_entry: str) -> str:
    source_path = _normalize_repair_path(source_entry)
    out_dir = _typescript_compiler_option(base_files, "outDir") or "dist"
    root_dir = _typescript_compiler_option(base_files, "rootDir")
    normalized_out = _normalize_repair_path(out_dir) or "dist"
    normalized_root = _normalize_repair_path(root_dir or "")
    relative_source = source_path
    if normalized_root and normalized_root not in {".", "./"}:
        prefix = f"{normalized_root.rstrip('/')}/"
        if source_path.startswith(prefix):
            relative_source = source_path.removeprefix(prefix)
    elif not normalized_root and source_path.startswith("src/"):
        relative_source = source_path.removeprefix("src/")
    return f"{normalized_out.rstrip('/')}/{PurePosixPath(relative_source).with_suffix('.js').as_posix()}"


def _typescript_compiler_option(base_files: Mapping[str, str], key: str) -> str:
    tsconfig = _parse_package_json(str(base_files.get("tsconfig.json") or ""))
    if tsconfig is None:
        return ""
    compiler_options = tsconfig.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return ""
    return str(compiler_options.get(key) or "").strip().replace("\\", "/")


def _fallback_script_for_missing_entrypoint(script_name: str) -> str:
    if script_name in {"lint", "typecheck", "check"}:
        return "tsc --noEmit"
    if script_name in {"serve", "dev", "preview"}:
        return "npm run build"
    return "npm run build"


def _is_javascript_test_target_path(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    suffix = PurePosixPath(normalized).suffix
    name = PurePosixPath(normalized).name
    return suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"} and (
        normalized.startswith("tests/") or ".test." in name or ".spec." in name
    )


def _missing_export_targets(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    targets: list[dict[str, str]] = []
    match = _UNRESOLVED_IMPORT_SYMBOL_RE.search(raw)
    if match:
        symbol = str(match.group("symbol") or "").strip()
        module_ref = str(match.group("module") or "").strip()
        importer = _normalize_repair_path(str(match.group("path") or ""))
        exporter = _resolve_js_module(base_files, importer, module_ref)
        if _JS_IDENTIFIER_RE.match(symbol) and exporter:
            targets.append({"exporter": exporter, "symbol": symbol, "importer": importer})
    named_export = _MISSING_NAMED_EXPORT_RE.search(raw)
    if named_export:
        symbol = str(named_export.group("symbol") or "").strip()
        module_ref = str(named_export.group("module") or "").strip()
        importer = _first_runtime_file(raw, base_files)
        if not importer:
            importer = _infer_importer_for_js_module_ref(base_files, module_ref)
        exporter = _resolve_js_module(base_files, importer, module_ref)
        if _JS_IDENTIFIER_RE.match(symbol) and exporter:
            targets.append({"exporter": exporter, "symbol": symbol, "importer": importer})
    return tuple(targets)


def _resolve_js_module(base_files: Mapping[str, str], importer: str, module_ref: str) -> str:
    importer = _normalize_repair_path(importer)
    if not importer or not module_ref.startswith("."):
        return ""
    base_dir = PurePosixPath(importer).parent
    raw = posixpath.normpath((base_dir / module_ref).as_posix())
    candidates = [raw]
    if PurePosixPath(raw).suffix:
        candidates.append(raw)
    else:
        candidates.extend(f"{raw}{suffix}" for suffix in (".js", ".mjs", ".cjs"))
        candidates.extend(f"{raw}/index{suffix}" for suffix in (".js", ".mjs", ".cjs"))
    for candidate in candidates:
        normalized = _normalize_repair_path(candidate)
        if normalized in base_files:
            return normalized
    return ""


def _infer_importer_for_js_module_ref(base_files: Mapping[str, str], module_ref: str) -> str:
    normalized_ref = str(module_ref or "").strip()
    if not normalized_ref.startswith("."):
        return ""
    quoted_ref = re.escape(normalized_ref)
    import_pattern = re.compile(
        rf"(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*)['\"]{quoted_ref}['\"]",
    )
    candidates = [
        path
        for path, text in base_files.items()
        if path.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")) and import_pattern.search(str(text or ""))
    ]
    return candidates[0] if len(candidates) == 1 else ""


def _javascript_module_exports_symbol(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    patterns = (
        rf"(?m)^\s*export\s+(?:async\s+)?(?:class|function)\s+{escaped}\b",
        rf"(?m)^\s*export\s+(?:const|let|var)\s+{escaped}\b",
        rf"(?m)^\s*export\s*\{{[^}}]*\b{escaped}\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _first_runtime_file(raw: str, base_files: Mapping[str, str]) -> str:
    for match in _JS_RUNTIME_FILE_RE.finditer(raw):
        resolved = _base_file_from_runtime_path(str(match.group("path") or ""), base_files)
        if resolved:
            return resolved
    for path in base_files:
        if path.endswith(".js") and path in raw:
            return path
    return ""


def _base_file_from_runtime_path(raw_path: str, base_files: Mapping[str, str]) -> str:
    raw_normalized = str(raw_path or "").removeprefix("file://").replace("\\", "/")
    normalized = _normalize_repair_path(raw_normalized)
    if normalized and normalized in base_files:
        return normalized
    for path in sorted(base_files, key=len, reverse=True):
        if raw_normalized.endswith("/" + path) or raw_normalized == path:
            return path
    return ""


def _dedupe_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> tuple[RepairDiagnostic, ...]:
    deduped: list[RepairDiagnostic] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        key = diagnostic.diagnostic_id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(diagnostic)
    return tuple(deduped)


def _find_matching_brace(text: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None
