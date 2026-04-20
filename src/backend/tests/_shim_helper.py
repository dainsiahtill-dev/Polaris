"""Shared shim loader -- eliminates duplicate bootstrapping across test files."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _find_shim() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "_root_test_shim.py"
        if candidate.is_file():
            return candidate
    raise RuntimeError("_root_test_shim.py not found")


def _load_shim():
    shim_file = _find_shim()
    spec = importlib.util.spec_from_file_location("_root_test_shim", shim_file)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load _root_test_shim")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shim_module = _load_shim()


def load_root_test(caller_globals: dict, relative_path: str) -> None:
    """Load a root-level test file into the caller's namespace."""
    _shim_module.load_root_test(caller_globals, relative_path)
