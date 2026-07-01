"""Architecture fence for retired audit-diagnosis time helper aliases."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
TOOLKIT_FILES = (
    BACKEND_ROOT / "polaris" / "cells" / "audit" / "diagnosis" / "internal" / "toolkit" / "service.py",
    BACKEND_ROOT / "polaris" / "cells" / "audit" / "diagnosis" / "internal" / "toolkit" / "hops.py",
)


def test_audit_diagnosis_toolkit_uses_canonical_utc_now_iso() -> None:
    """Toolkit code must call the canonical time helper directly."""
    for path in TOOLKIT_FILES:
        source = path.read_text(encoding="utf-8")
        assert "from polaris.kernelone.utils.time_utils import utc_now_iso" in source
        assert "generated_at" in source


def test_audit_diagnosis_toolkit_has_no_private_time_compat_alias() -> None:
    """Private compatibility aliases must not return to the toolkit."""
    for path in TOOLKIT_FILES:
        source = path.read_text(encoding="utf-8")
        assert "_utc_now_iso = utc_now_iso" not in source
        assert "_utc_now_iso()" not in source
