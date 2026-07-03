"""Architecture fence for PM Chief Engineer CLI time helper ownership."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CHIEF_ENGINEER_CLI = BACKEND_ROOT / "polaris" / "delivery" / "cli" / "pm" / "chief_engineer.py"


def test_pm_chief_engineer_cli_uses_canonical_time_helper_directly() -> None:
    """Chief Engineer planning code must not hide time dependencies behind aliases."""
    source = CHIEF_ENGINEER_CLI.read_text(encoding="utf-8")
    assert "_utc_now_iso =" not in source
    assert "_utc_now_iso()" not in source
    assert "from polaris.kernelone.utils.time_utils import utc_now_str" in source
