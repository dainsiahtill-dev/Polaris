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

"""TypeScript syntax repair module: object_literals."""

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

def build_typescript_duplicate_object_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1117/TS2300 duplicate property lines.

    TS1117 covers object-literal duplicate keys. TS2300 covers interface/type
    member duplicates (R180 fillStyle). Later same-name lines are deleted; the
    first occurrence is preserved.
    """

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

def repair_typescript_missing_closing_braces(text: str) -> str:
    """Repair narrow TypeScript TS1005 missing closing-brace diagnostics."""

    missing_count = _typescript_brace_balance_delta(str(text or ""))
    if missing_count <= 0 or missing_count > 8:
        return str(text or "")
    repaired = str(text or "").rstrip() + "\n"
    repaired += "\n".join("}" for _ in range(missing_count))
    return repaired + "\n"

def build_typescript_missing_closing_brace_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1005 missing closing braces."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_missing_closing_brace_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        operation = _missing_closing_brace_operation(path=path, content=original)
        if operation is None:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.append(operation)
        repaired_items.append(
            {
                "file": path,
                "missing_count": str(operation.metadata.get("missing_count") or ""),
            }
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.missing_closing_brace",
        source_tool=TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"syntax_errors": repaired_items},
    )

def build_typescript_shorthand_property_scope_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS18004 repair plan for missing object-literal shorthand values."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_shorthand_property_scope_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _shorthand_property_scope_operations(
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
                "property": str(operation.metadata.get("property") or ""),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.shorthand_property_scope",
        source_tool=TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"shorthand_properties": repaired_items},
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

def _parse_duplicate_object_property_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[int]]:
    by_path: dict[str, set[int]] = {}
    # Track named TS2300 dups so we can drop only *later* occurrences.
    named_lines: dict[str, dict[str, list[int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            if path and line > 0:
                by_path.setdefault(path, set()).add(line)
        for match in _TS_DUPLICATE_IDENTIFIER_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            name = str(match.group("name") or "").strip()
            if path and line > 0 and name:
                named_lines.setdefault(path, {}).setdefault(name, []).append(line)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        code = diagnostic.code.lower()
        if code == "typescript_ts1117" and path and diagnostic.line:
            by_path.setdefault(path, set()).add(int(diagnostic.line))
        if code == "typescript_ts2300" and path and diagnostic.line:
            message = str(diagnostic.message or diagnostic.raw or "")
            name_match = re.search(r"Duplicate\s+identifier\s+['\"](?P<name>[^'\"]+)['\"]", message, re.I)
            name = str(name_match.group("name") if name_match else "").strip()
            if name:
                named_lines.setdefault(path, {}).setdefault(name, []).append(int(diagnostic.line))
    for path, names in named_lines.items():
        for _name, lines in names.items():
            ordered = sorted(set(lines))
            # Keep first occurrence; delete subsequent duplicate lines.
            for line in ordered[1:]:
                by_path.setdefault(path, set()).add(line)
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

def _parse_missing_closing_brace_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_MISSING_CLOSING_BRACE_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_missing_closing_brace_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path

def _parse_shorthand_property_scope_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str]]]:
    by_path: dict[str, set[tuple[int, int, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            prop = str(match.group("property") or "").strip()
            if path and line > 0 and column > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((line, column, prop))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_shorthand_property_scope_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            prop_match = re.search(
                r"shorthand property ['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
                text,
                re.IGNORECASE,
            )
            prop = str(prop_match.group("property") or "").strip() if prop_match else ""
            if _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column), prop))
    return by_path

def _is_missing_closing_brace_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return diagnostic.code.lower() == "typescript_ts1005" and "expected" in message and "}" in message

def _is_shorthand_property_scope_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts18004"
        and "no value exists in scope" in message
        and "shorthand property" in message
    )

def _missing_closing_brace_operation(*, path: str, content: str) -> RepairOperation | None:
    missing_count = _typescript_brace_balance_delta(content)
    if missing_count <= 0 or missing_count > 8:
        return None
    repaired = repair_typescript_missing_closing_braces(content)
    if repaired == content:
        return None
    start = len(content.rstrip())
    unique_context = content[max(0, start - 160) : len(content)]
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=len(content),
        expected=content[start:],
        replacement=repaired[start:],
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_missing_closing_brace",
            "missing_count": missing_count,
            "unique_context": unique_context,
        },
    )

def _shorthand_property_scope_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    targets_by_line: dict[int, set[str]] = {}
    for line, _column, prop in targets:
        if line > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
            targets_by_line.setdefault(line, set()).add(prop)
    operations: list[RepairOperation] = []
    for line_number in sorted(targets_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        repaired_line_body, removed = _remove_shorthand_properties(line_body, targets_by_line[line_number])
        if repaired_line_body == line_body or not removed:
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
                    "repair_kind": "typescript_shorthand_property_scope",
                    "line": line_number,
                    "property": ",".join(sorted(removed)),
                    "unique_context": original_line,
                },
            )
        )
    return tuple(operations)

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
    # Object-literal property or interface/type member (optional trailing `;`).
    property_re = re.compile(
        r"^(?:readonly\s+|public\s+|private\s+|protected\s+)*"
        r"(?:[A-Za-z_$][A-Za-z0-9_$]*|['\"][^'\"]+['\"]|\[[^\]]+\])\s*[?]?\s*:\s*.+[,;]?\s*(?://.*)?$"
    )
    return bool(property_re.match(stripped))

def build_typescript_param_object_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Retype ``param: number`` when body uses ``param.prop`` and a type owns prop (R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    property_types = _typescript_types_with_named_properties(normalized_base)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        m = _TS2339_PROP_ON_PRIMITIVE_RE.search(raw)
        code = str(diagnostic.code or "").lower()
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        prop = ""
        if m:
            path = path or _normalize_repair_path(str(m.group("file") or ""))
            line = line or int(m.group("line") or 0)
            prop = str(m.group("prop") or "")
        elif code == "typescript_ts2339":
            prop_match = re.search(r"Property\s+['\"]([A-Za-z_$][\w$]*)['\"]", raw)
            type_match = re.search(r"on type\s+['\"](number|string|boolean)['\"]", raw, re.I)
            if not prop_match or not type_match:
                continue
            prop = str(prop_match.group(1) or "")
        else:
            continue
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0 or not prop:
            continue
        candidates = property_types.get(prop) or ()
        if not candidates:
            continue
        op = _typescript_param_type_from_property_operation(
            path=path,
            content=content,
            line=line,
            prop=prop,
            candidate_types=candidates,
        )
        if op is None:
            continue
        operations.append(op)
        type_name = str((op.metadata or {}).get("type_name") or "")
        if type_name:
            import_op = _typescript_ensure_named_type_import_operation(
                path=path,
                content=content,
                type_name=type_name,
                base_files=normalized_base,
            )
            if import_op is not None:
                operations.append(import_op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.param_object_property",
        source_tool=TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"param_object_property_repairs": len(operations)},
    )

def build_typescript_object_literal_missing_props_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Inject stub members into object literals missing required type properties.

    Live L1-01 r170: incomplete ``CompilerHost`` object literal in verify.ts fails
    TS2345 (missing getCurrentDirectory / getCanonicalFileName / getNewLine).
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    updated: dict[str, str] = dict(normalized_base)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    repairs: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        parsed = _parse_typescript_missing_props_diagnostic(diagnostic)
        if parsed is None:
            continue
        path, line, col, type_name, props = parsed
        if not path or path not in updated or line <= 0 or not props:
            continue
        content = str(updated.get(path) or "")
        op = _typescript_inject_missing_object_props_operation(
            path=path,
            content=content,
            line=line,
            column=col,
            missing_props=props,
            type_name=type_name,
        )
        if op is None or not op.content or op.content == content:
            continue
        repaired = str(op.content)
        updated[path] = repaired
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=content,
                repaired=repaired,
                metadata=dict(op.metadata or {}),
            )
        )
        matched.append(diagnostic)
        repairs.append({"file": path, "type": type_name, "props": list(props)})
    return _repair_plan_or_none(
        rule_id="typescript.object_literal_missing_props",
        source_tool=TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"object_literal_missing_props_repairs": repairs},
    )

def _typescript_object_prop_stub_expression(prop: str) -> str:
    known = _TS_KNOWN_METHOD_STUBS.get(prop)
    if known:
        return known
    # Heuristic: method-like names become no-op callables; data props stay unknown.
    if re.match(
        r"^(get|set|is|has|read|write|create|resolve|file|directory|use|real)",
        prop,
        re.IGNORECASE,
    ):
        return "(..._args: never[]) => undefined as never"
    return "undefined as never"

def _typescript_object_literal_open_at_diagnostic(
    content: str,
    *,
    line: int,
    column: int,
    missing_props: Sequence[str],
) -> int:
    """Locate the object-literal ``{`` that tsc flagged for missing props.

    For ``createProgram(files, { opts }, { host })`` the diagnostic column may
    land past the host brace or on the options brace. Prefer the last ``{`` on
    the diagnostic line whose body still lacks the missing properties (the host
    object), not the short options bag on the same line.
    """

    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return -1
    line_start = sum(len(lines[i]) for i in range(line - 1))
    line_text = lines[line - 1]
    brace_offsets = [line_start + idx for idx, char in enumerate(line_text) if char == "{"]
    if not brace_offsets:
        # Multi-line: object may open on a later line near the diagnostic.
        offset = line_start + (max(0, column - 1) if column > 0 else 0)
        nearby = content.find("{", max(0, offset - 20), min(len(content), offset + 120))
        return nearby if nearby >= 0 else -1

    def body_missing_count(open_brace: int) -> int:
        close = _typescript_matching_brace_index(content, open_brace)
        if close < 0:
            return -1
        body = content[open_brace + 1 : close]
        count = 0
        for prop in missing_props:
            if not _TS_IDENTIFIER_RE.fullmatch(prop):
                continue
            if re.search(rf"\b{re.escape(prop)}\s*:", body):
                continue
            count += 1
        return count

    # Score each brace on the diagnostic line; prefer the one missing the most
    # required props and, on ties, the rightmost (argument host object).
    ranked = sorted(
        brace_offsets,
        key=lambda pos: (body_missing_count(pos), pos),
    )
    best = ranked[-1]
    if body_missing_count(best) <= 0:
        return -1
    return best

def _typescript_inject_missing_object_props_operation(
    *,
    path: str,
    content: str,
    line: int,
    column: int,
    missing_props: Sequence[str],
    type_name: str,
) -> RepairOperation | None:
    if line < 1 or not missing_props:
        return None
    open_brace = _typescript_object_literal_open_at_diagnostic(
        content,
        line=line,
        column=column,
        missing_props=missing_props,
    )
    if open_brace < 0:
        return None
    close_brace = _typescript_matching_brace_index(content, open_brace)
    if close_brace < 0:
        return None
    body = content[open_brace + 1 : close_brace]
    still_missing = [
        prop
        for prop in missing_props
        if _TS_IDENTIFIER_RE.fullmatch(prop) and not re.search(rf"\b{re.escape(prop)}\s*:", body)
    ]
    if not still_missing:
        return None
    body_lines = body.splitlines()
    indent = "    "
    for body_line in reversed(body_lines):
        stripped = body_line.lstrip(" \t")
        if stripped:
            indent = body_line[: len(body_line) - len(stripped)] or indent
            break
    close_line_start = content.rfind("\n", 0, close_brace) + 1
    close_indent = content[close_line_start:close_brace]
    if close_indent.strip():
        close_indent = indent[: max(0, len(indent) - 2)] if len(indent) >= 2 else ""
    stub_lines = [f"{indent}{prop}: {_typescript_object_prop_stub_expression(prop)}," for prop in still_missing]
    prefix = content[:close_brace]
    suffix = content[close_brace:]
    body_stripped = body.rstrip()
    if body_stripped and not body_stripped.endswith((",", "{", "(")):
        trim_idx = len(prefix.rstrip())
        if trim_idx > 0 and prefix[trim_idx - 1] not in {",", "{", "(", "[", ";"}:
            prefix = prefix[:trim_idx] + "," + prefix[trim_idx:]
    insertion = "\n" + "\n".join(stub_lines) + "\n"
    if not suffix.startswith("}"):
        return None
    if prefix.endswith("\n"):
        repaired = prefix + "\n".join(stub_lines) + "\n" + close_indent + suffix
    else:
        repaired = prefix + insertion + close_indent + suffix
    if repaired == content:
        return None
    return RepairOperation(
        kind="write_file",
        path=path,
        content=repaired,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_literal_missing_props",
            "write_file_reason": "inject_missing_interface_props",
            "type_name": type_name,
            "props": list(still_missing),
        },
    )

__all__ = (
    "repair_typescript_object_literal_commas",
    "build_typescript_object_literal_comma_plan",
    "build_typescript_duplicate_object_property_plan",
    "build_typescript_enum_member_separator_plan",
    "repair_typescript_missing_closing_braces",
    "build_typescript_missing_closing_brace_plan",
    "build_typescript_shorthand_property_scope_plan",
    "_repair_missing_object_property_comma_line",
    "_repair_object_property_semicolon_line",
    "_object_property_line_needs_previous_comma",
    "_append_object_property_comma",
    "_parse_duplicate_object_property_targets",
    "_parse_enum_member_separator_targets",
    "_parse_missing_closing_brace_targets",
    "_parse_shorthand_property_scope_targets",
    "_is_missing_closing_brace_diagnostic",
    "_is_shorthand_property_scope_diagnostic",
    "_missing_closing_brace_operation",
    "_shorthand_property_scope_operations",
    "_enum_member_separator_operations",
    "_repair_typescript_enum_member_line",
    "_duplicate_object_property_delete_operations",
    "_looks_like_single_line_typescript_object_property",
    "build_typescript_param_object_property_plan",
    "build_typescript_object_literal_missing_props_plan",
    "_typescript_object_prop_stub_expression",
    "_typescript_object_literal_open_at_diagnostic",
    "_typescript_inject_missing_object_props_operation",
)
