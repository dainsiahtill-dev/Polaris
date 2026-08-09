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
from .parse_ops import *  # noqa: F403
from .misc_ops import *  # noqa: F403

"""Shared TypeScript repair helpers: arg_shape_ops."""

def _strip_javascript_callable_type_match(match: re.Match[str]) -> str:
    params = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        default = ""
        head = param
        if "=" in param:
            head, default_value = param.split("=", 1)
            default = " = " + default_value.strip()
        head = re.sub(r"^(?P<name>\.\.\.[A-Za-z_$][\w$]*|[A-Za-z_$][\w$]*)\s*:\s*[^=,]+$", r"\g<name>", head.strip())
        params.append(f"{head}{default}")
    return f"{match.group('prefix')}({', '.join(params)}){match.group('brace')}"

def _parse_typescript_unused_declaration_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_UNUSED_DECLARATION_ERROR_RE.finditer(raw):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "name": str(match.group("name") or ""),
            }
            key = (item["file"], item["line"], item["column"], item["name"])
            if item["file"] and item["line"] and item["name"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return [item for item in parsed if item["file"] and item["line"] and item["name"]]

def _typescript_unused_parameter_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]]]:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    items = _parse_typescript_unused_declaration_errors(diagnostics)
    named_import_operations, named_import_repairs, consumed_item_keys = (
        _typescript_unused_named_import_binding_group_operations(base_files=base_files, items=items)
    )
    operations.extend(named_import_operations)
    repairs.extend(named_import_repairs)
    for item in items:
        if _typescript_unused_declaration_item_key(item) in consumed_item_keys:
            continue
        path = item["file"]
        name = item["name"]
        content = str(base_files.get(path) or "")
        line_number = _to_positive_int(item.get("line"))
        column = _to_positive_int(item.get("column"))
        operation = _typescript_unused_import_declaration_operation(
            path=path,
            content=content,
            name=name,
            line_number=line_number,
        )
        if operation is None:
            operation = _typescript_unused_parameter_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
                column=column,
            )
        if operation is None:
            operation = _typescript_unused_function_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            operation = _typescript_unused_local_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            continue
        operations.append(operation)
        repairs.append({"file": path, "parameter": name, "replacement": str(operation.replacement or "")})
    return tuple(operations), repairs

def _typescript_unused_declaration_item_key(item: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        str(item.get("file") or ""),
        str(item.get("line") or ""),
        str(item.get("column") or ""),
        str(item.get("name") or ""),
    )

def _typescript_unused_named_import_binding_group_operations(
    *,
    base_files: Mapping[str, str],
    items: Sequence[Mapping[str, str]],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]], set[tuple[str, str, str, str]]]:
    grouped: dict[tuple[str, int, int], dict[str, object]] = {}
    consumed_item_keys: set[tuple[str, str, str, str]] = set()
    for item in items:
        path = str(item.get("file") or "")
        name = str(item.get("name") or "")
        line_number = _to_positive_int(item.get("line"))
        content = str(base_files.get(path) or "")
        if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
            continue
        for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
            start_line = content.count("\n", 0, match.start()) + 1
            end_line = content.count("\n", 0, match.end()) + 1
            if line_number < start_line or line_number > end_line:
                continue
            pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
            if len(pairs) <= 1 or not any(local == name for _, local in pairs):
                continue
            group_key = (path, match.start(), match.end())
            group = grouped.setdefault(
                group_key,
                {
                    "content": content,
                    "import_text": content[match.start() : match.end()],
                    "module_specifier": str(match.group("module") or ""),
                    "names": set(),
                    "lines": [],
                },
            )
            names = group["names"]
            lines = group["lines"]
            if isinstance(names, set):
                names.add(name)
            if isinstance(lines, list):
                lines.append(line_number)
            consumed_item_keys.add(_typescript_unused_declaration_item_key(item))
            break

    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    for (path, start, end), group in sorted(grouped.items()):
        content = str(group.get("content") or "")
        import_text = str(group.get("import_text") or "")
        raw_names = group.get("names", set())
        raw_lines = group.get("lines", [])
        names = {str(name) for name in raw_names if str(name)} if isinstance(raw_names, set) else set()
        diagnostic_lines = (
            [int(line) for line in raw_lines if isinstance(line, int) and int(line) > 0]
            if isinstance(raw_lines, list)
            else []
        )
        if not content or not import_text or not names:
            continue
        replacement = _remove_typescript_named_import_bindings(import_text=import_text, names=names)
        if replacement == import_text:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=import_text,
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_unused_import_specifier",
                    "compiler_reported_unused_binding": True,
                    "bindings": tuple(sorted(names)),
                    "module_specifier": str(group.get("module_specifier") or ""),
                    "diagnostic_lines": tuple(diagnostic_lines),
                },
            )
        )
        repairs.extend({"file": path, "parameter": name, "replacement": replacement} for name in sorted(names))
    return tuple(operations), repairs, consumed_item_keys

def _typescript_unused_import_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    operation = _typescript_unused_named_import_binding_operation(
        path=path,
        content=content,
        name=name,
        line_number=line_number,
    )
    if operation is not None:
        return operation
    for match in _TS_IMPORT_FROM_ANY_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause(str(match.group("clause") or ""))
        if len(pairs) != 1 or pairs[0][1] != name:
            continue
        start, end = match.span()
        if end < len(content) and content[end : end + 1] == "\n":
            end += 1
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=start,
            span_end=end,
            expected=content[start:end],
            replacement="",
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("specifier") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None

def _typescript_unused_named_import_binding_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        import_text = content[match.start() : match.end()]
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if len(pairs) <= 1 or not any(local == name for _, local in pairs):
            continue
        replacement = _remove_typescript_named_import_binding(import_text=import_text, name=name)
        if not replacement or replacement == import_text:
            continue
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=import_text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import_specifier",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("module") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None

def _remove_typescript_named_import_binding(*, import_text: str, name: str) -> str:
    return _remove_typescript_named_import_bindings(import_text=import_text, names={name})

def _remove_typescript_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    normalized_names = {name for name in names if _TS_IDENTIFIER_RE.fullmatch(name)}
    if not normalized_names:
        return import_text
    if "\n" in import_text:
        replacement = _remove_typescript_multiline_named_import_bindings(
            import_text=import_text,
            names=normalized_names,
        )
        if replacement != import_text:
            return replacement
    match = _TS_REEXPORTABLE_NAMED_IMPORT_RE.fullmatch(import_text)
    if match is None:
        return import_text
    names_clause = str(match.group("names") or "")
    kept_parts: list[str] = []
    removed = False
    for raw_part in names_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        local = _typescript_named_import_local_name(part)
        if local in normalized_names:
            removed = True
            continue
        kept_parts.append(part)
    if not removed:
        return import_text
    if not kept_parts:
        return ""
    return (
        f"{match.group('indent') or ''}import "
        f"{match.group('type_only') or ''}{{ {', '.join(kept_parts)} }} "
        f"from {match.group('quote')}{match.group('module')}{match.group('quote')};"
    )

def _remove_typescript_multiline_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    lines = import_text.splitlines(keepends=True)
    kept_lines: list[str] = []
    removed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        part = line_body.strip().rstrip(",").strip()
        if not part or _typescript_named_import_local_name(part) not in names:
            kept_lines.append(line)
            continue
        removed = True
    if not removed:
        return import_text
    remaining_names = [
        _typescript_named_import_local_name(line.strip().rstrip(",").strip())
        for line in kept_lines
        if _typescript_named_import_local_name(line.strip().rstrip(",").strip())
    ]
    if not remaining_names:
        return ""
    return "".join(kept_lines)

def _typescript_named_import_local_name(part: str) -> str:
    normalized = str(part or "").strip().rstrip(",").strip()
    if normalized.startswith("type "):
        normalized = normalized[5:].strip()
    alias_parts = re.split(r"\s+as\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
    local = alias_parts[-1].strip()
    return local if _TS_IDENTIFIER_RE.fullmatch(local) else ""

def _typescript_unused_local_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    original_line = lines[line_index]
    line_body = original_line.rstrip("\r\n")
    newline = original_line[len(line_body) :]
    match = _TS_UNUSED_LOCAL_DECLARATION_LINE_RE.match(line_body)
    if match is None or str(match.group("name") or "") != name:
        return None
    expression = str(match.group("expr") or "").strip()
    if not expression or _typescript_unused_local_expression_requires_binding(expression):
        return None
    replacement = f"{match.group('indent') or ''!s}{expression};{newline}"
    if replacement == original_line:
        return None
    return _line_text_replace_operation(
        path=path,
        content=content,
        line_index=line_index,
        replacement=replacement,
        metadata={
            "repair_kind": "typescript_unused_local_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "initializer_expression_statement",
        },
    )

def _typescript_unused_function_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    first_line = lines[line_index].rstrip("\r\n")
    if first_line.lstrip().startswith("export "):
        return None
    match = _TS_UNUSED_FUNCTION_DECLARATION_LINE_RE.match(first_line)
    if match is None or str(match.group("name") or "") != name:
        return None
    offsets = _line_start_offsets(lines)
    brace_depth = 0
    saw_open_brace = False
    end_line_index = -1
    for current_index in range(line_index, len(lines)):
        line = lines[current_index]
        if "{" in line:
            saw_open_brace = True
        brace_depth += line.count("{") - line.count("}")
        if saw_open_brace and brace_depth <= 0:
            end_line_index = current_index
            break
    if not saw_open_brace or end_line_index < line_index:
        return None
    span_start = offsets[line_index]
    span_end = offsets[end_line_index + 1]
    expected = content[span_start:span_end]
    if not expected.strip():
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement="",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_unused_function_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "delete_non_exported_function_declaration",
        },
    )

def _typescript_unused_local_expression_requires_binding(expression: str) -> bool:
    stripped = str(expression or "").lstrip()
    if not stripped:
        return True
    if stripped.startswith(("{", "function ", "class ", "interface ", "type ")):
        return True
    return "=>" in stripped

def _typescript_unused_parameter_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
    column: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    candidate_indexes: list[int] = []
    if 0 <= line_index < len(lines):
        candidate_indexes.append(line_index)
    candidate_indexes.extend(index for index in range(len(lines)) if index not in candidate_indexes)
    for candidate_index in candidate_indexes:
        original_line = lines[candidate_index]
        repaired_line = _typescript_unused_parameter_line_replacement(
            line=original_line,
            name=name,
            column=column if candidate_index == line_index else 0,
        )
        if not repaired_line:
            repaired_line = _typescript_unused_multiline_parameter_line_replacement(
                lines=lines,
                line_index=candidate_index,
                name=name,
                column=column if candidate_index == line_index else 0,
            )
        if not repaired_line or repaired_line == original_line:
            continue
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=candidate_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_unused_parameter",
                "parameter": name,
                "replacement": f"_{name}",
                "diagnostic_line": line_number,
                "matched_line": candidate_index + 1,
            },
        )
    return None

def _typescript_unused_parameter_line_replacement(*, line: str, name: str, column: int) -> str:
    if name.startswith("_") or f"_{name}" in line:
        return ""
    occurrences = list(re.finditer(rf"\b{re.escape(name)}\b", line))
    if not occurrences:
        return ""
    column_index = max(0, column - 1)
    occurrences.sort(key=lambda match: abs(match.start() - column_index))
    for match in occurrences:
        if not _typescript_identifier_occurrence_is_parameter(line, match.start(), match.end()):
            continue
        return f"{line[: match.start()]}_{name}{line[match.end() :]}"
    return ""

def _typescript_unused_multiline_parameter_line_replacement(
    *,
    lines: Sequence[str],
    line_index: int,
    name: str,
    column: int,
) -> str:
    if name.startswith("_") or line_index < 0 or line_index >= len(lines):
        return ""
    line = lines[line_index]
    if f"_{name}" in line:
        return ""
    occurrences = list(re.finditer(rf"\b{re.escape(name)}\b", line))
    if not occurrences:
        return ""
    column_index = max(0, column - 1)
    occurrences.sort(key=lambda match: abs(match.start() - column_index))
    for match in occurrences:
        if not _typescript_identifier_occurrence_has_parameter_shape(line, match.start(), match.end()):
            continue
        if not _typescript_identifier_occurrence_is_in_multiline_parameter_list(
            lines=lines,
            line_index=line_index,
            start=match.start(),
            end=match.end(),
        ):
            continue
        return f"{line[: match.start()]}_{name}{line[match.end() :]}"
    return ""

def _typescript_identifier_occurrence_is_parameter(line: str, start: int, end: int) -> bool:
    open_index = line.rfind("(", 0, start)
    close_index = line.find(")", end)
    if open_index < 0 or close_index < 0:
        return False
    segment_before = line[open_index + 1 : start]
    segment_after = line[end:close_index]
    if "{" in segment_before or "}" in segment_before:
        return False
    before_token = segment_before.rsplit(",", 1)[-1].strip()
    if before_token:
        return False
    tail = segment_after.lstrip()
    return not tail or tail.startswith((":", "?", "=", ","))

def _typescript_identifier_occurrence_has_parameter_shape(line: str, start: int, end: int) -> bool:
    before = line[:start].strip()
    if before:
        modifier_tokens = before.split()
        allowed_modifiers = {"public", "private", "protected", "readonly", "override"}
        if any(token not in allowed_modifiers for token in modifier_tokens):
            return False
    tail = line[end:].lstrip()
    return not tail or tail.startswith((":", "?", "=", ","))

def _typescript_identifier_occurrence_is_in_multiline_parameter_list(
    *,
    lines: Sequence[str],
    line_index: int,
    start: int,
    end: int,
) -> bool:
    window_start = max(0, line_index - 20)
    window_end = min(len(lines), line_index + 21)
    before = "".join(lines[window_start:line_index]) + lines[line_index][:start]
    after = lines[line_index][end:] + "".join(lines[line_index + 1 : window_end])
    open_index = before.rfind("(")
    if open_index < 0:
        return False
    segment_since_open = before[open_index + 1 :]
    if ")" in segment_since_open or ";" in segment_since_open:
        return False
    close_index = after.find(")")
    return close_index >= 0

def _line_text_replace_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    replacement: str,
    metadata: Mapping[str, object],
) -> RepairOperation:
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=offsets[line_index],
        span_end=offsets[line_index + 1],
        expected=lines[line_index],
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata=dict(metadata),
    )

def _too_many_arguments_declaration_operation(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
) -> RepairOperation | None:
    if expected_count != 0:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_line = declaration
    repaired_line = _add_rest_param_to_typescript_callable(declaration_line.rstrip("\r\n"))
    if repaired_line == declaration_line.rstrip("\r\n"):
        return None
    newline = declaration_line[len(declaration_line.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=str(base_files[declaration_path]),
        line_index=declaration_line_index,
        replacement=f"{repaired_line}{newline}",
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": method_name,
            "repair": "declaration_rest_parameter",
        },
    )

def _too_many_arguments_callsite_trim_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    method_name: str,
    expected_count: int,
    got_count: int,
    column: int,
    base_files: Mapping[str, str],
) -> RepairOperation | None:
    """Drop surplus call arguments by aligning identifiers to declaration params (R169).

    Live: ``paintFlowers(ctx, surface, garden, t)`` vs
    ``function paintFlowers(ctx, garden, t)`` — drop the unmatched middle ``surface``.
    """

    if got_count <= expected_count or expected_count <= 0:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        declaration = _find_unique_typescript_function_declaration_multiline(
            base_files=base_files,
            function_name=method_name,
            expected_count=expected_count,
        )
    if declaration is None:
        return None
    _, _, decl_header = declaration
    params = _typescript_function_param_names_from_header(decl_header)
    if len(params) != expected_count:
        return None
    lines = content.splitlines(keepends=True)
    line = lines[line_index]
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    column_index = max(0, int(column) - 1)
    call_re = re.compile(rf"\b{re.escape(method_name)}\s*\(")
    for match in call_re.finditer(line_body):
        open_index = line_body.find("(", match.start())
        close_index = _find_matching_paren(line_body, open_index)
        if close_index < 0 or not (match.start() <= column_index <= close_index):
            continue
        spans = _split_typescript_argument_spans(line_body, open_index + 1, close_index)
        if len(spans) != got_count:
            continue
        arg_texts = [line_body[start:end].strip() for start, end in spans]
        selected = _typescript_select_args_for_params(arg_texts, params)
        if selected is None or len(selected) != expected_count:
            continue
        repaired_args = ", ".join(selected)
        repaired_line = f"{line_body[: open_index + 1]}{repaired_args}{line_body[close_index:]}{newline}"
        if repaired_line == line:
            return None
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=line_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_too_many_arguments",
                "method": method_name,
                "repair": "callsite_drop_unmatched_args",
            },
        )
    return None

def _too_many_arguments_declaration_expand_operation(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
    got_count: int,
) -> RepairOperation | None:
    """Insert ``_extraN: unknown`` parameters so declaration accepts the call (R169)."""

    if got_count <= expected_count:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    multiline = False
    if declaration is None:
        declaration = _find_unique_typescript_function_declaration_multiline(
            base_files=base_files,
            function_name=method_name,
            expected_count=expected_count,
        )
        multiline = declaration is not None
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_header = declaration
    content = str(base_files.get(declaration_path) or "")
    if not content:
        return None
    if multiline:
        return _expand_multiline_function_params(
            path=declaration_path,
            content=content,
            function_name=method_name,
            expected_count=expected_count,
            got_count=got_count,
        )
    repaired = _insert_unknown_params_into_callable_header(
        declaration_header.rstrip("\r\n"),
        add_count=got_count - expected_count,
    )
    if repaired == declaration_header.rstrip("\r\n"):
        return None
    newline = declaration_header[len(declaration_header.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=content,
        line_index=declaration_line_index,
        replacement=f"{repaired}{newline}",
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": method_name,
            "repair": "declaration_insert_unknown_params",
        },
    )

def _typescript_function_param_names_from_header(header: str) -> list[str]:
    open_index = header.find("(")
    close_index = _find_matching_paren(header, open_index)
    if open_index < 0 or close_index < 0:
        return []
    params = _split_typescript_params(header[open_index + 1 : close_index])
    names: list[str] = []
    for param in params:
        name = param.strip().split(":", 1)[0].strip().rstrip("?").lstrip("...")
        if _TS_IDENTIFIER_RE.fullmatch(name):
            names.append(name)
    return names

def _typescript_select_args_for_params(
    arg_texts: Sequence[str],
    param_names: Sequence[str],
) -> list[str] | None:
    """Select a subsequence of call args that best matches declaration param names."""

    if not param_names or len(arg_texts) < len(param_names):
        return None
    # Prefer exact identifier matches for each param name.
    selected: list[str] = []
    used: set[int] = set()
    for param in param_names:
        found = None
        for index, arg in enumerate(arg_texts):
            if index in used:
                continue
            if _TS_IDENTIFIER_RE.fullmatch(arg.strip()) and arg.strip() == param:
                found = index
                break
        if found is None:
            # Fall back to first unused arg (positional fill for non-matching).
            for index in range(len(arg_texts)):
                if index not in used:
                    found = index
                    break
        if found is None:
            return None
        used.add(found)
        selected.append(arg_texts[found])
    # Require that at least one non-positional (name) match happened when surplus exists.
    if len(arg_texts) > len(param_names):
        name_hits = sum(
            1
            for param, arg in zip(param_names, selected, strict=False)
            if _TS_IDENTIFIER_RE.fullmatch(arg.strip()) and arg.strip() == param
        )
        if name_hits < max(1, len(param_names) - 1):
            return None
    return selected

def _find_unique_typescript_function_declaration_multiline(
    *,
    base_files: Mapping[str, str],
    function_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    """Find multi-line ``function name(\\n params \\n)`` with exact param count."""

    if not _TS_IDENTIFIER_RE.fullmatch(function_name):
        return None
    header_re = re.compile(
        rf"(?ms)^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        content = str(text or "")
        for match in header_re.finditer(content):
            open_index = content.find("(", match.start())
            close_index = _find_matching_paren(content, open_index)
            if open_index < 0 or close_index < 0:
                continue
            params = _split_typescript_params(content[open_index + 1 : close_index])
            if len(params) != expected_count:
                continue
            # header from line start through closing paren (and optional return type start)
            line_start = content.rfind("\n", 0, match.start()) + 1
            header = content[line_start : close_index + 1]
            line_index = content.count("\n", 0, line_start)
            matches.append((path, line_index, header))
    return matches[0] if len(matches) == 1 else None

def _insert_unknown_params_into_callable_header(header: str, *, add_count: int) -> str:
    if add_count <= 0:
        return header
    open_index = header.find("(")
    close_index = _find_matching_paren(header, open_index)
    if open_index < 0 or close_index < 0:
        return header
    params = _split_typescript_params(header[open_index + 1 : close_index])
    extras = [f"_extra{index + 1}: unknown" for index in range(add_count)]
    # Insert extras before the last param when possible (common surface padding).
    if len(params) >= 2:
        new_params = [*params[:-1], *extras, params[-1]]
    else:
        new_params = [*params, *extras]
    return header[: open_index + 1] + ", ".join(new_params) + header[close_index:]

def _expand_multiline_function_params(
    *,
    path: str,
    content: str,
    function_name: str,
    expected_count: int,
    got_count: int,
) -> RepairOperation | None:
    header_re = re.compile(
        rf"(?ms)^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
    )
    match = header_re.search(content)
    if match is None:
        return None
    open_index = content.find("(", match.start())
    close_index = _find_matching_paren(content, open_index)
    if open_index < 0 or close_index < 0:
        return None
    params_block = content[open_index + 1 : close_index]
    params = _split_typescript_params(params_block)
    if len(params) != expected_count:
        return None
    add_count = got_count - expected_count
    extras = [f"_extra{index + 1}: unknown" for index in range(add_count)]
    # Prefer multi-line style with trailing commas.
    indent_match = re.search(r"\n(?P<indent>[ \t]+)\S", params_block)
    indent = indent_match.group("indent") if indent_match else "  "
    if "\n" in params_block:
        body_params = [*params[:-1], *extras, params[-1]] if len(params) >= 2 else [*params, *extras]
        new_block = "\n" + ",\n".join(f"{indent}{item}" for item in body_params) + ",\n"
        # keep indent of closing paren line
        close_line_start = content.rfind("\n", 0, close_index) + 1
        close_indent = re.match(r"[ \t]*", content[close_line_start:close_index])
        close_pad = close_indent.group(0) if close_indent else ""
        new_block = "\n" + ",\n".join(f"{indent}{item}" for item in body_params) + f",\n{close_pad}"
    else:
        new_block = ", ".join([*params[:-1], *extras, params[-1]] if len(params) >= 2 else [*params, *extras])
    replacement = content[: open_index + 1] + new_block + content[close_index:]
    if replacement == content:
        return None
    return RepairOperation(
        kind="write_file",
        path=path,
        content=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": function_name,
            "repair": "declaration_insert_unknown_params_multiline",
            "write_file_reason": "too_many_arguments_expand_signature",
        },
    )

def _find_unique_typescript_function_declaration(
    *,
    base_files: Mapping[str, str],
    function_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(function_name):
        return None
    function_re = re.compile(
        rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*"
        r"\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        for line_index, line in enumerate(str(text or "").splitlines(keepends=True)):
            match = function_re.search(line.rstrip("\r\n"))
            if not match:
                continue
            params = _split_typescript_params(str(match.group("params") or ""))
            if len(params) == expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None

def _add_rest_param_to_typescript_callable(line: str) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index].strip()
    if "..._args" in params_text:
        return line
    separator = ", " if params_text else ""
    repaired_params = f"{params_text}{separator}..._args: unknown[]"
    return line[: open_index + 1] + repaired_params + line[close_index:]

def _add_defaults_to_typescript_method_params(line: str, *, got_count: int, expected_count: int) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index]
    params = _split_typescript_params(params_text)
    if len(params) < expected_count or got_count >= expected_count:
        return line
    changed = False
    for index in range(got_count, min(expected_count, len(params))):
        repaired = _typescript_param_with_default(params[index])
        if repaired != params[index]:
            params[index] = repaired
            changed = True
    if not changed:
        return line
    return line[: open_index + 1] + ", ".join(params) + line[close_index:]

def _typescript_param_with_default(param: str) -> str:
    if "=" in param:
        return param
    if ":" not in param:
        return f"{param} = undefined"
    name, annotation = param.split(":", 1)
    ts_type = annotation.strip()
    return f"{name.strip()}: {ts_type} = {_typescript_default_value_for_type(ts_type)}"

def _typescript_function_param_names_for_line(lines: Sequence[str], target_index: int) -> list[str]:
    for start_index in range(target_index, -1, -1):
        line_body = lines[start_index].rstrip("\r\n")
        match = _TS_FUNCTION_DECLARATION_LINE_RE.match(line_body) or _TS_ARROW_FUNCTION_DECLARATION_LINE_RE.match(
            line_body
        )
        if not match:
            continue
        if not _typescript_line_is_inside_scope(lines, start_index, target_index):
            continue
        return _parse_typescript_param_names(str(match.group("params") or ""))
    return []

def _typescript_line_is_inside_scope(lines: Sequence[str], start_index: int, target_index: int) -> bool:
    depth = 0
    for index in range(start_index, target_index + 1):
        line_body = lines[index].rstrip("\r\n")
        depth += line_body.count("{")
        depth -= line_body.count("}")
        if index < target_index and depth <= 0:
            return False
    return depth > 0

def _parse_typescript_param_names(params_text: str) -> list[str]:
    names: list[str] = []
    for raw_param in _split_typescript_params(params_text):
        param = raw_param.split("=", 1)[0].split(":", 1)[0].strip().removeprefix("...").strip()
        if _TS_IDENTIFIER_RE.fullmatch(param):
            names.append(param)
    return names

def _is_number_to_function_argument(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    if diagnostic.code.lower() == "typescript_ts2345" and "number" in message and "(n: number) => number" in message:
        return True
    return bool(_TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE.search(str(diagnostic.raw or diagnostic.message or "")))

def _has_number_to_function_argument_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    return any(_is_number_to_function_argument(diagnostic) for diagnostic in diagnostics)

def _remove_shorthand_properties(line: str, properties: set[str]) -> tuple[str, tuple[str, ...]]:
    if not properties or "{" not in line or "}" not in line:
        return line, ()
    open_index = line.find("{")
    close_index = line.rfind("}")
    if close_index <= open_index:
        return line, ()
    inner = line[open_index + 1 : close_index]
    if "{" in inner or "}" in inner:
        return line, ()
    parts = [part.strip() for part in inner.split(",")]
    kept: list[str] = []
    removed: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in properties and _TS_IDENTIFIER_RE.fullmatch(part):
            removed.append(part)
            continue
        kept.append(part)
    if not removed:
        return line, ()
    replacement_inner = f" {', '.join(kept)} " if kept else ""
    return f"{line[: open_index + 1]}{replacement_inner}{line[close_index:]}", tuple(sorted(removed))

def _wrap_typescript_argument_at_column_as_string(line: str, column: int) -> str:
    span = _find_typescript_argument_span_at_column(line, column)
    if span is None:
        return line
    start, end = span
    argument = line[start:end]
    stripped = argument.strip()
    if not stripped or stripped.startswith(("String(", '"', "'", "`")):
        return line
    leading = argument[: len(argument) - len(argument.lstrip())]
    trailing = argument[len(argument.rstrip()) :]
    replacement = f"{leading}String({stripped}){trailing}"
    return line[:start] + replacement + line[end:]

def _find_typescript_argument_span_at_column(line: str, column: int) -> tuple[int, int] | None:
    index = max(0, min(len(line), int(column) - 1))
    open_index = line.rfind("(", 0, index + 1)
    close_index = line.find(")", index)
    if open_index < 0 or close_index < 0 or close_index <= open_index:
        return None
    spans = _split_typescript_argument_spans(line, open_index + 1, close_index)
    for start, end in spans:
        if start <= index <= end:
            if "=>" in line[start:end]:
                return None
            return start, end
    return None

def _find_matching_paren(text: str, open_paren: int) -> int:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1

def _typescript_extract_argument_expression(line: str, col_index: int) -> str:
    text = str(line or "")
    if not text:
        return ""
    # Walk outward from column to capture identifier / member / call chain.
    start = min(max(0, col_index), len(text) - 1)
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._$"):
        start -= 1
    end = start
    depth_paren = 0
    depth_brace = 0
    depth_bracket = 0
    while end < len(text):
        ch = text[end]
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            if depth_paren == 0:
                break
            depth_paren -= 1
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            if depth_brace == 0:
                break
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            if depth_bracket == 0:
                break
            depth_bracket -= 1
        elif ch in {",", ";"} and depth_paren == 0 and depth_brace == 0 and depth_bracket == 0:
            break
        end += 1
    expr = text[start:end].strip()
    # Reject empty or keyword-only tokens.
    if not expr or expr in {"if", "return", "const", "let", "var"}:
        return ""
    return expr

def _typescript_param_type_from_property_operation(
    *,
    path: str,
    content: str,
    line: int,
    prop: str,
    candidate_types: Sequence[str],
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    # Find receiver of .prop on diagnostic line
    if line < 1 or line > len(lines):
        return None
    use_line = lines[line - 1]
    receiver_match = re.search(rf"\b([A-Za-z_$][\w$]*)\s*\.\s*{re.escape(prop)}\b", use_line)
    if receiver_match is None:
        return None
    receiver = str(receiver_match.group(1) or "")
    # Search enclosing function signature upward
    for idx in range(line - 1, max(-1, line - 80), -1):
        if idx < 0:
            continue
        text = lines[idx]
        if "function" not in text and "=>" not in text and "(" not in text:
            continue
        # multi-line signatures: join a small window
        window = "".join(lines[idx : min(len(lines), idx + 6)])
        param_re = re.compile(rf"([,(]\s*)({re.escape(receiver)})\s*:\s*(number|string|boolean)\b")
        param_match = param_re.search(window)
        if param_match is None:
            continue
        # Prefer candidate whose name relates to receiver (Humidity ~ humidityPercent)
        chosen = ""
        receiver_l = receiver.lower()
        for cand in candidate_types:
            if cand.lower() in receiver_l or receiver_l.replace("percent", "").replace("value", "") in cand.lower():
                chosen = cand
                break
        if not chosen and len(candidate_types) == 1:
            chosen = candidate_types[0]
        if not chosen:
            # Prefer types imported or declared in this file
            for cand in candidate_types:
                if re.search(rf"\b{re.escape(cand)}\b", content):
                    chosen = cand
                    break
        if not chosen:
            return None
        new_param = f"{param_match.group(1)}{param_match.group(2)}: {chosen}"
        window_start = sum(len(item) for item in lines[:idx])
        span_start = window_start + param_match.start()
        span_end = window_start + param_match.end()
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=content[span_start:span_end],
            replacement=new_param,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property",
                "parameter": receiver,
                "property": prop,
                "type_name": chosen,
                "diagnostic_line": line,
            },
        )
    return None


__all__ = (
    "_strip_javascript_callable_type_match",
    "_parse_typescript_unused_declaration_errors",
    "_typescript_unused_parameter_operations",
    "_typescript_unused_declaration_item_key",
    "_typescript_unused_named_import_binding_group_operations",
    "_typescript_unused_import_declaration_operation",
    "_typescript_unused_named_import_binding_operation",
    "_remove_typescript_named_import_binding",
    "_remove_typescript_named_import_bindings",
    "_remove_typescript_multiline_named_import_bindings",
    "_typescript_named_import_local_name",
    "_typescript_unused_local_declaration_operation",
    "_typescript_unused_function_declaration_operation",
    "_typescript_unused_local_expression_requires_binding",
    "_typescript_unused_parameter_operation",
    "_typescript_unused_parameter_line_replacement",
    "_typescript_unused_multiline_parameter_line_replacement",
    "_typescript_identifier_occurrence_is_parameter",
    "_typescript_identifier_occurrence_has_parameter_shape",
    "_typescript_identifier_occurrence_is_in_multiline_parameter_list",
    "_line_text_replace_operation",
    "_too_many_arguments_declaration_operation",
    "_too_many_arguments_callsite_trim_operation",
    "_too_many_arguments_declaration_expand_operation",
    "_typescript_function_param_names_from_header",
    "_typescript_select_args_for_params",
    "_find_unique_typescript_function_declaration_multiline",
    "_insert_unknown_params_into_callable_header",
    "_expand_multiline_function_params",
    "_find_unique_typescript_function_declaration",
    "_add_rest_param_to_typescript_callable",
    "_add_defaults_to_typescript_method_params",
    "_typescript_param_with_default",
    "_typescript_function_param_names_for_line",
    "_typescript_line_is_inside_scope",
    "_parse_typescript_param_names",
    "_is_number_to_function_argument",
    "_has_number_to_function_argument_diagnostic",
    "_remove_shorthand_properties",
    "_wrap_typescript_argument_at_column_as_string",
    "_find_typescript_argument_span_at_column",
    "_find_matching_paren",
    "_typescript_extract_argument_expression",
    "_typescript_param_type_from_property_operation",
)
