# ruff: noqa: F403, F405, SIM102
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def build_typescript_readonly_assignment_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build TS2540/TS2542/TS4104 repairs for readonly assignment mismatches."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_readonly_assignment_targets(diagnostics)
    index_targets_by_path = _parse_readonly_index_assignment_targets(
        diagnostics,
        base_files=normalized_base_files,
    )
    value_targets_by_path = _parse_readonly_value_assignment_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    all_paths = sorted(set(targets_by_path) | set(index_targets_by_path) | set(value_targets_by_path))
    for path in all_paths:
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = list(
            _readonly_assignment_operations(
                path=path,
                content=original,
                targets=targets_by_path.get(path, set()),
                base_files=normalized_base_files,
            )
        )
        # Apply ReadonlyArray mutability ops on content after property readonly strips
        # are planned independently (composer handles multi-op per path).
        path_operations.extend(
            _readonly_array_index_assignment_operations(
                path=path,
                content=original,
                properties=index_targets_by_path.get(path, set()),
            )
        )
        path_operations.extend(
            _readonly_value_assignment_operations(
                path=path,
                content=original,
                targets=value_targets_by_path.get(path, set()),
            )
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "property": str(operation.metadata.get("property") or ""),
                "diagnostic_lines": tuple(operation.metadata.get("diagnostic_lines") or ()),
                "repair_kind": str(operation.metadata.get("repair_kind") or ""),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.readonly_assignment",
        source_tool=TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"readonly_properties": repaired_items},
    )


def build_typescript_readonly_array_mutation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Retype ``const x: readonly T[] = []`` so ``push`` is legal (TS2339 R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen_bindings: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        if "push" not in raw.lower() or "readonly" not in raw.lower():
            if "push" not in raw.lower() or "ReadonlyArray" not in raw:
                code = str(diagnostic.code or "").lower()
                if code != "typescript_ts2339" or "push" not in raw.lower():
                    continue
                if "readonly" not in raw.lower() and "ReadonlyArray" not in raw:
                    continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        m = _TS2339_PUSH_READONLY_RE.search(raw)
        if m:
            path = path or _normalize_repair_path(str(m.group("file") or ""))
            line = line or int(m.group("line") or 0)
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0:
            continue
        op = _typescript_readonly_array_binding_operation(path=path, content=content, line=line)
        if op is None:
            continue
        binding_key = (path, str((op.metadata or {}).get("binding") or ""))
        if binding_key in seen_bindings:
            matched.append(diagnostic)
            continue
        seen_bindings.add(binding_key)
        operations.append(op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.readonly_array_mutation",
        source_tool=TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"readonly_array_mutations": len(operations)},
    )


def _parse_readonly_assignment_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str]]]:
    by_path: dict[str, set[tuple[int, int, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_READONLY_ASSIGNMENT_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            prop = str(match.group("property") or "").strip()
            if path and line > 0 and column > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((line, column, prop))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_readonly_assignment_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            prop_match = re.search(r"Cannot assign to ['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]", text)
            prop = str(prop_match.group("property") or "").strip() if prop_match else ""
            if _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column), prop))
    return by_path


def _parse_readonly_index_assignment_targets(
    diagnostics: Sequence[RepairDiagnostic],
    *,
    base_files: Mapping[str, str],
) -> dict[str, set[str]]:
    """Map path -> property names for TS2542 readonly-array index assignments."""

    by_path: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        text = f"{diagnostic.code}\n{diagnostic.message}\n{diagnostic.raw}"
        lowered = text.lower()
        if "ts2542" not in lowered and "index signature in type" not in lowered:
            continue
        path = ""
        line = 0
        for match in _TS_READONLY_INDEX_ASSIGNMENT_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            break
        if not path:
            path = _normalize_repair_path(str(diagnostic.path or ""))
            line = int(diagnostic.line or 0)
        content = str(base_files.get(path) or "")
        if not path or not content or line <= 0:
            continue
        lines = content.splitlines()
        if line - 1 >= len(lines):
            continue
        prop_match = _TS_INDEX_ASSIGN_PROP_RE.search(lines[line - 1])
        if prop_match is None:
            continue
        prop = str(prop_match.group("prop") or prop_match.group("prop2") or "").strip()
        if _TS_IDENTIFIER_RE.fullmatch(prop):
            by_path.setdefault(path, set()).add(prop)
    return by_path


def _parse_readonly_value_assignment_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    """Map TS4104 diagnostics to exact assignment lines.

    TS4104 means a readonly array/tuple value is assigned to a mutable target.
    Keep discovery strict: typed code, path, line, column, and canonical compiler
    wording are all required before an edit can be planned.
    """

    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
        if (
            str(diagnostic.code or "").lower() != "typescript_ts4104"
            or "readonly" not in text
            or "cannot be assigned to the mutable type" not in text
        ):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        column = int(diagnostic.column or 0)
        if path and line > 0 and column > 0:
            by_path.setdefault(path, set()).add((line, column))
    return by_path


def _readonly_value_assignment_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int]],
) -> tuple[RepairOperation, ...]:
    """Copy simple readonly member-chain values before mutable assignment.

    Example: ``fireflies = next.fireflies`` becomes
    ``fireflies = [...next.fireflies]``. Complex expressions fail closed.
    """

    if not targets:
        return ()
    lines = content.splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    assignment = re.compile(
        r"^(?P<indent>[ \t]*)(?P<lhs>[A-Za-z_$][A-Za-z0-9_$]*)"
        r"(?P<between>[ \t]*=[ \t]*)"
        r"(?P<rhs>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)"
        r"(?P<suffix>[ \t]*;?[ \t]*(?://[^\r\n]*)?)(?P<newline>\r?\n)?$"
    )
    for line_number, column in sorted(targets):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original = lines[line_index]
        match = assignment.fullmatch(original)
        if match is None:
            continue
        rhs = str(match.group("rhs") or "")
        if not rhs or rhs.startswith("..."):
            continue
        start = offsets[line_index] + match.start("rhs")
        end = offsets[line_index] + match.end("rhs")
        if content[start:end] != rhs:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=rhs,
                replacement=f"[...{rhs}]",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_readonly_value_assignment_copy",
                    "target": str(match.group("lhs") or ""),
                    "diagnostic_lines": (line_number,),
                    "diagnostic_column": column,
                    "unique_context": original,
                },
            )
        )
    return tuple(operations)


def _readonly_array_index_assignment_operations(
    *,
    path: str,
    content: str,
    properties: set[str],
) -> list[RepairOperation]:
    """Mutate ReadonlyArray/readonly[] property types so index assignment compiles."""

    if not properties:
        return []
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    for prop in sorted(properties):
        pattern = re.compile(
            rf"(?P<prefix>^[\t ]*(?:(?:public|private|protected)\s+)?)"
            rf"(?P<readonly>readonly\s+)?(?P<name>{re.escape(prop)})\s*(?P<optional>\?)?\s*:\s*"
            rf"(?P<type>ReadonlyArray\s*<\s*(?P<inner>[^>;]+?)\s*>|readonly\s+(?P<inner_arr>[^\[\];\n]+)\[\s*\])"
            rf"(?P<suffix>\s*[;,]?)",
            re.MULTILINE,
        )
        match = pattern.search(content)
        if match is None:
            continue
        inner = str(match.group("inner") or match.group("inner_arr") or "").strip()
        if not inner:
            continue
        replacement = (
            f"{match.group('prefix')}{match.group('name')}"
            f"{match.group('optional') or ''}: {inner}[]"
            f"{match.group('suffix') or ''}"
        )
        expected = match.group(0)
        if expected == replacement:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=expected,
                replacement=replacement,
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_readonly_array_index_assignment",
                    "property": prop,
                    "element_type": inner,
                },
            )
        )
    return operations


def _is_readonly_assignment_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts2540"
        and "cannot assign to" in message
        and "read-only property" in message
    )


def _readonly_assignment_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str]],
    base_files: Mapping[str, str] | None = None,
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    by_property: dict[str, set[int]] = {}
    for line, _column, prop in targets:
        if line > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
            by_property.setdefault(prop, set()).add(line)
    if not by_property:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    operations: list[RepairOperation] = []
    for prop in sorted(by_property):
        if not all(_line_mentions_assignment_property(lines, line, prop) for line in by_property[prop]):
            continue
        declaration_spans = _readonly_property_declaration_spans(content, prop)
        if len(declaration_spans) == 1:
            line_index, readonly_start, readonly_end = declaration_spans[0]
            original_line = lines[line_index]
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=readonly_start,
                    span_end=readonly_end,
                    expected=content[readonly_start:readonly_end],
                    replacement="",
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_readonly_assignment",
                        "property": prop,
                        "diagnostic_lines": tuple(sorted(by_property[prop])),
                        "declaration_line": line_index + 1,
                        "unique_context": original_line,
                    },
                )
            )
            continue
        # Cross-file class field: strip readonly on the unique implementing class.
        class_ops = _readonly_assignment_class_field_operations(
            property_name=prop,
            diagnostic_lines=tuple(sorted(by_property[prop])),
            base_files=base_files or {path: content},
        )
        if class_ops:
            operations.extend(class_ops)
            continue
        # Assignment-site cast fallback (receiver is class with multi-file readonly).
        cast_ops = _readonly_assignment_cast_operations(
            path=path,
            content=content,
            property_name=prop,
            diagnostic_lines=tuple(sorted(by_property[prop])),
        )
        operations.extend(cast_ops)
    return tuple(operations)


def _readonly_assignment_class_field_operations(
    *,
    property_name: str,
    diagnostic_lines: tuple[int, ...],
    base_files: Mapping[str, str],
) -> tuple[RepairOperation, ...]:
    """Strip ``readonly`` from unique class fields across the repair base files.

    R175/M10: assignments in ``main.ts`` target ``readonly sex`` declared on
    ``Firefly`` / ``Flower`` classes (and mirrored on interfaces). Same-file
    unique-span logic never sees the class declaration; prefer the unique
    implementing class field (file has ``class`` + ``this.<prop> =``).
    """

    if not _TS_IDENTIFIER_RE.fullmatch(property_name):
        return ()
    candidates: list[tuple[str, str, int, int, int]] = []
    for decl_path, decl_content in sorted(base_files.items()):
        text = str(decl_content or "")
        if not re.search(r"\bclass\b", text):
            continue
        if not re.search(rf"\bthis\.{re.escape(property_name)}\s*=", text):
            continue
        for line_index, start, end in _readonly_class_field_declaration_spans(text, property_name):
            candidates.append((decl_path, text, line_index, start, end))
    if len(candidates) != 1:
        return ()
    decl_path, text, line_index, start, end = candidates[0]
    lines = text.splitlines(keepends=True)
    return (
        RepairOperation(
            kind="text_replace",
            path=decl_path,
            span_start=start,
            span_end=end,
            expected=text[start:end],
            replacement="",
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "typescript_readonly_assignment_class_field",
                "property": property_name,
                "diagnostic_lines": diagnostic_lines,
                "declaration_line": line_index + 1,
                "unique_context": lines[line_index] if 0 <= line_index < len(lines) else "",
            },
        ),
    )


def _readonly_class_field_declaration_spans(content: str, prop: str) -> list[tuple[int, int, int]]:
    """Readonly field spans that sit inside ``class`` bodies (not interface/type)."""

    if not _TS_IDENTIFIER_RE.fullmatch(prop):
        return []
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    pattern = re.compile(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?)"
        rf"(?P<readonly>readonly\s+)(?P<property>{re.escape(prop)})(?=\s*[?:!:])"
    )
    spans: list[tuple[int, int, int]] = []
    stack: list[tuple[str, int]] = []  # (kind, depth_at_open)
    depth = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        kind_open: str | None = None
        if re.match(r"(?:export\s+)?(?:abstract\s+)?class\b", stripped):
            kind_open = "class"
        elif re.match(r"(?:export\s+)?interface\b", stripped):
            kind_open = "interface"
        elif re.match(r"(?:export\s+)?type\b[^{]*=\s*\{", stripped):
            kind_open = "type"
        line_opens = line.count("{")
        line_closes = line.count("}")
        if kind_open is not None and line_opens > 0:
            stack.append((kind_open, depth))
        current = stack[-1][0] if stack else ""
        match = pattern.search(line)
        if match and current == "class":
            spans.append((index, offsets[index] + match.start("readonly"), offsets[index] + match.end("readonly")))
        depth += line_opens - line_closes
        while stack and depth <= stack[-1][1]:
            stack.pop()
    return spans


def _readonly_assignment_cast_operations(
    *,
    path: str,
    content: str,
    property_name: str,
    diagnostic_lines: tuple[int, ...],
) -> tuple[RepairOperation, ...]:
    """Cast assignment receiver to ``any`` so readonly property writes compile.

    Live L1-01 r175: ``fireflies[0]!.sex = ...`` after ``createFirefly`` when
    class fields stay readonly for domain integrity. Cast is local and fail-closed.
    """

    if not _TS_IDENTIFIER_RE.fullmatch(property_name) or not diagnostic_lines:
        return ()
    lines = str(content or "").splitlines(keepends=True)
    operations: list[RepairOperation] = []
    before_hash = sha256_text(content)
    offsets = _line_start_offsets(lines)
    # fireflies[0]!.sex =  or  obj.sex =
    assign_re = re.compile(rf"(?P<recv>[\w$[\]!?.]+)\.(?P<prop>{re.escape(property_name)})\s*=")
    for line_number in diagnostic_lines:
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original = lines[line_index]
        match = assign_re.search(original)
        if match is None:
            continue
        recv = str(match.group("recv") or "")
        if not recv or " as any" in recv or "(as any)" in original:
            continue
        # Avoid double-wrap.
        if re.search(rf"\(\s*{re.escape(recv)}\s+as\s+any\s*\)", original):
            continue
        start = offsets[line_index] + match.start("recv")
        end = offsets[line_index] + match.end("recv")
        expected = content[start:end]
        if expected != recv:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement=f"({recv} as any)",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_readonly_assignment_cast",
                    "property": property_name,
                    "diagnostic_lines": (line_number,),
                    "unique_context": original,
                },
            )
        )
    return tuple(operations)


def _readonly_property_declaration_spans(content: str, prop: str) -> list[tuple[int, int, int]]:
    if not _TS_IDENTIFIER_RE.fullmatch(prop):
        return []
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    pattern = re.compile(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?)"
        rf"(?P<readonly>readonly\s+)(?P<property>{re.escape(prop)})(?=\s*[?:!:])"
    )
    spans: list[tuple[int, int, int]] = []
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if not match:
            continue
        spans.append((index, offsets[index] + match.start("readonly"), offsets[index] + match.end("readonly")))
    return spans


def _typescript_readonly_array_binding_operation(
    *,
    path: str,
    content: str,
    line: int,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    # Search upward for const binding with typed empty array.
    for idx in range(line - 1, max(-1, line - 40), -1):
        if idx < 0 or idx >= len(lines):
            continue
        text = lines[idx]
        match = re.match(
            r"^(?P<indent>\s*)const\s+(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?P<type>[^=]+?)\s*=\s*\[\s*\]\s*;\s*$",
            text.rstrip("\r\n"),
        )
        if match is None:
            continue
        type_text = str(match.group("type") or "").strip()
        if "readonly" not in type_text.lower() and "ReadonlyArray" not in type_text and "[" not in type_text:
            # Indexed access like GardenState["events"] often resolves to readonly
            if '["' not in type_text and "['" not in type_text:
                continue
        name = str(match.group("name") or "")
        # Prefer Array<T[number]> for indexed access types.
        if re.search(r'\[["\']', type_text):
            new_type = f"Array<{type_text}[number]>"
        elif type_text.startswith("readonly "):
            new_type = "Array<" + type_text[len("readonly ") :].rstrip("[]").strip() + ">"
            if type_text.endswith("[]"):
                inner = type_text[len("readonly ") : -2].strip()
                new_type = f"Array<{inner}>"
        elif type_text.startswith("ReadonlyArray<") and type_text.endswith(">"):
            new_type = "Array<" + type_text[len("ReadonlyArray<") : -1] + ">"
        else:
            new_type = f"Array<{type_text}[number]>" if "[" in type_text else f"Array<{type_text}>"
        replacement = f"{match.group('indent')}const {name}: {new_type} = [];"
        if text.endswith("\n"):
            replacement += "\n"
        span_start = sum(len(item) for item in lines[:idx])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_start + len(text),
            expected=text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_readonly_array_mutation",
                "binding": name,
                "diagnostic_line": line,
            },
        )
    return None
