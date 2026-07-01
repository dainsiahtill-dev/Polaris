"""Architecture fence for retired Cell-private ``_utc_now`` aliases."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CELL_FILES = (
    BACKEND_ROOT / "polaris" / "cells" / "delivery" / "cli" / "public" / "service.py",
    BACKEND_ROOT / "polaris" / "cells" / "roles" / "profile" / "internal" / "schema.py",
)


def test_cell_private_utc_now_aliases_are_retired() -> None:
    """Cell-local time helper aliases should not hide canonical dependencies."""
    for path in CELL_FILES:
        source = path.read_text(encoding="utf-8")
        assert "_utc_now =" not in source
        assert "_utc_now()" not in source


def test_cell_time_helper_dependencies_are_visible() -> None:
    """Direct imports make the time source explicit at each call site."""
    delivery_cli_source = CELL_FILES[0].read_text(encoding="utf-8")
    roles_profile_source = CELL_FILES[1].read_text(encoding="utf-8")

    assert "from polaris.kernelone.utils.time_utils import utc_now_iso_compact" in delivery_cli_source
    assert "from polaris.kernelone.utils.time_utils import utc_now" in roles_profile_source
