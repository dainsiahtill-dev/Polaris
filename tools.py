# This file is kept for backward compatibility.
# Please use infrastructure.tools.main directly instead.

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_TOOLS_DIR = _PROJECT_ROOT / "infrastructure" / "tools"
_TOOLS_PACKAGE = "_polaris_repo_tools"


def _load_tools_module(name: str):
    """Load root infrastructure/tools modules without colliding with backend.infrastructure."""
    if not _TOOLS_DIR.is_dir():
        raise ModuleNotFoundError(f"Missing tools directory: {_TOOLS_DIR}")

    if _TOOLS_PACKAGE not in sys.modules:
        package = types.ModuleType(_TOOLS_PACKAGE)
        package.__path__ = [str(_TOOLS_DIR)]  # type: ignore[attr-defined]
        sys.modules[_TOOLS_PACKAGE] = package

    return importlib.import_module(f"{_TOOLS_PACKAGE}.{name}")


_files = _load_tools_module("files")
_search = _load_tools_module("search")
_treesitter = _load_tools_module("treesitter")
_utils = _load_tools_module("utils")

repo_read_head = _files.repo_read_head
repo_read_slice = _files.repo_read_slice
repo_read_tail = _files.repo_read_tail
repo_rg = _search.repo_rg
_ts_apply_replacement = _treesitter._ts_apply_replacement
_ensure_within_root = _utils.ensure_within_root

__all__ = [
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_rg",
    "_ensure_within_root",
    "_ts_apply_replacement",
]
