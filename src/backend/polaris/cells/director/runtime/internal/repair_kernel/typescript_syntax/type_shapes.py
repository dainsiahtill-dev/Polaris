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

"""TypeScript syntax repair module: type_shapes."""

def _typescript_camel_case_hyphenated_identifier(left: str, right: str) -> str:
    if not left or not right:
        return ""
    return f"{left}{right[0].upper()}{right[1:]}"

def _repair_typescript_hyphenated_identifiers(
    *,
    original: str,
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    lines = str(original or "").splitlines(keepends=True)
    replacements: dict[str, str] = {}
    diagnostic_ids: list[str] = []
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        line_number = _typescript_diagnostic_line(diagnostic)
        if not line_number or line_number < 1 or line_number > len(lines):
            continue
        line = lines[line_number - 1].rstrip("\r\n")
        match = _TS_HYPHENATED_VARIABLE_DECLARATION_RE.search(line)
        if not match:
            continue
        old_name = f"{match.group('left')}-{match.group('right')}"
        new_name = _typescript_camel_case_hyphenated_identifier(match.group("left"), match.group("right"))
        if not old_name or not new_name or old_name == new_name:
            continue
        replacements[old_name] = new_name
        diagnostic_ids.append(diagnostic.diagnostic_id)

    repaired = str(original or "")
    for old_name, new_name in sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])):
        token_re = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(old_name)}(?![A-Za-z0-9_$])")
        repaired = token_re.sub(new_name, repaired)
    return repaired, replacements, tuple(diagnostic_ids)

def build_typescript_hyphenated_identifier_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS1005 repair plan for illegal hyphenated variable identifiers."""

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
        repaired, replacements, diagnostic_ids = _repair_typescript_hyphenated_identifiers(
            original=original,
            diagnostics=diagnostics_by_path[path],
        )
        if repaired == original or not replacements:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "diagnostic_ids": diagnostic_ids,
                    "repair_kind": "typescript_hyphenated_identifier",
                    "replacements": dict(replacements),
                },
            )
        )
        matched_diagnostics.extend(diagnostics_by_path[path])

    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.hyphenated_identifier",
        source_tool=TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"runtime_plan_scope": "same_file_hyphenated_variable_identifier"},
    )

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

def build_typescript_readonly_assignment_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build TS2540/TS2542 repairs for readonly property and ReadonlyArray index writes."""

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
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    all_paths = sorted(set(targets_by_path) | set(index_targets_by_path))
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
                    literal
                    for literal in sorted(literals)
                    if f'"{literal}"' not in body and f"'{literal}'" not in body
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

def build_typescript_init_property_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Rename common excess init keys on object literals (R160 TS2353).

    Live L1-01 r160: ``createGarden({ fireflies: 6, flowers: 5, humidity: 0.7 })``
    against ``GardenInit { fireflyCount?, flowerCount?, initialHumidity? }``.
    Only applies a fail-closed known alias table — never invents domain fields.
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    renames: list[dict[str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_EXCESS_OBJECT_PROPERTY_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line_number = _to_positive_int(match.group("line"))
            property_name = str(match.group("property") or "")
            type_name = str(match.group("type_name") or "")
            if not path or line_number <= 0:
                continue
            # Prefer types that declare aliases. *Init interfaces may use optional
            # fields that the member scanner misses — allow known Init aliases then.
            existing = _typescript_existing_member_names_for_type(base_files=normalized_base, type_name=type_name)
            content = str(normalized_base.get(path) or "")
            if not content:
                continue
            lines = content.splitlines(keepends=True)
            line_index = line_number - 1
            if line_index < 0 or line_index >= len(lines):
                continue
            key = (path, line_index, "*")
            if key in seen:
                continue
            seen.add(key)
            # Apply all known aliases on the same object-literal line once tsc flags
            # any excess property (R160: fireflies then flowers then humidity).
            repaired_line = lines[line_index]
            applied: list[tuple[str, str]] = []
            for source_name, alias in _INIT_PROPERTY_ALIASES.items():
                if existing and alias not in existing:
                    continue
                if not existing and not type_name.endswith("Init"):
                    continue
                candidate = re.sub(
                    rf"\b{re.escape(source_name)}\s*:",
                    f"{alias}:",
                    repaired_line,
                )
                if candidate != repaired_line:
                    repaired_line = candidate
                    applied.append((source_name, alias))
            if not applied or repaired_line == lines[line_index]:
                continue
            operations.append(
                _line_text_replace_operation(
                    path=path,
                    content=content,
                    line_index=line_index,
                    replacement=repaired_line,
                    metadata={
                        "repair_kind": "typescript_init_property_alias",
                        "type": type_name,
                        "renames": tuple(applied),
                    },
                )
            )
            for source_name, alias in applied:
                renames.append({"file": path, "from": source_name, "to": alias, "type": type_name})
    return _repair_plan_or_none(
        rule_id="typescript.init_property_alias",
        source_tool=TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"renames": renames},
    )

def build_typescript_arg_type_function_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Rewrite wrong-domain callees when argument type mismatches the parameter (R161).

    Live L1-01 r161: ``adjustHumidity(flowerState, ...)`` where Flower domain exposes
    ``adjustHydration(state: FlowerState, ...)``. Prefer renaming the callee to a
    same-arity function that accepts the actual argument type. Also renames the
    matching named import so the new callee resolves (avoids TS2304).
    """

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    rewrites: list[dict[str, str]] = []
    seen: set[tuple[str, int, str, str]] = set()
    import_seen: set[tuple[str, str, str]] = set()
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_ARG_TYPE_MISMATCH_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line_number = _to_positive_int(match.group("line"))
            actual = str(match.group("actual") or "")
            expected = str(match.group("expected") or "")
            if not path or line_number <= 0 or actual == expected:
                continue
            content = str(normalized_base.get(path) or "")
            if not content:
                continue
            lines = content.splitlines(keepends=True)
            line_index = line_number - 1
            if line_index < 0 or line_index >= len(lines):
                continue
            line = lines[line_index]
            # Find callee identifiers on the line that take a first-arg type of expected
            # (the diagnosed parameter type) and rewrite to a function accepting actual.
            candidates = _functions_accepting_type(base_files=normalized_base, type_name=actual)
            if not candidates:
                continue
            call_matches = list(re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\(", line))
            for call in call_matches:
                current = str(call.group(1) or "")
                if current in {"if", "for", "while", "switch", "catch", "function", "return"}:
                    continue
                alias = _pick_function_alias(current=current, candidates=candidates)
                if not alias:
                    continue
                key = (path, line_index, current, alias)
                if key in seen:
                    continue
                seen.add(key)
                repaired_line = line[: call.start(1)] + alias + line[call.end(1) :]
                if repaired_line == line:
                    continue
                operations.append(
                    _line_text_replace_operation(
                        path=path,
                        content=content,
                        line_index=line_index,
                        replacement=repaired_line,
                        metadata={
                            "repair_kind": "typescript_arg_type_function_alias",
                            "from": current,
                            "to": alias,
                            "actual_type": actual,
                            "expected_type": expected,
                        },
                    )
                )
                rewrites.append(
                    {
                        "file": path,
                        "from": current,
                        "to": alias,
                        "actual_type": actual,
                        "expected_type": expected,
                    }
                )
                matched.append(diagnostic)
                import_key = (path, current, alias)
                if import_key not in import_seen:
                    import_seen.add(import_key)
                    for imp_index, imp_line in _rewrite_named_import_binding_lines(
                        content=content,
                        old_name=current,
                        new_name=alias,
                    ):
                        if imp_index == line_index:
                            continue
                        operations.append(
                            _line_text_replace_operation(
                                path=path,
                                content=content,
                                line_index=imp_index,
                                replacement=imp_line,
                                metadata={
                                    "repair_kind": "typescript_arg_type_function_alias_import",
                                    "from": current,
                                    "to": alias,
                                },
                            )
                        )
                # One rewrite per diagnostic line is enough (first matching callee).
                break
    return _repair_plan_or_none(
        rule_id="typescript.arg_type_function_alias",
        source_tool=TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"rewrites": rewrites},
    )

def build_typescript_string_literal_suggestion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS2820 string-literal suggestion repair plan."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_string_literal_suggestion_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _string_literal_suggestion_operations(
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
                "actual": str(operation.metadata.get("actual") or ""),
                "suggestion": str(operation.metadata.get("suggestion") or ""),
                "line": int(operation.metadata.get("line") or 0),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.string_literal_suggestion",
        source_tool=TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"string_literal_suggestions": repaired_items},
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

def build_typescript_canvas_scale_return_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for scaleToCanvas return type drift."""

    if not _has_number_to_function_argument_diagnostic(diagnostics):
        return None
    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(normalized_base_files):
        if path.endswith(".d.ts") or not path.endswith((".ts", ".tsx")):
            continue
        original = str(normalized_base_files.get(path) or "")
        operation = _canvas_scale_return_type_operation(path=path, content=original)
        if operation is None:
            continue
        operations.append(operation)
        repaired_items.append({"file": path, "kind": "scaleToCanvas"})
    if not operations:
        return None
    matched_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _is_number_to_function_argument(diagnostic))
    return RepairPlan(
        rule_id="typescript.canvas_scale_return_type",
        source_tool=TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"return_types": repaired_items},
    )

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
        too_many = _too_many_arguments_declaration_expand_operation(
            base_files=base_files,
            method_name=method_name,
            expected_count=expected_count,
            got_count=got_count,
        )
        if too_many is not None:
            return too_many
        return _too_many_arguments_declaration_operation(
            base_files=base_files,
            method_name=method_name,
            expected_count=expected_count,
        )
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
    replacement = _select_typescript_unresolved_identifier_replacement(lines, target_index, missing_symbol)
    line = lines[target_index]
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

def _parse_string_literal_suggestion_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str, str]]]:
    by_path: dict[str, set[tuple[int, int, str, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_STRING_LITERAL_SUGGESTION_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            actual = _strip_typescript_literal_type(str(match.group("actual") or "").strip())
            suggestion = _strip_typescript_literal_type(str(match.group("suggestion") or "").strip())
            if _valid_string_literal_suggestion_target(path, line, column, actual, suggestion):
                by_path.setdefault(path, set()).add((line, column, actual, suggestion))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_string_literal_suggestion_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
            actual = _strip_typescript_literal_type(str(metadata.get("actual") or "").strip())
            suggestion = _strip_typescript_literal_type(str(metadata.get("suggestion") or "").strip())
            if not actual or not suggestion:
                inline_match = re.search(
                    r"Type (?P<actual_quote>['\"])(?P<actual>.*?)(?P=actual_quote) is not assignable to type "
                    r"(?P<target_quote>['\"]).*?(?P=target_quote)\.\s+Did you mean "
                    r"(?P<suggestion_quote>['\"])(?P<suggestion>.*?)(?P=suggestion_quote)\?",
                    text,
                )
                actual = (
                    _strip_typescript_literal_type(str(inline_match.group("actual") or "").strip())
                    if inline_match
                    else ""
                )
                suggestion = (
                    _strip_typescript_literal_type(str(inline_match.group("suggestion") or "").strip())
                    if inline_match
                    else ""
                )
            line = int(diagnostic.line)
            column = int(diagnostic.column)
            if _valid_string_literal_suggestion_target(path, line, column, actual, suggestion):
                by_path.setdefault(path, set()).add((line, column, actual, suggestion))
    return by_path

def _valid_string_literal_suggestion_target(
    path: str,
    line: int,
    column: int,
    actual: str,
    suggestion: str,
) -> bool:
    return (
        bool(path)
        and line > 0
        and column > 0
        and bool(actual)
        and bool(suggestion)
        and actual != suggestion
        and "\n" not in actual
        and "\r" not in actual
        and "\n" not in suggestion
        and "\r" not in suggestion
        and len(actual) <= 160
        and len(suggestion) <= 160
    )

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

def _is_readonly_assignment_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts2540"
        and "cannot assign to" in message
        and "read-only property" in message
    )

def _is_string_literal_suggestion_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code.lower() != "typescript_ts2820":
        return False
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    if str(metadata.get("actual") or "").strip() and str(metadata.get("suggestion") or "").strip():
        return True
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return "not assignable to type" in message and "did you mean" in message

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

def _string_literal_suggestion_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str, str]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    operations: list[RepairOperation] = []
    seen_lines: set[int] = set()
    for line_number, column, actual, suggestion in sorted(targets):
        if line_number in seen_lines:
            continue
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        matches = list(_string_literal_matches(line_body, actual))
        if len(matches) != 1:
            continue
        match = matches[0]
        if not _column_is_near_span(column, match.start(), match.end()):
            continue
        quote = str(match.group("quote") or '"')
        replacement_literal = f"{quote}{_escape_typescript_string_literal(suggestion, quote)}{quote}"
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index] + match.start(),
                span_end=offsets[line_index] + match.end(),
                expected=match.group(0),
                replacement=replacement_literal,
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_string_literal_suggestion",
                    "actual": actual,
                    "suggestion": suggestion,
                    "line": line_number,
                    "column": column,
                    "unique_context": f"{line_body}{newline}",
                },
            )
        )
        seen_lines.add(line_number)
    return tuple(operations)

def _string_literal_matches(line: str, actual: str) -> tuple[re.Match[str], ...]:
    if not actual:
        return ()
    double_escaped = _escape_typescript_string_literal(actual, '"')
    pattern = re.compile(rf"(?P<quote>['\"]){re.escape(double_escaped)}(?P=quote)")
    matches = list(pattern.finditer(line))
    if matches:
        return tuple(matches)
    single_escaped = _escape_typescript_string_literal(actual, "'")
    pattern = re.compile(rf"(?P<quote>['\"]){re.escape(single_escaped)}(?P=quote)")
    return tuple(pattern.finditer(line))

def _escape_typescript_string_literal(value: str, quote: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\")
    if quote == "'":
        return escaped.replace("'", "\\'")
    return escaped.replace('"', '\\"')

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

def _canvas_scale_return_type_operation(*, path: str, content: str) -> RepairOperation | None:
    if "scaleToCanvas" not in content or "sx:" not in content or "sy:" not in content:
        return None
    if not re.search(r"sx\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    if not re.search(r"sy\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    match = _TS_CANVAS_SCALE_RETURN_TYPE_RE.search(content)
    if not match:
        return None
    replacement = "{ sx: (n: number) => number; sy: (n: number) => number; scale: number }"
    expected = str(match.group("return_type") or "")
    if expected == replacement:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("return_type"),
        span_end=match.end("return_type"),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_canvas_scale_return_type",
            "symbol": "scaleToCanvas",
        },
    )

def build_typescript_implicit_return_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Add ``: void`` to interface method signatures reported by TS7010 (R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        match = _TS7010_IMPLICIT_RETURN_RE.search(raw)
        code = str(diagnostic.code or "").lower()
        name = ""
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        if match:
            path = path or _normalize_repair_path(str(match.group("file") or ""))
            line = line or int(match.group("line") or 0)
            name = str(match.group("name") or "")
        elif code == "typescript_ts7010":
            name_match = re.search(r"['\"]([A-Za-z_$][\w$]*)['\"],\s*which lacks return-type", raw, re.I)
            name = str(name_match.group(1) if name_match else "")
        else:
            continue
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0 or not name:
            continue
        op = _typescript_implicit_return_void_operation(path=path, content=content, line=line, name=name)
        if op is None:
            continue
        operations.append(op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.implicit_return_type",
        source_tool=TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"implicit_return_type_count": len(operations)},
    )

def _typescript_implicit_return_void_operation(
    *,
    path: str,
    content: str,
    line: int,
    name: str,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    # Prefer interface method declarations (end with ");").
    in_interface = False
    depth = 0
    for idx, text in enumerate(lines, start=1):
        stripped = text.strip()
        if re.match(r"(?:export\s+)?interface\s+\w+", stripped):
            in_interface = True
            depth = 0
        if in_interface:
            depth += text.count("{") - text.count("}")
            if depth <= 0 and "}" in stripped and idx > 1:
                in_interface = False
        if idx != line:
            continue
        pattern = re.compile(rf"^(?P<indent>\s*){re.escape(name)}\s*\((?P<params>[^;]*)\)\s*;\s*$")
        match = pattern.match(text.rstrip("\n") + ("\n" if text.endswith("\n") else ""))
        # allow trailing newline variations
        match = pattern.match(text.rstrip("\r\n"))
        if match is None or re.search(r"\)\s*:", text):
            return None
        if not in_interface and "interface" not in "".join(lines[max(0, idx - 30) : idx]).lower():
            # Only auto-fix pure declaration form ending with semicolon (interface-like).
            if not text.rstrip().endswith(";"):
                return None
        replacement = f"{match.group('indent')}{name}({match.group('params')}): void;"
        if text.endswith("\n"):
            replacement += "\n"
        span_start = sum(len(item) for item in lines[: idx - 1])
        span_end = span_start + len(text)
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_implicit_return_type",
                "method": name,
                "diagnostic_line": line,
            },
        )
    return None

def build_typescript_object_assign_assertion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Assert ``Object.freeze({...}) as Type`` for TS2322 object→named-type assigns (R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        code = str(diagnostic.code or "").lower()
        if code not in {"", "typescript_ts2322"} and "ts2322" not in code:
            if "is not assignable to type" not in raw.lower():
                continue
        match = _TS2322_ASSIGN_TO_NAMED_TYPE_RE.search(raw)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        type_name = ""
        if match:
            path = path or _normalize_repair_path(str(match.group("file") or ""))
            line = line or int(match.group("line") or 0)
            type_name = str(match.group("type") or "")
        else:
            type_match = re.search(r"not assignable to type ['\"]([A-Za-z_$][\w$]*)['\"]", raw, re.I)
            type_name = str(type_match.group(1) if type_match else "")
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0 or not type_name:
            continue
        op = _typescript_object_freeze_assert_operation(path=path, content=content, line=line, type_name=type_name)
        if op is None:
            continue
        operations.append(op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.object_assign_assertion",
        source_tool=TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"object_assign_assertions": len(operations)},
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

__all__ = (
    "_typescript_camel_case_hyphenated_identifier",
    "_repair_typescript_hyphenated_identifiers",
    "build_typescript_hyphenated_identifier_plan",
    "build_typescript_number_to_string_argument_plan",
    "build_typescript_readonly_assignment_plan",
    "build_typescript_literal_union_expand_plan",
    "build_typescript_init_property_alias_plan",
    "build_typescript_arg_type_function_alias_plan",
    "build_typescript_string_literal_suggestion_plan",
    "build_typescript_number_property_call_plan",
    "build_typescript_canvas_scale_return_type_plan",
    "_build_typescript_value_used_as_type_plan",
    "_build_typescript_branded_literal_cast_plan",
    "_build_typescript_literal_union_value_facade_plan",
    "_build_typescript_too_few_arguments_plan",
    "_build_typescript_unresolved_identifier_plan",
    "_parse_typescript_value_used_as_type_errors",
    "_replace_typescript_value_used_as_type_reference",
    "_typescript_branded_literal_target_type",
    "_typescript_branded_literal_cast_operation",
    "_find_string_literal_after_column",
    "_typescript_string_literal_union_type_aliases",
    "_typescript_identifier_string_literal_union_values",
    "_typescript_literal_union_value_facade_operation",
    "_parse_typescript_too_few_arguments_errors",
    "_too_few_arguments_operation",
    "_too_few_arguments_callsite_operation",
    "_repair_typescript_unresolved_identifier_lines",
    "_select_typescript_unresolved_identifier_replacement",
    "_typescript_unresolved_identifier_is_array_length_assertion",
    "_parse_number_to_string_argument_targets",
    "_parse_number_property_call_targets",
    "_parse_readonly_assignment_targets",
    "_parse_readonly_index_assignment_targets",
    "_readonly_array_index_assignment_operations",
    "_parse_string_literal_suggestion_targets",
    "_valid_string_literal_suggestion_target",
    "_is_number_to_string_argument",
    "_is_number_property_call_diagnostic",
    "_is_readonly_assignment_diagnostic",
    "_is_string_literal_suggestion_diagnostic",
    "_number_to_string_argument_operations",
    "_number_property_call_operations",
    "_number_property_call_candidate",
    "_string_literal_suggestion_operations",
    "_string_literal_matches",
    "_escape_typescript_string_literal",
    "_readonly_assignment_operations",
    "_readonly_assignment_class_field_operations",
    "_readonly_class_field_declaration_spans",
    "_readonly_assignment_cast_operations",
    "_readonly_property_declaration_spans",
    "_canvas_scale_return_type_operation",
    "build_typescript_implicit_return_type_plan",
    "_typescript_implicit_return_void_operation",
    "build_typescript_object_assign_assertion_plan",
    "build_typescript_readonly_array_mutation_plan",
    "_typescript_readonly_array_binding_operation",
    "build_typescript_identifier_suggestion_plan",
    "build_typescript_unused_local_plan",
    "_parse_typescript_unused_local_diagnostic",
    "_typescript_delete_unused_local_function_operation",
    "_typescript_prefix_unused_local_operation",
    "build_typescript_argument_shape_adapter_plan",
    "_parse_typescript_identifier_suggestion_diagnostic",
    "_typescript_adapt_argument_shape_operation",
    "_typescript_build_argument_shape_adapter",
)
