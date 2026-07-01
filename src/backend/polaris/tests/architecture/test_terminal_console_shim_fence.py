"""Architecture fence for the retired terminal_console import surface."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
CANONICAL_TERMINAL_MODULE = "polaris.delivery.cli.terminal"
RETIRED_TERMINAL_MODULE = "polaris.delivery.cli.terminal_console"
RETIRED_TERMINAL_IMPORT_FROM = "polaris.delivery.cli"
RETIRED_TERMINAL_IMPORT_NAME = "terminal_console"

ALLOWED_RETIRED_TERMINAL_IMPORTS = frozenset(
    {
        "polaris/tests/architecture/test_terminal_console_shim_fence.py",
    }
)


def _active_python_files() -> list[Path]:
    return [path for path in sorted(POLARIS_ROOT.rglob("*.py")) if "__pycache__" not in path.parts]


def _retired_terminal_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_TERMINAL_MODULE:
                    violations.append(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == RETIRED_TERMINAL_MODULE:
            imported = ", ".join(alias.name for alias in node.names)
            violations.append(f"{node.module} import {imported}")
            continue
        if node.module == RETIRED_TERMINAL_IMPORT_FROM and any(
            alias.name == RETIRED_TERMINAL_IMPORT_NAME for alias in node.names
        ):
            violations.append(f"{node.module} import {RETIRED_TERMINAL_IMPORT_NAME}")
    return violations


def test_retired_terminal_console_file_is_absent() -> None:
    """The retired module file must not be recreated."""
    retired_path = POLARIS_ROOT / "delivery" / "cli" / "terminal_console.py"
    assert not retired_path.exists(), (
        f"Retired terminal_console shim was recreated; import {CANONICAL_TERMINAL_MODULE!r} instead."
    )


def test_active_code_imports_canonical_terminal_package() -> None:
    """Active code must not route through the terminal_console shim."""
    violations: list[str] = []
    for path in _active_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in ALLOWED_RETIRED_TERMINAL_IMPORTS:
            continue
        for imported in _retired_terminal_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import "
        f"{CANONICAL_TERMINAL_MODULE!r}; retired terminal_console imports remain:\n" + "\n".join(violations)
    )
