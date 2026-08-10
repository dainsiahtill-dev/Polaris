"""Issue construction, coding, metadata, and public issue helpers."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_COMPILER_PATH_RE,
    _ARTIFACT_QUALITY_ERROR_PREFIX,
    _ARTIFACT_QUALITY_GO_UNDEFINED_RE,
    _ARTIFACT_QUALITY_IN_PATH_RE,
    _ARTIFACT_QUALITY_JAVASCRIPT_MODULE_ERROR_RE,
    _ARTIFACT_QUALITY_NODE_CANNOT_FIND_MODULE_RE,
    _ARTIFACT_QUALITY_NPM_MISSING_ENTRYPOINT_RE,
    _ARTIFACT_QUALITY_NPM_PYTHON_COMMAND_RE,
    _ARTIFACT_QUALITY_NPM_SCRIPT_RE,
    _ARTIFACT_QUALITY_PATH_EXTENSIONS,
    _ARTIFACT_QUALITY_QUOTED_PATH_RE,
    _ARTIFACT_QUALITY_RUST_ERROR_RE,
    _ARTIFACT_QUALITY_RUST_LOCATION_RE,
    _ARTIFACT_QUALITY_RUST_MISSING_BIN_RE,
    _ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE,
    _ARTIFACT_QUALITY_UNDECLARED_RUNTIME_IMPORT_RE,
    _ARTIFACT_QUALITY_UNRESOLVED_IMPORT_SYMBOL_RE,
    _ARTIFACT_QUALITY_UNRESOLVED_RELATIVE_IMPORT_RE,
    _CROSS_ARTIFACT_CONSISTENCY_DIAGNOSTIC_KINDS,
    _DIAGNOSTIC_KIND_SOURCE_RULES,
    _FILE_ARTIFACT_SCANNER_DIAGNOSTIC_KINDS,
    _LegacyIssueCodeClassifier,
)
from polaris.kernelone.quality.artifact_quality._helpers import (
    _package_root_name,
)
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityEvidence,
    ArtifactQualityIssue,
)
from polaris.kernelone.quality.cross_artifact_interfaces import (
    ContractAmendmentRequest,
    CrossArtifactConsistencyIssue,
    CrossArtifactRepairPlan,
)


def _artifact_quality_scan_failure_issue(
    message: str,
    *,
    exc: BaseException | None = None,
) -> ArtifactQualityIssue:
    """Return typed evidence for scanner infrastructure failures."""

    metadata: dict[str, Any] = {
        "raw": message,
        "diagnostic_kind": "artifact_quality_scan_failed",
    }
    if exc is not None:
        metadata["exception_type"] = type(exc).__name__
    return ArtifactQualityIssue(
        code="artifact_quality_scan_failed",
        message=message,
        source="artifact_quality_scanner",
        metadata=metadata,
    )


def _legacy_artifact_quality_issue_code_from_message(message: str) -> str:
    """Classify legacy display-string artifact quality diagnostics."""

    normalized = message.lower()
    for classifier in _LEGACY_ARTIFACT_QUALITY_ISSUE_CODE_CLASSIFIERS:
        issue_code = classifier(message, normalized)
        if issue_code:
            return issue_code
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug[:80] or "artifact_quality_error"


def _artifact_quality_issue_code_from_typed_metadata(
    metadata: Mapping[str, Any],
    *,
    source: str,
) -> str:
    """Classify structured issue metadata before legacy message parsing.

    Only stable scanner-owned metadata fields are mapped here. Display strings
    remain a compatibility fallback rather than the primary source of issue
    identity.
    """

    source_token = str(source or "").strip()
    script_issue = str(metadata.get("script_issue") or "").strip()
    script_issue_source = str(metadata.get("script_issue_source") or "").strip()
    package_script_issue_code = str(metadata.get("package_script_issue_code") or "").strip()
    if package_script_issue_code or (
        script_issue
        and source_token in {"package_manifest_scanner", "package_scripts"}
        and script_issue_source in {"", "package_manifest_scanner", "package_scripts"}
    ):
        return "npm_manifest_invalid"
    if (
        script_issue in {"missing_compiled_entrypoint", "typescript_source_loader_require_cycle"}
        and source_token == "runtime_smoke"
    ):
        return "javascript_module_error"
    if (
        script_issue == "missing_local_config"
        and source_token == "npm_script_config_scanner"
        and script_issue_source == "npm_script_config_scanner"
    ):
        return "npm_script_missing_local_config"
    if (
        script_issue == "missing_local_entrypoint"
        and source_token == "npm_script_entrypoint_scanner"
        and script_issue_source == "npm_script_entrypoint_scanner"
    ):
        return "npm_script_missing_local_entrypoint"

    diagnostic_kind = str(metadata.get("diagnostic_kind") or "").strip()
    language = str(metadata.get("language") or "").strip().lower()
    allowed_sources = _DIAGNOSTIC_KIND_SOURCE_RULES.get(diagnostic_kind)
    if allowed_sources is not None and source_token in allowed_sources:
        return diagnostic_kind
    if diagnostic_kind == "undefined_identifier" and language == "go":
        return "go_compile_error"
    if source_token == "file_artifact_scanner" and diagnostic_kind in _FILE_ARTIFACT_SCANNER_DIAGNOSTIC_KINDS:
        return diagnostic_kind
    if source_token == "cross_artifact_consistency" and diagnostic_kind in _CROSS_ARTIFACT_CONSISTENCY_DIAGNOSTIC_KINDS:
        return diagnostic_kind
    return ""


def _legacy_target_or_import_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy target-contract and import-topology diagnostics."""

    if "declared target file" in normalized_message and "missing" in normalized_message:
        return "declared_target_missing"
    if "unresolved import symbol" in normalized_message:
        return "unresolved_import_symbol"
    if "unresolved relative import" in normalized_message:
        return "unresolved_relative_import"
    if "undeclared runtime import" in normalized_message:
        return "undeclared_runtime_import"
    return ""


def _legacy_npm_manifest_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy npm manifest display diagnostics."""

    if (
        "npm default failing test script" in normalized_message
        or "npm placeholder test script" in normalized_message
        or "npm manifest-only test script" in normalized_message
    ):
        return "npm_manifest_invalid"
    runtime_script_invoked = (
        "npm run start" in normalized_message
        or "npm start" in normalized_message
        or "npm run serve" in normalized_message
        or "npm run dev" in normalized_message
        or "npm run preview" in normalized_message
    )
    runtime_port_conflict = "eaddrinuse" in normalized_message or "address already in use" in normalized_message
    if runtime_script_invoked and runtime_port_conflict:
        return "npm_manifest_invalid"
    if "test script must use node --test" in normalized_message:
        return "npm_manifest_invalid"
    if "npm package manifest" in normalized_message:
        return "npm_manifest_invalid"
    return ""


def _legacy_language_or_syntax_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy broad language and syntax diagnostics."""

    if "typescript project typecheck failed" in normalized_message:
        return "typescript_project_typecheck_failed"
    if "syntax error" in normalized_message or "invalid json" in normalized_message:
        return "syntax_error"
    return ""


def _legacy_hygiene_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy hygiene and contamination diagnostics."""

    if "patch residue" in normalized_message:
        return "patch_residue"
    if "tool execution receipt contamination" in normalized_message:
        return "tool_receipt_contamination"
    if "source narration contamination" in normalized_message:
        return "source_narration_contamination"
    return ""


def _legacy_compiler_issue_code_from_explicit_code(message: str, _normalized_message: str) -> str:
    """Classify legacy compiler diagnostics with explicit TS/Rust error codes."""

    typescript_match = _ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE.search(message)
    if typescript_match:
        return f"typescript_{str(typescript_match.group('code') or '').lower()}"
    rust_match = _ARTIFACT_QUALITY_RUST_ERROR_RE.search(message)
    if rust_match:
        return f"rust_{str(rust_match.group('code') or '').lower()}"
    return ""


def _legacy_rust_missing_binary_issue_code(message: str, _normalized_message: str) -> str:
    """Classify cargo-shaped missing binary entrypoint display diagnostics."""

    if _ARTIFACT_QUALITY_RUST_MISSING_BIN_RE.search(message):
        return "rust_missing_binary_entrypoint"
    return ""


def _relative_rust_bin_path_from_cargo_message(path: str) -> str:
    """Project absolute or relative cargo bin paths to workspace-relative form."""

    normalized = str(path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    while normalized.startswith("./"):
        normalized = normalized[2:]
    is_absolute = normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:/", normalized))
    if is_absolute:
        return "src/main.rs" if normalized.lower().endswith("/src/main.rs") else ""
    if normalized.startswith("src/"):
        return normalized
    if any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized


def _legacy_compiler_issue_code_from_path(message: str, normalized_message: str) -> str:
    """Classify legacy compiler diagnostics that only expose a source path."""

    compiler_path = _artifact_quality_issue_path(message)
    if not compiler_path:
        return ""
    compiler_suffix = Path(compiler_path).suffix.lower()
    if compiler_suffix == ".go":
        return "go_compile_error"
    if compiler_suffix == ".java" and "error:" in normalized_message:
        return "java_compile_error"
    if compiler_suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
        return "cpp_compile_error"
    return ""


_LEGACY_ARTIFACT_QUALITY_ISSUE_CODE_CLASSIFIERS: tuple[_LegacyIssueCodeClassifier, ...] = (
    _legacy_target_or_import_issue_code,
    _legacy_rust_missing_binary_issue_code,
    _legacy_compiler_issue_code_from_explicit_code,
    _legacy_compiler_issue_code_from_path,
    _legacy_language_or_syntax_issue_code,
    _legacy_npm_manifest_issue_code,
    _legacy_hygiene_issue_code,
)


def _artifact_quality_issue_path(message: str) -> str | None:
    rust_missing_bin = _ARTIFACT_QUALITY_RUST_MISSING_BIN_RE.search(message)
    if rust_missing_bin:
        relative = _relative_rust_bin_path_from_cargo_message(str(rust_missing_bin.group("path") or ""))
        if relative:
            return relative
    rust_location = _ARTIFACT_QUALITY_RUST_LOCATION_RE.search(message)
    if rust_location:
        return str(rust_location.group("path") or "").strip().replace("\\", "/")
    for regex in (
        _ARTIFACT_QUALITY_COMPILER_PATH_RE,
        _ARTIFACT_QUALITY_QUOTED_PATH_RE,
        _ARTIFACT_QUALITY_IN_PATH_RE,
    ):
        match = regex.search(message)
        if not match:
            continue
        path = str(match.group("path") or "").strip().replace("\\", "/")
        if path.endswith(_ARTIFACT_QUALITY_PATH_EXTENSIONS):
            return path
    return None


def _artifact_quality_optional_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _artifact_quality_issue_location(message: str) -> tuple[int | None, int | None]:
    rust_location = _ARTIFACT_QUALITY_RUST_LOCATION_RE.search(message)
    if rust_location:
        return (
            _artifact_quality_optional_int(rust_location.group("line")),
            _artifact_quality_optional_int(rust_location.group("column")),
        )
    match = _ARTIFACT_QUALITY_COMPILER_PATH_RE.search(message)
    if not match:
        return None, None
    raw_line = match.group("line_paren") or match.group("line_colon")
    raw_column = match.group("column_paren") or match.group("column_colon")
    try:
        line = int(raw_line) if raw_line else None
    except ValueError:
        line = None
    try:
        column = int(raw_column) if raw_column else None
    except ValueError:
        column = None
    return line, column


def _artifact_quality_issue_metadata(text: str, message: str, code: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw": text}
    if code == "declared_target_missing":
        metadata.update(_legacy_declared_target_missing_metadata(message))
    elif code == "rust_missing_binary_entrypoint":
        match = _ARTIFACT_QUALITY_RUST_MISSING_BIN_RE.search(message)
        bin_name = str(match.group("bin") if match else "").strip()
        bin_path = _relative_rust_bin_path_from_cargo_message(
            str(match.group("path") if match else "") or "src/main.rs"
        )
        metadata["diagnostic_kind"] = "rust_missing_binary_entrypoint"
        if bin_name:
            metadata["bin_name"] = bin_name
        if bin_path:
            metadata["bin_path"] = bin_path
            metadata["path"] = bin_path
        metadata.setdefault("missing_bin_reason", "legacy_display_rehydration")
        metadata.setdefault("manifest_path", "Cargo.toml")
    elif code == "npm_manifest_invalid":
        metadata["manifest_path"] = "package.json"
        metadata.update(_legacy_npm_manifest_issue_metadata(message))
    elif code == "unresolved_import_symbol":
        metadata.update(_legacy_unresolved_import_symbol_metadata(message))
    elif code == "unresolved_relative_import":
        metadata.update(_legacy_unresolved_relative_import_metadata(message))
    elif code == "undeclared_runtime_import":
        metadata.update(_legacy_undeclared_runtime_import_metadata(message))
    elif code.startswith(("typescript_ts", "rust_e")) or code in {
        "go_compile_error",
        "java_compile_error",
        "cpp_compile_error",
    }:
        metadata.update(_legacy_compiler_diagnostic_metadata(message, code))
    return {key: value for key, value in metadata.items() if value}


def _legacy_declared_target_missing_metadata(message: str) -> dict[str, str]:
    """Project old declared-target display errors into typed metadata.

    Target contract scanners should emit structured metadata directly. This
    helper isolates the legacy display-string path while callers migrate.
    """

    path = _artifact_quality_issue_path(message)
    if not path:
        return {}
    return {"target_file": path}


def _legacy_npm_script_metadata(script_name: str, script_issue: str, *, entrypoint: str = "") -> dict[str, str]:
    """Project old display-only npm script errors into typed metadata.

    New package-script scanner paths should construct ArtifactQualityIssue rows
    directly from PackageScriptIssue. This compatibility helper is only for
    legacy diagnostic strings that still reach _artifact_quality_issue_metadata.
    """

    metadata = {
        "script_name": script_name.strip(),
        "script_issue": script_issue.strip(),
        "script_issue_source": "legacy_error_text",
    }
    if entrypoint:
        metadata["entrypoint"] = entrypoint.strip()
    return {key: value for key, value in metadata.items() if value}


def _legacy_npm_manifest_issue_metadata(message: str) -> dict[str, str]:
    script_match = _ARTIFACT_QUALITY_NPM_SCRIPT_RE.search(message)
    if script_match:
        detail = str(script_match.group("detail") or "").strip()
        entrypoint = ""
        entrypoint_match = _ARTIFACT_QUALITY_NPM_MISSING_ENTRYPOINT_RE.search(detail)
        if entrypoint_match:
            entrypoint = str(entrypoint_match.group("entrypoint") or "").strip()
        return _legacy_npm_script_metadata(
            str(script_match.group("script") or ""),
            _npm_manifest_script_issue(detail),
            entrypoint=entrypoint,
        )

    python_command_match = _ARTIFACT_QUALITY_NPM_PYTHON_COMMAND_RE.search(message)
    if python_command_match:
        return _legacy_npm_script_metadata(str(python_command_match.group("script") or ""), "python_command")

    normalized_message = message.lower()
    if "test script must use node --test" in normalized_message:
        return _legacy_npm_script_metadata("test", "node_test_runner_contract")

    script_name = ""
    for candidate in ("start", "serve", "dev", "preview"):
        if f"npm run {candidate}" in normalized_message:
            script_name = candidate
            break
    if not script_name and "npm start" in normalized_message:
        script_name = "start"
    port_conflict = "eaddrinuse" in normalized_message or "address already in use" in normalized_message
    if script_name and port_conflict:
        return _legacy_npm_script_metadata(script_name, "fixed_port_conflict")
    if "npm default failing test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "default_failing_test_script")
    if "npm placeholder test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "placeholder_test_script")
    if "npm manifest-only test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "manifest_only_test_script")
    return {}


def _legacy_unresolved_import_symbol_metadata(message: str) -> dict[str, str]:
    """Project old unresolved-import-symbol display text into metadata.

    Cross-file interface scanners should prefer typed import/export evidence.
    This compatibility helper keeps legacy diagnostic parsing in one place until
    all callers emit structured import issues directly.
    """

    match = _ARTIFACT_QUALITY_UNRESOLVED_IMPORT_SYMBOL_RE.search(message)
    if not match:
        return {}
    return {
        key: value
        for key, value in {
            "symbol": str(match.group("symbol") or "").strip(),
            "module": str(match.group("module") or "").strip(),
            "importer_path": str(match.group("path") or "").strip(),
        }.items()
        if value
    }


def _legacy_unresolved_relative_import_metadata(message: str) -> dict[str, str]:
    """Project old unresolved-relative-import display text into metadata."""

    match = _ARTIFACT_QUALITY_UNRESOLVED_RELATIVE_IMPORT_RE.search(message)
    if not match:
        return {}
    return {
        key: value
        for key, value in {
            "specifier": str(match.group("specifier") or "").strip(),
            "importer_path": str(match.group("path") or "").strip(),
        }.items()
        if value
    }


def _legacy_undeclared_runtime_import_metadata(message: str) -> dict[str, str]:
    """Project old undeclared-runtime-import display text into metadata."""

    match = _ARTIFACT_QUALITY_UNDECLARED_RUNTIME_IMPORT_RE.search(message)
    if not match:
        return {}
    specifier = str(match.group("specifier") or "").strip()
    package_root = _package_root_name(specifier) if specifier else ""
    return {
        key: value
        for key, value in {
            "specifier": specifier,
            "package_root": package_root,
            "path": str(match.group("path") or "").strip(),
            "diagnostic_kind": "undeclared_runtime_import",
            "archetype": "missing_dependency",
        }.items()
        if value
    }


def _legacy_compiler_diagnostic_metadata(message: str, code: str) -> dict[str, str]:
    """Project legacy compiler diagnostic text into metadata.

    Parser-backed scanners should emit these fields directly. This helper keeps
    compatibility parsing centralized while typed compiler issue rows replace
    display-string diagnostics one language family at a time.
    """

    metadata: dict[str, str] = {}
    if code.startswith("typescript_ts"):
        typescript_match = _ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE.search(message)
        if typescript_match:
            metadata["diagnostic_code"] = str(typescript_match.group("code") or "").strip()
    elif code.startswith("rust_e"):
        rust_match = _ARTIFACT_QUALITY_RUST_ERROR_RE.search(message)
        if rust_match:
            metadata["diagnostic_code"] = str(rust_match.group("code") or "").strip()
    elif code in {"go_compile_error", "java_compile_error", "cpp_compile_error"}:
        metadata["language"] = code.removesuffix("_compile_error")
        if code == "go_compile_error":
            go_undefined_match = _ARTIFACT_QUALITY_GO_UNDEFINED_RE.search(message)
            if go_undefined_match:
                metadata["identifier"] = str(go_undefined_match.group("identifier") or "").strip()
                metadata["diagnostic_kind"] = "undefined_identifier"
    return {key: value for key, value in metadata.items() if value}


def _npm_manifest_script_issue(detail: str) -> str:
    normalized = detail.lower()
    if "placeholder command" in normalized:
        return "placeholder_command"
    if "references missing local entrypoint" in normalized:
        return "missing_local_entrypoint"
    if "recursively invokes itself" in normalized:
        return "recursive_script"
    if "invalid shell syntax" in normalized:
        return "invalid_shell_syntax"
    if "invalid node eval syntax" in normalized:
        return "invalid_node_eval_syntax"
    if "shell command substitution" in normalized:
        return "shell_command_substitution"
    if "swallows command failures" in normalized:
        return "swallows_command_failures"
    return "manifest_script_error"


def _javascript_module_error_metadata(text: str, message: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw": text, "diagnostic_kind": "javascript_module_error"}
    normalized = f"{text}\n{message}".lower()
    start_invoked = "npm run start" in normalized or "npm start" in normalized
    source_loader = "ts-node" in normalized or "node --loader" in normalized or ".ts" in normalized
    require_cycle = "err_require_cycle_module" in normalized or "cannot require() es module" in normalized
    if start_invoked and source_loader and require_cycle:
        metadata["script_name"] = "start"
        metadata["script_issue"] = "typescript_source_loader_require_cycle"
    missing_compiled_entrypoint = _compiled_entrypoint_from_node_module_error(message)
    if missing_compiled_entrypoint:
        metadata["script_issue"] = "missing_compiled_entrypoint"
        metadata["script_issue_source"] = "node_module_not_found"
        metadata["entrypoint"] = missing_compiled_entrypoint
        script_name = _script_name_from_npm_invocation(normalized)
        if script_name:
            metadata["script_name"] = script_name
    return metadata


def _compiled_entrypoint_from_node_module_error(message: str) -> str:
    match = _ARTIFACT_QUALITY_NODE_CANNOT_FIND_MODULE_RE.search(message)
    if not match:
        return ""
    raw_path = str(match.group("path") or "").strip().replace("\\", "/")
    normalized_path = raw_path.removeprefix("./")
    for segment in ("dist/", "build/", "out/"):
        if normalized_path.startswith(segment):
            return normalized_path
        marker = f"/{segment}"
        marker_index = normalized_path.rfind(marker)
        if marker_index >= 0:
            return normalized_path[marker_index + 1 :]
    return ""


def _script_name_from_npm_invocation(normalized_text: str) -> str:
    for script_name in ("start", "serve", "dev", "preview", "build", "test", "verify"):
        if f"npm run {script_name}" in normalized_text:
            return script_name
    if "npm start" in normalized_text:
        return "start"
    return ""


def _javascript_module_error_issue(
    *,
    text: str,
    message: str,
    match: re.Match[str],
    line: int | None,
    column: int | None,
) -> ArtifactQualityIssue:
    """Project old JavaScript module-loader output into a typed issue row."""

    module_message = str(match.group("message") or message).strip()
    return ArtifactQualityIssue(
        code="javascript_module_error",
        message=module_message,
        path=_artifact_quality_issue_path(message),
        source="runtime_smoke",
        line=line,
        column=column,
        metadata=_javascript_module_error_metadata(text, module_message),
    )


def _artifact_quality_issue_from_error(error: str) -> ArtifactQualityIssue:
    text = str(error or "").strip()
    message = text
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    line, column = _artifact_quality_issue_location(message)
    javascript_module_error = _ARTIFACT_QUALITY_JAVASCRIPT_MODULE_ERROR_RE.search(message)
    if javascript_module_error:
        return _javascript_module_error_issue(
            text=text,
            message=message,
            match=javascript_module_error,
            line=line,
            column=column,
        )
    code = _legacy_artifact_quality_issue_code_from_message(message)
    path = "package.json" if code == "npm_manifest_invalid" else _artifact_quality_issue_path(message)
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=path,
        line=line,
        column=column,
        metadata=_artifact_quality_issue_metadata(text, message, code),
    )


def _artifact_quality_issue_from_mapping(payload: Mapping[str, Any]) -> ArtifactQualityIssue | None:
    code = str(payload.get("code") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not code and not message:
        return None
    metadata_raw = payload.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    for key, value in payload.items():
        if key in {"code", "message", "path", "severity", "source", "line", "column", "metadata"}:
            continue
        if key not in metadata:
            metadata[str(key)] = value
    path_raw = payload.get("path")
    path = str(path_raw).strip().replace("\\", "/") if path_raw is not None else None
    source = str(payload.get("source") or "artifact_quality").strip() or "artifact_quality"
    return ArtifactQualityIssue(
        code=code
        or _artifact_quality_issue_code_from_typed_metadata(metadata, source=source)
        or _legacy_artifact_quality_issue_code_from_message(message),
        message=message or code,
        path=path or None,
        severity=str(payload.get("severity") or "error").strip() or "error",
        source=source,
        line=_artifact_quality_optional_int(payload.get("line")),
        column=_artifact_quality_optional_int(payload.get("column")),
        metadata=metadata,
    )


def _artifact_quality_issue_from_value(value: Any) -> ArtifactQualityIssue | None:
    if isinstance(value, ArtifactQualityIssue):
        return value
    if isinstance(value, Mapping):
        return _artifact_quality_issue_from_mapping(value)
    text = str(value or "").strip()
    if not text:
        return None
    return _artifact_quality_issue_from_error(text)


def _artifact_quality_issues_from_errors(errors: Iterable[Any]) -> tuple[ArtifactQualityIssue, ...]:
    return tuple(issue for value in errors if (issue := _artifact_quality_issue_from_value(value)) is not None)


def _artifact_quality_issue_from_cross_artifact_issue(
    issue: CrossArtifactConsistencyIssue,
) -> ArtifactQualityIssue:
    """Project cross-file interface evidence without reparsing its message."""

    raw_message = issue.to_error_message()
    metadata: dict[str, Any] = {
        "raw": raw_message,
        "importer_path": issue.importer_path,
        "owner_path": issue.owner_path,
        "symbol": issue.symbol,
        "details": dict(issue.details),
    }
    if issue.code in _CROSS_ARTIFACT_CONSISTENCY_DIAGNOSTIC_KINDS:
        metadata["diagnostic_kind"] = issue.code
    return ArtifactQualityIssue(
        code=issue.code,
        message=issue.message,
        path=issue.importer_path or issue.owner_path or None,
        severity=issue.severity,
        source="cross_artifact_consistency",
        metadata=metadata,
    )


def artifact_quality_issues_from_errors(errors: Iterable[Any]) -> tuple[dict[str, Any], ...]:
    """Project artifact-quality findings into typed issue payloads."""

    return tuple(issue.to_dict() for issue in sys.modules[__package__]._artifact_quality_issues_from_errors(errors))


def artifact_quality_issues_for_errors(
    errors: Iterable[Any],
    issue_payloads: Iterable[Any],
) -> tuple[dict[str, Any], ...]:
    """Return issue payloads matching a filtered artifact-quality error list.

    Scanners can emit both display errors and structured issues. Downstream
    gates often filter the display errors by task scope, then need the matching
    typed issues without reparsing message prose in the adapter layer. This
    helper keeps that matching and residual projection inside KernelOne
    artifact quality.

    Complexity:
        O(e + i) average time for ``e`` errors and ``i`` issue payloads,
        excluding the small tuple keys built for each row; O(e + i) memory.
    """

    error_rows = [str(error or "").strip() for error in errors if str(error or "").strip()]
    allowed_raw = set(error_rows)
    allowed_structural_keys = {key for error in error_rows if (key := artifact_quality_issue_structural_key(error))}
    merged: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    seen_raw: set[str] = set()
    seen_structural_keys: set[tuple[str, ...]] = set()

    for payload in issue_payloads:
        issue = _artifact_quality_issue_from_value(payload)
        if issue is None:
            continue
        issue_payload = dict(payload) if isinstance(payload, Mapping) else issue.to_dict()
        raw = artifact_quality_issue_raw(issue_payload)
        key = artifact_quality_issue_key(issue_payload)
        structural_key = artifact_quality_issue_structural_key(issue_payload)
        if key in seen_keys:
            continue
        if raw not in allowed_raw and (not structural_key or structural_key not in allowed_structural_keys):
            continue
        merged.append(issue_payload)
        seen_keys.add(key)
        if raw:
            seen_raw.add(raw)
        if structural_key:
            seen_structural_keys.add(structural_key)

    residual_errors: list[str] = []
    for raw in error_rows:
        if raw in seen_raw:
            continue
        parsed_structural_key = artifact_quality_issue_structural_key(raw)
        if parsed_structural_key and parsed_structural_key in seen_structural_keys:
            continue
        residual_errors.append(raw)
    for residual_issue in sys.modules[__package__].artifact_quality_issues_from_errors(residual_errors):
        raw = artifact_quality_issue_raw(residual_issue)
        key = artifact_quality_issue_key(residual_issue)
        if key in seen_keys or (raw and raw in seen_raw):
            continue
        merged.append(dict(residual_issue))
        seen_keys.add(key)
        if raw:
            seen_raw.add(raw)
    return tuple(merged)


def artifact_quality_issue_raw(value: Any) -> str:
    """Return the canonical raw diagnostic text for an artifact-quality issue."""

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ""
    metadata = issue.metadata
    if isinstance(metadata, Mapping):
        raw = str(metadata.get("raw") or "").strip()
        if raw:
            return raw
    return str(issue.message or "").strip()


def artifact_quality_issue_key(value: Any) -> tuple[str, ...]:
    """Return the canonical identity key for artifact-quality issue de-duplication."""

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ("legacy_raw", "")
    code = str(issue.code or "").strip()
    path = str(issue.path or "").strip().replace("\\", "/")
    line = str(issue.line or "").strip() if issue.line is not None else ""
    column = str(issue.column or "").strip() if issue.column is not None else ""
    message = str(issue.message or "").strip()
    if code or path or line or column:
        return ("structured", code, path, line, column, message)
    raw = artifact_quality_issue_raw(issue)
    return ("legacy_raw", raw or message)


def artifact_quality_issue_structural_key(value: Any) -> tuple[str, ...]:
    """Return a message-independent structured key for issue matching.

    This key is intentionally coarser than :func:`artifact_quality_issue_key`.
    It lets downstream gates match a typed issue to its source diagnostic without
    reparsing the diagnostic message, while still requiring code and path facts.

    Complexity:
        O(1) time and memory for one issue payload.
    """

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ()
    code = str(issue.code or "").strip()
    path = str(issue.path or "").strip().replace("\\", "/")
    if not code or not path:
        return ()
    line = str(issue.line or "").strip() if issue.line is not None else ""
    column = str(issue.column or "").strip() if issue.column is not None else ""
    return code, path, line, column


def _artifact_quality_evidence(
    *,
    errors: Iterable[str] = (),
    issues: Iterable[Any] = (),
    scanned_relative_paths: Iterable[str] = (),
    cross_artifact_issues: Iterable[CrossArtifactConsistencyIssue] = (),
    cross_artifact_repair_plans: Iterable[CrossArtifactRepairPlan] = (),
    contract_amendment_request: ContractAmendmentRequest | None = None,
) -> ArtifactQualityEvidence:
    deduped_errors = tuple(dict.fromkeys(str(error).strip() for error in errors if str(error or "").strip()))
    direct_issues = sys.modules[__package__]._artifact_quality_issues_from_errors(issues)
    deduped_cross_artifact_issues = tuple(cross_artifact_issues)
    cross_artifact_error_messages = {
        issue.to_error_message() for issue in deduped_cross_artifact_issues if not issue.code.startswith("contract_")
    }
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in direct_issues}
    residual_errors = tuple(
        error
        for error in deduped_errors
        if str(error or "").strip() not in (*cross_artifact_error_messages, *direct_issue_messages)
    )
    string_projected_issues = sys.modules[__package__]._artifact_quality_issues_from_errors(residual_errors)
    projected_cross_artifact_issues = tuple(
        _artifact_quality_issue_from_cross_artifact_issue(issue)
        for issue in deduped_cross_artifact_issues
        if not issue.code.startswith("contract_")
    )
    return ArtifactQualityEvidence(
        errors=deduped_errors,
        issues=(*direct_issues, *string_projected_issues, *projected_cross_artifact_issues),
        scanned_relative_paths=tuple(scanned_relative_paths),
        cross_artifact_issues=deduped_cross_artifact_issues,
        cross_artifact_repair_plans=tuple(cross_artifact_repair_plans),
        contract_amendment_request=contract_amendment_request,
    )


def _file_artifact_quality_issue(
    error: str,
    relative_path: str,
    *,
    code: str,
    source: str = "file_artifact_scanner",
    metadata: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    normalized_error = str(error or "").strip()
    message = normalized_error
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    issue_metadata: dict[str, Any] = {
        "raw": normalized_error,
        "artifact_path": relative_path,
    }
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            if value is None:
                continue
            issue_metadata[str(key)] = value
    if (
        source == "file_artifact_scanner"
        and code in _FILE_ARTIFACT_SCANNER_DIAGNOSTIC_KINDS
        and "diagnostic_kind" not in issue_metadata
    ):
        issue_metadata["diagnostic_kind"] = code
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source=source,
        metadata=issue_metadata,
    )
