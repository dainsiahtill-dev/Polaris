from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..javascript_syntax import repair_javascript_export_contract_placeholders
from ..path_files import normalize_base_files_strict, normalize_repair_path_strict
from .constants import *  # noqa: F403
from .common import *  # noqa: F403

"""TypeScript syntax repair module: text_repairs."""

def build_typescript_duplicate_function_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Remove later duplicate function declarations (TS2393 / TS2323 redeclare export).

    Live L1-01 r157: ``src/verify.ts`` carried both ``export async function
    runVerification`` and a trailing stub ``export function runVerification``,
    blocking ``tsc`` / four-pillar build. Keep the first declaration; drop the
    later complete function body span.
    """

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets = _parse_duplicate_function_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    for path, names in sorted(targets.items()):
        original = str(normalized_base_files.get(path) or "")
        if not original:
            continue
        # Resolve __line__ tokens and dedupe by function name so composition
        # does not receive overlapping removals for the same declaration.
        resolved_names: list[str] = []
        seen_names: set[str] = set()
        for raw_name in sorted(names):
            resolved = _resolve_duplicate_function_name(content=original, name=raw_name)
            if not resolved or resolved in seen_names:
                continue
            seen_names.add(resolved)
            resolved_names.append(resolved)
        for name in resolved_names:
            op = _duplicate_function_removal_operation(path=path, content=original, name=name)
            if op is None:
                continue
            operations.append(op)
            repaired.append({"file": path, "name": name})
        matched_diagnostics.extend(
            diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path)
        )
    return _repair_plan_or_none(
        rule_id="typescript.duplicate_function",
        source_tool=TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"functions": repaired},
    )

def _build_typescript_test_block_residue_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Remove trailing duplicated test-block residue that leaves TS1128 closers."""

    if not any(_is_typescript_test_block_residue_diagnostic(diagnostic) for diagnostic in diagnostics):
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    seen_spans: set[tuple[str, int, int]] = set()
    target_paths = {
        _normalize_repair_path(str(diagnostic.path or ""))
        for diagnostic in diagnostics
        if _is_typescript_test_block_residue_diagnostic(diagnostic)
    }
    target_paths = {path for path in target_paths if path in base_files}
    candidate_paths = target_paths or {path for path in base_files if _is_typescript_test_file(path)}

    for path in sorted(candidate_paths):
        if not _is_typescript_test_file(path):
            continue
        original = str(base_files.get(path) or "")
        if not original:
            continue
        diagnostic_line_numbers = {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if _is_typescript_test_block_residue_diagnostic(diagnostic)
            and _normalize_repair_path(str(diagnostic.path or "")) == path
            and int(diagnostic.line or 0) > 0
        }
        path_operations = _typescript_test_block_residue_operations(
            path=path,
            content=original,
            line_numbers=diagnostic_line_numbers,
            seen_spans=seen_spans,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if _is_typescript_test_block_residue_diagnostic(diagnostic)
            and (not diagnostic.path or _normalize_repair_path(str(diagnostic.path or "")) == path)
        )
        repaired_items.extend(
            {
                "file": path,
                "start_line": int(operation.metadata.get("start_line") or 0),
                "end_line": int(operation.metadata.get("end_line") or 0),
            }
            for operation in path_operations
        )

    return _repair_plan_or_none(
        rule_id="typescript.test_block_residue",
        source_tool=TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"test_block_residue_removals": repaired_items},
    )

def _build_typescript_expect_error_placement_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    moved_comments: list[dict[str, int | str]] = []
    diagnostics_by_path = _typescript_expect_error_diagnostics_by_path(diagnostics)
    for path in sorted(diagnostics_by_path):
        original = str(base_files.get(path) or "")
        if not original:
            continue
        repaired, moved = _repair_typescript_expect_error_placement(
            original,
            diagnostics=diagnostics_by_path[path],
        )
        if repaired == original or not moved:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_expect_error_placement",
                    "moved_expect_error_comments": tuple(moved),
                },
            )
        )
        moved_comments.extend({"file": path, **item} for item in moved)

    matched_diagnostics = tuple(
        diagnostic
        for path_diagnostics in diagnostics_by_path.values()
        for diagnostic in path_diagnostics
        if _is_typescript_expect_error_placement_diagnostic(diagnostic)
    )
    return _repair_plan_or_none(
        rule_id="typescript.expect_error_placement",
        source_tool=TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"moved_comments": moved_comments},
    )

def _build_typescript_escaped_newline_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_typescript_escaped_newline_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = repair_typescript_escaped_newline_in_line_comments(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_escaped_newline_in_line_comment"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.escaped_newline",
        source_tool=TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )

def _parse_typescript_escaped_newline_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        typed_path = _typed_typescript_escaped_newline_path(diagnostic)
        if typed_path:
            paths.append(typed_path)
            continue
        match = _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)

def _typed_typescript_escaped_newline_path(diagnostic: RepairDiagnostic) -> str:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not path:
        return ""
    code = str(diagnostic.code or "").casefold()
    if "escaped_newline" in code:
        return path
    metadata_kind = str(diagnostic.metadata.get("issue_kind") or diagnostic.metadata.get("archetype") or "").casefold()
    if "escaped_newline" in metadata_kind:
        return path
    return ""

def repair_typescript_escaped_newline_in_line_comments(text: str) -> str:
    changed = False
    repaired_lines: list[str] = []

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group('prefix')}\n{match.group('code')}"

    for line in str(text or "").splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if "//" not in line_body or "\\n" not in line_body:
            repaired_lines.append(line)
            continue
        comment_index = line_body.find("//")
        prefix = line_body[:comment_index]
        repaired_lines.append(
            f"{prefix}{_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE.sub(replace, line_body[comment_index:])}{newline}"
        )
    return "".join(repaired_lines) if changed else str(text or "")

def _parse_duplicate_function_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[str]]:
    by_path: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_DUPLICATE_FUNCTION_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            name = str(match.group("name") or "").strip()
            if path and name and _TS_IDENTIFIER_RE.fullmatch(name):
                by_path.setdefault(path, set()).add(name)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        code = str(diagnostic.code or "").lower()
        lowered = text.lower()
        if path and (
            "ts2393" in code
            or "ts2323" in code
            or "duplicate function implementation" in lowered
            or "cannot redeclare exported variable" in lowered
        ):
            name_match = re.search(
                r"(?:exported variable|function)\s+['\"](?P<name>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
                text,
                re.IGNORECASE,
            )
            # TS2393 often has no name in message; recover from source line later via all funcs
            if name_match:
                name = str(name_match.group("name") or "").strip()
                if _TS_IDENTIFIER_RE.fullmatch(name):
                    by_path.setdefault(path, set()).add(name)
            elif "duplicate function implementation" in lowered and diagnostic.line:
                # Defer name resolution to source at diagnostic line
                by_path.setdefault(path, set()).add(f"__line__{int(diagnostic.line)}")
    # Resolve __line__ tokens using nothing here — resolved in operation builder via content
    return by_path

def _resolve_duplicate_function_name(*, content: str, name: str) -> str:
    """Resolve a diagnostic name token (including ``__line__N``) to a function id."""

    if not name.startswith("__line__"):
        return name if _TS_IDENTIFIER_RE.fullmatch(name) else ""
    try:
        line_hint = int(name.removeprefix("__line__"))
    except ValueError:
        return ""
    lines = content.splitlines()
    if line_hint <= 0 or line_hint > len(lines):
        return ""
    for offset in range(0, 4):
        idx = line_hint - 1 - offset
        if idx < 0:
            break
        decl = _TS_FUNCTION_DECL_RE.search(lines[idx])
        if decl is not None:
            candidate = str(decl.group("name") or "").strip()
            if _TS_IDENTIFIER_RE.fullmatch(candidate):
                return candidate
    return ""

def _duplicate_function_removal_operation(
    *,
    path: str,
    content: str,
    name: str,
) -> RepairOperation | None:
    """Remove the last complete function declaration of ``name`` when duplicates exist."""

    resolved_name = _resolve_duplicate_function_name(content=content, name=name)
    if not _TS_IDENTIFIER_RE.fullmatch(resolved_name):
        return None

    spans: list[tuple[int, int]] = []
    for match in _TS_FUNCTION_DECL_RE.finditer(content):
        if str(match.group("name") or "") != resolved_name:
            continue
        start = match.start("full")
        end = _function_body_end_offset(content, match.end("full") - 1)
        if end is None or end <= start:
            continue
        spans.append((start, end))
    if len(spans) < 2:
        return None
    # Drop the last duplicate (usually a trailing stub after a real implementation).
    start, end = spans[-1]
    # Expand to include trailing newline
    if end < len(content) and content[end] == "\n":
        end += 1
    expected = content[start:end]
    if not expected.strip():
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=end,
        expected=expected,
        replacement="",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_duplicate_function_removal",
            "function_name": resolved_name,
            "kept_declaration_count": len(spans) - 1,
        },
    )

def _typescript_expect_error_diagnostics_by_path(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, list[RepairDiagnostic]]:
    grouped: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_expect_error_placement_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
            continue
        grouped.setdefault(path, []).append(diagnostic)
    return grouped

def _is_typescript_expect_error_placement_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    code = str(diagnostic.code or "").lower()
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if code == "typescript_ts2578":
        return "@ts-expect-error" in text and "unused" in text
    if code == "typescript_ts2345":
        return "argument of type" in text and "not assignable to parameter of type" in text
    return False

def _repair_typescript_expect_error_placement(
    text: str,
    *,
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, tuple[dict[str, int | str], ...]]:
    lines = str(text or "").splitlines(keepends=True)
    unused_lines = sorted(
        {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if str(diagnostic.code or "").lower() == "typescript_ts2578" and int(diagnostic.line or 0) > 0
        }
    )
    assignability_lines = sorted(
        {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if str(diagnostic.code or "").lower() == "typescript_ts2345" and int(diagnostic.line or 0) > 0
        }
    )
    if not unused_lines or not assignability_lines:
        return str(text or ""), ()

    remove_indices: set[int] = set()
    insertions: dict[int, list[str]] = {}
    moved: list[dict[str, int | str]] = []
    for unused_line in unused_lines:
        unused_index = unused_line - 1
        if unused_index < 0 or unused_index >= len(lines):
            continue
        comment_line = lines[unused_index]
        if "@ts-expect-error" not in comment_line:
            continue
        target_line = next((line for line in assignability_lines if unused_line < line <= unused_line + 5), 0)
        target_index = target_line - 1
        if target_index < 0 or target_index >= len(lines):
            continue
        if _typescript_previous_line_is_expect_error(lines, target_index):
            continue
        target_indent = re.match(r"^\s*", lines[target_index])
        indent = target_indent.group(0) if target_indent else ""
        comment_text = comment_line.strip()
        newline = "\n" if comment_line.endswith("\n") else ""
        insertions.setdefault(target_index, []).append(f"{indent}{comment_text}{newline}")
        remove_indices.add(unused_index)
        moved.append({"from_line": unused_line, "to_line": target_line, "comment": comment_text})

    if not moved:
        return str(text or ""), ()

    repaired_lines: list[str] = []
    for index, line in enumerate(lines):
        repaired_lines.extend(insertions.get(index, ()))
        if index not in remove_indices:
            repaired_lines.append(line)
    return "".join(repaired_lines), tuple(moved)

def _typescript_previous_line_is_expect_error(lines: Sequence[str], target_index: int) -> bool:
    previous_index = target_index - 1
    while previous_index >= 0:
        previous = str(lines[previous_index] or "").strip()
        if not previous:
            previous_index -= 1
            continue
        return "@ts-expect-error" in previous
    return False

def _is_typescript_test_block_residue_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return diagnostic.code.lower() == "typescript_ts1128" and "declaration or statement expected" in message

def _typescript_test_block_residue_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
    seen_spans: set[tuple[str, int, int]],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    for line_number in sorted(line_numbers):
        diagnostic_index = line_number - 1
        if diagnostic_index < 0 or diagnostic_index >= len(lines):
            continue
        residue_start = _find_typescript_test_block_residue_suffix_start(lines, diagnostic_index)
        if residue_start is None:
            continue
        start = offsets[residue_start]
        end = offsets[len(lines)]
        span_key = (path, start, end)
        if span_key in seen_spans:
            continue
        expected = content[start:end]
        if not expected.strip():
            continue
        seen_spans.add(span_key)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_test_block_residue",
                    "edit_strategy": "text_replace",
                    "start_line": residue_start + 1,
                    "end_line": len(lines),
                    "diagnostic_line": line_number,
                },
            )
        )
    return tuple(operations)

def _find_typescript_test_block_residue_suffix_start(lines: Sequence[str], diagnostic_index: int) -> int | None:
    diagnostic_line = lines[diagnostic_index].rstrip("\r\n")
    if not _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(diagnostic_line):
        return None
    search_start = max(0, diagnostic_index - 24)
    for index in range(search_start, diagnostic_index + 1):
        line_body = lines[index].rstrip("\r\n")
        if not re.match(r"^\s{2,}(?:assert\.|expect\()", line_body):
            continue
        if _typescript_test_block_residue_suffix_is_safe(lines, index, diagnostic_index):
            return index
    return None

def _typescript_test_block_residue_suffix_is_safe(
    lines: Sequence[str],
    start_index: int,
    diagnostic_index: int,
) -> bool:
    prefix = "".join(lines[:start_index])
    suffix = "".join(lines[start_index:])
    if _typescript_brace_balance_delta(prefix) != 0:
        return False
    if _typescript_brace_balance_delta(suffix) >= 0:
        return False
    residue_lines = [line.rstrip("\r\n") for line in lines[start_index:]]
    nonblank_lines = [line for line in residue_lines if line.strip()]
    if not nonblank_lines:
        return False
    if not any(_TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(line) for line in nonblank_lines):
        return False
    if not any(
        index >= diagnostic_index and _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(lines[index].rstrip("\r\n"))
        for index in range(start_index, len(lines))
    ):
        return False
    return all(
        _TS_TEST_BLOCK_RESIDUE_STATEMENT_RE.match(line) or _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(line)
        for line in nonblank_lines
    )

def build_typescript_truncated_eof_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Close truncated TypeScript files ending mid-declaration (R168 Flower.ts).

    Content-driven (and TS1005-assisted): when a ``.ts`` file ends mid-token /
    mid-signature with unbalanced braces/parens, append a conservative closer so
    ``tsc`` can parse again. Prefer diagnostic paths when present.
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    candidate_paths: list[str] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        code = str(diagnostic.code or "").lower()
        raw = str(diagnostic.raw or diagnostic.message or "").lower()
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if code in {"typescript_ts1005", "typescript_ts1003", "typescript_ts1109", "typescript_ts1128"} or (
            "expected" in raw and path.endswith((".ts", ".tsx"))
        ):
            if path and path in normalized_base:
                candidate_paths.append(path)
                matched.append(diagnostic)
    if not candidate_paths:
        # Content-driven scan for truncated source files.
        for path, content in normalized_base.items():
            if not path.endswith((".ts", ".tsx")):
                continue
            if _typescript_file_looks_truncated(content):
                candidate_paths.append(path)
    operations: list[RepairOperation] = []
    for path in dict.fromkeys(candidate_paths):
        content = str(normalized_base.get(path) or "")
        op = _typescript_truncated_eof_operation(path=path, content=content)
        if op is not None:
            operations.append(op)
    return _repair_plan_or_none(
        rule_id="typescript.truncated_eof",
        source_tool=TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"truncated_eof_repairs": len(operations)},
    )

def _typescript_truncated_eof_operation(*, path: str, content: str) -> RepairOperation | None:
    if not _typescript_file_looks_truncated(content):
        return None
    stripped = content.rstrip()
    # Complete mid-signature: public consume(am  → public consume(amount: number): number { return 0; }
    last_line_start = stripped.rfind("\n") + 1
    last_line = stripped[last_line_start:]
    closer_parts: list[str] = []
    method_match = re.match(
        r"^(?P<indent>\s*)(?:public|private|protected|static|async|export|abstract|override|\s)*"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)$",
        last_line,
    )
    if method_match and "(" in last_line and ")" not in last_line:
        indent = str(method_match.group("indent") or "  ")
        name = str(method_match.group("name") or "method")
        # Replace incomplete signature line with stub method.
        replacement_line = f"{indent}public {name}(..._args: unknown[]): unknown {{\n{indent}  return undefined as unknown;\n{indent}}}\n"
        # If class/interface still open, close remaining braces after.
        body = stripped[:last_line_start] + replacement_line
        depth_brace = body.count("{") - body.count("}")
        depth_paren = body.count("(") - body.count(")")
        if depth_paren > 0:
            body += ")" * depth_paren
        if depth_brace > 0:
            body += "\n" + "}\n" * depth_brace
        if not body.endswith("\n"):
            body += "\n"
        return RepairOperation(
            kind="write_file",
            path=path,
            content=body,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_truncated_eof",
                "write_file_reason": "truncated_mid_signature",
                "method": name,
            },
        )
    # Generic balance closer
    body = stripped
    if not body.endswith("\n"):
        body += "\n"
    depth_paren = body.count("(") - body.count(")")
    depth_brace = body.count("{") - body.count("}")
    depth_bracket = body.count("[") - body.count("]")
    if depth_paren <= 0 and depth_brace <= 0 and depth_bracket <= 0 and body.rstrip().endswith(("}", ";")):
        return None
    # If last line is incomplete token, comment it out then close.
    last = body.rstrip().splitlines()[-1] if body.rstrip().splitlines() else ""
    if last and not last.strip().endswith(("}", ";", ",", "{", ")", "]", "*/")):
        # comment incomplete last line
        lines = body.splitlines(keepends=True)
        lines[-1] = f"// polaris-truncated-eof: {lines[-1].lstrip()}"
        if not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        body = "".join(lines)
        depth_paren = body.count("(") - body.count(")")
        depth_brace = body.count("{") - body.count("}")
        depth_bracket = body.count("[") - body.count("]")
    if depth_paren > 0:
        body += ")" * depth_paren + "\n"
    if depth_bracket > 0:
        body += "]" * depth_bracket + "\n"
    if depth_brace > 0:
        body += "}\n" * depth_brace
    if body == content:
        return None
    return RepairOperation(
        kind="write_file",
        path=path,
        content=body if body.endswith("\n") else body + "\n",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_truncated_eof",
            "write_file_reason": "truncated_unbalanced_eof",
        },
    )

__all__ = (
    "build_typescript_duplicate_function_plan",
    "_build_typescript_test_block_residue_plan",
    "_build_typescript_expect_error_placement_plan",
    "_build_typescript_escaped_newline_plan",
    "_parse_typescript_escaped_newline_paths",
    "_typed_typescript_escaped_newline_path",
    "repair_typescript_escaped_newline_in_line_comments",
    "_parse_duplicate_function_targets",
    "_resolve_duplicate_function_name",
    "_duplicate_function_removal_operation",
    "_typescript_expect_error_diagnostics_by_path",
    "_is_typescript_expect_error_placement_diagnostic",
    "_repair_typescript_expect_error_placement",
    "_typescript_previous_line_is_expect_error",
    "_is_typescript_test_block_residue_diagnostic",
    "_typescript_test_block_residue_operations",
    "_find_typescript_test_block_residue_suffix_start",
    "_typescript_test_block_residue_suffix_is_safe",
    "build_typescript_truncated_eof_plan",
    "_typescript_truncated_eof_operation",
)
