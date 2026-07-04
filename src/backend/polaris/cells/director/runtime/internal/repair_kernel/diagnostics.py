"""Diagnostic normalization for Director repair input."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import RepairDiagnostic

_TS_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<column>\d+)\):\s*error\s+(?P<code>TS\d+):\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_TS_ROOTDIR_FILE_ERROR_RE = re.compile(
    r"error\s+TS6059:\s*File\s+['\"](?P<path>[^'\"]+\.tsx?)['\"]\s+is\s+not\s+under\s+"
    r"['\"]rootDir['\"]\s+['\"](?P<root_dir>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_ESBUILD_CONFIG_SYNTAX_RE = re.compile(
    r"(?:✘\s*)?\[ERROR\]\s*Expected\s+[\"'`](?P<expected>[^\"'`]+)[\"'`]\s+"
    r"but\s+found\s+[\"'`](?P<found>[^\"'`]+)[\"'`].*?"
    r"(?P<path>[^\s:\n]+\.config\.tsx?):(?P<line>\d+):(?P<column>\d+):",
    re.IGNORECASE | re.DOTALL,
)
_TS_RETURN_OBJECT_SEMICOLON_RE = re.compile(
    r"TypeScript return object contains semicolon-terminated property in (?P<path>\S+)",
    re.IGNORECASE,
)
_RUST_ERROR_RE = re.compile(
    r"error\[(?P<code>E\d+)\]:\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_RUST_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE,
)
_GO_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.go):(?P<line>\d+):(?P<column>\d+):\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_CPP_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.(?:cc|cpp|cxx|hpp|hh|hxx|c|h)):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?:(?P<severity>fatal error|error|warning):\s*)?(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_JAVA_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.java):(?P<line>\d+):\s*error:\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_PYTHON_TRACEBACK_FILE_RE = re.compile(
    r'File\s+"(?P<path>[^"\n]+\.py)",\s+line\s+(?P<line>\d+)',
    re.IGNORECASE,
)
_PYTHON_EXCEPTION_RE = re.compile(
    r"(?P<exception>(?:ModuleNotFoundError|ImportError|SyntaxError|NameError|AttributeError|TypeError|"
    r"AssertionError|RuntimeError)):\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_JAVASCRIPT_MODULE_ERROR_RE = re.compile(
    r"(?P<message>The requested module\s+['\"]?[^'\"\s]+['\"]?\s+"
    r"does not provide an export named\s+(?:['\"][^'\"]+['\"]|[A-Za-z_$][\w$]*)|"
    r"Cannot find module ['\"][^'\"]+['\"]|"
    r"does not provide an export named (?:['\"][^'\"]+['\"]|[A-Za-z_$][\w$]*)|"
    r"require is not defined in ES module scope|exports is not defined in ES module scope|"
    r"Cannot require\(\) ES Module [^\n]+|ERR_REQUIRE_CYCLE_MODULE|"
    r"Cannot use import statement outside a module|"
    r"[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\s+is not a function)",
    re.IGNORECASE,
)
_JAVASCRIPT_DOM_GLOBAL_RUNTIME_RE = re.compile(
    r"(?P<file>(?:file://)?/[^\s:]+\.js):(?P<line>\d+).*?"
    r"ReferenceError:\s+(?P<global>document|window)\s+is not defined",
    re.IGNORECASE | re.DOTALL,
)
_TYPESCRIPT_COMMONJS_PACKAGE_TYPE_RUNTIME_RE = re.compile(
    r"exports is not defined in ES module scope.*?package\.json.*?contains\s+['\"]type['\"]:\s*['\"]module['\"]"
    r".*?CommonJS",
    re.IGNORECASE | re.DOTALL,
)
_HTML_CONTAINER_VALIDATION_RE = re.compile(
    r"htmlTag\s*=\s*true\s+canvas\s*=\s*true\s+container\s*=\s*false",
    re.IGNORECASE,
)
_DECLARED_TARGET_MISSING_RE = re.compile(
    r"declared target file(?:\s+missing)?\s+['\"]?(?P<path>[^'\"\n]+?)['\"]?(?:\s+is\s+missing)?(?:$|\s)",
    re.IGNORECASE,
)
_UNRESOLVED_RELATIVE_IMPORT_RE = re.compile(
    r"unresolved relative import ['\"](?P<specifier>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_WORKSPACE_VALIDATION_RE = re.compile(
    r"workspace validation command failed(?:\s+\((?P<command>[^)]+)\))?",
    re.IGNORECASE,
)
_PYTHON_RUNTIME_SMOKE_RE = re.compile(
    r"python runtime smoke (?:crashed|timed out|could not launch) for ['\"](?P<path>[^'\"]+)['\"]",
    re.IGNORECASE,
)


def normalize_artifact_quality_errors(errors: Sequence[Any]) -> tuple[RepairDiagnostic, ...]:
    """Convert raw or structured artifact-quality input into repair diagnostics."""

    diagnostics: list[RepairDiagnostic] = []
    for raw in errors or ():
        if isinstance(raw, RepairDiagnostic):
            diagnostics.append(raw)
            continue
        if isinstance(raw, Mapping):
            diagnostic = _normalize_structured_error(raw)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            continue
        text = str(raw or "").strip()
        if not text:
            continue
        expanded = _normalize_typescript_errors(text)
        if expanded:
            diagnostics.extend(expanded)
            continue
        diagnostics.append(_normalize_one_error(text))
    return tuple(diagnostics)


def _normalize_structured_error(raw: Mapping[str, Any]) -> RepairDiagnostic | None:
    metadata_raw = raw.get("metadata")
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    for key in (
        "raw",
        "line",
        "column",
        "span_start",
        "span_end",
        "symbol",
        "symbol_kind",
        "module",
        "specifier",
        "importer_path",
        "owner_path",
        "target_file",
        "manifest_path",
        "script_name",
        "script_issue",
        "actual",
        "suggestion",
        "issue_kind",
        "raw_path",
        "language",
        "confidence",
        "archetype",
        "diagnostic_archetype",
        "details",
        "path",
        "source",
        "severity",
        "diagnostic_code",
    ):
        if key in raw and key not in metadata:
            metadata[key] = raw[key]

    code = _structured_text(raw, metadata, "code", "diagnostic_code") or "artifact_quality_issue"
    message = _structured_text(raw, metadata, "message", "raw") or code
    if not message:
        return None
    return RepairDiagnostic(
        source=_structured_text(raw, metadata, "source") or "artifact_quality",
        code=code,
        message=message,
        severity=_structured_text(raw, metadata, "severity") or "error",
        path=_structured_path(raw, metadata),
        line=_to_int(raw.get("line") if "line" in raw else metadata.get("line")),
        column=_to_int(raw.get("column") if "column" in raw else metadata.get("column")),
        span_start=_to_int(raw.get("span_start") if "span_start" in raw else metadata.get("span_start")),
        span_end=_to_int(raw.get("span_end") if "span_end" in raw else metadata.get("span_end")),
        diagnostic_id=_structured_text(raw, metadata, "diagnostic_id", "id"),
        raw=_structured_text(raw, metadata, "raw") or message,
        metadata=metadata,
    )


def _structured_text(raw: Mapping[str, Any], metadata: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        for container in (raw, metadata):
            value = container.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return ""


def _structured_path(raw: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    path = _structured_text(raw, metadata, "path", "importer_path", "target_file", "file")
    return path.replace("\\", "/") if path else None


def _normalize_typescript_errors(text: str) -> list[RepairDiagnostic]:
    diagnostics: list[RepairDiagnostic] = []
    for match in _TS_ERROR_RE.finditer(text):
        code = str(match.group("code") or "typescript_error").lower()
        diagnostics.append(
            RepairDiagnostic(
                source="artifact_quality",
                code=f"typescript_{code}",
                message=str(match.group("message") or text).strip(),
                path=str(match.group("path") or "").strip(),
                line=_to_int(match.group("line")),
                column=_to_int(match.group("column")),
                raw=str(match.group(0) or text).strip(),
            )
        )
    for match in _TS_ROOTDIR_FILE_ERROR_RE.finditer(text):
        raw_path = str(match.group("path") or "").strip()
        root_dir = str(match.group("root_dir") or "").strip()
        rel_path = _typescript_rootdir_relative_path(raw_path)
        diagnostics.append(
            RepairDiagnostic(
                source="compiler",
                code="typescript_ts6059",
                message=f"TypeScript source file is outside rootDir: {rel_path or raw_path}",
                path=rel_path or None,
                raw=str(match.group(0) or text).strip(),
                metadata={"raw_path": raw_path, "root_dir": root_dir},
            )
        )
    for match in _ESBUILD_CONFIG_SYNTAX_RE.finditer(text):
        found = str(match.group("found") or "").strip()
        expected = str(match.group("expected") or "").strip()
        diagnostics.append(
            RepairDiagnostic(
                source="compiler",
                code="typescript_config_key_syntax",
                message=f"Expected {expected!r} but found {found!r} in TypeScript config.",
                path=str(match.group("path") or "").strip(),
                line=_to_int(match.group("line")),
                column=_to_int(match.group("column")),
                raw=str(match.group(0) or text).strip(),
                metadata={"language": "typescript", "expected": expected, "found": found},
            )
        )
    javascript_module_error = _JAVASCRIPT_MODULE_ERROR_RE.search(text)
    if javascript_module_error and _should_preserve_embedded_javascript_module_error(text, javascript_module_error):
        diagnostics.append(
            RepairDiagnostic(
                source="runtime_smoke",
                code="javascript_module_error",
                message=str(javascript_module_error.group("message") or text).strip(),
                raw=text,
                metadata={"embedded_in_compiler_output": bool(diagnostics)},
            )
        )
    return diagnostics


def _should_preserve_embedded_javascript_module_error(text: str, match: re.Match[str]) -> bool:
    del match
    lowered = str(text or "").lower()
    return "err_require_cycle_module" in lowered or "cannot require() es module" in lowered


def _looks_like_typescript_commonjs_package_type_runtime(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "exports is not defined in es module scope" in lowered
        and "package.json" in lowered
        and "type" in lowered
        and "module" in lowered
        and "commonjs" in lowered
    )


def _normalize_one_error(text: str) -> RepairDiagnostic:
    match = _TS_ERROR_RE.search(text)
    if match:
        code = str(match.group("code") or "typescript_error").lower()
        return RepairDiagnostic(
            source="artifact_quality",
            code=f"typescript_{code}",
            message=str(match.group("message") or text).strip(),
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            column=_to_int(match.group("column")),
            raw=text,
        )

    match = _TS_ROOTDIR_FILE_ERROR_RE.search(text)
    if match:
        raw_path = str(match.group("path") or "").strip()
        root_dir = str(match.group("root_dir") or "").strip()
        rel_path = _typescript_rootdir_relative_path(raw_path)
        return RepairDiagnostic(
            source="compiler",
            code="typescript_ts6059",
            message=f"TypeScript source file is outside rootDir: {rel_path or raw_path}",
            path=rel_path or None,
            raw=text,
            metadata={"raw_path": raw_path, "root_dir": root_dir},
        )

    match = _ESBUILD_CONFIG_SYNTAX_RE.search(text)
    if match:
        found = str(match.group("found") or "").strip()
        expected = str(match.group("expected") or "").strip()
        return RepairDiagnostic(
            source="compiler",
            code="typescript_config_key_syntax",
            message=f"Expected {expected!r} but found {found!r} in TypeScript config.",
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            column=_to_int(match.group("column")),
            raw=text,
            metadata={"language": "typescript", "expected": expected, "found": found},
        )

    match = _TS_RETURN_OBJECT_SEMICOLON_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="artifact_quality",
            code="typescript_return_object_property_semicolon",
            message="TypeScript return object contains semicolon-terminated property.",
            path=str(match.group("path") or "").strip(),
            raw=text,
        )

    match = _RUST_ERROR_RE.search(text)
    if match:
        location = _RUST_LOCATION_RE.search(text)
        code = str(match.group("code") or "rust_error").lower()
        return RepairDiagnostic(
            source="compiler",
            code=f"rust_{code}",
            message=str(match.group("message") or text).strip(),
            path=str(location.group("path") or "").strip() if location else None,
            line=_to_int(location.group("line")) if location else None,
            column=_to_int(location.group("column")) if location else None,
            raw=text,
        )

    match = _GO_ERROR_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="compiler",
            code="go_compile_error",
            message=str(match.group("message") or text).strip(),
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            column=_to_int(match.group("column")),
            raw=text,
        )

    match = _CPP_ERROR_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="compiler",
            code="cpp_compile_error",
            message=str(match.group("message") or text).strip(),
            severity=str(match.group("severity") or "error").strip().lower(),
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            column=_to_int(match.group("column")),
            raw=text,
        )

    match = _JAVA_ERROR_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="compiler",
            code="java_compile_error",
            message=str(match.group("message") or text).strip(),
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            raw=text,
        )

    match = _TYPESCRIPT_COMMONJS_PACKAGE_TYPE_RUNTIME_RE.search(text)
    if match or _looks_like_typescript_commonjs_package_type_runtime(text):
        return RepairDiagnostic(
            source="runtime_smoke",
            code="typescript_commonjs_package_type",
            message="TypeScript CommonJS module output requires package type commonjs, not module.",
            raw=text,
            metadata={"language": "typescript"},
        )

    match = _JAVASCRIPT_DOM_GLOBAL_RUNTIME_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="runtime_smoke",
            code="javascript_dom_global_in_node_runtime",
            message=f"Browser DOM global {str(match.group('global') or '').strip()} is not available in Node.",
            path=str(match.group("file") or "").removeprefix("file://"),
            line=_to_int(match.group("line")),
            raw=text,
            metadata={
                "language": "javascript",
                "runtime_global": str(match.group("global") or "").strip(),
            },
        )

    if _HTML_CONTAINER_VALIDATION_RE.search(text):
        return RepairDiagnostic(
            source="verifier",
            code="html_container_contract_failed",
            message="HTML verification found a canvas page but no recognized container id.",
            raw=text,
            metadata={"language": "html", "contract": "html_container"},
        )

    javascript_module_error = _JAVASCRIPT_MODULE_ERROR_RE.search(text)
    if javascript_module_error:
        return RepairDiagnostic(
            source="runtime_smoke",
            code="javascript_module_error",
            message=str(javascript_module_error.group("message") or text).strip(),
            raw=text,
        )

    python_exception = _PYTHON_EXCEPTION_RE.search(text)
    if python_exception:
        location = _PYTHON_TRACEBACK_FILE_RE.search(text)
        exception = str(python_exception.group("exception") or "python_exception").lower()
        return RepairDiagnostic(
            source="runtime_smoke",
            code=f"python_{exception}",
            message=str(python_exception.group("message") or text).strip(),
            path=str(location.group("path") or "").strip() if location else None,
            line=_to_int(location.group("line")) if location else None,
            raw=text,
        )

    match = _DECLARED_TARGET_MISSING_RE.search(text)
    if match:
        target_file = str(match.group("path") or "").strip()
        return RepairDiagnostic(
            source="artifact_quality",
            code="declared_target_missing",
            message="Declared target file is missing.",
            path=target_file,
            raw=text,
            metadata={"target_file": target_file},
        )

    match = _UNRESOLVED_RELATIVE_IMPORT_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="artifact_quality",
            code="unresolved_relative_import",
            message="Relative import target is unresolved.",
            path=str(match.group("path") or "").strip(),
            raw=text,
            metadata={"specifier": str(match.group("specifier") or "").strip()},
        )

    match = _UNRESOLVED_IMPORT_SYMBOL_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="artifact_quality",
            code="cross_artifact_unresolved_import_symbol",
            message="Cross-artifact import symbol is not exported by the resolved owner.",
            path=str(match.group("path") or "").strip(),
            raw=text,
            metadata={
                "symbol": str(match.group("symbol") or "").strip(),
                "module": str(match.group("module") or "").strip(),
                "contract_plane": "cross_artifact_interface",
            },
        )

    match = _PYTHON_RUNTIME_SMOKE_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="runtime_smoke",
            code="python_runtime_smoke_failed",
            message="Python runtime smoke failed.",
            path=str(match.group("path") or "").strip(),
            raw=text,
        )

    match = _WORKSPACE_VALIDATION_RE.search(text)
    if match:
        return RepairDiagnostic(
            source="verifier",
            code="workspace_validation_failed",
            message="Workspace validation command failed.",
            raw=text,
            metadata={"command": str(match.group("command") or "").strip()},
        )

    return RepairDiagnostic(
        source="artifact_quality",
        code="artifact_quality_error",
        message=text[:240],
        raw=text,
    )


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _typescript_rootdir_relative_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return ""
    for marker in ("/tests/", "/test/", "/src/"):
        index = normalized.find(marker)
        if index >= 0:
            return normalized[index + 1 :]
    if normalized.startswith(("tests/", "test/", "src/")):
        return normalized
    return ""
