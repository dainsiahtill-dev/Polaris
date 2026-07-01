"""Architecture fence for retired accel storage receipt-store re-exports."""

from __future__ import annotations

from pathlib import Path

import polaris.infrastructure.accel.storage as accel_storage

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ACCEL_STORAGE_INIT = BACKEND_ROOT / "polaris" / "infrastructure" / "accel" / "storage" / "__init__.py"


def test_session_receipt_store_reexport_is_retired() -> None:
    """Receipt stores are owned by infrastructure.db.repositories, not accel.storage."""
    assert not hasattr(accel_storage, "SessionReceiptStore")
    assert not hasattr(accel_storage, "SessionReceiptError")
    assert "SessionReceiptStore" not in accel_storage.__all__
    assert "SessionReceiptError" not in accel_storage.__all__


def test_accel_storage_source_does_not_reintroduce_receipt_reexport() -> None:
    """Source-level fence blocks the old package-root receipt-store bridge."""
    source = ACCEL_STORAGE_INIT.read_text(encoding="utf-8")
    assert "accel_session_receipt_store" not in source
    assert "SessionReceiptStore" not in source
    assert "SessionReceiptError" not in source
