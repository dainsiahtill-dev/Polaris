"""A6 one-way dependency fences for Task 4 directed-effect evidence."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_DIRECTOR_RUNTIME_ROOT = _BACKEND_ROOT / "polaris/cells/director/runtime"
_TASK_RUNTIME_ROOT = _BACKEND_ROOT / "polaris/cells/runtime/task_runtime"
_ROLES_KERNEL_ROOT = _BACKEND_ROOT / "polaris/cells/roles/kernel"
_DIRECTOR_PREFIX = "polaris.cells.director"
_DIRECTOR_RUNTIME_PUBLIC_PREFIX = "polaris.cells.director.runtime.public"
_ROLES_PREFIX = "polaris.cells.roles"
_TASK4_ROLES_KERNEL_PRODUCTION = (
    _ROLES_KERNEL_ROOT / "public/__init__.py",
    _ROLES_KERNEL_ROOT / "public/turn_contracts.py",
    _ROLES_KERNEL_ROOT / "internal/turn_decision_decoder.py",
    _ROLES_KERNEL_ROOT / "internal/tool_gateway.py",
    _ROLES_KERNEL_ROOT / "internal/directed_effect_policy_guard.py",
    _ROLES_KERNEL_ROOT / "internal/speculation/contracts.py",
    _ROLES_KERNEL_ROOT / "internal/stream_shadow_engine.py",
    _ROLES_KERNEL_ROOT / "internal/speculation/resolver.py",
    _ROLES_KERNEL_ROOT / "internal/speculation/write_phases.py",
)


@dataclass(frozen=True, slots=True)
class _ImportReference:
    path: Path
    module: str
    line: int

    def render(self) -> str:
        return f"{self.path.relative_to(_BACKEND_ROOT)}:{self.line}: {self.module}"


def _production_python_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in root.rglob("*.py")
            if "tests" not in path.relative_to(root).parts and "__pycache__" not in path.parts
        )
    )


def _resolve_import_from_module(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = list(path.parent.parts)
    polaris_indexes = [index for index, part in enumerate(package_parts) if part == "polaris"]
    if not polaris_indexes:
        raise ValueError(f"cannot resolve relative import outside polaris package: {path}:{node.lineno}")
    package_parts = package_parts[polaris_indexes[-1] :]
    parent_hops = node.level - 1
    if parent_hops >= len(package_parts):
        raise ValueError(f"relative import escapes polaris package: {path}:{node.lineno}")

    resolved_parts = package_parts[: len(package_parts) - parent_hops]
    if node.module:
        resolved_parts.extend(node.module.split("."))
    return ".".join(resolved_parts)


def _absolute_imports(path: Path) -> tuple[_ImportReference, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    references: list[_ImportReference] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            references.extend(_ImportReference(path, alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from_module(path, node)
            if module:
                references.append(_ImportReference(path, module, node.lineno))
                references.extend(
                    _ImportReference(path, f"{module}.{alias.name}", node.lineno)
                    for alias in node.names
                    if alias.name != "*"
                )
    return tuple(references)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _render(references: list[_ImportReference]) -> str:
    return "\n".join(reference.render() for reference in references)


@pytest.mark.parametrize(
    ("relative_path", "source", "expected_module"),
    [
        (
            "polaris/cells/director/runtime/internal/mutation.py",
            "from ....roles.kernel import public\n",
            "polaris.cells.roles.kernel",
        ),
        (
            "polaris/cells/runtime/task_runtime/internal/mutation.py",
            "from ....director import runtime\n",
            "polaris.cells.director",
        ),
        (
            "polaris/cells/roles/kernel/internal/mutation.py",
            "from ....director.runtime.internal import x\n",
            "polaris.cells.director.runtime.internal",
        ),
    ],
)
def test_import_collector_resolves_relative_import_mutations(
    tmp_path: Path,
    relative_path: str,
    source: str,
    expected_module: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    modules = {reference.module for reference in _absolute_imports(path)}

    assert expected_module in modules


def test_import_collector_records_base_and_original_alias_targets(tmp_path: Path) -> None:
    path = tmp_path / "polaris/cells/runtime/task_runtime/internal/mutation.py"
    path.parent.mkdir(parents=True)
    path.write_text("from polaris.cells import director as director_cell, roles\n", encoding="utf-8")

    modules = {reference.module for reference in _absolute_imports(path)}

    assert modules == {
        "polaris.cells",
        "polaris.cells.director",
        "polaris.cells.roles",
    }


@pytest.mark.parametrize(
    ("relative_path", "source", "expected_module", "forbidden_prefix"),
    [
        (
            "polaris/cells/director/runtime/internal/mutation.py",
            "from polaris.cells.roles import kernel\n",
            "polaris.cells.roles.kernel",
            "polaris.cells.roles.kernel",
        ),
        (
            "polaris/cells/director/runtime/internal/mutation.py",
            "from ....roles import adapters\n",
            "polaris.cells.roles.adapters",
            "polaris.cells.roles.adapters",
        ),
        (
            "polaris/cells/runtime/task_runtime/internal/mutation.py",
            "from polaris.cells import director as director_cell\n",
            "polaris.cells.director",
            _DIRECTOR_PREFIX,
        ),
        (
            "polaris/cells/runtime/task_runtime/internal/mutation.py",
            "from .... import roles\n",
            "polaris.cells.roles",
            _ROLES_PREFIX,
        ),
        (
            "polaris/cells/roles/kernel/internal/mutation.py",
            "from polaris.cells import director\n",
            "polaris.cells.director",
            _DIRECTOR_PREFIX,
        ),
        (
            "polaris/cells/roles/kernel/internal/mutation.py",
            "from .... import director as director_cell\n",
            "polaris.cells.director",
            _DIRECTOR_PREFIX,
        ),
    ],
)
def test_import_collector_closes_parent_import_fence_bypasses(
    tmp_path: Path,
    relative_path: str,
    source: str,
    expected_module: str,
    forbidden_prefix: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    modules = {reference.module for reference in _absolute_imports(path)}

    assert expected_module in modules
    assert any(_matches_prefix(module, forbidden_prefix) for module in modules)


def test_director_runtime_production_does_not_import_roles_kernel_or_adapters() -> None:
    files = _production_python_files(_DIRECTOR_RUNTIME_ROOT)
    assert files, "expected director.runtime production files"

    violations = [
        reference
        for path in files
        for reference in _absolute_imports(path)
        if _matches_prefix(reference.module, "polaris.cells.roles.kernel")
        or _matches_prefix(reference.module, "polaris.cells.roles.adapters")
    ]

    assert not violations, "director.runtime must not import roles.kernel or roles.adapters:\n" + _render(violations)


def test_task_runtime_production_does_not_import_director_or_roles_cells() -> None:
    files = _production_python_files(_TASK_RUNTIME_ROOT)
    assert files, "expected runtime.task_runtime production files"

    violations = [
        reference
        for path in files
        for reference in _absolute_imports(path)
        if _matches_prefix(reference.module, _DIRECTOR_PREFIX) or _matches_prefix(reference.module, _ROLES_PREFIX)
    ]

    assert not violations, "runtime.task_runtime must not import Director or roles Cells:\n" + _render(violations)


def test_roles_kernel_task4_imports_director_only_through_runtime_public() -> None:
    missing = [path for path in _TASK4_ROLES_KERNEL_PRODUCTION if not path.is_file()]
    assert not missing, f"missing frozen Task 4 production files: {missing}"

    director_imports = [
        reference
        for path in _TASK4_ROLES_KERNEL_PRODUCTION
        for reference in _absolute_imports(path)
        if _matches_prefix(reference.module, _DIRECTOR_PREFIX)
    ]
    assert director_imports, "expected the approved roles.kernel -> director.runtime.public dependency"

    violations = [
        reference
        for reference in director_imports
        if not _matches_prefix(reference.module, _DIRECTOR_RUNTIME_PUBLIC_PREFIX)
    ]
    assert not violations, "Task 4 roles.kernel may import Director only through director.runtime.public:\n" + _render(
        violations
    )
