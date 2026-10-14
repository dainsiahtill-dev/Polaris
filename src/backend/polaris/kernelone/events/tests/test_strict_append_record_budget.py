"""Ordinary and guarded strict events use frame budgets, preserving stream truth."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import (
    AppendIfGuardedSnapshotCommandV1,
    EventSourcingError,
    GuardedFactEventV1,
    JsonlEventStore,
    ReadGuardedFactSnapshotCommandV1,
    append_if_guarded_snapshot,
    guarded,
    read_guarded_fact_snapshot,
)
from polaris.kernelone.events.tests.test_guarded_sourcing import _provision_store
from polaris.kernelone.fs import set_default_adapter

_FRAME_BUDGET = 4096
_STREAM = "test.strict_byte_budget"


@pytest.fixture(autouse=True)
def _local_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def _store(workspace: Path, *streams: str, frame_budget: int = _FRAME_BUDGET) -> JsonlEventStore:
    workspace.mkdir(parents=True, exist_ok=True)
    store = JsonlEventStore(str(workspace), strict_max_bytes=frame_budget)
    _provision_store(store, tuple(store.stream_logical_path(stream) for stream in streams))
    return store


def _path(store: JsonlEventStore, stream: str = _STREAM) -> Path:
    return Path(store.storage_identity.runtime_root) / "events" / f"{stream}.jsonl"


def _append(store: JsonlEventStore, index: int, *, stream: str = _STREAM, expected_seq: int | None = None):
    return store.append(
        stream=stream,
        event_type="committed",
        source="test.strict_frames",
        payload={"index": index, "unicode_source": "中文" * 500},
        strict_integrity=True,
        expected_seq=expected_seq,
        idempotency_key=f"{stream}-turn-{index}",
        durability="fsync",
    )


def _seed(workspace: Path, *streams: str) -> JsonlEventStore:
    store = _store(workspace, *streams, frame_budget=64 * 1024)
    for stream in streams:
        for index in range(3):
            _append(store, index, stream=stream, expected_seq=index + 1)
        assert _path(store, stream).stat().st_size > _FRAME_BUDGET
        assert all(len(frame) <= _FRAME_BUDGET for frame in _path(store, stream).read_bytes().splitlines(keepends=True))
    return store


def _limited_guarded_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        guarded, "JsonlEventStore", lambda workspace: JsonlEventStore(workspace, strict_max_bytes=_FRAME_BUDGET)
    )


def test_strict_appends_cross_page_budget_and_replay_after_restart(tmp_path: Path) -> None:
    store = _store(tmp_path / "workspace", _STREAM)
    events = [_append(store, index, expected_seq=index + 1) for index in range(3)]
    before = _path(store).read_bytes()
    assert len(before) > _FRAME_BUDGET
    assert all(len(frame) <= _FRAME_BUDGET for frame in before.splitlines(keepends=True))
    reopened = JsonlEventStore(store.workspace, strict_max_bytes=_FRAME_BUDGET)
    replay = _append(reopened, 0, expected_seq=1)
    assert replay.to_record() == events[0].to_record()
    assert _path(store).read_bytes() == before
    first = reopened.query(stream=_STREAM, strict_integrity=True, limit=10)
    assert first.total == 3 and len(first.events) == 1 and first.next_offset == 1
    assert reopened.query(stream=_STREAM, strict_integrity=True, offset=2).events[0].seq == 3
    assert reopened.current_seq(_STREAM, strict_integrity=True) == 3


def test_strict_cas_and_semantic_conflict_reject_after_total_budget_crossing(tmp_path: Path) -> None:
    _seed(tmp_path / "workspace", _STREAM)
    store = JsonlEventStore(str(tmp_path / "workspace"), strict_max_bytes=_FRAME_BUDGET)
    before = _path(store).read_bytes()
    with pytest.raises(EventSourcingError):
        _append(store, 3, expected_seq=3)
    with pytest.raises(EventSourcingError):
        _append(store, 0, expected_seq=9)
    with pytest.raises(EventSourcingError):
        store.append(
            stream=_STREAM,
            event_type="committed",
            source="test.strict_frames",
            payload={"different": True},
            idempotency_key=f"{_STREAM}-turn-0",
            strict_integrity=True,
        )
    assert _path(store).read_bytes() == before
    assert _append(store, 3, expected_seq=4).seq == 4


def test_guarded_snapshot_append_and_replay_cross_both_stream_budgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seed(tmp_path / "workspace", "target", "guard")
    _limited_guarded_store(monkeypatch)
    snapshot = read_guarded_fact_snapshot(
        ReadGuardedFactSnapshotCommandV1(workspace=store.workspace, target_stream="target", guard_stream="guard")
    )
    assert len(snapshot.target_facts) == len(snapshot.guard_facts) == 3
    guard_before = _path(store, "guard").read_bytes()
    command = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=snapshot.proof,
        event=GuardedFactEventV1(event_type="recorded", source="test.guarded", payload={"marker": "new"}),
        idempotency_key="guarded-new",
    )
    committed = append_if_guarded_snapshot(command)
    assert committed.appended_seq == 4
    assert _path(store, "guard").read_bytes() == guard_before
    _append(store, 3, stream="guard", expected_seq=4)
    target_before_replay = _path(store, "target").read_bytes()
    replay = append_if_guarded_snapshot(command)
    assert replay == committed
    assert _path(store, "target").read_bytes() == target_before_replay
    stale_new = AppendIfGuardedSnapshotCommandV1(
        snapshot_proof=snapshot.proof,
        event=GuardedFactEventV1(event_type="recorded", source="test.guarded", payload={"marker": "other"}),
        idempotency_key="stale-new",
    )
    with pytest.raises(EventSourcingError):
        append_if_guarded_snapshot(stale_new)
    assert _path(store, "target").read_bytes() == target_before_replay


@pytest.mark.parametrize("damage", ["middle", "digest", "torn_tail"])
def test_cross_budget_history_still_rejects_corruption_without_append(tmp_path: Path, damage: str) -> None:
    seeded = _seed(tmp_path / "workspace", _STREAM)
    path = _path(seeded)
    rows = path.read_bytes().splitlines(keepends=True)
    if damage == "middle":
        rows[1] = b"not-json\n"
    elif damage == "digest":
        row = json.loads(rows[1].decode("utf-8"))
        row["payload"]["unicode_source"] = "tampered"
        rows[1] = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    else:
        rows[-1] = rows[-1].rstrip(b"\n")
    path.write_bytes(b"".join(rows))
    corrupted = path.read_bytes()
    limited = JsonlEventStore(seeded.workspace, strict_max_bytes=_FRAME_BUDGET)
    for operation in (
        lambda: limited.query(stream=_STREAM, strict_integrity=True, limit=1),
        lambda: _append(limited, 3, expected_seq=4),
        lambda: _append(limited, 0, expected_seq=1),
    ):
        with pytest.raises(EventSourcingError) as rejected:
            operation()
        assert rejected.value.code == "strict_stream_corruption"
        assert rejected.value.details["strict_reason"] == (
            "torn_tail" if damage == "torn_tail" else "middle_corruption"
        )
        assert path.read_bytes() == corrupted


@pytest.mark.parametrize("guarded_append", [False, True])
def test_oversized_single_encoded_frame_is_rejected_without_consuming_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guarded_append: bool
) -> None:
    store = _store(tmp_path / "workspace", "target", "guard", _STREAM)
    oversized = {"unicode_source": "中文" * 1000}
    if guarded_append:
        _limited_guarded_store(monkeypatch)
        snapshot = read_guarded_fact_snapshot(
            ReadGuardedFactSnapshotCommandV1(workspace=store.workspace, target_stream="target", guard_stream="guard")
        )
        with pytest.raises(EventSourcingError):
            append_if_guarded_snapshot(
                AppendIfGuardedSnapshotCommandV1(
                    snapshot_proof=snapshot.proof,
                    event=GuardedFactEventV1(event_type="recorded", source="test.guarded", payload=oversized),
                    idempotency_key="oversized",
                )
            )
        assert not _path(store, "target").exists()
    else:
        with pytest.raises(EventSourcingError):
            store.append(
                stream=_STREAM,
                event_type="recorded",
                source="test.frame",
                payload=oversized,
                strict_integrity=True,
                expected_seq=1,
            )
        assert not _path(store).exists()
        assert _append(store, 0, expected_seq=1).seq == 1


def test_default_frame_budget_allows_history_larger_than_eight_mib_and_restart_replay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = JsonlEventStore(str(workspace))
    _provision_store(store, (store.stream_logical_path(_STREAM),))
    payload = {"unicode_source": "中文" * 180_000}
    first = None
    for index in range(9):
        event = store.append(
            stream=_STREAM,
            event_type="large-turn",
            source="test.default_budget",
            payload={**payload, "index": index},
            strict_integrity=True,
            expected_seq=index + 1,
            idempotency_key=f"large-{index}",
            durability="fsync",
        )
        first = first or event
    path = _path(store)
    historical_bytes = path.read_bytes()
    assert len(historical_bytes) > 8 * 1024 * 1024
    assert max(map(len, historical_bytes.splitlines(keepends=True))) < 8 * 1024 * 1024
    before_replay_hash = hashlib.sha256(historical_bytes).hexdigest()
    reopened = JsonlEventStore(store.workspace)
    replay = reopened.append(
        stream=_STREAM,
        event_type="large-turn",
        source="test.default_budget",
        payload={**payload, "index": 0},
        strict_integrity=True,
        expected_seq=1,
        idempotency_key="large-0",
        durability="fsync",
    )
    assert first is not None and replay.to_record() == first.to_record()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_replay_hash
    assert _append(reopened, 9, expected_seq=10).seq == 10
    assert path.read_bytes().startswith(historical_bytes)
