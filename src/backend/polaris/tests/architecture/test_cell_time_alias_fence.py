"""Architecture fence for retired Cell-local time helper aliases."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CELL_TIME_HELPER_FILES = (
    BACKEND_ROOT / "polaris" / "cells" / "context" / "catalog" / "service.py",
    BACKEND_ROOT / "polaris" / "cells" / "policy" / "protocol" / "public" / "contracts.py",
)


def test_cell_time_helpers_call_canonical_functions_directly() -> None:
    """Cell-local code should not hide canonical time helpers behind aliases."""
    for path in CELL_TIME_HELPER_FILES:
        source = path.read_text(encoding="utf-8")
        assert "_utc_now_iso =" not in source
        assert "_utc_now_iso()" not in source


def test_cell_time_helper_imports_remain_explicit() -> None:
    """Canonical time helper imports are intentionally visible at call sites."""
    context_catalog_source = CELL_TIME_HELPER_FILES[0].read_text(encoding="utf-8")
    policy_protocol_source = CELL_TIME_HELPER_FILES[1].read_text(encoding="utf-8")

    assert "from polaris.kernelone.utils.time_utils import utc_now_str" in context_catalog_source
    assert "from polaris.kernelone.utils.time_utils import utc_now_iso" in policy_protocol_source
