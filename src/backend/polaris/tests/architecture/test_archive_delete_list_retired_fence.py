"""Architecture fence for retired delete-list archive scripts."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RETIRED_ARCHIVE_SCRIPTS = (
    "add_session_tool.py",
    "audit_markers.py",
    "find_l3_case.py",
    "parse_logs.py",
)


def test_delete_list_archive_scripts_are_removed() -> None:
    """One-shot local archive scripts marked Delete in the roadmap must stay removed."""
    archive_dir = BACKEND_ROOT / "scripts" / "archive"
    for script_name in RETIRED_ARCHIVE_SCRIPTS:
        assert not (archive_dir / script_name).exists(), script_name
