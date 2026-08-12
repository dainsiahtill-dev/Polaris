"""TypeScript repair helpers: nullability.

Lossless extract from the former ``typescript_repairs`` module.
"""

from __future__ import annotations

import re

from ...task_scope_paths import _dedupe_preserve_order
from ._constants import (
    _TS_CANVAS_CONTEXT_DECLARATION_LINE_RE,
    _TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE,
    _TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE,
)


def _repair_typescript_nullable_canvas_context_guards(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
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
        # Generalized: any function call that may return nullable type
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
            # Add ! after the function call closing paren
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


def _repair_typescript_duplicate_object_property_lines(
    text: str,
    line_numbers: set[int],
) -> tuple[str, list[int]]:
    if not line_numbers:
        return text, []
    lines = text.splitlines()
    removed: list[int] = []
    for line_no in sorted(line_numbers, reverse=True):
        index = line_no - 1
        if index < 0 or index >= len(lines):
            continue
        line = lines[index]
        if not _looks_like_single_line_typescript_object_property(line):
            continue
        del lines[index]
        removed.append(line_no)
    if not removed:
        return text, []
    repaired = "\n".join(lines)
    if text.endswith("\n"):
        repaired += "\n"
    return repaired, sorted(removed)


def _looks_like_single_line_typescript_object_property(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped or ":" not in stripped:
        return False
    if stripped.startswith(("case ", "default", "return ", "if ", "for ", "while ", "//", "/*", "*")):
        return False
    property_re = re.compile(r"^(?:[A-Za-z_$][A-Za-z0-9_$]*|['\"][^'\"]+['\"]|\[[^\]]+\])\s*:\s*.+,?\s*(?://.*)?$")
    return bool(property_re.match(stripped))
