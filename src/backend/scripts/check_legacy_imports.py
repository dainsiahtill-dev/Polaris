#!/usr/bin/env python3
"""Legacy root import checker.

Scans polaris/ and tests/ for imports from old root directories:
- app/
- core/
- api/
- scripts/
- server.py
- director_interface.py

Usage:
    python scripts/check_legacy_imports.py

Exit codes:
    0 - No old root imports found
    1 - Old root imports detected
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path


OLD_ROOT_PATTERNS = [
    "app.",
    "core.",
    "api.",
    "scripts.",
    "server",
    "director_interface",
]


def is_old_root_import(module_name: str) -> bool:
    """Check if a module name is an old root import."""
    if not module_name:
        return False
    for pattern in OLD_ROOT_PATTERNS:
        if module_name == pattern or module_name.startswith(pattern + "."):
            return True
    return False


def find_legacy_imports(base_dir: Path, scan_dir: Path) -> dict[str, list[str]]:
    """Find all old root imports in Python files under scan_dir."""
    violations: dict[str, list[str]] = defaultdict(list)

    for py_file in scan_dir.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8-sig", errors="ignore")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        rel_path = str(py_file.relative_to(base_dir)).replace(chr(92), "/")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    continue
                mod = node.module or ""
                if is_old_root_import(mod):
                    violations[rel_path].append(f"from {mod}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if is_old_root_import(alias.name):
                        violations[rel_path].append(f"import {alias.name}")

    return dict(violations)


def main() -> int:
    backend_dir = Path(__file__).resolve().parents[1]
    polaris_dir = backend_dir / "polaris"
    tests_dir = backend_dir / "tests"

    all_violations: dict[str, list[str]] = {}

    if polaris_dir.exists():
        all_violations.update(find_legacy_imports(backend_dir, polaris_dir))

    if tests_dir.exists():
        all_violations.update(find_legacy_imports(backend_dir, tests_dir))

    if not all_violations:
        print("OK: No old root imports found in polaris/ or tests/")
        return 0

    print("FAIL: Old root imports detected!")
    print()
    for path, imports in sorted(all_violations.items()):
        print(f"  {path}")
        for imp in imports:
            print(f"    - {imp}")
        print()

    print(f"Total files with violations: {len(all_violations)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
