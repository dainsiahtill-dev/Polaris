"""
tests/test_cell_structure_guard.py — Architecture guard: Ghost Cell prevention.

A "ghost cell" is a directory under polaris/cells/ that contains only
__pycache__ / .pyc bytecode artifacts but has no live .py source files and
no cell.yaml governance descriptor.  Ghost cells arise when source files are
deleted without cleaning up Python's bytecode cache.

This guard scans every leaf directory under polaris/cells/ and fails if it
finds any directory that:
  - contains a __pycache__ sub-directory, AND
  - contains ZERO live .py files of its own (not counting __pycache__), AND
  - contains no cell.yaml

The intent is to prevent a regression like compatibility/legacy_bridge
(deleted 2026-03-22, P0-07) from silently re-appearing.

The test is intentionally *strict*: it must be a hard failure, not an xfail,
so that CI catches ghost cells immediately.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
CELLS_ROOT = BACKEND_DIR / "polaris" / "cells"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _has_live_py_files(directory: Path) -> bool:
    """Return True if *directory* (non-recursively) contains at least one .py file.

    Excludes __pycache__ subdirectories from the scan.  We only look at the
    immediate children of *directory* so that a parent namespace package that
    itself has no .py files but has legitimate child Cells is not incorrectly
    flagged (the child directories are handled by their own loop iteration).
    """
    for entry in directory.iterdir():
        if entry.is_file() and entry.suffix == ".py":
            return True
    return False


def _has_cell_yaml(directory: Path) -> bool:
    """Return True if *directory* contains a cell.yaml file."""
    return (directory / "cell.yaml").is_file()


def _has_pycache(directory: Path) -> bool:
    """Return True if *directory* contains a __pycache__ subdirectory."""
    return (directory / "__pycache__").is_dir()


def _collect_ghost_candidates(cells_root: Path) -> list[Path]:
    """Walk cells_root and return directories that look like ghost cells.

    A ghost cell candidate is any non-__pycache__ directory that:
      1. Has a __pycache__ sub-directory (evidence bytecode was generated), AND
      2. Has zero live .py files of its own, AND
      3. Has no cell.yaml.

    We skip the cells_root itself and only inspect its descendants.
    """
    ghosts: list[Path] = []
    if not cells_root.is_dir():
        return ghosts

    for dirpath, dirnames, _filenames in os.walk(cells_root):
        # Skip __pycache__ directories entirely — they are expected artifacts.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]

        current = Path(dirpath)
        if current == cells_root:
            continue  # Don't evaluate the root itself.

        if (
            _has_pycache(current)
            and not _has_live_py_files(current)
            and not _has_cell_yaml(current)
        ):
            ghosts.append(current)

    return ghosts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_ghost_cells_in_polaris_cells() -> None:
    """Fail if any ghost cell (bytecode only, no .py, no cell.yaml) exists.

    This is the primary anti-regression gate for P0-07 (compatibility/
    legacy_bridge ghost cell, cleaned 2026-03-22).

    If this test fails, the failing paths will be printed so that the
    developer knows exactly which directories to remove or restore source
    for.
    """
    if not CELLS_ROOT.is_dir():
        pytest.skip(f"cells root does not exist: {CELLS_ROOT}")

    ghosts = _collect_ghost_candidates(CELLS_ROOT)

    if ghosts:
        relative_paths = sorted(
            str(g.relative_to(BACKEND_DIR)) for g in ghosts
        )
        formatted = "\n  ".join(relative_paths)
        pytest.fail(
            f"Ghost cell(s) detected under polaris/cells/ — directories that "
            f"contain __pycache__ but have no live .py source files and no "
            f"cell.yaml:\n\n  {formatted}\n\n"
            f"Fix: either delete the directory entirely (if the cell was "
            f"intentionally removed) or restore the missing source files and "
            f"cell.yaml.  See P0-07 post-mortem for context."
        )


def test_cells_root_exists() -> None:
    """Sanity check that polaris/cells/ is present so the guard is not silently skipped."""
    assert CELLS_ROOT.is_dir(), (
        f"polaris/cells/ root is missing at {CELLS_ROOT}. "
        "If the directory was intentionally renamed, update CELLS_ROOT in "
        "tests/test_cell_structure_guard.py accordingly."
    )
