# ruff: noqa: F403, F405
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def build_typescript_identifier_suggestion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Apply TS2552 'Did you mean' identifier renames when the suggestion is in scope.

    Live L1-01 r172: ``if (_context === null)`` with local ``const context = ...``
    fails TS2552 and leaves the null-check un-narrowed (cascade TS2322).
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    updated: dict[str, str] = dict(normalized_base)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    renames: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        parsed = _parse_typescript_identifier_suggestion_diagnostic(diagnostic)
        if parsed is None:
            continue
        path, line, col, actual, suggestion = parsed
        if path not in updated or line <= 0 or not actual or not suggestion or actual == suggestion:
            continue
        content = str(updated.get(path) or "")
        if not _typescript_identifier_in_scope(content, suggestion, line=line):
            continue
        op = _typescript_rename_identifier_at_diagnostic(
            path=path,
            content=content,
            line=line,
            column=col,
            actual=actual,
            suggestion=suggestion,
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
        renames.append({"file": path, "actual": actual, "suggestion": suggestion, "line": line})
    return _repair_plan_or_none(
        rule_id="typescript.identifier_suggestion",
        source_tool=TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"identifier_suggestion_repairs": renames},
    )


def build_typescript_unused_local_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Prefix unused locals (TS6133) with underscore, including destructured names.

    Live L1-01 r172: ``const { canvas, context } = acquired`` leaves ``context``
    unused when callers pass the whole ``acquired`` object.

    Live L1-01 r176: never underscore-prefix import bindings — that rewrites
    ``type HumidityBand`` into ``type _HumidityBand`` and yields TS2724. Unused
    import names are removed via the existing named-import binding helpers.
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    updated: dict[str, str] = dict(normalized_base)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    locals_fixed: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        parsed = _parse_typescript_unused_local_diagnostic(diagnostic)
        if parsed is None:
            continue
        path, line, col, name = parsed
        if path not in updated or line <= 0 or not name:
            continue
        content = str(updated.get(path) or "")
        # Prefer import-specifier removal over underscore rewrite (r176 TS2724).
        import_op = _typescript_unused_import_declaration_operation(
            path=path,
            content=content,
            name=name,
            line_number=line,
        )
        if import_op is not None:
            repaired = _apply_single_text_operation(content, import_op)
            if repaired == content:
                continue
            updated[path] = repaired
            operations.append(import_op)
            matched.append(diagnostic)
            locals_fixed.append(
                {
                    "file": path,
                    "name": name,
                    "line": line,
                    "action": "remove_import_binding",
                }
            )
            continue
        # R175/M10: already-underscored unused helpers (e.g. `_decayMult`) should
        # be deleted rather than skipped / re-prefixed.
        if name.startswith("_"):
            op = _typescript_delete_unused_local_function_operation(
                path=path,
                content=content,
                line=line,
                name=name,
            )
        else:
            op = _typescript_prefix_unused_local_operation(
                path=path,
                content=content,
                line=line,
                column=col,
                name=name,
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
        locals_fixed.append({"file": path, "name": name, "line": line})
    return _repair_plan_or_none(
        rule_id="typescript.unused_local",
        source_tool=TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"unused_local_repairs": locals_fixed},
    )


def build_typescript_argument_shape_adapter_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Adapt call-site arguments that miss required object-literal properties.

    Live L1-01 r172: ``renderFirefly(..., flash.value, ...)`` where
    ``FireflyFlashEvent`` has ``intensity`` but the parameter expects
    ``{ glow: number; phase: number }``.
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    updated: dict[str, str] = dict(normalized_base)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    adapters: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        parsed = _parse_typescript_argument_missing_props_diagnostic(diagnostic)
        if parsed is None:
            continue
        path, line, col, source_type, target_shape, props = parsed
        if path not in updated or line <= 0 or not props:
            continue
        content = str(updated.get(path) or "")
        op = _typescript_adapt_argument_shape_operation(
            path=path,
            content=content,
            line=line,
            column=col,
            missing_props=props,
            source_type=source_type,
            target_shape=target_shape,
            base_files=updated,
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
        adapters.append(
            {
                "file": path,
                "source_type": source_type,
                "props": list(props),
                "line": line,
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.argument_shape_adapter",
        source_tool=TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"argument_shape_adapter_repairs": adapters},
    )


def _parse_typescript_unused_local_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    match = _TS6133_UNUSED_LOCAL_RE.search(raw)
    if match:
        path = path or _normalize_repair_path(str(match.group("file") or ""))
        line = line or int(match.group("line") or 0)
        col = col or int(match.group("col") or 0)
        name = str(match.group("name") or "")
    else:
        code = str(diagnostic.code or "").lower()
        if code not in {"typescript_ts6133", "ts6133"}:
            return None
        loose = re.search(
            r"['\"](?P<name>[A-Za-z_$][\w$]*)['\"]\s+is\s+declared\s+but",
            raw,
            re.IGNORECASE,
        )
        if loose is None or not path or line <= 0:
            return None
        name = str(loose.group("name") or "")
    if not path or line <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    return path, line, col, name


def _typescript_delete_unused_local_function_operation(
    *,
    path: str,
    content: str,
    line: int,
    name: str,
) -> RepairOperation | None:
    """Delete an already-underscored unused function declaration (TS6133)."""

    if not _TS_IDENTIFIER_RE.fullmatch(name) or not name.startswith("_"):
        return None
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    start = line - 1
    header = lines[start]
    if not re.search(rf"\bfunction\s+{re.escape(name)}\b", header) and not re.search(
        rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=",
        header,
    ):
        return None
    # Expand to a contiguous function block by brace depth.
    depth = 0
    end = start
    seen_open = False
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if "{" in lines[index]:
            seen_open = True
        end = index
        if seen_open and depth <= 0:
            break
        # One-liner without braces: `function _x() { return 1 }` handled above;
        # arrow one-liner ends on same line when no `{`.
        if not seen_open and index == start and ";" in lines[index]:
            break
    if end < start:
        return None
    # Drop trailing blank line after the function when present.
    if end + 1 < len(lines) and not lines[end + 1].strip():
        end += 1
    repaired_lines = lines[:start] + lines[end + 1 :]
    repaired = "".join(repaired_lines)
    if repaired == content:
        return None
    return RepairOperation(
        kind="write_file",
        path=path,
        content=repaired,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_unused_local_delete",
            "write_file_reason": "ts6133_delete_underscored_unused",
            "name": name,
            "line": line,
        },
    )


def _typescript_prefix_unused_local_operation(
    *,
    path: str,
    content: str,
    line: int,
    column: int,
    name: str,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    original_line = lines[line - 1]
    # Fail-closed: import bindings must not be underscore-prefixed (r176 TS2724).
    if _typescript_line_is_import_binding_context(content=content, line=line, name=name):
        return None
    # Destructuring: { canvas, context } → { canvas, context: _context }
    destructure = re.search(
        rf"(?P<pre>\{{[^}}]*?)(?P<ident>\b{re.escape(name)}\b)(?!\s*:)(?P<post>[^}}]*\}})",
        original_line,
    )
    if destructure:
        repaired_line = (
            original_line[: destructure.start("ident")] + f"{name}: _{name}" + original_line[destructure.end("ident") :]
        )
    else:
        # const context = ... / let context
        decl = re.search(
            rf"(?P<pre>\b(?:const|let|var)\s+)(?P<ident>{re.escape(name)})\b",
            original_line,
        )
        if decl:
            repaired_line = original_line[: decl.start("ident")] + f"_{name}" + original_line[decl.end("ident") :]
        else:
            col_index = max(0, column - 1) if column > 0 else 0
            symbol_re = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
            matches = list(symbol_re.finditer(original_line))
            if not matches:
                return None
            selected = min(
                matches,
                key=lambda match: (
                    0 if match.start() <= col_index <= match.end() else 1,
                    abs(match.start() - col_index),
                ),
            )
            repaired_line = original_line[: selected.start()] + f"_{name}" + original_line[selected.end() :]
    if repaired_line == original_line:
        return None
    lines[line - 1] = repaired_line
    repaired = "".join(lines)
    return RepairOperation(
        kind="write_file",
        path=path,
        content=repaired,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_unused_local",
            "write_file_reason": "ts6133_prefix_underscore",
            "name": name,
            "line": line,
        },
    )


def _parse_typescript_identifier_suggestion_diagnostic(
    diagnostic: RepairDiagnostic,
) -> tuple[str, int, int, str, str] | None:
    raw = str(diagnostic.raw or diagnostic.message or "")
    match = _TS2552_IDENTIFIER_SUGGESTION_RE.search(raw)
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = int(diagnostic.line or 0)
    col = int(diagnostic.column or 0)
    if match:
        path = path or _normalize_repair_path(str(match.group("file") or ""))
        line = line or int(match.group("line") or 0)
        col = col or int(match.group("col") or 0)
        actual = str(match.group("actual") or "")
        suggestion = str(match.group("suggestion") or "")
    else:
        code = str(diagnostic.code or "").lower()
        if code not in {"typescript_ts2552", "ts2552"}:
            return None
        loose = re.search(
            r"Cannot\s+find\s+name\s+['\"](?P<actual>[A-Za-z_$][\w$]*)['\"]\.\s*"
            r"Did\s+you\s+mean\s+['\"](?P<suggestion>[A-Za-z_$][\w$]*)['\"]\?",
            raw,
            re.IGNORECASE,
        )
        if loose is None or not path or line <= 0:
            return None
        actual = str(loose.group("actual") or "")
        suggestion = str(loose.group("suggestion") or "")
    if not path or line <= 0 or not _TS_IDENTIFIER_RE.fullmatch(actual) or not _TS_IDENTIFIER_RE.fullmatch(suggestion):
        return None
    return path, line, col, actual, suggestion


def _typescript_adapt_argument_shape_operation(
    *,
    path: str,
    content: str,
    line: int,
    column: int,
    missing_props: Sequence[str],
    source_type: str,
    target_shape: str,
    base_files: Mapping[str, str],
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    original_line = lines[line - 1]
    col_index = max(0, column - 1) if column > 0 else 0
    # Extract argument expression starting near the diagnostic column.
    expr = _typescript_extract_argument_expression(original_line, col_index)
    if not expr:
        return None
    expr_start = original_line.find(expr, max(0, col_index - len(expr)))
    if expr_start < 0:
        expr_start = original_line.find(expr)
    if expr_start < 0:
        return None
    adapter = _typescript_build_argument_shape_adapter(
        expr=expr,
        missing_props=missing_props,
        source_type=source_type,
        target_shape=target_shape,
        base_files=base_files,
    )
    if not adapter or adapter == expr:
        return None
    repaired_line = original_line[:expr_start] + adapter + original_line[expr_start + len(expr) :]
    if repaired_line == original_line:
        return None
    lines[line - 1] = repaired_line
    repaired = "".join(lines)
    return RepairOperation(
        kind="write_file",
        path=path,
        content=repaired,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_argument_shape_adapter",
            "write_file_reason": "ts2345_arg_missing_object_props",
            "source_type": source_type,
            "missing_props": list(missing_props),
            "line": line,
        },
    )


def _typescript_build_argument_shape_adapter(
    *,
    expr: str,
    missing_props: Sequence[str],
    source_type: str,
    target_shape: str,
    base_files: Mapping[str, str],
) -> str:
    source_fields = _typescript_type_field_names(source_type, base_files)
    prop_types = {
        m.group("name"): str(m.group("type") or "number").strip()
        for m in _TS_ANON_OBJECT_PROP_RE.finditer(target_shape)
    }
    parts: list[str] = []
    for prop in missing_props:
        if not _TS_IDENTIFIER_RE.fullmatch(prop):
            continue
        aliases = _PROP_SOURCE_ALIASES.get(prop, (prop,))
        # Only map from real fields on the source type; never invent accessors.
        source_field = next((alias for alias in aliases if alias in source_fields), "")
        prop_type = prop_types.get(prop, "number").split("|")[0].strip()
        if source_field:
            access = f"({expr} as {{ {source_field}?: {prop_type} }}).{source_field}"
            if prop_type == "number" or "number" in prop_type:
                parts.append(f"{prop}: Number({access} ?? 0)")
            elif prop_type == "string" or "string" in prop_type:
                parts.append(f"{prop}: String({access} ?? '')")
            elif prop_type == "boolean" or "boolean" in prop_type:
                parts.append(f"{prop}: Boolean({access})")
            else:
                parts.append(f"{prop}: ({access} as {prop_type})")
        else:
            if prop_type == "number" or "number" in prop_type:
                parts.append(f"{prop}: 0")
            elif prop_type == "string" or "string" in prop_type:
                parts.append(f"{prop}: ''")
            elif prop_type == "boolean" or "boolean" in prop_type:
                parts.append(f"{prop}: false")
            else:
                parts.append(f"{prop}: undefined as unknown as {prop_type}")
    if not parts:
        return ""
    return "{ " + ", ".join(parts) + " }"
