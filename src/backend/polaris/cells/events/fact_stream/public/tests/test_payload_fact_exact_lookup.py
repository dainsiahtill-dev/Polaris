"""Exact payload lookup expands only its own authenticated content reference."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream import public as api
from polaris.cells.events.fact_stream.public.tests.test_payload_fact_history import STREAM, _append, _bootstrap
from polaris.kernelone.events.sourcing.payload_store import DurablePayloadStore
from polaris.kernelone.events.sourcing.segmented_file_store import SegmentedJsonlEventStore
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def _payload(root: Path, label: str):
    return api.store_fact_payload(
        api.StoreFactPayloadCommandV1(
            workspace=str(root),
            payload={"schema_version": "test.large_fact.v1", "text": label * 5000},
            payload_schema="test.large_fact.v1",
            maintenance_reason="private exact lookup",
        )
    )


def _query(root: Path, key: str):
    return api.query_payload_fact_by_idempotency_key(api.QueryPayloadFactByIdempotencyKeyV1(str(root), STREAM, key))


def test_lookup_expands_only_selected_blob_and_retains_actual_raw_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bootstrap(tmp_path)
    ref = _payload(tmp_path, "中文")
    other = _payload(tmp_path, "unrelated")
    first = _append(tmp_path, ref, "selected", expected=1)
    _append(tmp_path, other, "other", expected=2)
    original = DurablePayloadStore.read
    reads = []

    def read(store: Any, reference: Any):
        reads.append(reference.sha256)
        assert reference.sha256 == ref.sha256, "lookup must not expand history blobs"
        return original(store, reference)

    monkeypatch.setattr(DurablePayloadStore, "read", read)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = _query(tmp_path, "selected")
    assert len(result.events) == 1 and result.captured_head.global_seq == 2
    row = result.events[0]
    assert row["event_id"] == first.event_id and row["global_seq"] == 1
    raw = row["storage_record"]
    data = {k: v for k, v in raw.items() if k != "event_hash"}
    assert (
        hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        == raw["event_hash"]
    )
    assert row["expanded_payload"]["text"] == "中文" * 5000
    assert reads == [ref.sha256, ref.sha256]
    assert _query(tmp_path, "absent").events == ()
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("attack", ["missing_blob", "corrupt_blob", "foreign_blob"])
def test_invalid_blob_is_never_missing_or_a_valid_fact(tmp_path: Path, attack: str) -> None:
    _bootstrap(tmp_path)
    ref = _payload(tmp_path, "actual")
    if attack == "foreign_blob":
        other = tmp_path / "other-workspace"
        _bootstrap(other)
        ref = _payload(other, "foreign")
        # Technical fixture writes a real small record with a foreign locator;
        # the public payload boundary must refuse that content reference.
        SegmentedJsonlEventStore(str(tmp_path), logical_stream=STREAM).append(
            event_type="test.event",
            source="technical.fixture",
            payload={"schema_version": "kernelone.payload_fact.v1", "payload_ref": ref.to_record()},
            idempotency_key="selected",
        )
    else:
        _append(tmp_path, ref, "selected", expected=1)
        fs = KernelFileSystem(str(tmp_path), get_default_adapter())
        if attack == "missing_blob":
            fs.remove(ref.logical_path)
        else:
            fs.write_bytes(ref.logical_path, b"corrupt")
    with pytest.raises(api.FactStreamError):
        _query(tmp_path, "selected")


def test_real_append_between_expansion_and_recheck_invalidates_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bootstrap(tmp_path)
    ref = _payload(tmp_path, "actual")
    _append(tmp_path, ref, "selected", expected=1)
    original = DurablePayloadStore.read
    changed = False

    def read(store: Any, reference: Any):
        nonlocal changed
        payload = original(store, reference)
        if not changed:
            changed = True
            _append(tmp_path, ref, "concurrent", expected=2)
        return payload

    monkeypatch.setattr(DurablePayloadStore, "read", read)
    with pytest.raises(api.FactStreamError, match="changed"):
        _query(tmp_path, "selected")
