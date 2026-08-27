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
_CARGO_TEST_STDOUT_RE = re.compile(r"^---- (?P<name>\S+) stdout ----\s*$", re.MULTILINE)
_CARGO_TEST_PANIC_LOC_RE = re.compile(
    r"panicked at (?P<path>[^:\n]+\.rs):(?P<line>\d+)",
)
_CARGO_TEST_FAILURE_DIAGNOSTIC_LIMIT = 16

_GO_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.go):(?P<line>\d+):(?P<column>\d+):\s*(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_GO_UNDEFINED_IDENTIFIER_RE = re.compile(
    r"\bundefined:\s*(?P<identifier>(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_CPP_ERROR_RE = re.compile(
    r"(?P<path>[^:\n]+\.(?:cc|cpp|cxx|hpp|hh|hxx|c|h)):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?:(?P<severity>fatal error|error|warning):\s*)?(?P<message>[^\n]+)",
    re.IGNORECASE,
)
_CPP_STANDARD_INCOMPATIBILITY_RE = re.compile(
    r"(?P<symbol>std::[A-Za-z_][A-Za-z0-9_]*)[^\r\n]{0,96}(?:"
    r"only\s+available\s+from\s+c\+\+(?P<available>\d+)|"
    r"requires\s+c\+\+(?P<requires>\d+)|"
    r"c\+\+(?P<extension>\d+)\s+extension"
    r")",
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


def _workspace_validation_output_payload(text: str) -> str:
    """Remove a balanced command envelope while retaining verifier output.

    Workspace validation records may embed a multi-line ``python -c`` program
    inside ``command failed (<command>): <output>``.  A regex that stops at the
    first ``)`` cannot delimit such a command, so every source line was later
    projected as an independent repair diagnostic (L3-24 r43: 79 rows from
    three real failures).  Parse only the balanced outer command parentheses;
    command provenance remains authoritative in the verifier receipt.
    """

    raw = str(text or "")
    prefix = re.search(r"workspace validation command failed\s*\(", raw, re.IGNORECASE)
    if prefix is None:
        return raw
    depth = 1
    quote = ""
    escaped = False
    for index in range(prefix.end(), len(raw)):
        char = raw[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                suffix = raw[index + 1 :].lstrip()
                if suffix.startswith(":"):
                    payload = suffix[1:].lstrip()
                    if payload:
                        return payload
                return raw
    return raw
_TAP_FIELD_RE = re.compile(r"(?m)^\s*(?P<key>actual|expected|operator):\s*(?P<value>.+?)\s*$", re.IGNORECASE)


def normalize_artifact_quality_errors(errors: Sequence[Any]) -> tuple[RepairDiagnostic, ...]:
    """Convert raw or structured artifact-quality input into repair diagnostics."""

    raw_errors = tuple(errors or ())
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

    for raw in raw_errors:
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
        # Multi-line string already carries primary + continuations.  Quoted
        # verifier output may instead carry an escaped stdout/stderr transcript
        # on one physical line; route that through the same causal-blob parser.
        escaped_transcript = "\\n" in text and bool(
            re.search(r"\\n(?:stdout|stderr):\\n", text, re.IGNORECASE)
        )
        if "\n" in text or escaped_transcript:
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
    cpp_standard_requirements: dict[str, str] = {}
    for raw in raw_errors:
        if isinstance(raw, RepairDiagnostic):
            evidence_text = "\n".join((raw.message, raw.raw))
        elif isinstance(raw, Mapping):
            evidence_text = "\n".join(
                str(raw.get(key) or "") for key in ("message", "raw", "stderr", "stdout", "error")
            )
        else:
            evidence_text = str(raw or "")
        for match in _CPP_STANDARD_INCOMPATIBILITY_RE.finditer(evidence_text):
            required = next(
                (
                    str(match.group(group) or "").strip()
                    for group in ("available", "requires", "extension")
                    if str(match.group(group) or "").strip()
                ),
                "",
            )
            cpp_standard_requirements[match.group("symbol").casefold()] = f"c++{required}" if required else ""
    if cpp_standard_requirements:
        promoted: list[RepairDiagnostic] = []
        for diagnostic in diagnostics:
            diagnostic_text = "\n".join((diagnostic.message, diagnostic.raw)).casefold()
            matched_symbol = next(
                (symbol for symbol in cpp_standard_requirements if symbol in diagnostic_text),
                "",
            )
            if diagnostic.code != "cpp_compile_error" or not matched_symbol:
                promoted.append(diagnostic)
                continue
            promoted.append(
                RepairDiagnostic(
                    source=diagnostic.source,
                    code="cpp_language_standard_incompatibility",
                    message=diagnostic.message,
                    severity=diagnostic.severity,
                    path=diagnostic.path,
                    line=diagnostic.line,
                    column=diagnostic.column,
                    span_start=diagnostic.span_start,
                    span_end=diagnostic.span_end,
                    raw=diagnostic.raw,
                    metadata={
                        **dict(diagnostic.metadata),
                        "language": "cpp",
                        "diagnostic_kind": "language_standard_incompatibility",
                        "incompatible_symbol": matched_symbol,
                        "required_standard": cpp_standard_requirements[matched_symbol],
                        "origin_diagnostic_id": diagnostic.diagnostic_id,
                    },
                )
            )
        diagnostics = promoted
    # One physical failure may be reported by both the direct build verifier
    # and a nested test wrapper.  Stable diagnostic identity represents the
    # causal fact; counting the same fact twice inflates coverage and can trip
    # convergence guards without any new residual.
    unique: dict[str, RepairDiagnostic] = {}
    for diagnostic in diagnostics:
        unique.setdefault(diagnostic.diagnostic_id, diagnostic)
    return tuple(unique.values())


def _normalize_text_error_blob(text: str) -> list[RepairDiagnostic]:
    """Normalize one text blob that may contain multi-line tsc diagnostics."""

    blob = _workspace_validation_output_payload(str(text or ""))
    if not blob.strip():
        return []
    # Verifier frameworks can quote a nested compiler transcript, preserving
    # newlines as literal ``\\n`` sequences.  Parsing that transport form as a
    # path turns ``\\n/tmp/...`` into the fake ``/n/tmp/...`` after canonical
    # slash normalization.  Decode only an identified stdout/stderr build
    # transcript; ordinary source diagnostics may legitimately contain the
    # two characters ``\\n`` and must remain byte-faithful.
    if "\\n" in blob and re.search(r"\\n(?:stdout|stderr):\\n", blob, re.IGNORECASE):
        blob = blob.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
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
    cargo_test_failures = _normalize_cargo_test_failures(blob)
    if cargo_test_failures:
        # Live L1-09: cargo test --quiet compiled, then two assertion islands
        # were shredded into per-line artifact_quality_error rows. Coverage
        # missed rust.test_assertion_local_structure (raw_terms AND never
        # held on one line). Keep one verifier diagnostic per failed test.
        return cargo_test_failures
    rust_location = _RUST_LOCATION_RE.search(blob)
    if rust_location and re.search(r"(?m)^\s*(?:error(?:\[E\d+\])?|warning):", blob, re.IGNORECASE):
        # Live L1-09: one cargo transcript carries independent parser, E0432,
        # format-string, E0063 and serde errors. Collapsing them into a single
        # rust_diagnostic made coverage pick only line_suggestion (unplannable)
        # and hid rust_dependency / serde_derive. Keep each error headline as
        # one causal block with its own --> path and excerpt.
        rust_blocks = _split_rust_compiler_error_blocks(blob)
        if rust_blocks:
            return rust_blocks
    cpp_linker_failures = _normalize_cpp_linker_failures(blob)
    if cpp_linker_failures:
        return cpp_linker_failures
    cpp_compiler_failures = _normalize_cpp_compiler_failures(blob)
    if cpp_compiler_failures:
        return cpp_compiler_failures
    lowered = blob.lower()
    if ("npm run test" in lowered or "npm test" in lowered) and (
        "module_not_found" in lowered or "cannot find module" in lowered or "could not find" in lowered
    ):
        # A Node verifier failure is one causal diagnostic. Splitting its stack
        # trace into independent rows discards the conjunction between the npm
        # test command and MODULE_NOT_FOUND, so coverage becomes unplannable and
        # the Director needlessly escalates instead of editing the exact task.
        return [_normalize_one_error(blob.strip())]
    if ".js" in lowered and "requires" in lowered and " at new " in lowered:
        # A JavaScript constructor-contract error and its ``at new`` frame are
        # one causal island. Splitting them makes both coverage and the runtime
        # planner unplannable: one row owns the required field, the other owns
        # the class/file location.
        return [_normalize_one_error(blob.strip())]
    if "can't find library" in lowered and "at path" in lowered and ".rs" in lowered:
        # Cargo's missing library headline, target path, and explanatory note
        # form one diagnostic. Per-line fallback creates fake extra residuals
        # and disconnects the path evidence required by the repair rule.
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


def _normalize_cpp_linker_failures(text: str) -> list[RepairDiagnostic]:
    """Keep C++ compiler warnings separate from linker undefined symbols."""

    blob = str(text or "")
    if "undefined reference" not in blob.casefold():
        return []

    diagnostics: list[RepairDiagnostic] = []
    seen_compiler: set[tuple[str, int | None, int | None, str]] = set()
    for match in _CPP_ERROR_RE.finditer(blob):
        diagnostic = _normalize_one_error(str(match.group(0) or "").strip())
        key = (str(diagnostic.path or ""), diagnostic.line, diagnostic.column, diagnostic.message)
        if key in seen_compiler:
            continue
        seen_compiler.add(key)
        diagnostics.append(diagnostic)

    undefined_lines: list[str] = []
    for line in blob.splitlines():
        stripped = line.strip()
        if "undefined reference" in stripped.casefold() and stripped not in undefined_lines:
            undefined_lines.append(stripped)
    diagnostics.append(
        RepairDiagnostic(
            source="compiler",
            code="cpp_linker_undefined_reference",
            message="\n".join(undefined_lines),
            raw=blob,
            metadata={
                "language": "cpp",
                "diagnostic_kind": "linker_undefined_reference",
            },
        )
    )
    return diagnostics


def _normalize_cpp_compiler_failures(text: str) -> list[RepairDiagnostic]:
    """Preserve every distinct GCC/Clang occurrence in one compiler transcript.

    ``_normalize_one_error`` intentionally returns one diagnostic.  Applying it
    to a complete multi-error C++ transcript therefore retained only the first
    location.  That made a one-of-many edit look like a stagnant 1 -> 1 repair
    and hid the remaining occurrence from the next same-task repair round.
    """

    blob = str(text or "")
    matches = list(_CPP_ERROR_RE.finditer(blob))
    if len(matches) < 2:
        return []

    diagnostics: list[RepairDiagnostic] = []
    seen: set[tuple[str, int | None, int | None, str]] = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        block = blob[match.start() : end].strip()
        diagnostic = _normalize_one_error(block)
        key = (str(diagnostic.path or ""), diagnostic.line, diagnostic.column, diagnostic.message)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(diagnostic)
    return diagnostics


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


def _normalize_cargo_test_failures(text: str) -> list[RepairDiagnostic]:
    """Project ``cargo test`` assertion islands into causal verifier diagnostics."""

    blob = str(text or "")
    if (
        "test result: FAILED" not in blob
        and "--- FAILED" not in blob
        and "panicked at" not in blob
        and "error: test failed" not in blob.lower()
    ):
        return []
    matches = list(_CARGO_TEST_STDOUT_RE.finditer(blob))
    if not matches:
        return []
    diagnostics: list[RepairDiagnostic] = []
    for index, match in enumerate(matches[:_CARGO_TEST_FAILURE_DIAGNOSTIC_LIMIT]):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        block = blob[match.start() : end].strip()
        if "panicked at" not in block and "assertion" not in block.lower():
            continue
        name = str(match.group("name") or "test").strip()
        location = _CARGO_TEST_PANIC_LOC_RE.search(block)
        message = next(
            (
                line.strip()
                for line in block.splitlines()
                if "assertion" in line.lower() or line.startswith("left:") or line.startswith("right:")
            ),
            f"cargo test failed: {name}",
        )
        diagnostics.append(
            RepairDiagnostic(
                source="verifier",
                code="verifier_test_failure",
                message=message,
                path=str(location.group("path") or "").strip() if location is not None else None,
                line=_to_int(location.group("line")) if location is not None else None,
                raw=block,
                metadata={"framework": "cargo_test", "test_name": name, "language": "rust"},
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
        next_name = str(matches[index + 1].group("name") or "").strip() if index + 1 < len(matches) else ""
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
        message = str(location.group("message") or "").strip() if location is not None else f"Test failed: {name}"
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


def _split_rust_compiler_error_blocks(blob: str) -> list[RepairDiagnostic]:
    """Split one cargo transcript into one diagnostic per rustc error headline."""

    parts = re.split(r"(?m)(?=^(?:error(?:\[E\d+\])?|warning):)", str(blob or ""))
    diagnostics: list[RepairDiagnostic] = []
    seen: set[tuple[str, str, int | None]] = set()
    for part in parts:
        block = str(part or "").strip()
        if not re.match(r"(?:error(?:\[E\d+\])?|warning):", block):
            continue
        first_line = next((line.strip() for line in block.splitlines() if line.strip()), "")
        if first_line.startswith("error: could not compile"):
            continue
        location = _RUST_LOCATION_RE.search(block)
        if location is None and _RUST_ERROR_RE.search(block) is None:
            continue
        if _RUST_ERROR_RE.search(block):
            diagnostic = _normalize_one_error(block)
        else:
            diagnostic = RepairDiagnostic(
                source="compiler",
                code="rust_diagnostic",
                severity="warning" if first_line.lower().startswith("warning:") else "error",
                message=first_line or "Rust diagnostic",
                path=str(location.group("path") or "").strip() if location else None,
                line=_to_int(location.group("line")) if location else None,
                column=_to_int(location.group("column")) if location else None,
                raw=block,
                metadata={"language": "rust"},
            )
        key = (diagnostic.code, str(diagnostic.path or ""), diagnostic.line)
        if key in seen:
            continue
        seen.add(key)
        diagnostics.append(diagnostic)
    return diagnostics


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

    match = _GO_TEST_LOCATION_RE.search(text)
    if match and "want" in str(match.group("message") or "").lower():
        message = str(match.group("message") or text).strip()
        return RepairDiagnostic(
            source="verifier",
            code="go_test_assertion_error",
            message=message,
            path=str(match.group("path") or "").strip(),
            line=_to_int(match.group("line")),
            raw=text,
            metadata={
                "language": "go",
                "diagnostic_kind": "go_test_assertion",
                "framework": "go_test",
            },
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
