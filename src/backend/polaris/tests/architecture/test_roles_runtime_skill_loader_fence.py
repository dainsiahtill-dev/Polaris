"""Architecture fence for the retired roles-runtime skill-loader facade."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_SKILL_LOADER_MODULE = "polaris.cells.roles.runtime.internal.skill_loader"
RETIRED_PUBLIC_EXPORTS = frozenset(
    {
        "RoleSkillManager",
        "SkillLoader",
        "create_role_skill_manager",
        "create_skill_loader",
    }
)
CANONICAL_SKILL_MODULE = "polaris.kernelone.single_agent.skill_system"


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "generated" not in path.parts
        and not path.name.startswith("test_")
    ]


def _retired_skill_loader_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_SKILL_LOADER_MODULE:
                    violations.append(alias.name)
            continue
        if isinstance(node, ast.ImportFrom) and node.module == RETIRED_SKILL_LOADER_MODULE:
            imported = ", ".join(sorted(alias.name for alias in node.names))
            violations.append(f"{node.module} import {imported}")
    return violations


def _module_all(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    exports: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            continue
        if isinstance(node.value, ast.List):
            exports.update(
                item.value for item in node.value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return exports


def test_roles_runtime_skill_loader_facade_is_removed() -> None:
    """Role runtime must use KernelOne skills directly instead of a second facade."""
    retired_path = POLARIS_ROOT / "cells/roles/runtime/internal/skill_loader.py"
    assert not retired_path.exists(), "Retired roles-runtime skill_loader.py facade was recreated."

    violations: list[str] = []
    for path in _production_python_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for imported in _retired_skill_loader_imports(path):
            violations.append(f"{rel}: {imported}")

    assert violations == [], (
        "Production code must import skills from "
        f"{CANONICAL_SKILL_MODULE!r}; retired roles-runtime skill loader imports remain:\n"
        + "\n".join(violations)
    )


def test_roles_runtime_public_boundary_does_not_export_retired_skill_facade() -> None:
    """Public roles-runtime exports must not keep old skill-loader aliases alive."""
    checked_paths = [
        POLARIS_ROOT / "cells/roles/runtime/__init__.py",
        POLARIS_ROOT / "cells/roles/runtime/public/__init__.py",
        POLARIS_ROOT / "cells/roles/runtime/public/service.py",
    ]

    violations: list[str] = []
    for path in checked_paths:
        retired_exports = _module_all(path) & RETIRED_PUBLIC_EXPORTS
        if retired_exports:
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            violations.append(f"{rel}: {sorted(retired_exports)}")

    assert violations == [], (
        "roles.runtime public boundaries must expose KernelOne skill contracts directly, "
        "not retired roles-runtime skill loader aliases:\n" + "\n".join(violations)
    )
