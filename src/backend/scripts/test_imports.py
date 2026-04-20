"""Smoke checks for critical import surfaces.

This module validates migration-safe import paths that are used by bootstrap
and runtime setup code.
"""

from __future__ import annotations

import tempfile

from polaris.cells.storage.layout.internal.settings_utils import (
    get_polaris_root,
    get_settings_path,
)
from polaris.kernelone.llm.runtime_config import RuntimeConfigManager
from polaris.kernelone.storage.io_paths import build_cache_root
from scripts.lancedb_store import normalize_db_dir


def test_import_surfaces_are_available() -> None:
    """Ensure key modules import and resolve paths on the current branch."""
    assert get_polaris_root()
    assert get_settings_path()

    runtime_config = RuntimeConfigManager()
    assert runtime_config._get_config_path()

    with tempfile.TemporaryDirectory(prefix="polaris_test_") as workspace:
        cache_root = build_cache_root("", workspace)
        assert cache_root

        lancedb_dir = normalize_db_dir("", workspace)
        assert lancedb_dir
