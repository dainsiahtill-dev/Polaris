# ruff: noqa: F403, F405
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def build_typescript_literal_union_expand_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Repair string-literal assignments rejected by aliases or type-only enums.

    Live L1-01 r160: ``return "waxing"`` / ``"waning"`` against
    ``MoonPhaseName = "new" | "waxing-crescent" | ...``. Prefer adding the
    emitted literal to the union over rewriting every call site.

    A string enum used only as a type is semantically equivalent to its literal
    union, but TypeScript rejects assigning the serialized literals directly.
    Normalize that enum only when every member is a string literal and no
    ``Enum.Member`` runtime consumer exists anywhere in the project. Runtime
    enum authority remains fail-closed.
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    literals_by_type: dict[str, set[str]] = {}
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_STRING_NOT_ASSIGNABLE_RE.finditer(text):
            type_name = (
                match.group("type1") or match.group("type2") or match.group("type3") or match.group("type4") or ""
            )
            literal = (
                match.group("literal1")
                or match.group("literal2")
                or match.group("literal3")
                or match.group("literal4")
                or ""
            )
            type_name = str(type_name)
            literal = str(literal)
            if not _TS_IDENTIFIER_RE.fullmatch(type_name) or not literal:
                continue
            literals_by_type.setdefault(type_name, set()).add(literal)
            matched.append(diagnostic)
        if diagnostic.code.lower() == "typescript_ts2322":
            message_match = re.search(
                r"Type\s+(?:'\"(?P<literal1>[^'\"]+)\"'|\"'(?P<literal2>[^'\"]+)'\"|"
                r"'(?P<literal3>[^']+)'|\"(?P<literal4>[^\"]+)\")\s+"
                r"is\s+not\s+assignable\s+to\s+type\s+"
                r"(?:'\"(?P<type1>[A-Za-z_$][\w$]+)\"'|\"'(?P<type2>[A-Za-z_$][\w$]+)'\"|"
                r"'(?P<type3>[A-Za-z_$][\w$]+)'|\"(?P<type4>[A-Za-z_$][\w$]+)\")",
                text,
                re.IGNORECASE,
            )
            if message_match:
                type_name = str(
                    message_match.group("type1")
                    or message_match.group("type2")
                    or message_match.group("type3")
                    or message_match.group("type4")
                    or ""
                )
                literal = str(
                    message_match.group("literal1")
                    or message_match.group("literal2")
                    or message_match.group("literal3")
                    or message_match.group("literal4")
                    or ""
                )
                if _TS_IDENTIFIER_RE.fullmatch(type_name) and literal:
                    literals_by_type.setdefault(type_name, set()).add(literal)
                    matched.append(diagnostic)
    operations: list[RepairOperation] = []
    expanded: list[dict[str, object]] = []
    for type_name, literals in sorted(literals_by_type.items()):
        for path, content in sorted(normalized_base.items()):
            type_match = re.search(
                rf"(?m)^(?P<prefix>\s*(?:export\s+)?type\s+{re.escape(type_name)}\s*=\s*)"
                rf"(?P<body>[^;]+);",
                content,
            )
            if type_match is not None:
                body = str(type_match.group("body") or "")
                if "|" not in body and not re.search(r"['\"]", body):
                    continue
                missing = [
                    literal for literal in sorted(literals) if f'"{literal}"' not in body and f"'{literal}'" not in body
                ]
                if not missing:
                    continue
                addition = " | ".join(f'"{literal}"' for literal in missing)
                new_body = f"{body.strip()} | {addition}"
                expected = str(type_match.group(0) or "")
                replacement = f"{type_match.group('prefix')}{new_body};"
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=path,
                        span_start=type_match.start(),
                        span_end=type_match.end(),
                        expected=expected,
                        replacement=replacement,
                        before_hash=sha256_text(content),
                        metadata={
                            "repair_kind": "typescript_literal_union_expand",
                            "type": type_name,
                            "literals": tuple(missing),
                        },
                    )
                )
                expanded.append({"file": path, "type": type_name, "literals": list(missing)})
                break

            enum_match = re.search(
                _TS_STRING_ENUM_DECL_RE_TEMPLATE.format(type_name=re.escape(type_name)),
                content,
            )
            if enum_match is None:
                continue
            if _typescript_enum_has_runtime_member_access(
                base_files=normalized_base,
                type_name=type_name,
                declaration_path=path,
                declaration_span=(enum_match.start(), enum_match.end()),
            ):
                continue
            enum_body = str(enum_match.group("body") or "")
            members = tuple(_TS_STRING_ENUM_MEMBER_RE.finditer(enum_body))
            if not members:
                continue
            residue = enum_body
            for member_match in reversed(members):
                residue = residue[: member_match.start()] + residue[member_match.end() :]
            residue = re.sub(r"/\*.*?\*/|//[^\n]*", "", residue, flags=re.DOTALL)
            if residue.replace(",", "").strip():
                continue
            enum_literals = tuple(str(member.group("literal") or "") for member in members)
            if not literals.issubset(set(enum_literals)):
                continue
            export_prefix = str(enum_match.group("export") or "")
            indent = str(enum_match.group("indent") or "")
            replacement = (
                f"{indent}{export_prefix}type {type_name} = "
                + " | ".join(f'"{literal}"' for literal in enum_literals)
                + ";"
            )
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=enum_match.start(),
                    span_end=enum_match.end(),
                    expected=str(enum_match.group(0) or ""),
                    replacement=replacement,
                    before_hash=sha256_text(content),
                    metadata={
                        "repair_kind": "typescript_string_enum_to_literal_union",
                        "type": type_name,
                        "literals": enum_literals,
                    },
                )
            )
            expanded.append(
                {
                    "file": path,
                    "type": type_name,
                    "literals": list(enum_literals),
                    "normalized_from": "string_enum",
                }
            )
            break
    return _repair_plan_or_none(
        rule_id="typescript.literal_union_expand",
        source_tool=TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"expanded_unions": expanded},
    )


def _build_typescript_branded_literal_cast_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    brand_sources = _typescript_string_brand_type_sources(base_files)
    if not brand_sources:
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    import_requirements: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        target_type = _typescript_branded_literal_target_type(diagnostic)
        if not target_type or target_type not in brand_sources:
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        content = str(base_files.get(path) or "")
        if not path or not content:
            continue
        operation = _typescript_branded_literal_cast_operation(
            path=path,
            content=content,
            diagnostic=diagnostic,
            target_type=target_type,
        )
        if operation is None:
            continue
        operations.append(operation)
        import_requirements.setdefault(path, set()).add(target_type)
        matched_diagnostics.append(diagnostic)
        repaired.append(
            {
                "file": path,
                "target_type": target_type,
                "line": diagnostic.line,
                "column": diagnostic.column,
            }
        )

    for path, type_names in sorted(import_requirements.items()):
        content = str(base_files.get(path) or "")
        for type_name in sorted(type_names):
            source_path = brand_sources.get(type_name, "")
            if not source_path or source_path == path:
                continue
            if _typescript_file_has_type_name_import(content, type_name):
                continue
            import_operation = _typescript_insert_type_import_operation(
                path=path,
                content=content,
                type_name=type_name,
                source_path=source_path,
            )
            if import_operation is not None:
                operations.append(import_operation)

    return _repair_plan_or_none(
        rule_id="typescript.branded_literal_cast",
        source_tool=TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"branded_literal_casts": repaired},
    )


def _build_typescript_literal_union_value_facade_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    aliases = _typescript_string_literal_union_type_aliases(base_files)
    if not aliases:
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    processed_symbols: set[str] = set()
    for diagnostic in diagnostics:
        symbol = _typescript_type_only_value_usage_symbol(diagnostic)
        if not symbol or symbol in processed_symbols or symbol not in aliases:
            continue
        path, span_start, span_end, expected, literals, exported = aliases[symbol]
        use_member = _typescript_type_value_dot_member(
            base_files=base_files,
            diagnostic=diagnostic,
            symbol=symbol,
        )
        if not use_member or use_member not in literals:
            continue
        operation = _typescript_literal_union_value_facade_operation(
            path=path,
            content=str(base_files.get(path) or ""),
            span_start=span_start,
            span_end=span_end,
            expected=expected,
            type_name=symbol,
            literals=literals,
            exported=exported,
        )
        if operation is None:
            continue
        operations.append(operation)
        matched_diagnostics.append(diagnostic)
        processed_symbols.add(symbol)
        repaired.append(
            {
                "file": path,
                "type_name": symbol,
                "literals": tuple(literals),
                "usage_member": use_member,
                "diagnostic_path": diagnostic.path,
            }
        )

    return _repair_plan_or_none(
        rule_id="typescript.literal_union_value_facade",
        source_tool=TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"literal_union_value_facades": repaired},
    )


def _typescript_branded_literal_target_type(diagnostic: RepairDiagnostic) -> str:
    text = f"{diagnostic.message}\n{diagnostic.raw}"
    match = _TS_BRANDED_STRING_ASSIGNMENT_MESSAGE_RE.search(text)
    if not match:
        return ""
    candidate = str(match.group("type") or "").strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""


def _typescript_branded_literal_cast_operation(
    *,
    path: str,
    content: str,
    diagnostic: RepairDiagnostic,
    target_type: str,
) -> RepairOperation | None:
    line_number = int(diagnostic.line or 0)
    column_number = int(diagnostic.column or 0)
    if line_number <= 0:
        return None
    lines = content.splitlines(keepends=True)
    if line_number > len(lines):
        return None
    line_start = sum(len(line) for line in lines[: line_number - 1])
    line = lines[line_number - 1]
    search_start = max(0, min(len(line), column_number - 1 if column_number > 0 else 0))
    literal_match = _find_string_literal_after_column(line, search_start)
    if literal_match is None:
        return None
    literal_end = literal_match.end()
    trailing = line[literal_end : literal_end + 40]
    if re.match(r"\s+as\s+[A-Za-z_$][\w$]*", trailing):
        return None
    before_hash = sha256_text(content)
    literal = literal_match.group(0)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start + literal_match.start(),
        span_end=line_start + literal_match.end(),
        expected=literal,
        replacement=f"{literal} as {target_type}",
        before_hash=before_hash,
        metadata={
            "repair_kind": "typescript_branded_literal_cast",
            "target_type": target_type,
            "line": line_number,
            "column": column_number,
        },
    )


def _find_string_literal_after_column(line: str, column_index: int) -> re.Match[str] | None:
    for match in re.finditer(r"(['\"])(?:\\.|(?!\1).)*\1", line):
        if match.end() <= column_index:
            continue
        return match
    return None


def _typescript_string_literal_union_type_aliases(
    base_files: Mapping[str, str],
) -> dict[str, tuple[str, int, int, str, tuple[str, ...], bool]]:
    aliases: dict[str, tuple[str, int, int, str, tuple[str, ...], bool]] = {}
    for path, content in base_files.items():
        normalized = _normalize_repair_path(path)
        if not normalized.endswith((".ts", ".tsx")):
            continue
        text = str(content or "")
        for match in _TS_STRING_LITERAL_UNION_TYPE_ALIAS_RE.finditer(text):
            name = str(match.group("name") or "").strip()
            if not name or name in aliases:
                continue
            literals = _typescript_identifier_string_literal_union_values(str(match.group("body") or ""))
            if len(literals) < 2:
                continue
            aliases[name] = (
                normalized,
                match.start(),
                match.end(),
                str(match.group(0) or ""),
                literals,
                bool(match.group("export")),
            )
    return aliases


def _typescript_identifier_string_literal_union_values(body: str) -> tuple[str, ...]:
    literals: list[str] = []
    seen: set[str] = set()
    for part in str(body or "").split("|"):
        token = part.strip()
        match = re.fullmatch(r"(['\"])(?P<literal>[A-Za-z_$][\w$]*)\1", token)
        if not match:
            return ()
        literal = str(match.group("literal") or "")
        if literal in seen:
            continue
        seen.add(literal)
        literals.append(literal)
    return tuple(literals)


def _typescript_literal_union_value_facade_operation(
    *,
    path: str,
    content: str,
    span_start: int,
    span_end: int,
    expected: str,
    type_name: str,
    literals: Sequence[str],
    exported: bool,
) -> RepairOperation | None:
    if re.search(rf"\bconst\s+{re.escape(type_name)}\s*=", content):
        return None
    export_prefix = "export " if exported else ""
    entries = "\n".join(f'  {literal}: "{literal}",' for literal in literals)
    replacement = (
        f"{export_prefix}const {type_name} = {{\n"
        f"{entries}\n"
        f"}} as const;\n"
        f"{export_prefix}type {type_name} = (typeof {type_name})[keyof typeof {type_name}];"
    )
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_literal_union_value_facade",
            "type_name": type_name,
            "literal_count": len(tuple(literals)),
        },
    )
