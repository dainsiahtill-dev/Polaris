"""Architecture fence: repair_kernel path-normalize SSoT (F-09 / B5 / D1).

Blueprint:
``src/backend/docs/blueprints/KERNELONE_FOUNDATION_HARDENING_PLAN_20260810.md``

- F-09: ``_normalize_repair_path`` historical copies must not grow.
- B5 Path SSoT: new repair_kernel code must import ``path_files`` instead of
  re-copying the ``./`` strip / traversal loop.
- D1: language modules migrate to ``path_files`` in later PRs; this gate only
  ratchets the copy count and locks the TypeScript package wrapper.

This module scans the live ``repair_kernel`` tree. It does not hard-code PASS.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

POLARIS_ROOT = Path(__file__).resolve().parents[2]
REPAIR_KERNEL_ROOT = POLARIS_ROOT / "cells" / "director" / "runtime" / "internal" / "repair_kernel"
PATH_FILES = REPAIR_KERNEL_ROOT / "path_files.py"
TYPESCRIPT_SYNTAX_DIR = REPAIR_KERNEL_ROOT / "typescript_syntax"

# Disk scan 2026-08-14: 18 ``def _normalize_repair_path`` under repair_kernel
# (17 local-loop language copies + 1 typescript_syntax thin wrapper).
# Historical copies may remain; new copies must use path_files instead.
EXPECTED_LEGACY_COPIES = 18

_SSOT_STRICT = "normalize_repair_path_strict"
_SSOT_PERMISSIVE = "normalize_repair_path_permissive"
_LOCAL_NORMALIZE_LOOP = "while normalized.startswith"
_NORMALIZE_HELPER = "_normalize_repair_path"
_NEW_COPY_HINT = "新增副本，改用 path_files"


@dataclass(frozen=True)
class _NormalizeHelperDef:
    """One ``_normalize_repair_path`` definition found on disk."""

    path: Path
    lineno: int
    calls_strict: bool


def _iter_python_files(root: Path) -> tuple[Path, ...]:
    """Return sorted ``*.py`` files under *root* (real directory walk)."""

    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.py") if path.is_file()))


def _read_utf8(path: Path) -> str:
    """Read *path* as UTF-8 text."""

    return path.read_text(encoding="utf-8")


def _top_level_function_names(module: ast.Module) -> frozenset[str]:
    """Return names of top-level ``FunctionDef`` / ``AsyncFunctionDef`` nodes."""

    names: set[str] = set()
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return frozenset(names)


def _call_names(func: ast.AST) -> frozenset[str]:
    """Return callee names/attrs referenced by ``Call`` nodes inside *func*."""

    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return frozenset(names)


def _iter_named_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield every function/async function in *tree*, including nested defs."""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _scan_normalize_helper_defs(root: Path) -> tuple[_NormalizeHelperDef, ...]:
    """AST-scan *root* for every ``def _normalize_repair_path``."""

    found: list[_NormalizeHelperDef] = []
    for path in _iter_python_files(root):
        source = _read_utf8(path)
        module = ast.parse(source, filename=str(path))
        for func in _iter_named_functions(module):
            if func.name != _NORMALIZE_HELPER:
                continue
            found.append(
                _NormalizeHelperDef(
                    path=path,
                    lineno=func.lineno,
                    calls_strict=_SSOT_STRICT in _call_names(func),
                )
            )
    return tuple(found)


def _rel(path: Path) -> str:
    """POSIX path relative to ``repair_kernel`` when possible."""

    try:
        return path.relative_to(REPAIR_KERNEL_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def test_path_files_ssot_exists_and_defines_named_apis() -> None:
    """``path_files.py`` is the only allowed owner of named path-normalize APIs."""

    assert PATH_FILES.is_file(), f"path SSoT missing: {PATH_FILES}"

    source = _read_utf8(PATH_FILES)
    module = ast.parse(source, filename=str(PATH_FILES))
    defined = _top_level_function_names(module)
    missing = {_SSOT_STRICT, _SSOT_PERMISSIVE} - defined
    assert not missing, (
        f"{PATH_FILES.name} must define {sorted({_SSOT_STRICT, _SSOT_PERMISSIVE})}; missing {sorted(missing)}"
    )
    # SSoT may own the ``./`` strip loop; this file is excluded from the
    # typescript_syntax local-loop ban below.
    assert PATH_FILES.resolve() != TYPESCRIPT_SYNTAX_DIR.resolve()
    assert not str(PATH_FILES.resolve()).startswith(str(TYPESCRIPT_SYNTAX_DIR.resolve()) + "/")


def test_typescript_syntax_package_must_not_reimplement_normalize_loop() -> None:
    """TS package may wrap path_files; it must not copy the strip loop."""

    assert TYPESCRIPT_SYNTAX_DIR.is_dir(), f"missing package: {TYPESCRIPT_SYNTAX_DIR}"
    py_files = _iter_python_files(TYPESCRIPT_SYNTAX_DIR)
    assert py_files, f"no python files under {TYPESCRIPT_SYNTAX_DIR}"

    offenders: list[str] = []
    for path in py_files:
        if _LOCAL_NORMALIZE_LOOP in _read_utf8(path):
            offenders.append(_rel(path))

    assert not offenders, (
        "typescript_syntax must not contain a local "
        f"{_LOCAL_NORMALIZE_LOOP!r} loop; SSoT is {PATH_FILES.name} only. "
        f"offenders={offenders}"
    )


def test_typescript_syntax_normalize_wrappers_must_delegate_to_strict_ssot() -> None:
    """Any TS ``_normalize_repair_path`` must call ``normalize_repair_path_strict``."""

    assert TYPESCRIPT_SYNTAX_DIR.is_dir(), f"missing package: {TYPESCRIPT_SYNTAX_DIR}"

    wrappers: list[tuple[str, int, bool]] = []
    for path in _iter_python_files(TYPESCRIPT_SYNTAX_DIR):
        source = _read_utf8(path)
        module = ast.parse(source, filename=str(path))
        for func in _iter_named_functions(module):
            if func.name != _NORMALIZE_HELPER:
                continue
            wrappers.append((_rel(path), func.lineno, _SSOT_STRICT in _call_names(func)))

    non_delegating = [item for item in wrappers if not item[2]]
    assert not non_delegating, (
        f"typescript_syntax def _normalize_repair_path must call {_SSOT_STRICT} (AST). non_delegating={non_delegating}"
    )


def test_repair_kernel_normalize_repair_path_copies_must_not_increase() -> None:
    """Historical ``_normalize_repair_path`` copies may stay; new ones may not.

    Count is a live disk/AST scan of ``repair_kernel/**/*.py``. The cap is the
    snapshot taken when this fence landed. Later language PRs should delete
    copies and lower ``EXPECTED_LEGACY_COPIES``.
    """

    assert REPAIR_KERNEL_ROOT.is_dir(), f"missing repair_kernel: {REPAIR_KERNEL_ROOT}"

    found = _scan_normalize_helper_defs(REPAIR_KERNEL_ROOT)
    locations = [f"{_rel(item.path)}:{item.lineno}" for item in found]
    count = len(found)

    assert count <= EXPECTED_LEGACY_COPIES, (
        f"{_NEW_COPY_HINT}: found {count} def {_NORMALIZE_HELPER} under "
        f"repair_kernel (cap {EXPECTED_LEGACY_COPIES}). locations={locations}"
    )
    # Cap is an upper bound only. A drop is progress; do not fail on deletions.
    assert count >= 1, (
        f"scanner found no def {_NORMALIZE_HELPER}; expected historical copies "
        f"or the typescript_syntax wrapper under {REPAIR_KERNEL_ROOT}"
    )
