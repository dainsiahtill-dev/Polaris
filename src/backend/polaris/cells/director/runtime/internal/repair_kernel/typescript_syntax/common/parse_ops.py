from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ...javascript_syntax import repair_javascript_export_contract_placeholders
from ...path_files import normalize_base_files_strict, normalize_repair_path_strict
from ..constants import *  # noqa: F403
from .path_ops import *  # noqa: F403
from .plan_ops import *  # noqa: F403

"""Shared TypeScript repair helpers: parse_ops."""

def _typescript_syntax_error_paths(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if diagnostic.code.lower() not in {"typescript_ts1003", "typescript_ts1005"}:
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if path and path.endswith((".ts", ".tsx")) and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)

def _diagnostic_text(diagnostics: Sequence[RepairDiagnostic]) -> str:
    return "\n".join(f"{diagnostic.message}\n{diagnostic.raw}" for diagnostic in diagnostics)

def _typescript_glob_points_outside_root(entry: str, *, root_dir: str) -> bool:
    normalized_entry = str(entry or "").strip().replace("\\", "/")
    normalized_root = _normalize_repair_path(root_dir).rstrip("/")
    if not normalized_entry or not normalized_root:
        return False
    if normalized_entry.startswith(f"{normalized_root}/") or normalized_entry == normalized_root:
        return False
    return normalized_entry.startswith(("tests/", "test/", "*."))

def _parse_html_truncated_entrypoint_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    """Return unique HTML paths with truncated/incomplete HTML diagnostics."""

    paths: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        text = f"{diagnostic.path or ''}\n{diagnostic.message or ''}\n{diagnostic.raw or ''}"
        candidates: list[str] = []
        for match in _HTML_TRUNCATED_ERROR_RE.finditer(text):
            candidates.append(str(match.group("path") or "").strip())
        lowered = text.lower()
        if "truncated/incomplete html" in lowered:
            path_hint = _normalize_repair_path(str(diagnostic.path or ""))
            if path_hint.endswith((".html", ".htm")):
                candidates.append(path_hint)
        for candidate in candidates:
            normalized = _normalize_repair_path(candidate)
            if not normalized or normalized in seen:
                continue
            if not normalized.endswith((".html", ".htm")):
                continue
            seen.add(normalized)
            paths.append(normalized)
    return paths

def _parse_undeclared_runtime_import_paths(
    diagnostics: Sequence[RepairDiagnostic],
    *,
    package_name: str,
) -> list[str]:
    paths: list[str] = []
    expected = str(package_name or "").split("/", 1)[0].lower()
    for diagnostic in diagnostics:
        for match in _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            package = str(match.group("package") or "").split("/", 1)[0].lower()
            path = _normalize_repair_path(str(match.group("path") or ""))
            if package == expected and path:
                paths.append(path)
    return _dedupe_preserve_order(paths)

def _parse_typescript_cannot_find_name_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_NAME_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "col": str(match.group("col") or ""),
                    "symbol": str(match.group("symbol") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["symbol"]]

def _typescript_errors_require_dom_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    dom_global_names = ("console", "window", "document", "navigator", "location")
    dom_type_names = ("htmlelement", "htmlelementtagnamemap")
    return ("include 'dom'" in text and any(f"cannot find name '{name}'" in text for name in dom_global_names)) or any(
        f"cannot find name '{name}'" in text for name in dom_type_names
    )

def _typescript_errors_require_import_meta_module(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "ts1343" in text and "import.meta" in text and "module" in text

def _typescript_errors_require_es2021_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return (
        ("ts2550" in text or "property 'replaceall' does not exist" in text)
        and "replaceall" in text
        and ("es2021" in text or "target library" in text or "lib" in text)
    )

def _parse_typescript_missing_test_global_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_TEST_GLOBAL_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            symbol = str(match.group("symbol") or "")
            if path.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")) and symbol in _TS_TEST_GLOBAL_NAMES:
                parsed.append({"file": path, "symbol": symbol})
    return parsed

def _is_typescript_config_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return basename.endswith((".config.ts", ".config.tsx"))

def _is_typescript_test_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return "/__tests__/" in f"/{normalized}" or basename.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))

def _is_typescript_comma_expected_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "typescript_return_object_property_semicolon":
        return True
    if diagnostic.code.lower() != "typescript_ts1005":
        return False
    message = diagnostic.message.lower()
    raw = diagnostic.raw.lower()
    return "expected" in message and "," in (message + raw)

def _parse_typescript_argument_missing_props_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str, str, list[str]] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    match = _TS2345_ARG_MISSING_PROPS_RE.search(raw)
    if match:
        path = path or _normalize_repair_path(str(match.group("file") or ""))
        line = line or int(match.group("line") or 0)
        col = col or int(match.group("col") or 0)
        source_type = str(match.group("source") or "").strip()
        target_shape = str(match.group("target") or "").strip()
        props_raw = str(match.group("props") or "")
    else:
        code = str(diagnostic.code or "").lower()
        if code not in {"typescript_ts2345", "ts2345"} and "argument of type" not in raw.lower():
            return None
        loose = _TS2345_ARG_MISSING_PROPS_LOOSE_RE.search(raw)
        if loose is None or not path or line <= 0:
            return None
        source_type = str(loose.group("source") or "").strip()
        target_shape = str(loose.group("target") or "").strip()
        props_raw = ""
        clause = _TS2345_MISSING_PROPS_CLAUSE_RE.search(raw)
        if clause:
            props_raw = str(clause.group("props") or "")
    if not props_raw:
        # Infer property names from anonymous target shape.
        props_raw = ", ".join(m.group("name") for m in _TS_ANON_OBJECT_PROP_RE.finditer(target_shape))
    props = [token.strip() for token in props_raw.split(",") if _TS_IDENTIFIER_RE.fullmatch(token.strip() or "")]
    if not path or line <= 0 or not props or not target_shape.startswith("{"):
        return None
    return path, line, col, source_type, target_shape, props

def _parse_typescript_missing_props_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str, list[str]] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    code = str(diagnostic.code or "").lower()
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    primary = _TS_MISSING_PROPS_PRIMARY_RE.search(raw)
    if primary:
        path = path or _normalize_repair_path(str(primary.group("file") or ""))
        line = line or int(primary.group("line") or 0)
        col = col or int(primary.group("col") or 0)
    props_match = _TS_MISSING_PROPS_FROM_TYPE_RE.search(raw)
    if props_match is None and code not in {
        "typescript_ts2345",
        "typescript_ts2739",
        "typescript_ts2740",
        "ts2740",
    }:
        return None
    if props_match is None:
        return None
    type_name = str(props_match.group("type") or "")
    props = [
        token.strip()
        for token in str(props_match.group("props") or "").split(",")
        if _TS_IDENTIFIER_RE.fullmatch(token.strip() or "")
    ]
    if not path or line <= 0 or not props:
        return None
    return path, line, col, type_name, props


__all__ = (
    "_typescript_syntax_error_paths",
    "_diagnostic_text",
    "_typescript_glob_points_outside_root",
    "_parse_html_truncated_entrypoint_paths",
    "_parse_undeclared_runtime_import_paths",
    "_parse_typescript_cannot_find_name_errors",
    "_typescript_errors_require_dom_lib",
    "_typescript_errors_require_import_meta_module",
    "_typescript_errors_require_es2021_lib",
    "_parse_typescript_missing_test_global_errors",
    "_is_typescript_config_file",
    "_is_typescript_test_file",
    "_is_typescript_comma_expected_diagnostic",
    "_parse_typescript_argument_missing_props_diagnostic",
    "_parse_typescript_missing_props_diagnostic",
)
