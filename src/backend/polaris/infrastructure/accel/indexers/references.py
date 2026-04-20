from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import MAX_FILE_SIZE_BYTES

if TYPE_CHECKING:
    from pathlib import Path


CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _py_call_references(text: str, rel_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError:
        return rows

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = ""
            if isinstance(node.func, ast.Name):
                target = node.func.id
            elif isinstance(node.func, ast.Attribute):
                target = node.func.attr
            if not target:
                continue
            rows.append(
                {
                    "source_symbol": rel_path,
                    "target_symbol": target,
                    "relation": "call",
                    "file": rel_path,
                    "line": int(getattr(node, "lineno", 1)),
                    "source": "semantic",
                    "confidence": 0.8,
                }
            )
    return rows


def _ts_js_references(text: str, rel_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = text.splitlines()
    for line_no, line in enumerate(lines, start=1):
        for match in CALL_RE.finditer(line):
            symbol = match.group(1)
            if symbol in {"if", "for", "while", "switch", "catch", "function", "return"}:
                continue
            rows.append(
                {
                    "source_symbol": rel_path,
                    "target_symbol": symbol,
                    "relation": "call",
                    "file": rel_path,
                    "line": line_no,
                    "source": "text",
                    "confidence": 0.5,
                }
            )
    return rows


def extract_references(file_path: Path, rel_path: str, lang: str) -> list[dict[str, Any]]:
    # Check file size to prevent blocking
    try:
        file_size = file_path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            return [
                {
                    "type": "error",
                    "name": "file_too_large",
                    "line_start": 1,
                    "line_end": 1,
                    "char_start": 0,
                    "char_end": 0,
                    "error": f"File too large: {file_size:,} bytes",
                }
            ]
    except OSError:
        return [
            {
                "type": "error",
                "name": "stat_error",
                "line_start": 1,
                "line_end": 1,
                "char_start": 0,
                "char_end": 0,
                "error": "Cannot read file stats",
            }
        ]

    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return [
            {
                "type": "error",
                "name": "read_error",
                "line_start": 1,
                "line_end": 1,
                "char_start": 0,
                "char_end": 0,
                "error": str(exc),
            }
        ]
    if lang == "python":
        return _py_call_references(text, rel_path)
    if lang in {"typescript", "javascript"}:
        return _ts_js_references(text, rel_path)
    return []
