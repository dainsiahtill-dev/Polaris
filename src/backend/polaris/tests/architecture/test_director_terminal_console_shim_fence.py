"""Architecture fence for the retired Director terminal console re-export."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
CANONICAL_DIRECTOR_PACKAGE = "polaris.delivery.cli.director"
CANONICAL_TERMINAL_PACKAGE = "polaris.delivery.cli.terminal"
RETIRED_DIRECTOR_TERMINAL_MODULE = "polaris.delivery.cli.director.terminal_console"
RETIRED_DIRECTOR_IMPORT_FROM = "polaris.delivery.cli.director"
RETIRED_DIRECTOR_IMPORT_NAME = "terminal_console"

ALLOWED_RETIRED_DIRECTOR_TERMINAL_IMPORTS = frozenset(
    {
        "polaris/tests/architecture/test_director_terminal_console_shim_fence.py",
    }
)


def _active_python_files() -> list[Path]:
    return [path for path in sorted(POLARIS_ROOT.rglob("*.py")) if "__pycache__" not in path.parts]


def _retired_director_terminal_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_DIRECTOR_TERMINAL_MODULE:
                    violations.append(alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == RETIRED_DIRECTOR_TERMINAL_MODULE:
            imported = ", ".join(alias.name for alias in node.names)
            violations.append(f"{node.module} import {imported}")
            continue
        if node.module == RETIRED_DIRECTOR_IMPORT_FROM and any(
            alias.name == RETIRED_DIRECTOR_IMPORT_NAME for alias in node.names
        ):
            violations.append(f"{node.module} import {RETIRED_DIRECTOR_IMPORT_NAME}")
    return violations


def test_retired_director_terminal_console_file_is_absent() -> None:
    """The retired Director terminal re-export file must not be recreated."""
    retired_path = POLARIS_ROOT / "delivery" / "cli" / "director" / "terminal_console.py"
    assert not retired_path.exists(), (
        "Retired Director terminal_console shim was recreated; import "
        f"{CANONICAL_DIRECTOR_PACKAGE!r} or {CANONICAL_TERMINAL_PACKAGE!r} instead."
    )


def test_active_code_imports_canonical_director_or_terminal_package() -> None:
    """Active code must not route through the retired Director terminal re-export."""
    violations: list[str] = []
    for path in _active_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in ALLOWED_RETIRED_DIRECTOR_TERMINAL_IMPORTS:
            continue
        for imported in _retired_director_terminal_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Active code must import "
        f"{CANONICAL_DIRECTOR_PACKAGE!r} or {CANONICAL_TERMINAL_PACKAGE!r}; retired Director "
        "terminal_console imports remain:\n" + "\n".join(violations)
    )


def test_director_package_root_still_exposes_console_entrypoint() -> None:
    """The canonical Director package root keeps its public console entrypoint."""
    from polaris.delivery.cli import director

    assert callable(director.run_director_console)
