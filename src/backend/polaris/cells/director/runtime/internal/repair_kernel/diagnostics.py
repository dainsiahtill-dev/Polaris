"""Diagnostic normalization for Director repair input."""

from __future__ import annotations

import re

from .contracts import RepairDiagnostic

_TS_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<column>\d+)\):\s*error\s+(?P<code>TS\d+):\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
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
    r"(?P<message>Cannot find module ['\"][^'\"]+['\"]|does not provide an export named ['\"][^'\"]+['\"]|"
    r"require is not defined in ES module scope|Cannot use import statement outside a module)",
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


def normalize_artifact_quality_errors(errors: list[str]) -> tuple[RepairDiagnostic, ...]:
    """Convert raw artifact-quality strings into typed repair diagnostics."""

    diagnostics: list[RepairDiagnostic] = []
    for raw in errors:
        text = str(raw or "").strip()
        if not text:
            continue
        diagnostics.append(_normalize_one_error(text))
    return tuple(diagnostics)


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
        return RepairDiagnostic(
            source="artifact_quality",
            code="declared_target_missing",
            message="Declared target file is missing.",
            path=str(match.group("path") or "").strip(),
            raw=text,
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
