"""TypeScript repair helpers: imports_exports.

Lossless extract from the former ``typescript_repairs`` module.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ...task_scope_paths import _dedupe_preserve_order
from .._common import (
    _TS_OBJECT_LITERAL_START_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE,
    _TS_RETURN_OBJECT_START_RE,
    _TS_RUNTIME_EXPORT_TEMPLATE,
    _relative_import_suffix_order,
)
from ._constants import (
    _TS_ENUM_DECLARATION_LINE_RE,
    _TS_ENUM_MEMBER_LINE_RE,
    _TS_IDENTIFIER_RE,
)


def _resolve_case_variant_relative_path(root: Path, relative_path: str) -> str:
    current = root
    parts = [part for part in Path(relative_path).parts if part not in {"", "."}]
    resolved_parts: list[str] = []
    for part in parts:
        candidate = current / part
        if candidate.exists():
            current = candidate
            resolved_parts.append(part)
            continue
        if not current.is_dir():
            return ""
        try:
            match = next((item for item in current.iterdir() if item.name.lower() == part.lower()), None)
        except OSError:
            return ""
        if match is None:
            return ""
        current = match
        resolved_parts.append(match.name)
    try:
        resolved = current.resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    return "/".join(resolved_parts)


def _relative_import_specifier_for_actual_path(
    *,
    root: Path,
    importer_rel: str,
    original_specifier: str,
    actual_target_rel: str,
) -> str:
    importer_path = (root / importer_rel).resolve()
    actual_target_path = (root / actual_target_rel).resolve()
    relative = os.path.relpath(actual_target_path, importer_path.parent).replace(os.sep, "/")
    if not relative.startswith("."):
        relative = f"./{relative}"
    if not Path(original_specifier).suffix:
        suffix_order = _relative_import_suffix_order(importer_rel)
        for suffix in suffix_order:
            if relative.endswith(suffix):
                relative = relative[: -len(suffix)]
                break
    return relative


_TS_IMPORT_FROM_SPECIFIER_TEMPLATE = (
    r"(?m)^(?P<indent>\s*)import\s+(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"]){specifier}(?P=quote)\s*;?\s*$"
)


def _typescript_import_pairs_for_specifier(content: str, specifier: str) -> list[tuple[str, str]]:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    match = pattern.search(content)
    if not match:
        return []
    clause = str(match.group("clause") or "").strip()
    if clause.startswith("type "):
        clause = clause[5:].strip()
    pairs: list[tuple[str, str]] = []
    namespace_match = re.fullmatch(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if namespace_match:
        return []
    if clause.startswith("{") and clause.endswith("}"):
        named_clause = clause[1:-1]
        default_clause = ""
    elif ",{" in clause.replace(" ", ""):
        default_clause, named_clause = clause.split(",", 1)
        named_clause = named_clause.strip()
        named_clause = named_clause[1:-1] if named_clause.startswith("{") and named_clause.endswith("}") else ""
    else:
        default_clause = clause
        named_clause = ""

    default_name = default_clause.strip()
    if _TS_IDENTIFIER_RE.fullmatch(default_name):
        pairs.append(("default", default_name))

    for raw_part in named_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part[5:].strip()
        alias_parts = re.split(r"\s+as\s+", part, maxsplit=1, flags=re.IGNORECASE)
        imported = alias_parts[0].strip()
        local = alias_parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported) and _TS_IDENTIFIER_RE.fullmatch(local):
            pairs.append((imported, local))
    return pairs


def _typescript_import_statement_for_specifier(content: str, specifier: str) -> re.Match[str] | None:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    return pattern.search(content)


def _typescript_identifier_used_outside_span(content: str, name: str, span: tuple[int, int]) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return False
    outside = content[: span[0]] + content[span[1] :]
    return re.search(rf"\b{re.escape(name)}\b", outside) is not None


def _remove_unused_typescript_import(content: str, specifier: str) -> str:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return content
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    if not pairs:
        return content
    span = match.span()
    if any(_typescript_identifier_used_outside_span(content, local, span) for _, local in pairs):
        return content
    start, end = span
    if end < len(content) and content[end : end + 1] == "\n":
        end += 1
    return content[:start] + content[end:]


def _find_unique_typescript_export_for_import(
    *,
    workspace_path: Path,
    importer_path: Path,
    content: str,
    specifier: str,
) -> Path | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    needed_symbols = [
        imported
        for imported, local in pairs
        if imported != "default" and _typescript_identifier_used_outside_span(content, local, match.span())
    ]
    if not needed_symbols:
        return None

    candidates: list[Path] = []
    for candidate in workspace_path.rglob("*.ts"):
        if candidate == importer_path or candidate.name.endswith(".d.ts"):
            continue
        try:
            candidate.relative_to(workspace_path)
            candidate_text = candidate.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
            continue
        if all(_typescript_module_runtime_exports_symbol(candidate_text, symbol) for symbol in needed_symbols):
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def _typescript_brace_balance_delta(source: str) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def _repair_typescript_missing_closing_braces(source: str) -> str:
    missing_count = _typescript_brace_balance_delta(source)
    if missing_count <= 0 or missing_count > 8:
        return source
    repaired = source.rstrip() + "\n"
    repaired += "\n".join("}" for _ in range(missing_count))
    return repaired + "\n"


def _repair_typescript_enum_member_separator_lines(text: str, target_line_numbers: set[int]) -> str:
    if not target_line_numbers:
        return str(text or "")
    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    brace_depth = 0
    enum_depth: int | None = None
    changed = False
    for line_number, line in enumerate(lines, start=1):
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        line_to_append = line
        if enum_depth is not None and line_number in target_line_numbers:
            repaired_line = _repair_typescript_enum_member_line(line_body)
            if repaired_line != line_body:
                line_to_append = repaired_line + newline
                changed = True
        repaired.append(line_to_append)

        opens = line_body.count("{")
        closes = line_body.count("}")
        if enum_depth is None and _TS_ENUM_DECLARATION_LINE_RE.search(line_body):
            enum_depth = brace_depth + max(opens, 1)
        brace_depth += opens - closes
        if enum_depth is not None and brace_depth < enum_depth:
            enum_depth = None
    return "".join(repaired) if changed else str(text or "")


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


def _repair_typescript_return_object_semicolon_lines(text: str) -> str:
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
        in_object_literal = bool(object_literal_depths) or starts_object_literal
        if in_object_literal:
            match = _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE.match(line_body)
            if match:
                repaired.append(f"{match.group('indent')}{match.group('name')},{newline}")
                changed = True
            else:
                value_match = _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE.match(line_body)
                if value_match:
                    repaired.append(f"{value_match.group('indent')}{value_match.group('property').rstrip()},{newline}")
                    changed = True
                else:
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


def _typescript_relative_import_without_suffix(
    *,
    importer_relative_path: str,
    imported_relative_path: str,
    workspace_path: Path,
) -> str:
    importer_path = (workspace_path / importer_relative_path).resolve()
    imported_path = (workspace_path / imported_relative_path).resolve().with_suffix("")
    module_ref = os.path.relpath(imported_path, importer_path.parent).replace("\\", "/")
    if not module_ref.startswith("."):
        module_ref = f"./{module_ref}"
    return module_ref


def _iter_typescript_files(workspace_path: Path) -> list[Path]:
    ignored = {".git", ".polaris", "node_modules", "dist", "build", ".vite", ".pytest_cache"}
    results: list[Path] = []
    for path in workspace_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".ts", ".tsx"}:
            continue
        rel = path.relative_to(workspace_path)
        if any(part in ignored for part in rel.parts):
            continue
        results.append(path)
    return sorted(results, key=lambda item: item.as_posix())


def _typescript_module_runtime_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(_TS_RUNTIME_EXPORT_TEMPLATE.format(symbol=escaped), module_text):
        return True
    export_block_re = re.compile(r"export\s*\{(?P<symbols>[^}]+)\}", re.DOTALL)
    for match in export_block_re.finditer(module_text):
        if symbol in _parse_named_export_symbols(match.group("symbols")):
            return True
    return False


def _parse_named_export_symbols(symbols_text: str) -> list[str]:
    symbols: list[str] = []
    for raw in str(symbols_text or "").replace("\n", " ").split(","):
        token = raw.strip()
        if token.startswith("type "):
            token = token[5:].strip()
        parts = re.split(r"\s+as\s+", token, maxsplit=1)
        exported = parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(exported):
            symbols.append(exported)
    return _dedupe_preserve_order(symbols)
