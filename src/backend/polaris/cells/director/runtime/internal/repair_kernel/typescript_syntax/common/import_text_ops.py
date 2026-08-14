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

"""Shared TypeScript repair helpers: import_text_ops."""

def _rewrite_named_import_binding_lines(
    *,
    content: str,
    old_name: str,
    new_name: str,
) -> list[tuple[int, str]]:
    """Return (line_index, repaired_line) for named-import bindings old_name → new_name.

    Keeps multi-line ``import { a, b } from '…'`` forms working so call-site
    renames do not leave TS2304 (missing import for the new callee).
    """

    if not old_name or not new_name or old_name == new_name:
        return []
    if not _TS_IDENTIFIER_RE.fullmatch(old_name) or not _TS_IDENTIFIER_RE.fullmatch(new_name):
        return []
    lines = content.splitlines(keepends=True)
    # Track whether we are inside an ``import { … }`` brace group.
    in_named_import = False
    results: list[tuple[int, str]] = []
    name_re = re.compile(rf"\b{re.escape(old_name)}\b")
    already_new_re = re.compile(rf"\b{re.escape(new_name)}\b")
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if re.match(r"import\s*(type\s*)?\{", stripped):
            in_named_import = True
        if not in_named_import:
            # Single-line: import { foo as bar, adjustHumidity } from '…'
            if re.search(r"\bimport\b", line) and "{" in line and name_re.search(line):
                if already_new_re.search(line):
                    # Already imports alias; drop old binding only.
                    repaired = re.sub(
                        rf",\s*{re.escape(old_name)}\b|\b{re.escape(old_name)}\s*,\s*|\b{re.escape(old_name)}\b",
                        lambda m: (
                            "" if m.group(0).strip() == old_name else (", " if m.group(0).startswith(",") else "")
                        ),
                        line,
                        count=1,
                    )
                else:
                    repaired = name_re.sub(new_name, line, count=1)
                if repaired != line:
                    results.append((index, repaired))
            continue
        if name_re.search(line) and not re.search(r"\bas\b", line):
            if already_new_re.search(line):
                repaired = name_re.sub("", line)
                # tidy double commas / leading commas left on the line
                repaired = re.sub(r",\s*,", ",", repaired)
                repaired = re.sub(r"\{\s*,", "{", repaired)
                repaired = re.sub(r",\s*\}", "}", repaired)
            else:
                repaired = name_re.sub(new_name, line, count=1)
            if repaired != line:
                results.append((index, repaired))
        if "}" in line:
            in_named_import = False
    return results

def _typescript_line_is_import_binding_context(*, content: str, line: int, name: str) -> bool:
    """Return True when ``name`` on ``line`` is an import/type import binding."""

    if line <= 0 or not name:
        return False
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line < start_line or line > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if any(local == name for _, local in pairs):
            return True
    for match in _TS_IMPORT_FROM_ANY_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line < start_line or line > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause(str(match.group("clause") or ""))
        if any(local == name for _, local in pairs):
            return True
    lines = content.splitlines()
    if 0 < line <= len(lines):
        candidate = lines[line - 1]
        if re.search(r"\bimport\b", candidate) or re.search(
            rf"^\s*(?:type\s+)?{re.escape(name)}\b",
            candidate,
        ):
            # Heuristic: multi-line import body lines often lack the word import.
            window = "\n".join(lines[max(0, line - 8) : min(len(lines), line + 3)])
            if re.search(r"\bimport\b[\s\S]{0,400}\bfrom\b", window):
                return True
    return False

def _typescript_exported_private_constructor_modifier_span(text: str, class_name: str) -> tuple[int, int, int] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return None
    class_pattern = re.compile(
        rf"\bexport\s+(?:default\s+)?class\s+{re.escape(class_name)}\b[^\{{]*\{{",
        re.MULTILINE,
    )
    line_offsets = _text_line_start_offsets(text)
    for class_match in class_pattern.finditer(text):
        open_brace = str(text or "").find("{", class_match.start(), class_match.end())
        if open_brace < 0:
            continue
        close_brace = _typescript_matching_brace_index(text, open_brace)
        if close_brace <= open_brace:
            continue
        body = text[open_brace + 1 : close_brace]
        constructor_match = re.search(r"(?m)^(?P<indent>\s*)private\s+constructor\s*\(", body)
        if constructor_match is None:
            continue
        start = open_brace + 1 + constructor_match.start() + len(str(constructor_match.group("indent") or ""))
        end = start + len("private ")
        line_index = _line_index_for_offset(line_offsets, start)
        if line_index < 0:
            continue
        return line_index, start, end
    return None

def _line_index_for_offset(offsets: Sequence[int], offset: int) -> int:
    if offset < 0:
        return -1
    for index, start in enumerate(offsets):
        next_start = offsets[index + 1] if index + 1 < len(offsets) else None
        if offset >= start and (next_start is None or offset < next_start):
            return index
    return -1

def _normalize_typescript_module_ref(raw: object) -> str:
    value = str(raw or "").strip().rstrip(".")
    previous = None
    while value != previous:
        previous = value
        value = value.strip().strip("'\"`").strip()
    return value.rstrip(".")

def _repair_typescript_unresolved_identifier_import(
    *,
    path: str,
    original: str,
    base_files: Mapping[str, str],
    missing_symbol: str,
) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return original, ""
    for match in _TS_NAMED_IMPORT_RE.finditer(original):
        module_ref = str(match.group("module") or "")
        module_path = _resolve_relative_ts_module_path(path, module_ref, base_files)
        if not module_path:
            continue
        # Follow barrel star/named reexports (R170 MoonPhase via export * from './types').
        if not _typescript_module_exports_symbol_resolved(
            module_path=module_path,
            base_files=base_files,
            symbol=missing_symbol,
        ):
            continue
        symbols = str(match.group("symbols") or "")
        existing = _parse_named_import_symbols(symbols)
        if missing_symbol in existing:
            continue
        replacement_symbols = _typescript_named_import_symbols_with_added_symbol(symbols, missing_symbol)
        if replacement_symbols == symbols:
            continue
        return (
            f"{original[: match.start('symbols')]}{replacement_symbols}{original[match.end('symbols') :]}",
            f"import:{module_ref}:{missing_symbol}",
        )
    # A lone `export type { X } from "mod"` does not create a local binding.
    # When the file already names exactly one reexport module, import the
    # missing type-position symbol from that same module.
    reexport_modules: list[str] = []
    for match in _TS_NAMED_REEXPORT_RE.finditer(original):
        module_ref = str(match.group("module") or "").strip()
        if not module_ref:
            continue
        reexport_modules.append(module_ref)
    unique_modules = list(dict.fromkeys(reexport_modules))
    if len(unique_modules) == 1:
        module_ref = unique_modules[0]
        import_line = f'import type {{ {missing_symbol} }} from "{module_ref}";\n'
        if import_line not in original:
            return import_line + original, f"import:{module_ref}:{missing_symbol}"
    return original, ""

def _parse_named_import_symbols(symbols: str) -> list[str]:
    parsed: list[str] = []
    for raw in str(symbols or "").split(","):
        token = raw.strip().split(" as ", 1)[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(token):
            parsed.append(token)
    return _dedupe_preserve_order(parsed)

def _typescript_named_import_symbols_with_added_symbol(symbols: str, missing_symbol: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return str(symbols or "")
    raw_symbols = str(symbols or "")
    parts = [part.strip() for part in raw_symbols.split(",") if part.strip()]
    if not parts:
        return raw_symbols
    if "\n" not in raw_symbols and "\r" not in raw_symbols:
        leading = " " if raw_symbols[:1].isspace() else ""
        trailing = " " if raw_symbols[-1:].isspace() else ""
        return f"{leading}{', '.join([*parts, missing_symbol])}{trailing}"
    newline = "\r\n" if "\r\n" in raw_symbols else "\n"
    indent = _typescript_named_specifier_indent(raw_symbols)
    return newline + "".join(f"{indent}{part},{newline}" for part in [*parts, missing_symbol])

def _typescript_imported_const_class_alias_available(
    *,
    base_files: Mapping[str, str],
    importer_path: str,
    importer_text: str,
    symbol: str,
) -> bool:
    for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
        imported_symbols = _parse_named_import_symbols(str(match.group("symbols") or ""))
        if symbol not in imported_symbols:
            continue
        module_path = _resolve_relative_ts_module_path(importer_path, str(match.group("module") or ""), base_files)
        if not module_path:
            continue
        if _typescript_module_exports_const_class_alias(str(base_files.get(module_path) or ""), symbol):
            return True
    return False

def _typescript_module_exports_const_class_alias(module_text: str, symbol: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return False
    alias_re = re.compile(
        rf"\bexport\s+const\s+{re.escape(symbol)}\s*=\s*(?P<class_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*;",
        re.MULTILINE,
    )
    alias = alias_re.search(module_text)
    if not alias:
        return False
    class_name = str(alias.group("class_name") or "")
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    class_re = re.compile(rf"\b(?:export\s+)?class\s+{re.escape(class_name)}\b", re.MULTILINE)
    return bool(class_re.search(module_text))

def _typescript_file_has_type_name_import(content: str, type_name: str) -> bool:
    escaped = re.escape(type_name)
    return bool(
        re.search(rf"\bimport\s+type\s*\{{[^}}]*\b{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
        or re.search(rf"\bimport\s*\{{[^}}]*\btype\s+{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
    )

def _typescript_insert_type_import_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    source_path: str,
) -> RepairOperation | None:
    module_specifier = _relative_import_specifier_for_actual_path(
        importer_rel=path,
        original_specifier="",
        actual_target_rel=source_path,
    )
    import_line = f'import type {{ {type_name} }} from "{module_specifier}";\n'
    insert_at = _typescript_import_insert_offset(content)
    before_hash = sha256_text(content)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=insert_at,
        span_end=insert_at,
        expected="",
        replacement=import_line,
        before_hash=before_hash,
        metadata={
            "repair_kind": "typescript_branded_literal_type_import",
            "target_type": type_name,
            "module_specifier": module_specifier,
            "expected_context_before": content[max(0, insert_at - 240) : insert_at],
            "expected_context_after": content[insert_at : insert_at + 120],
        },
    )

def _typescript_import_insert_offset(content: str) -> int:
    matches = list(re.finditer(r"^import\b[^\n]*(?:\n|$)", content, re.MULTILINE))
    if matches:
        return matches[-1].end()
    header_match = re.match(r"^(?:/\*.*?\*/\s*)", content, re.DOTALL)
    return header_match.end() if header_match else 0

def _typescript_named_type_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        token = str(raw or "").strip()
        if not token.lower().startswith("type "):
            continue
        name = _typescript_named_export_specifier_name(token[5:].strip())
        if name:
            names.add(name)
    return names

def _typescript_named_value_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        name = _typescript_named_value_specifier_name(raw)
        if name:
            names.add(name)
    return names

def _typescript_named_value_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token or token.startswith("type "):
        return ""
    return _typescript_named_export_specifier_name(token)

def _typescript_named_export_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    candidate = re.split(r"\s+as\s+", token, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""

def _typescript_named_specifier_indent(symbols: str) -> str:
    for line in str(symbols or "").splitlines():
        if line.strip():
            match = re.match(r"^\s*", line)
            indent = match.group(0) if match else ""
            return indent or "  "
    return "  "

def _typescript_module_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(rf"\bexport\s+(?:type|interface|enum|class|const|let|var|function)\s+{escaped}\b", module_text):
        return True
    for match in re.finditer(r"\bexport\s+(?:type\s+)?\{(?P<symbols>[^}]+)\}", module_text):
        for token in str(match.group("symbols") or "").split(","):
            parts = re.split(r"\s+as\s+", token.strip(), maxsplit=1)
            exported = parts[-1].strip()
            if exported == symbol:
                return True
    return False

def _typescript_module_exports_symbol_resolved(
    *,
    module_path: str,
    base_files: Mapping[str, str],
    symbol: str,
    _depth: int = 0,
    _seen: set[str] | None = None,
) -> bool:
    """True when module or its star/named reexport chain exports ``symbol``.

    R170: barrel ``export * from './types'`` re-exports ``MoonPhase``; local-text
    only checks miss star reexports and leave TS2304 on re-export sites.
    """

    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return False
    normalized_path = _normalize_repair_path(module_path)
    if not normalized_path:
        return False
    seen = _seen if _seen is not None else set()
    if normalized_path in seen or _depth > 5:
        return False
    seen.add(normalized_path)
    module_text = str(base_files.get(normalized_path) or "")
    if not module_text:
        return False
    if _typescript_module_exports_symbol(module_text, symbol):
        return True
    for match in _TS_STAR_REEXPORT_RE.finditer(module_text):
        child = _resolve_relative_ts_module_path(normalized_path, str(match.group("mod") or ""), base_files)
        if child and _typescript_module_exports_symbol_resolved(
            module_path=child,
            base_files=base_files,
            symbol=symbol,
            _depth=_depth + 1,
            _seen=seen,
        ):
            return True
    return False

def _relative_import_specifier_for_actual_path(
    *,
    importer_rel: str,
    original_specifier: str,
    actual_target_rel: str,
) -> str:
    relative = posixpath.relpath(actual_target_rel, posixpath.dirname(importer_rel) or ".")
    if not relative.startswith("."):
        relative = f"./{relative}"
    if not posixpath.splitext(original_specifier)[1]:
        for suffix in _relative_import_suffix_order(importer_rel):
            if relative.endswith(suffix):
                relative = relative[: -len(suffix)]
                break
    return relative

def _relative_import_suffix_order(importer_rel: str) -> tuple[str, ...]:
    if importer_rel.endswith(".tsx"):
        return (".tsx", ".ts", ".jsx", ".js")
    if importer_rel.endswith(".jsx"):
        return (".jsx", ".js", ".tsx", ".ts")
    return (".ts", ".tsx", ".js", ".jsx")

def _typescript_module_allows_import_meta(raw_module: object) -> bool:
    return str(raw_module or "").strip().lower() in {
        "es2020",
        "es2022",
        "esnext",
        "system",
        "node16",
        "node18",
        "node20",
        "nodenext",
    }

def _add_vitest_import_to_typescript_test(text: str, symbols: set[str]) -> str:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested:
        return text
    match = _TS_VITEST_IMPORT_RE.search(text)
    if match:
        existing = {token.strip() for token in str(match.group("symbols") or "").split(",") if token.strip()}
        replacement = f"import {{ {', '.join(sorted(existing | set(requested)))} }} from 'vitest';"
        return text[: match.start()] + replacement + text[match.end() :]
    return f"import {{ {', '.join(requested)} }} from 'vitest';\n{text}"

def _prepend_typescript_vitest_import_operation(
    *,
    path: str,
    original: str,
    symbols: set[str],
) -> RepairOperation | None:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested or not original:
        return None
    first_line_end = original.find("\n")
    if first_line_end < 0:
        span_start = 0
        span_end = len(original)
        expected = original
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{original}"
    else:
        span_start = 0
        span_end = first_line_end + 1
        expected = original[:span_end]
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{expected}"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(original),
        metadata={
            "repair_kind": "typescript_vitest_global_import",
            "symbols": tuple(requested),
            "prepend_import": True,
        },
    )

def _typescript_ensure_named_type_import_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    base_files: Mapping[str, str],
) -> RepairOperation | None:
    """Insert ``type_name`` into an existing relative import when missing."""

    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return None
    if re.search(rf"\bimport\s*{{[^}}]*\b{re.escape(type_name)}\b", content):
        return None
    # Prefer import from ./models when present (common L1 shape).
    for module in ("./models", "../models"):
        match = re.search(
            rf"(import\s*{{)([^}}]+)(}}\s*from\s*['\"]{re.escape(module)}['\"]\s*;)",
            content,
        )
        if match is None:
            continue
        clause = str(match.group(2) or "")
        if type_name in {part.strip().split(" as ")[0].strip() for part in clause.split(",")}:
            return None
        new_clause = clause.rstrip()
        if new_clause and not new_clause.endswith(","):
            new_clause = f"{new_clause}, "
        else:
            new_clause = f"{new_clause} "
        new_clause = f"{new_clause}{type_name}"
        replacement = f"{match.group(1)}{new_clause}{match.group(3)}"
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=match.group(0),
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property_import",
                "type_name": type_name,
                "module": module,
            },
        )
    # Locate declaration file for type_name and insert a new import.
    source_path = ""
    for rel, text in base_files.items():
        if re.search(rf"(?:export\s+)?(?:interface|type|class)\s+{re.escape(type_name)}\b", str(text or "")):
            source_path = rel
            break
    if not source_path or source_path == path:
        return None
    # relative import from path → source_path
    from_dir = PurePosixPath(path).parent
    target = PurePosixPath(source_path)
    rel = posixpath.relpath(target.as_posix(), from_dir.as_posix() if str(from_dir) != "." else ".")
    if not rel.startswith("."):
        rel = f"./{rel}"
    if rel.endswith(".ts"):
        rel = rel[:-3]
    insert = f'import {{ {type_name} }} from "{rel}";\n'
    # after last import
    last_import = None
    for m in re.finditer(r"(?m)^import\s.+;\s*$", content):
        last_import = m
    if last_import is not None:
        span_start = last_import.end()
        if not content[span_start : span_start + 1].startswith("\n"):
            insert = "\n" + insert
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_start,
            expected="",
            replacement=insert if content[span_start:].startswith("\n") else "\n" + insert,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_param_object_property_import",
                "type_name": type_name,
                "module": rel,
            },
        )
    return None


__all__ = (
    "_rewrite_named_import_binding_lines",
    "_typescript_line_is_import_binding_context",
    "_typescript_exported_private_constructor_modifier_span",
    "_line_index_for_offset",
    "_normalize_typescript_module_ref",
    "_repair_typescript_unresolved_identifier_import",
    "_parse_named_import_symbols",
    "_typescript_named_import_symbols_with_added_symbol",
    "_typescript_imported_const_class_alias_available",
    "_typescript_module_exports_const_class_alias",
    "_typescript_file_has_type_name_import",
    "_typescript_insert_type_import_operation",
    "_typescript_import_insert_offset",
    "_typescript_named_type_specifier_names",
    "_typescript_named_value_specifier_names",
    "_typescript_named_value_specifier_name",
    "_typescript_named_export_specifier_name",
    "_typescript_named_specifier_indent",
    "_typescript_module_exports_symbol",
    "_typescript_module_exports_symbol_resolved",
    "_relative_import_specifier_for_actual_path",
    "_relative_import_suffix_order",
    "_typescript_module_allows_import_meta",
    "_add_vitest_import_to_typescript_test",
    "_prepend_typescript_vitest_import_operation",
    "_typescript_ensure_named_type_import_operation",
)
