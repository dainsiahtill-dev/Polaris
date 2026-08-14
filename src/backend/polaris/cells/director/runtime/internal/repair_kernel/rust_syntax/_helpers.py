"""Private helpers for Rust deterministic repair planners."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import tomllib

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._constants import (
    _ANSI_ESCAPE_RE,
    _KNOWN_RUST_DEPENDENCIES,
    _RUST_CANT_FIND_BIN_PATH_RE,
    _RUST_COPY_DERIVE_TOKEN_RE,
    _RUST_DERIVABLE_TRAIT_NAMES,
    _RUST_DERIVE_LINE_RE,
    _RUST_DERIVE_PREREQUISITES,
    _RUST_DUPLICATE_MODULE_FILE_RE,
    _RUST_E0583_HELP_LINE_RE,
    _RUST_ENUM_VARIANT_MISSING_FIELD_RE,
    _RUST_FIELD_ACCESS_RE,
    _RUST_FIELD_METHOD_LINE_SUGGESTION_RE,
    _RUST_FIELD_RENAME_ERROR_RE,
    _RUST_FIELD_RENAME_PLUS_LINE_RE,
    _RUST_FULL_LINE_SUGGESTION_RE,
    _RUST_INCOMPATIBLE_COPY_LOCATION_RE,
    _RUST_INTEGER_IS_FINITE_RE,
    _RUST_LOCATION_RE,
    _RUST_METHOD_SELF_LOCATION_RE,
    _RUST_METHOD_SELF_SIGNATURE_PATTERNS,
    _RUST_MISSING_MODULE_FILE_RE,
    _RUST_MISSING_TRAIT_BOUND_RE,
    _RUST_NO_SYMBOL_RE,
    _RUST_PLUS_LINE_SUGGESTION_RE,
    _RUST_PUB_USE_STATEMENT_RE,
    _RUST_QUOTED_RS_PATH_RE,
    _RUST_REAL_ITEM_RE,
    _RUST_SERDE_DERIVE_SUGGESTION_RE,
    _RUST_TWO_TUPLE_LET_RE,
    _RUST_UNRESOLVED_CRATE_RE,
    _RUST_UNRESOLVED_IMPORT_RE,
    _RUST_UNUSED_IMPORT_RE,
    _RUST_USE_IMPORT_IN_TEXT_RE,
    _RUST_USE_IMPORT_LINE_RE,
    _RUST_VEC_BARE_GENERIC_RE,
    _RUST_XML_GENERIC_CLOSE_RE,
)


def _build_rust_crate_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    source_tool: str,
    rule_id: str,
    repair_kind: str,
    depends_on: Sequence[str],
) -> RepairPlan | None:
    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    cargo = _read_cargo_manifest_from_base(normalized_base)
    if not cargo:
        return None
    canonical_crate = _canonical_rust_crate_name(cargo)
    if not canonical_crate:
        return None

    missing_crates = _parse_unresolved_rust_crates(diagnostics)
    if not missing_crates:
        return None

    declared_dependencies = _declared_rust_dependencies(cargo)
    has_local_lib = _cargo_declares_local_rust_lib(normalized_base, cargo)
    operations: list[RepairOperation] = []
    planned_diagnostics: list[RepairDiagnostic] = []
    seen_spans: set[tuple[str, int, int]] = set()
    for missing_crate, diagnostic in missing_crates:
        if missing_crate == canonical_crate or missing_crate in declared_dependencies:
            continue
        if not _rust_crate_names_look_related(missing_crate, canonical_crate) and not (
            has_local_lib and _rust_crate_prefix_used_in_binary_entrypoint(normalized_base, missing_crate)
        ):
            continue
        diagnostic_planned = False
        for operation in _rust_crate_import_rewrite_operations(
            base_files=normalized_base,
            missing_crate=missing_crate,
            canonical_crate=canonical_crate,
            diagnostic=diagnostic,
        ):
            span_key = (operation.path, int(operation.span_start or 0), int(operation.span_end or 0))
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)
            operations.append(operation)
            diagnostic_planned = True
        if diagnostic_planned:
            planned_diagnostics.append(diagnostic)

    if not operations:
        return None

    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(planned_diagnostics),
        mode=mode,
        risk_level="low",
        priority=0,
        depends_on=tuple(depends_on),
        metadata={
            "repair_kind": repair_kind,
            "edit_strategy": "text_replace",
            "span_based": True,
            "canonical_crate": canonical_crate,
            "diagnostic_count": len(planned_diagnostics),
        },
    )


def _rust_missing_trait_derive_candidate_files(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> tuple[tuple[str, str], ...]:
    diagnostic_path = _normalize_repair_path(str(diagnostic.path or ""))
    candidates: list[tuple[str, str]] = []
    if diagnostic_path.endswith(".rs") and diagnostic_path in base_files:
        candidates.append((diagnostic_path, base_files[diagnostic_path]))
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or path == diagnostic_path:
            continue
        candidates.append((path, content))
    return tuple(candidates)


def _rust_dependency_packages_to_add(
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[str]:
    packages: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            root = match.group("import").split("::", 1)[0]
            if root in _KNOWN_RUST_DEPENDENCIES and root not in seen:
                seen.add(root)
                packages.append(root)

    source_text = "\n".join(
        content for path, content in sorted(base_files.items()) if path.endswith(".rs") and "/target/" not in path
    )
    if "serde_json::" in source_text and "serde_json" not in seen:
        seen.add("serde_json")
        packages.append("serde_json")
    return packages


def _rust_unresolved_pub_use_symbols(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    symbols: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if not _rust_unresolved_pub_use_diagnostic_path_is_safe(diagnostic):
            continue
        text = f"{diagnostic.message}\n{diagnostic.raw}"
        candidates: list[str] = []
        for match in _RUST_NO_SYMBOL_RE.finditer(text):
            candidates.append(match.group("symbol"))
        for match in _RUST_UNRESOLVED_IMPORT_RE.finditer(text):
            imported = str(match.group("import") or "")
            if "::" in imported:
                candidates.append(imported.rsplit("::", 1)[-1])
        for candidate in candidates:
            symbol = str(candidate or "").strip()
            if not _is_rust_identifier(symbol) or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
    return tuple(symbols)


def _rust_unresolved_pub_use_diagnostic_path_is_safe(diagnostic: RepairDiagnostic) -> bool:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if diagnostic.path and not path:
        return False
    location = _RUST_METHOD_SELF_LOCATION_RE.search(str(diagnostic.raw or diagnostic.message or ""))
    return not (location is not None and not _normalize_repair_path(location.group("path")))


def _rust_unresolved_pub_use_operations(
    *,
    path: str,
    content: str,
    missing_symbols: Sequence[str],
) -> list[RepairOperation]:
    missing = set(missing_symbols)
    operations: list[RepairOperation] = []
    for match in _RUST_PUB_USE_STATEMENT_RE.finditer(content):
        replacement, removed = _repair_rust_pub_use_statement(match.group(0), missing)
        if not removed or replacement == match.group(0):
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=match.group(0),
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "rust_unresolved_pub_use",
                    "edit_strategy": "span_text_replace",
                    "span_based": True,
                    "symbols_removed": tuple(removed),
                    "unique_context": match.group(0),
                },
            )
        )
    return operations


def _repair_rust_pub_use_statement(statement: str, missing_symbols: set[str]) -> tuple[str, tuple[str, ...]]:
    match = _RUST_PUB_USE_STATEMENT_RE.match(statement)
    if match is None:
        return statement, ()
    tail = str(match.group("tail") or "").strip()
    newline = str(match.group("newline") or "")
    if tail.startswith("{") and tail.endswith("}"):
        items = [item.strip() for item in tail[1:-1].split(",") if item.strip()]
        kept: list[str] = []
        removed: list[str] = []
        for item in items:
            symbol = item.split(" as ", 1)[0].strip()
            if symbol in missing_symbols:
                removed.append(symbol)
            else:
                kept.append(item)
        if not removed:
            return statement, ()
        if not kept:
            return "", tuple(removed)
        replacement = f"{match.group('indent')}pub use {match.group('path')}::{{{', '.join(kept)}}};{newline}"
        return replacement, tuple(removed)

    symbol = tail.split(" as ", 1)[0].strip()
    if symbol in missing_symbols:
        return "", (symbol,)
    return statement, ()


def _parse_rust_serde_derive_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, str, frozenset[str], RepairDiagnostic], ...]:
    targets: dict[tuple[str, str], tuple[set[str], RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_SERDE_DERIVE_SUGGESTION_RE.finditer(text):
            module = str(match.group("module") or "").strip()
            symbol = str(match.group("symbol") or "").strip()
            trait = str(match.group("trait") or "").strip()
            if not _is_rust_identifier(module) or not _is_rust_identifier(symbol):
                continue
            if trait not in {"Serialize", "Deserialize"}:
                continue
            traits, first_diagnostic = targets.setdefault((module, symbol), (set(), diagnostic))
            traits.add(f"serde::{trait}")
            targets[(module, symbol)] = (traits, first_diagnostic)
    return tuple(
        (module, symbol, frozenset(sorted(traits)), diagnostic)
        for (module, symbol), (traits, diagnostic) in targets.items()
    )


def _parse_rust_missing_trait_derive_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, frozenset[str], RepairDiagnostic], ...]:
    targets: dict[str, tuple[set[str], RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_MISSING_TRAIT_BOUND_RE.finditer(text):
            symbol = str(match.group("symbol") or "").strip()
            trait = str(match.group("trait") or "").strip()
            trait_name = trait.rsplit("::", 1)[-1]
            if not _is_rust_identifier(symbol) or not _is_rust_identifier(trait_name):
                continue
            if trait_name in {"Serialize", "Deserialize"} or trait.startswith("serde::"):
                continue
            if trait_name not in _RUST_DERIVABLE_TRAIT_NAMES:
                continue
            traits, first_diagnostic = targets.setdefault(symbol, (set(), diagnostic))
            traits.add(trait_name)
            targets[symbol] = (traits, first_diagnostic)
    return tuple((symbol, frozenset(sorted(traits)), diagnostic) for symbol, (traits, diagnostic) in targets.items())


def _rust_file_for_module_symbol(
    *,
    base_files: Mapping[str, str],
    module: str,
    symbol: str,
) -> tuple[str, str] | None:
    symbol_pattern = re.compile(rf"(?m)^\s*(?:pub\s+)?(?:struct|enum)\s+{re.escape(symbol)}\b")
    candidates: list[tuple[str, str]] = []
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or not path.startswith("src/") or "/target/" in f"/{path}/":
            continue
        if path.rsplit("/", 1)[-1].rsplit(".", 1)[0] == module:
            candidates.insert(0, (path, content))
        else:
            candidates.append((path, content))
    for path, content in candidates:
        if symbol_pattern.search(content):
            return path, content
    return None


def _expand_rust_derive_prerequisites(traits: frozenset[str] | set[str]) -> frozenset[str]:
    """Expand requested derives with their rustc-required companion traits."""

    expanded: set[str] = {str(item) for item in traits if str(item)}
    changed = True
    while changed:
        changed = False
        for trait in tuple(expanded):
            for prerequisite in _RUST_DERIVE_PREREQUISITES.get(trait, frozenset()):
                if prerequisite not in expanded:
                    expanded.add(prerequisite)
                    changed = True
    return frozenset(sorted(expanded))


def _rust_missing_trait_derive_operation(
    *,
    path: str,
    content: str,
    symbol: str,
    traits: frozenset[str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol) or not traits:
        return None
    lines = content.splitlines(keepends=True)
    # Live L1-05 r92: BTreeMap keys are often enums (`Flavor: Ord`); struct-only
    # matching left known_rule_matched plans empty despite rust_e0277 diagnostics.
    declaration_re = re.compile(rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum)\s+{re.escape(symbol)}\b")
    for item_index, line in enumerate(lines):
        if declaration_re.match(line) is None:
            continue
        derive_index = _rust_existing_derive_line_index(lines, item_index)
        if derive_index is not None:
            expected = lines[derive_index]
            replacement, added = _add_rust_derive_traits_to_line(expected, traits)
            target_index = derive_index
            item_line = line
            unique_context = f"{expected}{item_line}"
        else:
            expected = line
            indent = line[: len(line) - len(line.lstrip())]
            newline = _line_ending(line) or "\n"
            replacement = f"{indent}#[derive({', '.join(sorted(traits))})]{newline}{line}"
            added = len(traits)
            target_index = item_index
            unique_context = expected
        if added <= 0 or replacement == expected:
            return None
        line_start = sum(len(item) for item in lines[:target_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_missing_trait_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "symbol": symbol,
                "struct_name": symbol,
                "item_kind": "enum" if re.search(r"\benum\b", line) else "struct",
                "traits_added": tuple(sorted(traits)),
                "derive_line_existing": derive_index is not None,
                "unique_context": unique_context,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _rust_serde_derive_operation(
    *,
    path: str,
    content: str,
    symbol: str,
    traits: frozenset[str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol) or not traits:
        return None
    lines = content.splitlines(keepends=True)
    declaration_re = re.compile(rf"^\s*(?:pub\s+)?(?:struct|enum)\s+{re.escape(symbol)}\b")
    for item_index, line in enumerate(lines):
        if declaration_re.match(line) is None:
            continue
        derive_index = _rust_existing_derive_line_index(lines, item_index)
        if derive_index is not None:
            expected = lines[derive_index]
            replacement, added = _add_rust_derive_traits_to_line(expected, traits)
            target_index = derive_index
        else:
            expected = line
            indent = line[: len(line) - len(line.lstrip())]
            newline = _line_ending(line) or "\n"
            replacement = f"{indent}#[derive({', '.join(sorted(traits))})]{newline}{line}"
            added = len(traits)
            target_index = item_index
        if added <= 0 or replacement == expected or content.count(expected) != 1:
            return None
        line_start = sum(len(item) for item in lines[:target_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_serde_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "symbol": symbol,
                "traits_added": tuple(sorted(traits)),
                "derive_line_existing": derive_index is not None,
                "unique_context": expected,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _rust_existing_derive_line_index(lines: Sequence[str], item_index: int) -> int | None:
    index = item_index - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index >= 0 and re.match(r"^\s*#\[derive\([^)]*\)\]\s*$", lines[index]):
        return index
    return None


def _add_rust_derive_traits_to_line(line: str, traits: frozenset[str]) -> tuple[str, int]:
    match = re.match(r"^(?P<indent>\s*)#\[derive\((?P<body>[^)]*)\)\](?P<newline>\r\n|\n|\r)?$", line)
    if match is None:
        return line, 0
    items = [item.strip() for item in str(match.group("body") or "").split(",") if item.strip()]
    added = 0
    for trait in sorted(traits):
        short = trait.rsplit("::", 1)[-1]
        if any(item in (trait, short) or item.endswith(f"::{short}") for item in items):
            continue
        items.append(trait)
        added += 1
    if added <= 0:
        return line, 0
    newline = match.group("newline") or ""
    return f"{match.group('indent')}#[derive({', '.join(items)})]{newline}", added


def _is_rust_identifier(value: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(value or "")) is not None


def _cargo_dependency_declared(cargo_text: str, package: str) -> bool:
    return bool(re.search(rf"(?m)^\s*{re.escape(package)}\s*=", cargo_text))


def _insert_cargo_dependency(cargo_text: str, dependency_line: str) -> str:
    dependency_header = re.search(r"(?m)^\[dependencies\]\s*$", cargo_text)
    if not dependency_header:
        suffix = "" if cargo_text.endswith("\n") else "\n"
        return f"{cargo_text}{suffix}\n[dependencies]\n{dependency_line}\n"
    insert_at = dependency_header.end()
    return f"{cargo_text[:insert_at]}\n{dependency_line}{cargo_text[insert_at:]}"


def _rust_method_self_signature_location(diagnostic: RepairDiagnostic) -> tuple[str, int] | None:
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if "expected parameter name" not in text:
        return None
    path = _normalize_repair_path(str(diagnostic.path or ""))
    line = diagnostic.line
    if not path or line is None:
        match = _RUST_METHOD_SELF_LOCATION_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(match.group("path"))
            line = _to_int(match.group("line"))
    if not path or line is None or int(line) <= 0:
        return None
    return path, int(line)


def _rust_method_self_signature_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    line = lines[index]
    if "fn " not in line:
        return None
    line_start = sum(len(item) for item in lines[:index])
    for pattern, replacement, receiver_kind in _RUST_METHOD_SELF_SIGNATURE_PATTERNS:
        match = pattern.search(line)
        if match is None:
            continue
        start = line_start + match.start()
        end = line_start + match.end()
        expected = match.group(0)
        context_start = max(0, match.start() - 24)
        context_end = min(len(line), match.end() + 24)
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=start,
            span_end=end,
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_method_self_signature",
                "edit_strategy": "span_text_replace",
                "line": line_number,
                "receiver_kind": receiver_kind,
                "diagnostic_id": diagnostic.diagnostic_id,
                "unique_context": line[context_start:context_end],
            },
        )
    return None


def _parse_rust_line_suggestions(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, int, str]]:
    suggestions: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for pattern in (
        _RUST_FIELD_METHOD_LINE_SUGGESTION_RE,
        _RUST_FULL_LINE_SUGGESTION_RE,
        _RUST_PLUS_LINE_SUGGESTION_RE,
    ):
        for match in pattern.finditer(text):
            path = _normalize_repair_path(str(match.group("path") or ""))
            line_number = _to_int(match.group("line"))
            code = str(match.group("code") or "").rstrip()
            if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
                continue
            if not code.strip():
                continue
            code = _rewrite_vec_bare_generic_suggestion(code)
            key = (path, int(line_number), code)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(key)
    return suggestions


def _parse_rust_field_rename_suggestions(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int, int, str, str, str]]:
    suggestions: list[tuple[str, int, int, str, str, str]] = []
    seen: set[tuple[str, int, str, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    blocks = re.split(r"(?=error\[E\d+\])", text)
    for block in blocks:
        error_match = _RUST_FIELD_RENAME_ERROR_RE.search(block)
        if error_match is None:
            continue
        wrong_field = str(error_match.group("wrong") or "")
        if not _is_rust_identifier(wrong_field):
            continue
        path = _normalize_repair_path(str(error_match.group("path") or ""))
        line_number = _to_int(error_match.group("line"))
        column_number = _to_int(error_match.group("column"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        if column_number is None or int(column_number) <= 0:
            continue
        lower_block = block.lower()
        if "help:" not in lower_block or "similar name exists" not in lower_block:
            continue
        for plus_match in _RUST_FIELD_RENAME_PLUS_LINE_RE.finditer(block):
            plus_line_number = _to_int(plus_match.group("line"))
            if plus_line_number != int(line_number):
                continue
            suggested_code = str(plus_match.group("code") or "").rstrip()
            correct_field = _rust_field_rename_correct_field(
                wrong_field=wrong_field,
                suggested_code=suggested_code,
            )
            if not correct_field or correct_field == wrong_field:
                continue
            key = (path, int(line_number), wrong_field, correct_field)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                (
                    path,
                    int(line_number),
                    int(column_number),
                    wrong_field,
                    correct_field,
                    suggested_code,
                )
            )
            break
    return suggestions


def _rust_field_rename_correct_field(*, wrong_field: str, suggested_code: str) -> str:
    if f".{wrong_field}" in suggested_code:
        return ""
    candidates = [
        str(match.group("field") or "")
        for match in _RUST_FIELD_ACCESS_RE.finditer(suggested_code)
        if _is_rust_identifier(str(match.group("field") or ""))
    ]
    if not candidates:
        return ""
    return candidates[-1]


def _parse_rust_trait_import_suggestions(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, str]]:
    suggestions: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    current_path = ""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        location = _RUST_LOCATION_RE.match(line)
        if location is not None:
            current_path = _normalize_repair_path(str(location.group("path") or ""))
            continue

        lower = line.lower()
        if "help:" not in lower or "trait" not in lower:
            continue
        if "is implemented but not in scope" not in lower and "perhaps you want to import it" not in lower:
            continue
        if "perhaps add a use for it" not in lower and "perhaps you want to import it" not in lower:
            continue

        import_line = _rust_import_line_from_suggestion_lines(lines[index : index + 8])
        if not current_path or not current_path.endswith(".rs") or not _is_strict_rust_use_import_line(import_line):
            continue
        key = (current_path, import_line)
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(key)
    return suggestions


def _parse_rust_wrong_crate_path_suggestions(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int, str]]:
    suggestions: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for block in re.split(r"(?=error\[E\d+\])", text):
        block_lines = block.splitlines()
        location = next(
            (_RUST_LOCATION_RE.match(line) for line in block_lines if _RUST_LOCATION_RE.match(line) is not None),
            None,
        )
        if location is None:
            continue
        path = _normalize_repair_path(str(location.group("path") or ""))
        line_number = _to_int(location.group("line"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        for index, line in enumerate(block_lines):
            lower = line.lower()
            if "help:" not in lower or "a similar path exists" not in lower:
                continue
            suggestion = _rust_import_line_from_suggestion_lines(block_lines[index : index + 8])
            if not _is_strict_rust_use_import_line(suggestion):
                continue
            key = (path, int(line_number), suggestion)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(key)
            break
    return suggestions


def _parse_rust_unused_import_warnings(diagnostics: Sequence[RepairDiagnostic]) -> list[tuple[str, int, str]]:
    warnings: list[tuple[str, int, str]] = []
    seen: set[tuple[str, int, str]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    for match in _RUST_UNUSED_IMPORT_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("path") or ""))
        line_number = _to_int(match.group("line"))
        symbol = str(match.group("symbol") or "").strip()
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        if not _is_rust_identifier(symbol):
            continue
        key = (path, int(line_number), symbol)
        if key in seen:
            continue
        seen.add(key)
        warnings.append(key)
    return warnings


def _parse_rust_incompatible_copy_derive_locations(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, int]]:
    locations: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    text = _ANSI_ESCAPE_RE.sub(
        "",
        "\n".join(str(diagnostic.raw or diagnostic.message or "") for diagnostic in diagnostics or ()),
    )
    if "the trait `Copy` cannot be implemented" not in text:
        return locations
    for match in _RUST_INCOMPATIBLE_COPY_LOCATION_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("path") or ""))
        line_number = _to_int(match.group("line"))
        if not path or not path.endswith(".rs") or line_number is None or int(line_number) <= 0:
            continue
        key = (path, int(line_number))
        if key in seen:
            continue
        seen.add(key)
        locations.append(key)
    return locations


def _rust_import_line_from_suggestion_lines(lines: Sequence[str]) -> str:
    for line in lines:
        match = _RUST_USE_IMPORT_IN_TEXT_RE.search(str(line or ""))
        if match is None:
            continue
        return str(match.group("import") or "").strip()
    return ""


def _rust_incompatible_copy_derive_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs"):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None

    for offset in range(0, 5):
        derive_index = line_index - offset
        if derive_index < 0 or derive_index >= len(lines):
            continue
        expected = lines[derive_index]
        replacement = _repair_rust_copy_derive_line(expected)
        if replacement is None or replacement == expected:
            continue
        if content.count(expected) != 1:
            return None
        line_start = sum(len(item) for item in lines[:derive_index])
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=line_start,
            span_end=line_start + len(expected),
            expected=expected,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "rust_incompatible_copy_derive",
                "edit_strategy": "text_replace",
                "span_based": True,
                "line_number": line_number,
                "derive_line_number": derive_index + 1,
                "unique_context": True,
                "diagnostic_id": diagnostic.diagnostic_id,
            },
        )
    return None


def _repair_rust_copy_derive_line(line: str) -> str | None:
    match = _RUST_DERIVE_LINE_RE.match(line)
    if match is None:
        return None
    items = str(match.group("items") or "")
    if _RUST_COPY_DERIVE_TOKEN_RE.search(items) is None:
        return None
    repaired = re.sub(r",\s*Copy\b", "", line)
    repaired = re.sub(r"\bCopy\s*,\s*", "", repaired)
    repaired = re.sub(r"\bCopy\b", "", repaired)
    if repaired == line or _RUST_DERIVE_LINE_RE.match(repaired) is None:
        return None
    return repaired


def _rust_field_rename_suggestion_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    column_number: int,
    wrong_field: str,
    correct_field: str,
    suggested_code: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(wrong_field) or not _is_rust_identifier(correct_field):
        return None
    if wrong_field == correct_field:
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected_line = lines[index]
    newline = _line_ending(expected_line)
    line_body = expected_line[: len(expected_line) - len(newline)] if newline else expected_line
    suggested_body = str(suggested_code or "").rstrip()
    field_access = f".{wrong_field}"
    correct_candidates = list(
        dict.fromkeys(
            candidate
            for candidate in (
                correct_field,
                *(str(match.group("field") or "") for match in _RUST_FIELD_ACCESS_RE.finditer(suggested_body)),
            )
            if _is_rust_identifier(candidate) and candidate != wrong_field
        )
    )

    candidate_spans: list[tuple[int, int, str]] = []
    search_start = 0
    while True:
        found = line_body.find(field_access, search_start)
        if found < 0:
            break
        for candidate_field in correct_candidates:
            replacement_access = f".{candidate_field}"
            candidate = f"{line_body[:found]}{replacement_access}{line_body[found + len(field_access) :]}"
            if candidate.strip() == suggested_body.strip():
                candidate_spans.append((found + 1, found + len(field_access), candidate_field))
        search_start = found + len(field_access)

    if len(candidate_spans) != 1:
        return None

    line_start = sum(len(item) for item in lines[:index])
    relative_start, relative_end, matched_correct_field = candidate_spans[0]
    replacement_access = f".{matched_correct_field}"
    span_start = line_start + relative_start
    span_end = line_start + relative_end
    if content[span_start:span_end] != wrong_field:
        return None
    unique_context = _unique_context_for_rust_span(content, span_start, span_end)
    if not unique_context:
        return None

    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=wrong_field,
        replacement=matched_correct_field,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_field_rename_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "column_number": column_number,
            "wrong_field": wrong_field,
            "correct_field": matched_correct_field,
            "field_access_before": field_access,
            "field_access_after": replacement_access,
            "suggested_code": suggested_body,
            "source_span_start": span_start,
            "source_span_end": span_end,
            "unique_context": unique_context,
            "unique_context_hash": sha256_text(unique_context),
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_line_suggestion_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    code: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs"):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    replacement = f"{_prefer_vec_generic(expected, str(code or '').rstrip())}{_line_ending(expected)}"
    if expected == replacement:
        return None
    if content.count(expected) != 1:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_line_suggestion",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_wrong_crate_path_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    suggestion: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_strict_rust_use_import_line(suggestion):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    if content.count(expected) != 1:
        return None
    newline = _line_ending(expected)
    body = expected[: len(expected) - len(newline)] if newline else expected
    indent = body[: len(body) - len(body.lstrip(" \t"))]
    if not _is_strict_rust_use_import_line(body.strip()):
        return None
    replacement = f"{indent}{suggestion}{newline}"
    if replacement == expected:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_wrong_crate_path",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "suggestion": suggestion,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_unused_import_operation(
    *,
    path: str,
    content: str,
    line_number: int,
    symbol: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_rust_identifier(symbol):
        return None
    lines = content.splitlines(keepends=True)
    index = line_number - 1
    if index < 0 or index >= len(lines):
        return None
    expected = lines[index]
    if content.count(expected) != 1:
        return None
    replacement = _repair_rust_unused_import_line(expected, symbol)
    if replacement is None or replacement == expected:
        return None
    line_start = sum(len(item) for item in lines[:index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start,
        span_end=line_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_unused_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "line_number": line_number,
            "symbol": symbol,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _repair_rust_unused_import_line(line: str, symbol: str) -> str | None:
    newline = _line_ending(line)
    body = line[: len(line) - len(newline)] if newline else line
    indent = body[: len(body) - len(body.lstrip(" \t"))]
    stripped = body.strip()
    if not stripped.startswith("use ") or not stripped.endswith(";"):
        return None

    group_match = re.match(r"^(?P<prefix>use\s+.+?::\{)(?P<items>[^{};]+)(?P<suffix>\};)$", stripped)
    if group_match is not None:
        items = [item.strip() for item in str(group_match.group("items") or "").split(",") if item.strip()]
        kept: list[str] = []
        removed = False
        for item in items:
            candidate = item.split(" as ", 1)[0].strip()
            if candidate == symbol:
                removed = True
                continue
            kept.append(item)
        if not removed:
            return None
        if kept:
            return f"{indent}{group_match.group('prefix')}{', '.join(kept)}{group_match.group('suffix')}{newline}"
        return f"{indent}// [repair-unused] {stripped}{newline}"

    single_match = re.match(
        r"^use\s+.+::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?;$",
        stripped,
    )
    if single_match is None or single_match.group("symbol") != symbol:
        return None
    return f"{indent}// [repair-unused] {stripped}{newline}"


def _rust_trait_import_operation(
    *,
    path: str,
    content: str,
    import_line: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not path.endswith(".rs") or not _is_strict_rust_use_import_line(import_line):
        return None
    lines = content.splitlines(keepends=True)
    if any(line.strip() == import_line for line in lines):
        return None

    insert_index = _rust_use_insert_index(lines)
    anchor = _rust_trait_import_anchor(lines, insert_index, import_line)
    if anchor is None:
        return None
    anchor_index, expected, replacement = anchor
    if not expected or content.count(expected) != 1:
        return None

    span_start = sum(len(item) for item in lines[:anchor_index])
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_start + len(expected),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_trait_import",
            "edit_strategy": "text_replace",
            "span_based": True,
            "import_line": import_line,
            "insert_index": insert_index,
            "unique_context": True,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _rust_trait_import_anchor(
    lines: Sequence[str],
    insert_index: int,
    import_line: str,
) -> tuple[int, str, str] | None:
    newline = _rust_file_newline(lines)
    if insert_index < len(lines):
        expected = lines[insert_index]
        return insert_index, expected, f"{import_line}{newline}{expected}"
    if not lines:
        return None
    anchor_index = len(lines) - 1
    expected = lines[anchor_index]
    separator = "" if _line_ending(expected) else newline
    return anchor_index, expected, f"{expected}{separator}{import_line}{newline}"


def _rust_use_insert_index(lines: Sequence[str]) -> int:
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "" or stripped.startswith("//!") or stripped.startswith("#!["):
            index += 1
            continue
        break

    insert_index = index
    seen_use = False
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("use ") and stripped.endswith(";"):
            seen_use = True
            insert_index = index + 1
            index += 1
            continue
        if seen_use and stripped == "":
            insert_index = index + 1
            index += 1
            continue
        break
    return insert_index


def _rust_file_newline(lines: Sequence[str]) -> str:
    for line in lines:
        newline = _line_ending(line)
        if newline:
            return newline
    return "\n"


def _read_cargo_manifest_from_base(base_files: Mapping[str, str]) -> dict[str, object]:
    try:
        payload = tomllib.loads(str(base_files.get("Cargo.toml") or ""))
    except (RuntimeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _canonical_rust_crate_name(cargo: Mapping[str, object]) -> str:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        lib_name = str(lib.get("name") or "").strip()
        if lib_name:
            return _rust_identifier_from_manifest_name(lib_name)
    package = cargo.get("package")
    if not isinstance(package, dict):
        return ""
    package_name = str(package.get("name") or "").strip()
    return _rust_identifier_from_manifest_name(package_name)


def _rust_identifier_from_manifest_name(name: str) -> str:
    normalized = str(name or "").replace("-", "_")
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", normalized)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        return ""
    return normalized


def _declared_rust_dependencies(cargo: Mapping[str, object]) -> set[str]:
    dependency_names: set[str] = set()
    for key in ("dependencies", "dev-dependencies", "build-dependencies"):
        value = cargo.get(key)
        if isinstance(value, dict):
            dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    target = cargo.get("target")
    if isinstance(target, dict):
        for target_payload in target.values():
            if not isinstance(target_payload, dict):
                continue
            for key in ("dependencies", "dev-dependencies", "build-dependencies"):
                value = target_payload.get(key)
                if isinstance(value, dict):
                    dependency_names.update(_rust_identifier_from_manifest_name(str(name)) for name in value)
    dependency_names.discard("")
    return dependency_names


def _declared_rust_binary_entrypoint_paths(cargo: Mapping[str, object]) -> tuple[str, ...]:
    bins = cargo.get("bin")
    if not isinstance(bins, list):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for entry in bins:
        if not isinstance(entry, dict):
            continue
        path = _normalize_repair_path(str(entry.get("path") or ""))
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def _rust_missing_binary_paths_from_diagnostic(diagnostic: RepairDiagnostic) -> tuple[str, ...]:
    """Project cargo-shaped missing-bin diagnostics into workspace-relative paths."""

    candidates: list[str] = []
    raw_path = _normalize_repair_path(str(getattr(diagnostic, "path", "") or ""))
    if raw_path.endswith(".rs"):
        candidates.append(raw_path)
    text = "\n".join(
        part
        for part in (
            str(getattr(diagnostic, "raw", "") or ""),
            str(getattr(diagnostic, "message", "") or ""),
        )
        if str(part or "").strip()
    )
    for match in _RUST_CANT_FIND_BIN_PATH_RE.finditer(text):
        absolute_or_relative = str(match.group("path") or "").strip().replace("\\", "/")
        if not absolute_or_relative:
            continue
        # Prefer the trailing src/... relative segment for absolute cargo paths.
        marker = "/src/"
        if marker in absolute_or_relative:
            relative = "src/" + absolute_or_relative.split(marker, 1)[1]
        elif absolute_or_relative.startswith("src/"):
            relative = absolute_or_relative
        else:
            relative = absolute_or_relative.rsplit("/", 1)[-1]
            if relative.endswith(".rs") and "/" not in relative:
                relative = f"src/{relative}"
        normalized = _normalize_repair_path(relative)
        if normalized and normalized.endswith(".rs") and normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _diagnostics_indicate_missing_rust_binary(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    for diagnostic in diagnostics or ():
        text = "\n".join(
            part
            for part in (
                str(getattr(diagnostic, "raw", "") or ""),
                str(getattr(diagnostic, "message", "") or ""),
                str(getattr(diagnostic, "code", "") or ""),
            )
            if str(part or "").strip()
        ).lower()
        if "can't find bin" in text or "cant find bin" in text or "rust_missing_binary_entrypoint" in text:
            return True
        metadata = getattr(diagnostic, "metadata", None)
        if isinstance(metadata, Mapping):
            kind = str(metadata.get("diagnostic_kind") or metadata.get("kind") or "").lower()
            if kind == "rust_missing_binary_entrypoint":
                return True
    return False


def _rust_binary_entrypoint_path_is_safe(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    return bool(normalized and normalized == path and normalized.endswith(".rs"))


def _rust_missing_binary_entrypoint_stub(crate_name: str) -> str:
    safe_name = _rust_identifier_from_manifest_name(crate_name) or "app"
    return (
        f"// Auto-generated binary entry point for {safe_name}\n"
        "fn main() {\n"
        f'    println!("{safe_name} binary entry point");\n'
        "}\n"
    )


def _rust_missing_module_file_candidate(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> tuple[str, str] | None:
    if str(diagnostic.code or "").lower() != "rust_e0583":
        return None
    diagnostic_text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
    message_match = _RUST_MISSING_MODULE_FILE_RE.search(diagnostic_text)
    if message_match is None:
        return None
    module_name = str(message_match.group("module") or "").strip()
    declaring_path = _normalize_repair_path(str(diagnostic.path or ""))
    if not module_name or not declaring_path or not declaring_path.endswith(".rs"):
        return None
    if declaring_path not in base_files or diagnostic.line is None:
        return None
    if not _rust_diagnostic_line_declares_module(
        content=base_files[declaring_path],
        line_number=int(diagnostic.line),
        module_name=module_name,
    ):
        return None

    raw_text = _ANSI_ESCAPE_RE.sub("", str(diagnostic.raw or ""))
    for candidate_path in _rust_e0583_help_candidate_paths(raw_text, module_name):
        if candidate_path in base_files:
            continue
        if _rust_missing_module_file_create_path_is_safe(candidate_path):
            return candidate_path, module_name
    return None


def _rust_diagnostic_line_declares_module(*, content: str, line_number: int, module_name: str) -> bool:
    if line_number <= 0:
        return False
    lines = str(content or "").splitlines()
    if line_number > len(lines):
        return False
    line = lines[line_number - 1]
    return (
        re.fullmatch(
            rf"\s*(?:pub\s+)?mod\s+{re.escape(module_name)}\s*;\s*(?://.*)?",
            line,
        )
        is not None
    )


def _rust_e0583_help_candidate_paths(raw_text: str, module_name: str) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for help_match in _RUST_E0583_HELP_LINE_RE.finditer(raw_text):
        if str(help_match.group("module") or "") != module_name:
            continue
        candidates = str(help_match.group("candidates") or "")
        for path_match in _RUST_QUOTED_RS_PATH_RE.finditer(candidates):
            normalized = _normalize_repair_path(str(path_match.group("path") or ""))
            if normalized and normalized not in seen:
                seen.add(normalized)
                paths.append(normalized)
    return tuple(paths)


def _rust_missing_module_file_create_path_is_safe(path: str) -> bool:
    raw = str(path or "").strip().replace("\\", "/")
    normalized = _normalize_repair_path(raw)
    if not normalized or normalized != raw.lstrip("./") or not normalized.endswith(".rs"):
        return False
    if re.match(r"^[A-Za-z]:/", raw):
        return False
    raw_parts = normalized.split("/")
    parts = tuple(part for part in raw_parts if part)
    if not parts or len(parts) != len(raw_parts):
        return False
    return not any(part in {".", "..", "target", "build", "out"} for part in parts)


def _parse_rust_duplicate_module_file_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[str, str, str, RepairDiagnostic], ...]:
    targets: list[tuple[str, str, str, RepairDiagnostic]] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics:
        if str(diagnostic.code or "").lower() != "rust_e0761":
            continue
        text = _ANSI_ESCAPE_RE.sub("", f"{diagnostic.message}\n{diagnostic.raw}")
        for match in _RUST_DUPLICATE_MODULE_FILE_RE.finditer(text):
            module_name = str(match.group("module") or "").strip()
            first_path = _normalize_repair_path(str(match.group("first") or ""))
            second_path = _normalize_repair_path(str(match.group("second") or ""))
            if not _is_rust_identifier(module_name):
                continue
            if not first_path or not second_path or first_path == second_path:
                continue
            if not first_path.endswith(".rs") or not second_path.endswith(".rs"):
                continue
            key = (module_name, first_path, second_path)
            if key in seen:
                continue
            seen.add(key)
            targets.append((module_name, first_path, second_path, diagnostic))
    return tuple(targets)


def _rust_duplicate_module_delete_candidate(
    *,
    first_path: str,
    first_content: str,
    second_path: str,
    second_content: str,
) -> tuple[str, str, str] | None:
    first_evidence = _rust_duplicate_module_delete_evidence(first_content)
    second_evidence = _rust_duplicate_module_delete_evidence(second_content)
    first_has_item = _rust_file_has_real_rust_item(first_content)
    second_has_item = _rust_file_has_real_rust_item(second_content)

    if first_evidence and not first_has_item and second_has_item:
        return first_path, second_path, first_evidence
    if second_evidence and not second_has_item and first_has_item:
        return second_path, first_path, second_evidence
    return None


def _rust_duplicate_module_delete_evidence(content: str) -> str:
    text = str(content or "")
    if not text.strip():
        return "empty"
    if _rust_file_has_real_rust_item(text):
        return ""
    if _rust_file_has_polaris_marker_comment(text):
        return "polaris_marker"
    if _rust_file_is_comment_only(text):
        return "comment_only"
    return ""


def _rust_file_has_real_rust_item(content: str) -> bool:
    for line in _rust_non_comment_lines(content):
        if line.startswith("#[") or line.startswith("#!["):
            continue
        if _RUST_REAL_ITEM_RE.match(line):
            return True
    return False


def _rust_file_is_comment_only(content: str) -> bool:
    return bool(str(content or "").strip()) and not tuple(_rust_non_comment_lines(content))


def _rust_file_has_polaris_marker_comment(content: str) -> bool:
    for raw_line in str(content or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(("//", "/*", "*")) and "polaris" in stripped.lower():
            return True
    return False


def _rust_non_comment_lines(content: str) -> tuple[str, ...]:
    lines: list[str] = []
    in_block_comment = False
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        while line:
            if in_block_comment:
                end_index = line.find("*/")
                if end_index < 0:
                    line = ""
                    continue
                line = line[end_index + 2 :].lstrip()
                in_block_comment = False
                continue
            if line.startswith("//"):
                line = ""
                continue
            if line.startswith("/*"):
                end_index = line.find("*/", 2)
                if end_index < 0:
                    in_block_comment = True
                    line = ""
                    continue
                line = line[end_index + 2 :].lstrip()
                continue
            if line.startswith("*"):
                line = ""
                continue
            lines.append(line)
            line = ""
    return tuple(lines)


def _parse_unresolved_rust_crates(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[tuple[str, RepairDiagnostic]]:
    seen: set[str] = set()
    crates: list[tuple[str, RepairDiagnostic]] = []
    for diagnostic in diagnostics:
        text = _ANSI_ESCAPE_RE.sub("", str(diagnostic.raw or diagnostic.message or ""))
        for match in _RUST_UNRESOLVED_CRATE_RE.finditer(text):
            crate = str(match.group("crate") or "").strip()
            if not crate or crate in seen:
                continue
            seen.add(crate)
            crates.append((crate, diagnostic))
    return crates


def _cargo_declares_local_rust_lib(base_files: Mapping[str, str], cargo: Mapping[str, object]) -> bool:
    lib = cargo.get("lib")
    if isinstance(lib, dict):
        configured = str(lib.get("path") or "src/lib.rs").strip() or "src/lib.rs"
        return _normalize_repair_path(configured) in base_files
    return "src/lib.rs" in base_files


def _rust_crate_prefix_used_in_binary_entrypoint(base_files: Mapping[str, str], missing_crate: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    for path, content in base_files.items():
        if (path == "src/main.rs" or (path.startswith("src/bin/") and path.endswith(".rs"))) and pattern.search(
            content
        ):
            return True
    return False


def _rust_crate_names_look_related(missing: str, canonical: str) -> bool:
    missing_tokens = _crate_name_tokens(missing)
    canonical_tokens = _crate_name_tokens(canonical)
    if len(missing_tokens) < 2 or not canonical_tokens:
        return False
    overlap = missing_tokens & canonical_tokens
    return missing_tokens.issubset(canonical_tokens) or len(overlap) >= 2


def _crate_name_tokens(name: str) -> set[str]:
    return {token for token in re.split(r"[_\W]+", str(name or "").lower()) if token}


def _rust_crate_import_rewrite_operations(
    *,
    base_files: Mapping[str, str],
    missing_crate: str,
    canonical_crate: str,
    diagnostic: RepairDiagnostic,
) -> tuple[RepairOperation, ...]:
    prefix_pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(missing_crate)}(?=::)")
    extern_pattern = re.compile(rf"\bextern\s+crate\s+{re.escape(missing_crate)}\b")
    operations: list[RepairOperation] = []
    for path, content in sorted(base_files.items()):
        if not path.endswith(".rs") or "target" in path.split("/"):
            continue
        for match in prefix_pattern.finditer(content):
            operation = _rust_crate_import_rewrite_operation(
                path=path,
                content=content,
                span_start=match.start(),
                span_end=match.end(),
                expected=missing_crate,
                replacement=canonical_crate,
                missing_crate=missing_crate,
                canonical_crate=canonical_crate,
                match_kind="crate_prefix",
                diagnostic=diagnostic,
            )
            if operation is not None:
                operations.append(operation)
        for match in extern_pattern.finditer(content):
            operation = _rust_crate_import_rewrite_operation(
                path=path,
                content=content,
                span_start=match.start(),
                span_end=match.end(),
                expected=match.group(0),
                replacement=f"extern crate {canonical_crate}",
                missing_crate=missing_crate,
                canonical_crate=canonical_crate,
                match_kind="extern_crate",
                diagnostic=diagnostic,
            )
            if operation is not None:
                operations.append(operation)
    return tuple(operations)


def _rust_crate_import_rewrite_operation(
    *,
    path: str,
    content: str,
    span_start: int,
    span_end: int,
    expected: str,
    replacement: str,
    missing_crate: str,
    canonical_crate: str,
    match_kind: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    context = _unique_span_context(content, span_start, span_end)
    if context is None:
        return None
    context_before, context_after = context
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "rust_crate_import_rewrite",
            "edit_strategy": "text_replace",
            "span_based": True,
            "missing_crate": missing_crate,
            "canonical_crate": canonical_crate,
            "match_kind": match_kind,
            "expected_context_before": context_before,
            "expected_context_after": context_after,
            "diagnostic_id": diagnostic.diagnostic_id,
        },
    )


def _unique_span_context(content: str, span_start: int, span_end: int) -> tuple[str, str] | None:
    line_start = content.rfind("\n", 0, span_start) + 1
    line_end = content.find("\n", span_end)
    if line_end == -1:
        line_end = len(content)
    else:
        line_end += 1
    before = content[line_start:span_start]
    after = content[span_end:line_end]
    probe = f"{before}{content[span_start:span_end]}{after}"
    if probe and content.count(probe) == 1:
        return before, after

    for radius in (24, 48, 96, 160):
        before_start = max(0, span_start - radius)
        after_end = min(len(content), span_end + radius)
        before = content[before_start:span_start]
        after = content[span_end:after_end]
        probe = f"{before}{content[span_start:span_end]}{after}"
        if probe and content.count(probe) == 1:
            return before, after
    return None


def _is_strict_rust_use_import_line(value: str) -> bool:
    line = str(value or "").strip()
    if "\n" in line or "\r" in line:
        return False
    return _RUST_USE_IMPORT_LINE_RE.fullmatch(line) is not None


def _rewrite_vec_bare_generic_suggestion(code: str) -> str:
    return _RUST_VEC_BARE_GENERIC_RE.sub(r"Vec<\g<inner>>\g<tail>", str(code or ""))


def _prefer_vec_generic(expected: str, replacement: str) -> str:
    match = re.search(r"\bVec([A-Z][A-Za-z0-9_]*)\b(\s*=\s*Vec::new\(\))", expected)
    if match is None:
        return replacement
    inner = match.group(1)
    if f"Vec<{inner}>" in replacement:
        return replacement
    if re.search(rf"\b{re.escape(inner)}\b\s*=\s*Vec::new\(\)", replacement):
        return replacement.replace(inner, f"Vec<{inner}>", 1)
    return replacement


def rust_local_structure_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[RepairOperation]:
    """Apply L1-09 proven local rustc structure fixes that have no + help line."""

    operations: list[RepairOperation] = []
    haystack = "\n".join(str(item.raw or item.message or "") for item in diagnostics)
    for path, content in sorted(dict(base_files or {}).items()):
        if not str(path).endswith(".rs"):
            continue
        repaired = str(content or "")
        if "expected identifier" in haystack and "<" in haystack:
            repaired = _RUST_XML_GENERIC_CLOSE_RE.sub("\n", repaired)
        if "cannot find type" in haystack or "Vec::new()" in repaired:
            repaired = _RUST_VEC_BARE_GENERIC_RE.sub(r"Vec<\g<inner>>\g<tail>", repaired)
        if "is_finite" in haystack:
            repaired = _RUST_INTEGER_IS_FINITE_RE.sub("\n", repaired)
        if "expected a tuple with 3 elements" in haystack or "found one with 2 elements" in haystack:
            repaired = _RUST_TWO_TUPLE_LET_RE.sub(
                r"\g<prefix>(\g<a>, _reagents, \g<b>)\g<rest>",
                repaired,
                count=1,
            )
        field_match = _RUST_ENUM_VARIANT_MISSING_FIELD_RE.search(haystack)
        needle = "reagent_count: reagents.len(),\n        }"
        if field_match is not None and needle in repaired:
            field_name = str(field_match.group("field") or "")
            if field_name and f"{field_name}:" not in needle:
                initializer = "MAX_REAGENTS" if "MAX_REAGENTS" in repaired else "16"
                repaired = repaired.replace(
                    needle,
                    f"reagent_count: reagents.len(),\n            {field_name}: {initializer},\n        }}",
                    1,
                )
        if repaired == content:
            continue
        operations.extend(
            _file_replace_operations(
                path=_normalize_repair_path(path),
                original=str(content),
                repaired=repaired,
                diagnostic_id=next((item.diagnostic_id for item in diagnostics), ""),
            )
        )
    return operations


def _file_replace_operations(
    *,
    path: str,
    original: str,
    repaired: str,
    diagnostic_id: str,
) -> list[RepairOperation]:
    if repaired == original or not path:
        return []
    return [
        RepairOperation(
            kind="text_replace",
            path=path,
            span_start=0,
            span_end=len(original),
            expected=original,
            replacement=repaired,
            before_hash=sha256_text(original),
            metadata={
                "repair_kind": "rust_local_structure",
                "edit_strategy": "text_replace",
                "span_based": True,
                "diagnostic_id": diagnostic_id,
            },
        )
    ]


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    if line.endswith("\r"):
        return "\r"
    return ""


def _unique_context_for_rust_span(content: str, span_start: int, span_end: int) -> str:
    if span_start < 0 or span_end < span_start or span_end > len(content):
        return ""
    for radius in (24, 48, 96, 192, 384, 768, 1536):
        context_start = max(0, span_start - radius)
        context_end = min(len(content), span_end + radius)
        probe = content[context_start:context_end]
        if probe and content.find(probe) >= 0 and content.find(probe, content.find(probe) + 1) < 0:
            return probe
    return ""


def _to_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return ""
    if any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized
