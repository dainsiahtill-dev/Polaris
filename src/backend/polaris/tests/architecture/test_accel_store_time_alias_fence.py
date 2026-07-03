"""Architecture fence for retired accel repository time aliases."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
ACCEL_REPOSITORY_FILES = (
    BACKEND_ROOT / "polaris" / "infrastructure" / "db" / "repositories" / "accel_semantic_cache_store.py",
    BACKEND_ROOT / "polaris" / "infrastructure" / "db" / "repositories" / "accel_session_receipt_store.py",
)


def test_accel_repositories_use_canonical_time_helpers_directly() -> None:
    """Repository code should not hide KernelOne time dependencies behind aliases."""
    for path in ACCEL_REPOSITORY_FILES:
        source = path.read_text(encoding="utf-8")
        assert "_utc_now =" not in source, str(path)
        assert "_utc_now()" not in source, str(path)
