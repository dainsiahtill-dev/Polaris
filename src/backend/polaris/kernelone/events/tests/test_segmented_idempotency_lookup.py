"""Exact readonly lookup reuses the existing declared idempotency index."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from polaris.kernelone.events.sourcing.segmented_file_store import SegmentedEventStoreError, SegmentedJsonlEventStore
from polaris.kernelone.events.tests.test_segmented_sourcing_store import _enroll_control_path


def _store(root: Path):
    store = SegmentedJsonlEventStore(str(root), segment_max_events=2)
    _enroll_control_path(root, store)
    store.ensure()
    rows = tuple(
        store.append(
            event_type="lookup.test", source="technical.fixture", payload={"value": i}, idempotency_key=f"键:{i}"
        )
        for i in range(3)
    )
    return store, rows


def _files(root: Path):
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_exact_lookup_uses_real_locator_and_preserves_files(tmp_path: Path) -> None:
    store, rows = _store(tmp_path)
    before = _files(tmp_path)
    result = store.query_by_idempotency_key("键:0")
    assert result.events == (rows[0],)
    assert result.captured_head.global_seq == result.total_count == 3
    assert result.continuation is None
    assert _files(tmp_path) == before
    missing = store.query_by_idempotency_key("missing")
    assert missing.events == () and missing.captured_head == result.captured_head
    assert _files(tmp_path) == before


@pytest.mark.parametrize("attack", ["cursor", "manifest", "locator", "segment", "symlink"])
def test_damaged_exact_lookup_never_repairs_or_returns_missing(tmp_path: Path, attack: str) -> None:
    store, _ = _store(tmp_path)
    fs = store._kernel_fs
    key = "键:0"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if attack == "cursor":
        fs.write_text(store._cursor_path, "{}", encoding="utf-8")
    elif attack == "manifest":
        fs.write_text(store._locator_manifest_logical_path(digest[:2]), "{}", encoding="utf-8")
    elif attack == "locator":
        fs.write_text(store.locator_logical_path("键:0"), "{}", encoding="utf-8")
    elif attack == "segment":
        fs.write_bytes(store.segment_logical_path(0), b"corrupted\n")
    else:
        locator = fs.resolve_path(store.locator_logical_path("键:0"))
        content = locator.read_text(encoding="utf-8")
        target = tmp_path / "outside-locator.json"
        target.write_text(content, encoding="utf-8")
        locator.unlink()
        locator.symlink_to(target)
    before = _files(tmp_path)
    with pytest.raises((SegmentedEventStoreError, OSError, ValueError)):
        store.query_by_idempotency_key("键:0")
    assert _files(tmp_path) == before
