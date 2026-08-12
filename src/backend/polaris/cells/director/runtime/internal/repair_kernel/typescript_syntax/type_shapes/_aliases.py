# ruff: noqa: F403, F405, F841
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


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


def _is_string_literal_suggestion_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code.lower() != "typescript_ts2820":
        return False
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    if str(metadata.get("actual") or "").strip() and str(metadata.get("suggestion") or "").strip():
        return True
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return "not assignable to type" in message and "did you mean" in message


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
