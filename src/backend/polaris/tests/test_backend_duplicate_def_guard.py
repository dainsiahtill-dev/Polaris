from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = [
    BACKEND_ROOT / "polaris" / "bootstrap",
    BACKEND_ROOT / "polaris" / "delivery",
    BACKEND_ROOT / "polaris" / "application",
    BACKEND_ROOT / "polaris" / "domain",
    BACKEND_ROOT / "polaris" / "kernelone",
    BACKEND_ROOT / "polaris" / "infrastructure",
    BACKEND_ROOT / "polaris" / "cells",
]


def _iter_python_files():
    for root in PRODUCTION_ROOTS:
        yield from sorted(root.rglob("*.py"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_no_duplicate_top_level_defs_or_classes_in_backend_production_modules() -> None:
    duplicates: dict[str, dict[str, list[int]]] = {}
    for path in _iter_python_files():
        tree = ast.parse(_read_text(path))
        counts = defaultdict(list)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                counts[node.name].append(node.lineno)
        dupes = {name: lines for name, lines in counts.items() if len(lines) > 1}
        if dupes:
            duplicates[path.as_posix()] = dupes
    assert duplicates == {}
