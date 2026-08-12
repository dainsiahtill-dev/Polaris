# ruff: noqa: F403, F405
from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def build_typescript_number_to_string_argument_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS2345 number-to-string arguments."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_number_to_string_argument_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _number_to_string_argument_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "line": str(operation.metadata.get("line") or ""),
                "columns": ",".join(str(column) for column in operation.metadata.get("columns") or ()),
            }
            for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.number_to_string_argument",
        source_tool=TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"arguments": repaired_items},
    )


def build_typescript_number_property_call_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS2349 repair plan for generated numeric property calls."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_number_property_call_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _number_property_call_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "line": int(operation.metadata.get("line") or 0),
                "call_expression": str(operation.metadata.get("call_expression") or ""),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.number_property_call",
        source_tool=TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"number_property_calls": repaired_items},
    )


def _parse_number_to_string_argument_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_number_to_string_argument(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path


def _parse_number_property_call_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_NUMBER_PROPERTY_CALL_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_number_property_call_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path


def _is_number_to_string_argument(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return (
        diagnostic.code.lower() == "typescript_ts2345"
        and "argument of type" in message
        and "number" in message
        and "not assignable" in message
        and "string" in message
    )


def _is_number_property_call_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return diagnostic.code.lower() == "typescript_ts2349" and "not callable" in message


def _number_to_string_argument_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    columns_by_line: dict[int, list[int]] = {}
    for line, column in targets:
        if line > 0 and column > 0:
            columns_by_line.setdefault(line, []).append(column)
    operations: list[RepairOperation] = []
    for line_number in sorted(columns_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        repaired_line_body = line_body
        for column in sorted(set(columns_by_line[line_number]), reverse=True):
            repaired_line_body = _wrap_typescript_argument_at_column_as_string(repaired_line_body, column)
        if repaired_line_body == line_body:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index],
                span_end=offsets[line_index + 1],
                expected=original_line,
                replacement=f"{repaired_line_body}{newline}",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_number_to_string_argument",
                    "line": line_number,
                    "columns": tuple(sorted(set(columns_by_line[line_number]))),
                },
            )
        )
    return tuple(operations)


def _number_property_call_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    columns_by_line: dict[int, set[int]] = {}
    for line, column in targets:
        if line > 0 and column > 0:
            columns_by_line.setdefault(line, set()).add(column)
    operations: list[RepairOperation] = []
    for line_number in sorted(columns_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        line_body = lines[line_index].rstrip("\r\n")
        candidate = _number_property_call_candidate(line_body, columns_by_line[line_number])
        if candidate is None:
            continue
        call_start, call_end, paren_start, paren_end = candidate
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index] + paren_start,
                span_end=offsets[line_index] + paren_end,
                expected=line_body[paren_start:paren_end],
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_number_property_call",
                    "line": line_number,
                    "columns": tuple(sorted(columns_by_line[line_number])),
                    "call_expression": line_body[call_start:call_end],
                    "unique_context": lines[line_index],
                },
            )
        )
    return tuple(operations)


def _number_property_call_candidate(
    line: str,
    columns: set[int],
) -> tuple[int, int, int, int] | None:
    matches = [
        match
        for match in _TS_ZERO_ARG_PROPERTY_CALL_RE.finditer(line)
        if _property_call_is_near_columns(match.start(), match.end(), columns)
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    paren_start = str(line).rfind("(", match.start(), match.end())
    if paren_start < 0:
        return None
    paren_end = str(line).find(")", paren_start)
    if paren_end < 0:
        return None
    return match.start(), match.end(), paren_start, paren_end + 1
