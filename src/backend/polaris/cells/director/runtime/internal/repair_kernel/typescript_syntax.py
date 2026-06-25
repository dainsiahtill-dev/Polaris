"""Canonical TypeScript syntax repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"
TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL = "deterministic_typescript_nullable_canvas_context_repair"
TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL = "deterministic_typescript_duplicate_object_property_repair"
TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL = "deterministic_typescript_enum_member_separator_repair"

_TS_INLINE_OBJECT_MISSING_COMMA_RE = re.compile(
    r"(?P<value>\b[A-Za-z_$][A-Za-z0-9_$]*\b|\)|\]|\}|['\"][^'\"]*['\"]|-?\d+(?:\.\d+)?)"
    r"(?P<gap>[ \t]{2,})"
    r"(?P<key>[A-Za-z_$][A-Za-z0-9_$]*\s*:)"
)
_TS_OBJECT_PROPERTY_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_$][A-Za-z0-9_$]*\s*:")
_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)\s*;\s*$")
_TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<property>(?:\[[^\]]+\]|[A-Za-z_$][\w$]*|['\"][^'\"]+['\"])\s*:\s*[^;{}]+);\s*$"
)
_TS_RETURN_OBJECT_START_RE = re.compile(r"\breturn\s*\{\s*$")
_TS_OBJECT_LITERAL_START_RE = re.compile(r"(?:\breturn\s*|=\s*)\{\s*$")
_TS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_TS_POSSIBLY_NULL_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18047:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)
_TS_POSSIBLY_NULL_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)
_TS_NULLABLE_ARGUMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\|\s*null['\"]\s+is\s+not\s+assignable\s+"
    r"to\s+parameter\s+of\s+type\s+['\"](?P=type)['\"]",
    re.IGNORECASE,
)
_TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1117:\s*"
    r"An\s+object\s+literal\s+cannot\s+have\s+multiple\s+properties\s+with\s+the\s+same\s+name",
    re.IGNORECASE,
)
_TS_ENUM_MEMBER_SEPARATOR_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1357:\s*"
    r"An\s+enum\s+member\s+name\s+must\s+be\s+followed\s+by\s+a\s+',',\s*'=',\s*or\s*'}'",
    re.IGNORECASE,
)
_TS_ENUM_DECLARATION_LINE_RE = re.compile(r"\benum\s+[A-Za-z_$][A-Za-z0-9_$]*\b[^{}]*{")
_TS_ENUM_MEMBER_LINE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\s*=\s*[^,;{}]+?)?)(?P<separator>[;,]?)(?P<space>\s*)(?P<comment>//.*)?$"
)
_TS_CANVAS_CONTEXT_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*[^;\n]*\.getContext\(\s*['\"]2d['\"]\s*\)\s*;?\s*$"
)
_TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<rhs>[^;\n]*\([^;\n]*\))\s*;?\s*$"
)
_TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<source>[^;\n]*"
    r"(?:document\.(?:getElementById|querySelector)|"
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
    r"\s*\([^;\n]*\)[^;\n]*)\s*;?\s*$"
)


def repair_typescript_object_literal_commas(text: str) -> str:
    """Repair narrow object-literal comma omissions reported as TS1005."""

    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    object_literal_depths: list[int] = []
    brace_depth = 0
    changed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        starts_object_literal = bool(
            _TS_RETURN_OBJECT_START_RE.search(line_body) or _TS_OBJECT_LITERAL_START_RE.search(line_body)
        )
        has_inline_missing_object_comma = bool(
            "{" in line_body and ":" in line_body and _TS_INLINE_OBJECT_MISSING_COMMA_RE.search(line_body)
        )
        in_object_literal = bool(object_literal_depths) or starts_object_literal or has_inline_missing_object_comma
        if in_object_literal:
            repaired_line = _repair_object_property_semicolon_line(line_body)
            if repaired_line == line_body:
                repaired_line = _repair_missing_object_property_comma_line(line_body)
            if repaired_line != line_body:
                repaired.append(f"{repaired_line}{newline}")
                changed = True
            else:
                if _object_property_line_needs_previous_comma(line_body, repaired):
                    repaired[-1] = _append_object_property_comma(repaired[-1])
                    changed = True
                repaired.append(line)
        else:
            repaired.append(line)

        opens = line_body.count("{")
        closes = line_body.count("}")
        if starts_object_literal:
            object_literal_depths.append(brace_depth + max(opens, 1))
        brace_depth += opens - closes
        while object_literal_depths and brace_depth < object_literal_depths[-1]:
            object_literal_depths.pop()
    return "".join(repaired) if changed else str(text or "")


def build_typescript_object_literal_comma_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1005 object literal comma omissions."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in normalized_base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path in sorted(diagnostics_by_path):
        original = str(normalized_base_files.get(path) or "")
        repaired = repair_typescript_object_literal_commas(original)
        if repaired == original:
            continue
        path_diagnostics = diagnostics_by_path[path]
        matched_diagnostics.extend(path_diagnostics)
        first_diagnostic = path_diagnostics[0]
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={
                    "line": first_diagnostic.line,
                    "column": first_diagnostic.column,
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                    "repair_kind": "typescript_object_literal_missing_comma",
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.object_literal_missing_comma",
        source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
    )


def repair_typescript_nullable_canvas_context_guards(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    """Repair nullable DOM/canvas handles by narrowing or adding explicit guards."""

    text, multiline_guarded = _repair_typescript_multiline_dom_handle_declarations(text, symbols)
    lines = text.splitlines()
    repaired_lines: list[str] = []
    guarded: list[str] = list(multiline_guarded)
    for index, line in enumerate(lines):
        match = _TS_CANVAS_CONTEXT_DECLARATION_LINE_RE.match(line)
        if match:
            symbol = str(match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_canvas_context_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("Canvas 2D context unavailable");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        dom_match = _TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE.match(line)
        if dom_match:
            symbol = str(dom_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_dom_handle_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(dom_match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("DOM element unavailable: {symbol}");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        func_match = _TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE.match(line)
        if func_match:
            symbol = str(func_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            rhs = str(func_match.group("rhs") or "")
            if "!" in rhs:
                repaired_lines.append(line)
                continue
            repaired_line = line.rstrip().rstrip(";")
            repaired_line = re.sub(r"\)\s*$", ")!", repaired_line)
            if line.rstrip().endswith(";"):
                repaired_line += ";"
            repaired_lines.append(repaired_line)
            guarded.append(symbol)
            continue
        repaired_lines.append(line)
    if not guarded:
        return text, []
    return "\n".join(repaired_lines) + ("\n" if text.endswith("\n") else ""), _dedupe_preserve_order(guarded)


def build_typescript_nullable_canvas_context_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for nullable DOM/canvas TypeScript handles."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_nullable_canvas_context_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_symbols: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        symbols = targets_by_path[path] or set()
        repaired, guarded_symbols = repair_typescript_nullable_canvas_context_guards(original, symbols)
        if repaired == original or not guarded_symbols:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        repaired_symbols.extend({"file": path, "symbol": symbol} for symbol in guarded_symbols)
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_nullable_canvas_context_guard",
                    "guarded_symbols": list(guarded_symbols),
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.nullable_canvas_context",
        source_tool=TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"guards": repaired_symbols},
    )


def build_typescript_duplicate_object_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1117 duplicate object property lines."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    line_numbers_by_path = _parse_duplicate_object_property_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    removed_lines: list[dict[str, str]] = []
    for path in sorted(line_numbers_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _duplicate_object_property_delete_operations(
            path=path,
            content=original,
            line_numbers=line_numbers_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        removed_lines.extend(
            {"file": path, "line": str(operation.metadata.get("line") or "")} for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.duplicate_object_property",
        source_tool=TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"duplicates": removed_lines},
    )


def build_typescript_enum_member_separator_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1357 enum member separators."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    line_numbers_by_path = _parse_enum_member_separator_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_members: list[dict[str, str]] = []
    for path in sorted(line_numbers_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _enum_member_separator_operations(
            path=path,
            content=original,
            line_numbers=line_numbers_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_members.extend(
            {
                "file": path,
                "line": str(operation.metadata.get("line") or ""),
                "col": str(operation.metadata.get("column") or ""),
            }
            for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.enum_member_separator",
        source_tool=TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"enum_members": repaired_members},
    )


def _repair_missing_object_property_comma_line(line_body: str) -> str:
    return _TS_INLINE_OBJECT_MISSING_COMMA_RE.sub(r"\g<value>, \g<key>", line_body)


def _repair_object_property_semicolon_line(line_body: str) -> str:
    match = _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE.match(line_body)
    if match:
        return f"{match.group('indent')}{match.group('name')},"
    value_match = _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE.match(line_body)
    if value_match:
        return f"{value_match.group('indent')}{value_match.group('property').rstrip()},"
    return line_body


def _object_property_line_needs_previous_comma(
    line_body: str,
    repaired_lines: list[str],
) -> bool:
    if not repaired_lines or not _TS_OBJECT_PROPERTY_KEY_LINE_RE.match(line_body):
        return False
    previous = repaired_lines[-1].rstrip("\r\n")
    previous_stripped = previous.rstrip()
    if not previous_stripped or previous_stripped.endswith((",", "{", "[", "(", ":", ";")):
        return False
    return not previous_stripped.lstrip().startswith(("//", "*", "/*"))


def _append_object_property_comma(line: str) -> str:
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    return f"{line_body.rstrip()},{newline}"


def _repair_typescript_multiline_dom_handle_declarations(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    guarded: list[str] = []
    declaration_re = re.compile(
        r"(?ms)^(?P<indent>\s*)(?P<kind>const|let|var)\s+"
        r"(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<source>(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\(.*?\)\s+as\s+(?P<type>[^;\n]*\bnull\b[^;\n]*)\s*;)"
    )

    def _replace(match: re.Match[str]) -> str:
        symbol = str(match.group("symbol") or "").strip()
        if symbols and symbol not in symbols:
            return match.group(0)
        source = str(match.group("source") or "")
        narrowed_source = re.sub(r"\s*\|\s*null\b", "", source)
        narrowed_source = re.sub(r"\bnull\s*\|\s*", "", narrowed_source)
        if narrowed_source == source:
            return match.group(0)
        guarded.append(symbol)
        declaration = f"{match.group('indent')}{match.group('kind')} {symbol} = {narrowed_source}"
        following = text[match.end() : match.end() + 240]
        if _typescript_nullable_guard_in_text_window(following, symbol):
            return declaration
        indent = str(match.group("indent") or "")
        return (
            f"{declaration}\n"
            f"{indent}if (!{symbol}) {{\n"
            f'{indent}  throw new Error("DOM element unavailable: {symbol}");\n'
            f"{indent}}}"
        )

    repaired = declaration_re.sub(_replace, text)
    return repaired, _dedupe_preserve_order(guarded)


def _typescript_canvas_context_non_null_assertion_line(line: str) -> str:
    if re.search(r"\.getContext\(\s*['\"]2d['\"]\s*\)!", line):
        return line
    return re.sub(r"(\.getContext\(\s*['\"]2d['\"]\s*\))(\s*;?\s*)$", r"\1!\2", line)


def _typescript_dom_handle_non_null_assertion_line(line: str) -> str:
    if "| null" in line or "null |" in line:
        narrowed = re.sub(r"\s*\|\s*null\b", "", line)
        narrowed = re.sub(r"\bnull\s*\|\s*", "", narrowed)
        return narrowed
    if re.search(
        r"(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\)!",
        line,
    ):
        return line
    return re.sub(
        r"((?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\))",
        r"\1!",
        line,
        count=1,
    )


def _typescript_nullable_guard_follows(lines: list[str], index: int, symbol: str) -> bool:
    window = "\n".join(lines[index + 1 : index + 7])
    return _typescript_nullable_guard_in_text_window(window, symbol)


def _typescript_nullable_guard_in_text_window(window: str, symbol: str) -> bool:
    compact = re.sub(r"\s+", "", window)
    return (
        f"if(!{symbol})" in compact
        or f"if({symbol}===null)" in compact
        or f"if({symbol}==null)" in compact
        or f"if(null==={symbol})" in compact
        or f"if(null=={symbol})" in compact
    )


def _parse_nullable_canvas_context_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[str] | None]:
    by_path: dict[str, set[str] | None] = {}
    for diagnostic in diagnostics:
        _add_nullable_targets_from_raw(by_path, diagnostic.raw or diagnostic.message)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        code = diagnostic.code.lower()
        message = diagnostic.message or diagnostic.raw
        if code == "typescript_ts18047":
            match = _TS_POSSIBLY_NULL_MESSAGE_RE.search(message)
            symbol = str(match.group("symbol") or "").strip() if match else ""
            if _TS_IDENTIFIER_RE.fullmatch(symbol):
                _add_nullable_target(by_path, path, symbol)
        elif code == "typescript_ts2345" and "null" in message.lower() and "not assignable" in message.lower():
            _add_nullable_target(by_path, path, "")
    return by_path


def _parse_duplicate_object_property_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[int]]:
    by_path: dict[str, set[int]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            if path and line > 0:
                by_path.setdefault(path, set()).add(line)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts1117" and path and diagnostic.line:
            by_path.setdefault(path, set()).add(int(diagnostic.line))
    return by_path


def _parse_enum_member_separator_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[int]]:
    by_path: dict[str, set[int]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_ENUM_MEMBER_SEPARATOR_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            if path and line > 0:
                by_path.setdefault(path, set()).add(line)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts1357" and path and diagnostic.line:
            by_path.setdefault(path, set()).add(int(diagnostic.line))
    return by_path


def _enum_member_separator_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    brace_depth = 0
    enum_depth: int | None = None
    offset = 0
    for line_number, line in enumerate(str(content or "").splitlines(keepends=True), start=1):
        line_start = offset
        line_end = offset + len(line)
        offset = line_end
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if enum_depth is not None and line_number in line_numbers:
            repaired_line = _repair_typescript_enum_member_line(line_body)
            if repaired_line != line_body:
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=path,
                        span_start=line_start,
                        span_end=line_end,
                        expected=line,
                        replacement=f"{repaired_line}{newline}",
                        before_hash=before_hash,
                        metadata={
                            "repair_kind": "typescript_enum_member_separator",
                            "line": line_number,
                        },
                    )
                )

        opens = line_body.count("{")
        closes = line_body.count("}")
        if enum_depth is None and _TS_ENUM_DECLARATION_LINE_RE.search(line_body):
            enum_depth = brace_depth + max(opens, 1)
        brace_depth += opens - closes
        if enum_depth is not None and brace_depth < enum_depth:
            enum_depth = None
    return tuple(operations)


def _repair_typescript_enum_member_line(line_body: str) -> str:
    match = _TS_ENUM_MEMBER_LINE_RE.match(line_body)
    if not match:
        return line_body
    if match.group("separator") == ",":
        return line_body
    prefix = str(match.group("prefix") or "").rstrip()
    if not prefix or prefix.lstrip().startswith(("enum ", "export ")):
        return line_body
    comment = str(match.group("comment") or "")
    space = " " if comment else str(match.group("space") or "")
    return f"{prefix},{space}{comment}"


def _duplicate_object_property_delete_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    offset = 0
    lines = str(content or "").splitlines(keepends=True)
    for line_no, line in enumerate(lines, start=1):
        line_start = offset
        line_end = offset + len(line)
        offset = line_end
        if line_no not in line_numbers:
            continue
        if not _looks_like_single_line_typescript_object_property(line.rstrip("\r\n")):
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=line_start,
                span_end=line_end,
                expected=line,
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_object_property",
                    "line": line_no,
                },
            )
        )
    return tuple(operations)


def _looks_like_single_line_typescript_object_property(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped or ":" not in stripped:
        return False
    if stripped.startswith(("case ", "default", "return ", "if ", "for ", "while ", "//", "/*", "*")):
        return False
    property_re = re.compile(r"^(?:[A-Za-z_$][A-Za-z0-9_$]*|['\"][^'\"]+['\"]|\[[^\]]+\])\s*:\s*.+,?\s*(?://.*)?$")
    return bool(property_re.match(stripped))


def _add_nullable_targets_from_raw(targets: dict[str, set[str] | None], raw: str) -> None:
    text = str(raw or "")
    for match in _TS_POSSIBLY_NULL_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        symbol = str(match.group("symbol") or "").strip()
        if path and _TS_IDENTIFIER_RE.fullmatch(symbol):
            _add_nullable_target(targets, path, symbol)
    for match in _TS_NULLABLE_ARGUMENT_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        if path:
            _add_nullable_target(targets, path, "")


def _add_nullable_target(targets: dict[str, set[str] | None], path: str, symbol: str) -> None:
    if not symbol:
        targets[path] = None
        return
    existing = targets.get(path)
    if existing is None and path in targets:
        return
    if existing is None:
        existing = set()
        targets[path] = existing
    existing.add(symbol)


def _text_replace_operations_from_repair(
    *,
    path: str,
    original: str,
    repaired: str,
    metadata: Mapping[str, object],
) -> tuple[RepairOperation, ...]:
    before_hash = sha256_text(original)
    operations: list[RepairOperation] = []
    original_lines = original.splitlines(keepends=True)
    repaired_lines = repaired.splitlines(keepends=True)
    original_offsets = _line_start_offsets(original_lines)
    matcher = SequenceMatcher(a=original_lines, b=repaired_lines, autojunk=False)
    for tag, start_line, end_line, replacement_start, replacement_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = original_offsets[start_line]
        end = original_offsets[end_line]
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected="".join(original_lines[start_line:end_line]),
                replacement="".join(repaired_lines[replacement_start:replacement_end]),
                before_hash=before_hash,
                metadata=dict(metadata),
            )
        )
    return tuple(operations)


def _line_start_offsets(lines: Sequence[str]) -> list[int]:
    offsets: list[int] = [0]
    current = 0
    for line in lines:
        current += len(line)
        offsets.append(current)
    return offsets


def _to_positive_int(value: object) -> int:
    try:
        parsed = int(str(value or "0"))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _dedupe_preserve_order(items: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _is_typescript_comma_expected_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "typescript_return_object_property_semicolon":
        return True
    if diagnostic.code.lower() != "typescript_ts1005":
        return False
    message = diagnostic.message.lower()
    raw = diagnostic.raw.lower()
    return "expected" in message and "," in (message + raw)


__all__ = [
    "TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL",
    "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "build_typescript_duplicate_object_property_plan",
    "build_typescript_enum_member_separator_plan",
    "build_typescript_nullable_canvas_context_plan",
    "build_typescript_object_literal_comma_plan",
    "repair_typescript_nullable_canvas_context_guards",
    "repair_typescript_object_literal_commas",
]
