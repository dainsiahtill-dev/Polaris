# ruff: noqa: F403, F405
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def _build_typescript_value_used_as_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    parsed = _parse_typescript_value_used_as_type_errors(diagnostics)
    if not parsed:
        return None

    updated: dict[str, str] = dict(base_files)
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    for item in parsed:
        path = str(item.get("file") or "")
        symbol = str(item.get("symbol") or "")
        original = str(updated.get(path) or "")
        if not original or not _TS_IDENTIFIER_RE.fullmatch(symbol):
            continue
        if not _typescript_imported_const_class_alias_available(
            base_files=base_files,
            importer_path=path,
            importer_text=original,
            symbol=symbol,
        ):
            continue
        next_text, line_repaired = _replace_typescript_value_used_as_type_reference(
            original,
            line_number=_to_positive_int(item.get("line")),
            column_number=_to_positive_int(item.get("col")),
            symbol=symbol,
        )
        if next_text == original or not line_repaired:
            continue
        updated[path] = next_text
        diagnostic = item.get("diagnostic")
        if isinstance(diagnostic, RepairDiagnostic):
            matched_diagnostics.append(diagnostic)
        repaired.append({"file": path, "symbol": symbol, "line": item.get("line")})

    operations: list[RepairOperation] = []
    for path, repaired_text in updated.items():
        original = str(base_files.get(path) or "")
        if not original or repaired_text == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired_text,
                metadata={
                    "repair_kind": "typescript_value_used_as_type",
                    "strategy": "instance_type_of_exported_const_class_alias",
                },
            )
        )

    return _repair_plan_or_none(
        rule_id="typescript.value_used_as_type",
        source_tool=TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        metadata={"value_type_references": repaired},
    )


def _build_typescript_too_few_arguments_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    methods: list[dict[str, str]] = []
    updated = {str(path): str(content) for path, content in dict(base_files or {}).items()}
    touched: set[str] = set()
    for item in _parse_typescript_too_few_arguments_errors(diagnostics):
        operation = _too_few_arguments_operation(updated, item)
        if operation is None:
            continue
        path = str(operation.path or "")
        if not path or path not in updated:
            continue
        updated[path] = _apply_single_text_operation(updated[path], operation)
        touched.add(path)
        methods.append(
            {
                "file": path,
                "method": str(operation.metadata.get("method") or ""),
                "repair": str(operation.metadata.get("repair") or ""),
            }
        )
    # Collapse sequential same-file edits into one write_file so PatchComposer
    # does not reject intermediate before_hash values (R169 multi TS2554).
    operations: list[RepairOperation] = []
    for path in sorted(touched):
        original = str(base_files.get(path) or "")
        repaired = str(updated.get(path) or "")
        if repaired == original:
            continue
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired if repaired.endswith("\n") else f"{repaired}\n",
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "typescript_too_few_arguments",
                    "write_file_reason": "argument_count_repairs_collapsed",
                    "methods": [row for row in methods if row.get("file") == path],
                },
            )
        )
    return _repair_plan_or_none(
        rule_id="typescript.too_few_arguments",
        source_tool=TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"methods": methods},
    )


def _build_typescript_unresolved_identifier_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    identifiers: list[dict[str, str]] = []
    import_repairs: set[tuple[str, str]] = set()
    for item in _parse_typescript_cannot_find_name_errors(diagnostics):
        path = item["file"]
        original = str(base_files.get(path) or "")
        missing_symbol = item["symbol"]
        if not original or not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
            continue
        line_number = _to_positive_int(item.get("line"))
        repaired, replacement = _repair_typescript_unresolved_identifier_lines(
            original,
            target_line_number=line_number,
            missing_symbol=missing_symbol,
        )
        repair_kind = "typescript_unresolved_identifier_alias"
        if repaired == original or not replacement:
            if (path, missing_symbol) in import_repairs:
                continue
            repaired, replacement = _repair_typescript_unresolved_identifier_import(
                path=path,
                original=original,
                base_files=base_files,
                missing_symbol=missing_symbol,
            )
            repair_kind = "typescript_unresolved_identifier_import"
            if repaired == original or not replacement:
                continue
            import_repairs.add((path, missing_symbol))
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": repair_kind,
                    "symbol": missing_symbol,
                    "replacement": replacement,
                },
            )
        )
        identifiers.append({"file": path, "symbol": missing_symbol, "replacement": replacement})
    return _repair_plan_or_none(
        rule_id="typescript.unresolved_identifier",
        source_tool=TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"identifiers": identifiers},
    )


def _parse_typescript_value_used_as_type_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        matched = False
        for match in _TS_VALUE_USED_AS_TYPE_RAW_RE.finditer(text):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "col": str(match.group("col") or ""),
                    "symbol": str(match.group("symbol") or "").strip(),
                    "diagnostic": diagnostic,
                }
            )
            matched = True
        if matched:
            continue
        if diagnostic.code.lower() != "typescript_ts2749":
            continue
        message_match = _TS_VALUE_USED_AS_TYPE_MESSAGE_RE.search(text)
        if not message_match:
            continue
        parsed.append(
            {
                "file": _normalize_repair_path(str(diagnostic.path or "")),
                "line": "",
                "col": "",
                "symbol": str(message_match.group("symbol") or "").strip(),
                "diagnostic": diagnostic,
            }
        )
    return [
        item
        for item in parsed
        if str(item.get("file") or "") and _TS_IDENTIFIER_RE.fullmatch(str(item.get("symbol") or ""))
    ]


def _replace_typescript_value_used_as_type_reference(
    text: str,
    *,
    line_number: int,
    column_number: int,
    symbol: str,
) -> tuple[str, bool]:
    if line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, False
    lines = str(text or "").splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return text, False
    original_line = lines[line_index]
    if re.search(r"\bimport\b|\bexport\s+(?:const|class|function)\b", original_line):
        return text, False
    replacement = f"InstanceType<typeof {symbol}>"
    if replacement in original_line:
        return text, False
    symbol_re = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])")
    matches = list(symbol_re.finditer(original_line))
    if not matches:
        return text, False
    column_index = max(0, column_number - 1)
    selected = min(
        matches,
        key=lambda match: (
            0
            if match.start() <= column_index <= match.end()
            else min(abs(match.start() - column_index), abs(match.end() - column_index))
        ),
    )
    prefix = original_line[max(0, selected.start() - 16) : selected.start()]
    if re.search(r"typeof\s+$", prefix):
        return text, False
    lines[line_index] = original_line[: selected.start()] + replacement + original_line[selected.end() :]
    return "".join(lines), True


def _parse_typescript_too_few_arguments_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_TOO_FEW_ARGUMENTS_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append({key: str(match.group(key) or "") for key in ("file", "line", "col", "expected", "got")})
    return parsed


def _too_few_arguments_operation(base_files: Mapping[str, str], item: Mapping[str, str]) -> RepairOperation | None:
    path = _normalize_repair_path(str(item.get("file") or ""))
    content = str(base_files.get(path) or "")
    line_number = _to_positive_int(item.get("line"))
    column = _to_positive_int(item.get("col"))
    expected_count = _to_positive_int(item.get("expected"))
    got_count = _to_positive_int(item.get("got"))
    if not path or not content or line_number <= 0 or column <= 0 or expected_count == got_count:
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    usage_line = lines[line_index].rstrip("\r\n")
    method_name = _typescript_call_name_from_usage_line(usage_line, column)
    if not method_name:
        return None
    if expected_count < got_count:
        too_many = _too_many_arguments_callsite_trim_operation(
            path=path,
            content=content,
            line_index=line_index,
            method_name=method_name,
            expected_count=expected_count,
            got_count=got_count,
            column=column,
            base_files=base_files,
        )
        if too_many is not None:
            return too_many
        if expected_count == 0:
            return _too_many_arguments_declaration_operation(
                base_files=base_files,
                method_name=method_name,
                expected_count=expected_count,
            )
        too_many = _too_many_arguments_declaration_expand_operation(
            base_files=base_files,
            method_name=method_name,
            expected_count=expected_count,
            got_count=got_count,
        )
        if too_many is not None:
            return too_many
        return None
    callsite_operation = _too_few_arguments_callsite_operation(
        path=path,
        content=content,
        line_index=line_index,
        method_name=method_name,
        expected_count=expected_count,
        got_count=got_count,
        column=column,
    )
    if callsite_operation is not None:
        return callsite_operation
    declaration = _find_unique_typescript_method_declaration(
        base_files=base_files,
        method_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_line = declaration
    repaired_line = _add_defaults_to_typescript_method_params(
        declaration_line.rstrip("\r\n"),
        got_count=got_count,
        expected_count=expected_count,
    )
    if repaired_line == declaration_line.rstrip("\r\n"):
        return None
    newline = declaration_line[len(declaration_line.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=str(base_files[declaration_path]),
        line_index=declaration_line_index,
        replacement=f"{repaired_line}{newline}",
        metadata={
            "repair_kind": "typescript_too_few_arguments",
            "method": method_name,
            "repair": "declaration_default_parameters",
        },
    )


def _too_few_arguments_callsite_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    method_name: str,
    expected_count: int,
    got_count: int,
    column: int,
) -> RepairOperation | None:
    if method_name != "clamp" or expected_count != 3 or got_count != 2:
        return None
    lines = content.splitlines(keepends=True)
    line = lines[line_index]
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    column_index = max(0, int(column) - 1)
    for match in re.finditer(r"\bclamp\s*\(", line_body):
        open_index = line_body.find("(", match.start())
        close_index = _find_matching_paren(line_body, open_index)
        if close_index < 0 or not (match.start() <= column_index <= close_index):
            continue
        spans = _split_typescript_argument_spans(line_body, open_index + 1, close_index)
        if len(spans) != 2:
            continue
        first_arg = line_body[spans[0][0] : spans[0][1]].strip()
        second_arg = line_body[spans[1][0] : spans[1][1]].strip()
        if not first_arg or not second_arg:
            continue
        repaired_line = f"{line_body[: open_index + 1]}{first_arg}, 0, {second_arg}{line_body[close_index:]}{newline}"
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=line_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_too_few_arguments",
                "method": method_name,
                "repair": "callsite_insert_default_min_bound",
            },
        )
    return None


def _repair_typescript_unresolved_identifier_lines(
    text: str,
    *,
    target_line_number: int,
    missing_symbol: str,
) -> tuple[str, str]:
    if target_line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return str(text or ""), ""
    lines = str(text or "").splitlines(keepends=True)
    target_index = target_line_number - 1
    if target_index < 0 or target_index >= len(lines):
        return str(text or ""), ""
    line = lines[target_index]
    # A phantom array element type used only by ``(... as Type[]).length =``
    # is a narrow, local assertion repair. Handle it before the general
    # type-position import guard; no runtime symbol is required or invented.
    array_length_assertion = _typescript_unresolved_identifier_is_array_length_assertion(line, missing_symbol)
    # Type-position TS2304 must import a real binding. Fuzzy-aliasing to a
    # nearby function (FlightReport → renderFlightReport) is a live L1-08
    # regression, not a local structural repair.
    type_item = {"symbol": missing_symbol, "line": str(target_line_number)}
    if not array_length_assertion and _typescript_missing_identifier_usage_is_type_position(str(text or ""), type_item):
        return str(text or ""), ""
    replacement = (
        "unknown"
        if array_length_assertion
        else _select_typescript_unresolved_identifier_replacement(lines, target_index, missing_symbol)
    )
    if replacement:
        repaired_line = re.sub(rf"\b{re.escape(missing_symbol)}\b", replacement, line)
        if repaired_line == line:
            return str(text or ""), ""
        lines[target_index] = repaired_line
        return "".join(lines), replacement
    # R175/M10: phantom unary wrapper `deltaMult(decayAdjusted(...))` → unwrap call.
    unwrapped = _typescript_unwrap_phantom_call(line, missing_symbol)
    if unwrapped and unwrapped != line:
        lines[target_index] = unwrapped
        return "".join(lines), f"(unwrap:{missing_symbol})"
    return str(text or ""), ""


def _select_typescript_unresolved_identifier_replacement(
    lines: Sequence[str],
    target_index: int,
    missing_symbol: str,
) -> str:
    line = str(lines[target_index] or "") if 0 <= target_index < len(lines) else ""
    if _typescript_unresolved_identifier_is_array_length_assertion(line, missing_symbol):
        return "unknown"
    for param in _typescript_function_param_names_for_line(lines, target_index):
        if _typescript_identifier_alias_matches(missing_symbol, param):
            return param
    # R175/M10: local helpers renamed (deltaMult vs _decayMult / decayAdjusted).
    local_funcs = _typescript_local_function_names(lines)
    fuzzy = _typescript_best_local_function_alias(missing_symbol, local_funcs)
    if fuzzy:
        return fuzzy
    return ""


def _typescript_unresolved_identifier_is_array_length_assertion(line: str, missing_symbol: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return False
    pattern = _TS_UNRESOLVED_ARRAY_ASSERTION_LENGTH_ASSIGNMENT_TEMPLATE.format(symbol=re.escape(missing_symbol))
    return bool(re.search(pattern, str(line or "")))
