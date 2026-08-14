"""Diagnostic normalization for Director repair input."""

from __future__ import annotations

import hashlib
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
_GO_UNDEFINED_IDENTIFIER_RE = re.compile(
    r"\bundefined:\s*(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)\b",
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
_NODE_ESM_TYPESCRIPT_MODULE_NOT_FOUND_RE = re.compile(
    r"Cannot find module\s+['\"]?(?P<missing>(?:file://)?/[^\s'\"]+\.js)['\"]?\s+"
    r"imported from\s+['\"]?(?P<importer>(?:file://)?/[^\s'\"]+\.tsx?)['\"]?",
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
_TAP_FAILURE_HEADER_RE = re.compile(
    r"(?m)^\s*not ok\s+(?P<ordinal>\d+)\s+-\s+(?P<name>.+?)\s*$",
    re.IGNORECASE,
)
_TAP_RESULT_BOUNDARY_RE = re.compile(r"(?m)^\s*(?:not ok|ok)\s+\d+\s+-\s+|^\s*1\.\.\d+\s*$", re.IGNORECASE)
_TAP_FAILURE_DIAGNOSTIC_LIMIT = 12
_UNITTEST_FAILURE_HEADER_RE = re.compile(
    r"(?m)^={20,}\s*\n(?P<kind>FAIL|ERROR):\s+(?P<name>[^\n]+)\s*$",
    re.IGNORECASE,
)
_UNITTEST_SUMMARY_BOUNDARY_RE = re.compile(r"(?m)^-{20,}\s*\nRan\s+\d+\s+tests?\b", re.IGNORECASE)
_UNITTEST_FAILURE_DIAGNOSTIC_LIMIT = 12
_GO_TEST_FAILURE_HEADER_RE = re.compile(
    r"(?m)^\s*---\s+FAIL:\s+(?P<name>\S+)\s+\((?P<duration>[^)]+)\)\s*$",
    re.IGNORECASE,
)
_GO_TEST_LOCATION_RE = re.compile(
    r"(?m)^\s*(?P<path>[^:\s]+\.go):(?P<line>\d+):\s*(?P<message>.+?)\s*$",
    re.IGNORECASE,
)
_GO_TEST_FAILURE_DIAGNOSTIC_LIMIT = 24
_VERIFIER_LOCATION_RE = re.compile(
    r"(?P<path>(?:file://)?(?:[A-Za-z]:)?[^\s()'\"]+\.(?:js|mjs|cjs|ts|tsx|jsx|py|go|rs|java|cc|cpp|cxx))"
    r":(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE,
)
_TAP_FIELD_RE = re.compile(r"(?m)^\s*(?P<key>actual|expected|operator):\s*(?P<value>.+?)\s*$", re.IGNORECASE)


def normalize_artifact_quality_errors(errors: Sequence[Any]) -> tuple[RepairDiagnostic, ...]:
    """Convert raw or structured artifact-quality input into repair diagnostics."""

    diagnostics: list[RepairDiagnostic] = []
    # Buffer only a TS primary line + its indented tsc continuations. Unrelated
    # non-TS rows stay separate so coverage counts remain stable.
    ts_buffer: list[str] = []

    def flush_ts_buffer() -> None:
        nonlocal ts_buffer
        if not ts_buffer:
            return
        diagnostics.extend(_normalize_text_error_blob("\n".join(ts_buffer)))
        ts_buffer = []

    for raw in errors or ():
        if isinstance(raw, RepairDiagnostic):
            flush_ts_buffer()
            diagnostics.append(raw)
            continue
        if isinstance(raw, Mapping):
            flush_ts_buffer()
            diagnostic = _normalize_structured_error(raw)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            continue
        text = str(raw or "")
        if not text.strip():
            continue
        # Multi-line string already carries primary + continuations.
        if "\n" in text:
            flush_ts_buffer()
            diagnostics.extend(_normalize_text_error_blob(text))
            continue
        line = text.rstrip("\n")
        if ts_buffer and line[:1] in {" ", "\t"}:
            ts_buffer.append(line)
            continue
        if _TS_ERROR_RE.search(line):
            flush_ts_buffer()
            ts_buffer.append(line)
            continue
        flush_ts_buffer()
        diagnostics.append(_normalize_one_error(line.strip()))
    flush_ts_buffer()
    return tuple(diagnostics)


def _normalize_text_error_blob(text: str) -> list[RepairDiagnostic]:
    """Normalize one text blob that may contain multi-line tsc diagnostics."""

    blob = str(text or "")
    if not blob.strip():
        return []
    expanded = _normalize_typescript_errors(blob)
    if expanded:
        residuals: list[RepairDiagnostic] = []
        expanded_codes = {item.code for item in expanded}
        for line in blob.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if line[:1] in {" ", "\t"}:
                continue
            if _TS_ERROR_RE.search(line):
                continue
            if _WORKSPACE_VALIDATION_RE.search(line):
                # The command wrapper is provenance, not an additional
                # compiler diagnostic.  Keeping it beside expanded TS rows
                # inflated coverage and could route a second generic repair.
                continue
            if (
                "typescript_config_key_syntax" in expanded_codes
                and "expected" in stripped.lower()
                and "but found" in stripped.lower()
            ):
                continue
            if "javascript_module_error" in expanded_codes and (
                _JAVASCRIPT_MODULE_ERROR_RE.search(stripped) or stripped.startswith("> ")
            ):
                # The canonical module diagnostic retains the complete blob,
                # including the npm command that selected the failing loader.
                # Emitting those lines again creates uncovered duplicates.
                continue
            if "missing the following properties from type" in stripped.lower():
                continue
            residual = _normalize_one_error(stripped)
            if residual.code.startswith("typescript_"):
                continue
            if residual.code in expanded_codes:
                continue
            residuals.append(residual)
        return expanded + residuals
    tap_failures = _normalize_tap_failures(blob)
    if tap_failures:
        # Node TAP output is one causal failure island per ``not ok`` block.
        # Treating every stack/summary/pass row as an independent generic
        # diagnostic inflated one assertion into 100+ repair inputs, hid the
        # responsible path, and starved the same-task repair prompt. Preserve
        # exact assertion evidence while keeping coverage bounded.
        return tap_failures
    unittest_failures = _normalize_unittest_failures(blob)
    if unittest_failures:
        # ``unittest -v`` emits one large command transcript.  Collapsing the
        # transcript to the first traceback makes a real 4 -> 3 reduction look
        # like a 1 -> 1 diagnostic swap, which can trip the two-stagnant-round
        # breaker before the Director gets a correction round.  Preserve one
        # causal diagnostic per FAIL/ERROR block so convergence is measured
        # from verifier facts rather than command-row count.
        return unittest_failures
    go_test_failures = _normalize_go_test_failures(blob)
    if go_test_failures:
        # ``go test`` also emits one command transcript containing multiple
        # causal FAIL blocks. Keeping it as one wrapper made a real reduction
        # in failing tests look like a one-to-one diagnostic swap, so Factory
        # stopped the same-Director repair before its remaining correction
        # round. Preserve each leaf test/subtest as an independent verifier
        # fact; parent FAIL rows that only summarize failing subtests are not
        # duplicated.
        return go_test_failures
    rust_location = _RUST_LOCATION_RE.search(blob)
    if rust_location and re.search(r"(?m)^\s*(?:error|warning):", blob, re.IGNORECASE):
        # Rust diagnostics without an E-code (parser errors and warnings) are
        # still one causal block.  Per-line fallback destroys the association
        # between the headline, ``--> path:line:column`` and source excerpt,
        # leaving planners without a usable path/raw block.  Keep the complete
        # block so existing Rust rules can match and compose precise patches.
        first_line = next((line.strip() for line in blob.splitlines() if line.strip()), "Rust diagnostic")
        return [
            RepairDiagnostic(
                source="compiler",
                code="rust_diagnostic",
                severity="warning" if first_line.lower().startswith("warning:") else "error",
                message=first_line,
                path=str(rust_location.group("path") or "").strip(),
                line=_to_int(rust_location.group("line")),
                column=_to_int(rust_location.group("column")),
                raw=blob.strip(),
                metadata={"language": "rust"},
            )
        ]
    lowered = blob.lower()
    if (
        ("npm run test" in lowered or "npm test" in lowered)
        and ("module_not_found" in lowered or "cannot find module" in lowered or "could not find" in lowered)
    ):
        # A Node verifier failure is one causal diagnostic. Splitting its stack
        # trace into independent rows discards the conjunction between the npm
        # test command and MODULE_NOT_FOUND, so coverage becomes unplannable and
        # the Director needlessly escalates instead of editing the exact task.
        return [_normalize_one_error(blob.strip())]
    # Compiler/runtime wrappers carry the actionable diagnostic on a later
    # line (Rust locations, Python traceback exceptions, Node ESM errors).
    # Normalize the causal blob before per-line fallback so the leading
    # wrapper cannot mask the nested error as a generic validation failure.
    combined = _normalize_one_error(blob.strip())
    if combined.code == "workspace_validation_failed" and "eaddrinuse" in lowered:
        # Keep the command wrapper and its nested port collision as one causal
        # diagnostic. The npm-script repair rule deliberately matches both the
        # command provenance and EADDRINUSE from this complete raw block.
        return [combined]
    if combined.code not in {"artifact_quality_error", "workspace_validation_failed"}:
        return [combined]
    # Non-TS blob: preserve one diagnostic per non-empty line.
    per_line = [_normalize_one_error(line.strip()) for line in blob.splitlines() if line.strip()]
    return per_line if per_line else [_normalize_one_error(blob.strip())]


def _normalize_tap_failures(text: str) -> list[RepairDiagnostic]:
    """Project Node TAP failure blocks into bounded, actionable diagnostics."""

    blob = str(text or "")
    matches = list(_TAP_FAILURE_HEADER_RE.finditer(blob))
    if not matches:
        return []

    diagnostics: list[RepairDiagnostic] = []
    total_failure_count = len(matches)
    truncated_failure_count = max(0, total_failure_count - _TAP_FAILURE_DIAGNOSTIC_LIMIT)
    source_blob_sha256 = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    for match in matches[:_TAP_FAILURE_DIAGNOSTIC_LIMIT]:
        next_boundary = _TAP_RESULT_BOUNDARY_RE.search(blob, match.end())
        end = next_boundary.start() if next_boundary is not None else len(blob)
        failure_block = blob[match.start() : end].strip()
        location = _VERIFIER_LOCATION_RE.search(failure_block)
        fields = {
            str(field.group("key") or "").strip().lower(): str(field.group("value") or "").strip()
            for field in _TAP_FIELD_RE.finditer(failure_block)
        }
        name = str(match.group("name") or "test").strip()
        message = f"Test failed: {name}"
        if fields.get("expected") or fields.get("actual"):
            message += f"; expected={fields.get('expected', 'unknown')}; actual={fields.get('actual', 'unknown')}"
        path = str(location.group("path") or "").removeprefix("file://") if location else None
        diagnostics.append(
            RepairDiagnostic(
                source="verifier",
                code="verifier_test_failure",
                message=message,
                path=path,
                line=_to_int(location.group("line")) if location else None,
                column=_to_int(location.group("column")) if location else None,
                raw=failure_block,
                metadata={
                    "framework": "tap",
                    "test_name": name,
                    "test_ordinal": _to_int(match.group("ordinal")),
                    "total_failure_count": total_failure_count,
                    "truncated_failure_count": truncated_failure_count,
                    "source_blob_sha256": source_blob_sha256,
                    **fields,
                },
            )
        )
    return diagnostics


def _normalize_unittest_failures(text: str) -> list[RepairDiagnostic]:
    """Project Python unittest FAIL/ERROR blocks into causal diagnostics."""

    blob = str(text or "")
    matches = list(_UNITTEST_FAILURE_HEADER_RE.finditer(blob))
    if not matches:
        return []

    summary_boundary = _UNITTEST_SUMMARY_BOUNDARY_RE.search(blob)
    source_blob_sha256 = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    total_failure_count = len(matches)
    truncated_failure_count = max(0, total_failure_count - _UNITTEST_FAILURE_DIAGNOSTIC_LIMIT)
    diagnostics: list[RepairDiagnostic] = []
    for index, match in enumerate(matches[:_UNITTEST_FAILURE_DIAGNOSTIC_LIMIT]):
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        elif summary_boundary is not None and summary_boundary.start() > match.start():
            end = summary_boundary.start()
        else:
            end = len(blob)
        failure_block = blob[match.start() : end].strip()
        normalized = _normalize_one_error(failure_block)
        name = str(match.group("name") or "test").strip()
        kind = str(match.group("kind") or "FAIL").strip().lower()
        metadata = dict(normalized.metadata)
        metadata.update(
            {
                "framework": "unittest",
                "test_name": name,
                "result_kind": kind,
                "total_failure_count": total_failure_count,
                "truncated_failure_count": truncated_failure_count,
                "source_blob_sha256": source_blob_sha256,
            }
        )
        diagnostics.append(
            RepairDiagnostic(
                source=normalized.source if normalized.code != "artifact_quality_error" else "verifier",
                code=(
                    normalized.code
                    if normalized.code not in {"artifact_quality_error", "workspace_validation_failed"}
                    else "verifier_test_failure"
                ),
                message=(
                    normalized.message
                    if normalized.code not in {"artifact_quality_error", "workspace_validation_failed"}
                    else f"Test failed: {name}"
                ),
                severity=normalized.severity,
                path=normalized.path,
                line=normalized.line,
                column=normalized.column,
                raw=failure_block,
                metadata=metadata,
            )
        )
    return diagnostics


def _normalize_go_test_failures(text: str) -> list[RepairDiagnostic]:
    """Project Go ``go test`` FAIL blocks into causal leaf diagnostics."""

    blob = str(text or "")
    matches = list(_GO_TEST_FAILURE_HEADER_RE.finditer(blob))
    if not matches:
        return []

    candidates: list[tuple[re.Match[str], str, re.Match[str] | None]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        block = blob[match.start() : end].strip()
        name = str(match.group("name") or "test").strip()
        next_name = (
            str(matches[index + 1].group("name") or "").strip()
            if index + 1 < len(matches)
            else ""
        )
        location = _GO_TEST_LOCATION_RE.search(block)
        if location is None and next_name.startswith(f"{name}/"):
            # Parent row only summarizes one or more failing subtests.
            continue
        candidates.append((match, block, location))

    total_failure_count = len(candidates)
    truncated_failure_count = max(0, total_failure_count - _GO_TEST_FAILURE_DIAGNOSTIC_LIMIT)
    source_blob_sha256 = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    diagnostics: list[RepairDiagnostic] = []
    for match, failure_block, location in candidates[:_GO_TEST_FAILURE_DIAGNOSTIC_LIMIT]:
        name = str(match.group("name") or "test").strip()
        message = (
            str(location.group("message") or "").strip()
            if location is not None
            else f"Test failed: {name}"
        )
        diagnostics.append(
            RepairDiagnostic(
                source="verifier",
                code="verifier_test_failure",
                message=message,
                path=str(location.group("path") or "").strip() if location is not None else None,
                line=_to_int(location.group("line")) if location is not None else None,
                raw=failure_block,
                metadata={
                    "framework": "go_test",
                    "test_name": name,
                    "duration": str(match.group("duration") or "").strip(),
                    "total_failure_count": total_failure_count,
                    "truncated_failure_count": truncated_failure_count,
                    "source_blob_sha256": source_blob_sha256,
                },
            )
        )
    return diagnostics


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
        "exporter_path",
        "imported_symbol",
        "owner_path",
        "target_file",
        "manifest_path",
        "script_name",
        "script_issue",
        "entrypoint",
        "config_path",
        "target_directory",
        "artifact_path",
        "collision_name",
        "command",
        "declared_type",
        "detail",
        "exit_code",
        "export_name",
        "html_path",
        "package_root",
        "required_dependency",
        "runtime_syntax",
        "script_src",
        "source_path",
        "syntax_error",
        "actual",
        "suggestion",
        "issue_kind",
        "raw_path",
        "runtime_global",
        "language",
        "confidence",
        "archetype",
        "diagnostic_archetype",
        "diagnostic_kind",
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


def _typescript_error_continuation_lines(text: str, match_end: int) -> list[str]:
    """Capture indented tsc follow-up lines (e.g. missing-properties clauses)."""

    remainder = str(text or "")[match_end:]
    if not remainder:
        return []
    # Skip the newline that ends the primary error line.
    if remainder.startswith("\r\n"):
        remainder = remainder[2:]
    elif remainder.startswith("\n") or remainder.startswith("\r"):
        remainder = remainder[1:]
    continuations: list[str] = []
    for line in remainder.splitlines():
        if not line:
            # blank line ends the diagnostic block
            break
        if line[0] in {" ", "\t"}:
            stripped = line.strip()
            if stripped:
                continuations.append(stripped)
            continue
        break
    return continuations


def _normalize_typescript_errors(text: str) -> list[RepairDiagnostic]:
    diagnostics: list[RepairDiagnostic] = []
    for match in _TS_ERROR_RE.finditer(text):
        code = str(match.group("code") or "typescript_error").lower()
        primary_message = str(match.group("message") or text).strip()
        continuations = _typescript_error_continuation_lines(text, match.end())
        message = primary_message
        raw = str(match.group(0) or text).strip()
        if continuations:
            message = primary_message + "\n" + "\n".join(continuations)
            raw = raw + "\n" + "\n".join(f"  {item}" for item in continuations)
        diagnostics.append(
            RepairDiagnostic(
                source="artifact_quality",
                code=f"typescript_{code}",
                message=message,
                path=str(match.group("path") or "").strip(),
                line=_to_int(match.group("line")),
                column=_to_int(match.group("column")),
                raw=raw,
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
        message = str(match.group("message") or text).strip()
        metadata: dict[str, str] = {}
        undefined_match = _GO_UNDEFINED_IDENTIFIER_RE.search(message)
        if undefined_match:
            identifier = str(undefined_match.group("identifier") or "").strip()
            if identifier:
                metadata = {
                    "language": "go",
                    "diagnostic_kind": "undefined_identifier",
                    "identifier": identifier,
                }
        return RepairDiagnostic(
            source="compiler",
            code="go_compile_error",
            message=message,
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            column=_to_int(match.group("column")),
            raw=text,
            metadata=metadata,
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

    node_esm_typescript_module_error = _NODE_ESM_TYPESCRIPT_MODULE_NOT_FOUND_RE.search(text)
    if node_esm_typescript_module_error:
        missing_path = str(node_esm_typescript_module_error.group("missing") or "").removeprefix("file://")
        importer_path = str(node_esm_typescript_module_error.group("importer") or "").removeprefix("file://")
        return RepairDiagnostic(
            source="runtime_smoke",
            code="javascript_module_error",
            message=str(node_esm_typescript_module_error.group(0) or text).strip(),
            path=importer_path,
            raw=text,
            metadata={
                "language": "javascript",
                "module_error_kind": "node_esm_typescript_source_import",
                "missing_module_path": missing_path,
                "importer_path": importer_path,
            },
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
