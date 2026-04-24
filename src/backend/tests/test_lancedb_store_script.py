from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "lancedb_store.py"
    spec = importlib.util.spec_from_file_location("lancedb_store", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load lancedb_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script_module()


def test_normalize_db_dir_handles_drive_root() -> None:
    result = SCRIPT.normalize_db_dir("X:")
    expected_suffix = os.path.join(".polaris", "lancedb")
    assert result.lower().endswith(expected_suffix.lower())


def test_normalize_db_dir_windows_extended_prefix_for_drive_paths() -> None:
    result = SCRIPT.normalize_db_dir(r"X:\.polaris\cache\abc\runtime\memory")
    if os.name == "nt":
        assert result.startswith("\\\\?\\X:\\")
    else:
        assert result == os.path.abspath(r"X:\.polaris\cache\abc\runtime\memory")
