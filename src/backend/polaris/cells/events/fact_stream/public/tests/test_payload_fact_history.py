"""Small facts retain actual segmented hashes while resolving durable payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream import public as api
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

STREAM = "roles.kernel.turn_outcomes.v3"


def _bootstrap(workspace: Path) -> None:
    api.bootstrap_fact_stream_workspace(
        api.BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="private history test",
            streams=api.fact_stream_bootstrap_streams(),
        )
    )
    api.ensure_segmented_fact_ledger(
        api.EnsureSegmentedFactLedgerCommandV1(
            workspace=str(workspace), logical_stream=STREAM, maintenance_reason="new payload-fact namespace"
        )
    )


def _payload():
    return {"schema_version": "test.large_fact.v1", "text": "中文" * 5000}


def _prepare(workspace: Path):
    prepare = getattr(api, "store_fact_payload", None)
    assert callable(prepare), "FactStream durable payload preparation is missing"
    return prepare(
        api.StoreFactPayloadCommandV1(
            workspace=str(workspace),
            payload=_payload(),
            payload_schema="test.large_fact.v1",
            maintenance_reason="prepare a large canonical payload",
        )
    )


def _append(workspace: Path, ref, key: str, *, expected: int | None = None):
    return api.append_payload_fact_event(
        api.AppendPayloadFactEventCommandV1(
            workspace=str(workspace),
            logical_stream=STREAM,
            event_type="test.event",
            source="test.owner",
            payload_ref=ref,
            idempotency_key=key,
            expected_global_seq=expected,
        )
    )


def test_public_large_payload_is_replayed_with_real_small_record_hash(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    ref = _prepare(tmp_path)
    first = _append(tmp_path, ref, "test:first", expected=1)
    page = api.query_payload_fact_events(api.QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=STREAM))
    row = page.events[0]
    assert row["expanded_payload"] == _payload()
    assert row["payload"]["payload_ref"] == ref.to_record()
    assert "text" not in row["payload"]
    record = row["storage_record"]
    without_hash = {key: value for key, value in record.items() if key != "event_hash"}
    canonical = json.dumps(without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == row["event_hash"]
    assert len(json.dumps(record, ensure_ascii=False).encode("utf-8")) < 4096
    assert _append(tmp_path, ref, "test:first") == first
    assert (
        api.query_segmented_fact_ledger_head(
            api.QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=STREAM)
        ).total_count
        == 1
    )


@pytest.mark.parametrize("attack", ["missing", "corrupt", "wrong_expected_seq"])
def test_invalid_payload_or_cas_never_creates_a_fact(tmp_path: Path, attack: str) -> None:
    _bootstrap(tmp_path)
    ref = _prepare(tmp_path)
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    if attack == "missing":
        fs.remove(ref.logical_path)
    elif attack == "corrupt":
        fs.write_bytes(ref.logical_path, b"corrupted")
    with pytest.raises(api.FactStreamError):
        _append(tmp_path, ref, "test:invalid", expected=2 if attack == "wrong_expected_seq" else 1)
    assert (
        api.query_segmented_fact_ledger_head(
            api.QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=STREAM)
        ).total_count
        == 0
    )


def test_default_segments_exceed_old_lifetime_budget_and_restart(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    ref = _prepare(tmp_path)
    first = None
    for index in range(4097):
        event = _append(tmp_path, ref, f"history:{index}", expected=index + 1)
        if first is None:
            first = event
    head = api.query_segmented_fact_ledger_head(
        api.QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=STREAM)
    )
    assert head.total_count == head.global_seq == 4097
    assert head.segment_count == 9
    assert _append(tmp_path, ref, "history:0") == first
    page = api.query_payload_fact_events(
        api.QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream=STREAM, limit=511)
    )
    count = len(page.events)
    while page.continuation:
        page = api.query_payload_fact_events(
            api.QueryPayloadFactEventsV1(
                workspace=str(tmp_path), logical_stream=STREAM, limit=511, continuation=page.continuation
            )
        )
        count += len(page.events)
    assert count == 4097


def test_new_namespace_does_not_modify_old_v2_bytes(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    old = api.append_fact_event(
        api.AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="roles.kernel.turn_outcomes",
            event_type="legacy",
            source="roles.kernel.transaction",
            payload={"schema_version": "roles.kernel.turn_outcome_fact.v2", "value": "original"},
            idempotency_key="legacy",
            strict_integrity=True,
        )
    )
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    before = fs.read_bytes(old.storage_path)
    ref = _prepare(tmp_path)
    _append(tmp_path, ref, "new:first")
    assert fs.read_bytes(old.storage_path) == before
    assert (
        api.query_fact_events(
            api.QueryFactEventsV1(workspace=str(tmp_path), stream="roles.kernel.turn_outcomes", strict_integrity=True)
        ).events[0]["event_id"]
        == old.event_id
    )


def test_generic_segmented_append_cannot_bypass_payload_confirmation(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    with pytest.raises(api.FactStreamError):
        api.append_segmented_fact_event(
            api.AppendSegmentedFactEventCommandV1(
                workspace=str(tmp_path),
                logical_stream=STREAM,
                event_type="test.fake",
                source="test.owner",
                payload={"schema_version": "unconfirmed", "fake": True},
                idempotency_key="unconfirmed",
            )
        )
    assert (
        api.query_segmented_fact_ledger_head(
            api.QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=STREAM)
        ).total_count
        == 0
    )


def test_namespace_presence_is_readonly_observation(tmp_path: Path) -> None:
    api.bootstrap_fact_stream_workspace(
        api.BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="presence observation test",
            streams=api.fact_stream_bootstrap_streams(),
        )
    )
    query = api.QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream=STREAM)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert api.query_payload_fact_ledger_presence(query) is False
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    api.ensure_segmented_fact_ledger(
        api.EnsureSegmentedFactLedgerCommandV1(
            workspace=str(tmp_path), logical_stream=STREAM, maintenance_reason="explicit namespace preparation"
        )
    )
    assert api.query_payload_fact_ledger_presence(query) is True
