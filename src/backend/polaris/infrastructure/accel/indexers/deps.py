from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import MAX_FILE_SIZE_BYTES

if TYPE_CHECKING:
    from pathlib import Path

IMPORT_FROM_RE = re.compile(r"""^\s*import\s+.+?\s+from\s+["']([^"']+)["']""", re.MULTILINE)
REQUIRE_RE = re.compile(r"""require\(\s*["']([^"']+)["']\s*\)""")


def _py_dependencies(text: str, rel_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError:
        return rows

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                rows.append(
                    {
                        "edge_from": rel_path,
                        "edge_to": alias.name,
                        "edge_type": "import",
                        "weight": 1.0,
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if int(getattr(node, "level", 0)) > 0:
                module = ("." * int(getattr(node, "level", 0))) + module
            rows.append(
                {
                    "edge_from": rel_path,
                    "edge_to": module,
                    "edge_type": "from_import",
                    "weight": 1.0,
                }
            )
    return rows


def _ts_js_dependencies(text: str, rel_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in IMPORT_FROM_RE.finditer(text):
        rows.append(
            {
                "edge_from": rel_path,
                "edge_to": match.group(1),
                "edge_type": "import",
                "weight": 1.0,
            }
        )
    for match in REQUIRE_RE.finditer(text):
        rows.append(
            {
                "edge_from": rel_path,
                "edge_to": match.group(1),
                "edge_type": "require",
                "weight": 1.0,
            }
        )
    return rows


def extract_dependencies(file_path: Path, rel_path: str, lang: str) -> list[dict[str, Any]]:
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
        return _py_dependencies(text, rel_path)
    if lang in {"typescript", "javascript"}:
        return _ts_js_dependencies(text, rel_path)
    return []
