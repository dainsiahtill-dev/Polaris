"""Architecture fence for retired one-shot config migration scripts."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_refactor_config_archive_script_is_removed() -> None:
    """The old config shim migration script must not be restored."""
    retired_script = BACKEND_ROOT / "scripts" / "archive" / "refactor_config.py"
    assert not retired_script.exists()
