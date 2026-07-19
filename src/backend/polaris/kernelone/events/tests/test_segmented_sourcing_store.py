from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.events.sourcing.segmented_file_store import (
    SegmentedEventStoreError,
    SegmentedJsonlEventStore,
)


def _enroll_control_path(workspace: Path, store: SegmentedJsonlEventStore) -> None:
    from polaris.kernelone.fs import LockedRegularFileSetV1
    from polaris.kernelone.fs.locked_regular_file import default_platform_lock_root

    identity = store.storage_identity
    LockedRegularFileSetV1.provision_authority(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
    )
    LockedRegularFileSetV1.enroll_stream_lock_keys(
        platform_lock_root=default_platform_lock_root(),
        storage_identity_token=identity.token,
        runtime_root=identity.runtime_root,
        logical_paths=(store.control_logical_path,),
    )


def test_segmented_store_rolls_with_continuous_global_sequence(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()

    appended = [
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-{index}"},
            idempotency_key=f"req-{index}:start",
            durability="fsync",
        )
        for index in range(5)
    ]

    assert [item.global_seq for item in appended] == [1, 2, 3, 4, 5]
    head = store.head(strict_integrity=True)
    assert head.total_count == 5
    assert head.segment_count == 3
    assert head.global_seq == 5
    assert head.tail_segment_index == 2

    page_one = store.query(limit=2, strict_integrity=True)
    page_two = store.query(limit=2, continuation=page_one.continuation, strict_integrity=True)
    page_three = store.query(limit=2, continuation=page_two.continuation, strict_integrity=True)
    assert [item.global_seq for item in (*page_one.events, *page_two.events, *page_three.events)] == [1, 2, 3, 4, 5]
    assert page_three.continuation is None


def test_segmented_store_idempotency_is_logical_stream_wide(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=1)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    original = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-1"},
        idempotency_key="req-1:start",
        durability="fsync",
    )
    store.append(
        event_type="provider_attempt.terminal",
        source="roles.kernel",
        payload={"provider_request_id": "req-1", "status": "ok"},
        idempotency_key="req-1:terminal",
        durability="fsync",
    )

    replay = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-1"},
        idempotency_key="req-1:start",
        durability="fsync",
    )
    assert replay == original
    assert store.head(strict_integrity=True).total_count == 2

    with pytest.raises(SegmentedEventStoreError, match="idempotency conflict"):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": "req-drift"},
            idempotency_key="req-1:start",
            durability="fsync",
        )


def test_segmented_store_replay_only_uses_locator_and_never_appends_when_missing(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=1)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    original = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-1"},
        idempotency_key="req-1:start",
        durability="fsync",
    )
    replay = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-1"},
        idempotency_key="req-1:start",
        durability="fsync",
        require_idempotency_replay=True,
    )
    assert replay == original

    with pytest.raises(SegmentedEventStoreError) as missing:
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": "req-2"},
            idempotency_key="req-2:start",
            durability="fsync",
            require_idempotency_replay=True,
        )
    assert missing.value.code == "idempotency_replay_missing"
    assert store.head(strict_integrity=True).total_count == 1


def test_segment_gap_fails_strict_read(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=1)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(3):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-{index}"},
            idempotency_key=f"req-{index}:start",
            durability="fsync",
        )

    segment_one = Path(store.segment_absolute_path(1))
    segment_one.unlink()
    with pytest.raises(SegmentedEventStoreError, match="segment gap"):
        store.head(strict_integrity=True)


def test_restart_rebuilds_missing_cursor_and_locator_from_segments(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    original = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-rebuild"},
        idempotency_key="req-rebuild:start",
        durability="fsync",
    )
    store.append(
        event_type="provider_attempt.terminal",
        source="roles.kernel",
        payload={"provider_request_id": "req-rebuild", "status": "ok"},
        idempotency_key="req-rebuild:terminal",
        durability="fsync",
    )
    Path(store.cursor_absolute_path).unlink()
    Path(store.locator_absolute_path("req-rebuild:start")).unlink()

    restarted = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    restarted.ensure()
    replay = restarted.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-rebuild"},
        idempotency_key="req-rebuild:start",
        durability="fsync",
    )
    assert replay == original
    assert restarted.head(strict_integrity=True).total_count == 2
    assert Path(restarted.locator_absolute_path("req-rebuild:start")).is_file()


def test_cursor_drift_is_rebuilt_once_from_authoritative_segments(tmp_path: Path) -> None:
    import json

    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-drift"},
        idempotency_key="req-drift:start",
        durability="fsync",
    )
    cursor_path = Path(store.cursor_absolute_path)
    cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    cursor["total_count"] = 99
    cursor_path.write_text(json.dumps(cursor), encoding="utf-8")

    restarted = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    assert restarted.ensure().total_count == 1

    cursor = json.loads(cursor_path.read_text(encoding="utf-8"))
    cursor["total_count"] = 77
    cursor_path.write_text(json.dumps(cursor), encoding="utf-8")
    appended = restarted.append(
        event_type="provider_attempt.terminal",
        source="roles.kernel",
        payload={"provider_request_id": "req-drift", "status": "ok"},
        idempotency_key="req-drift:terminal",
        durability="fsync",
    )
    assert appended.global_seq == 2


def test_missing_locator_with_valid_cursor_rebuilds_before_idempotent_replay(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    original = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-locator-only"},
        idempotency_key="req-locator-only:start",
        durability="fsync",
    )
    Path(store.locator_absolute_path("req-locator-only:start")).unlink()

    restarted = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    replay = restarted.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-locator-only"},
        idempotency_key="req-locator-only:start",
        durability="fsync",
    )
    assert replay == original
    assert restarted.head(strict_integrity=True).total_count == 1


def test_ambiguous_post_fsync_append_reconciles_same_idempotency_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    original_append = store._kernel_fs.append_jsonl
    raised = False

    def _append_then_raise(*args: object, **kwargs: object) -> object:
        nonlocal raised
        result = original_append(*args, **kwargs)
        if not raised:
            raised = True
            raise OSError("simulated ambiguous fsync acknowledgment")
        return result

    monkeypatch.setattr(store._kernel_fs, "append_jsonl", _append_then_raise)
    appended = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-ambiguous"},
        idempotency_key="req-ambiguous:start",
        durability="fsync",
    )
    assert appended.global_seq == 1
    assert store.head(strict_integrity=True).total_count == 1


def test_continuation_is_bound_to_captured_head_and_excludes_concurrent_append(tmp_path: Path) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(3):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-snapshot-{index}"},
            idempotency_key=f"req-snapshot-{index}:start",
            durability="fsync",
        )

    first = store.query(limit=1, strict_integrity=True)
    store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "req-post-cut"},
        idempotency_key="req-post-cut:start",
        durability="fsync",
    )
    second = store.query(limit=10, continuation=first.continuation, strict_integrity=True)
    assert [item.global_seq for item in (*first.events, *second.events)] == [1, 2, 3]
    assert second.continuation is None
    assert second.captured_head == first.captured_head
    assert store.head(strict_integrity=True).total_count == 4


def test_continuation_does_not_rescan_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(6):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-page-{index}"},
            idempotency_key=f"req-page-{index}:start",
            durability="fsync",
        )
    first = store.query(limit=2, strict_integrity=True)
    monkeypatch.setattr(
        store,
        "_full_scan_and_rebuild_locked",
        lambda: (_ for _ in ()).throw(AssertionError("prefix rescan")),
    )
    second = store.query(limit=2, continuation=first.continuation, strict_integrity=True)
    assert [item.global_seq for item in second.events] == [3, 4]


def test_continuation_is_self_authenticating_and_revalidates_physical_captured_head(tmp_path: Path) -> None:
    import base64
    import json

    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(3):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-token-{index}"},
            idempotency_key=f"req-token-{index}:start",
            durability="fsync",
        )
    first = store.query(limit=1, strict_integrity=True)
    assert first.continuation is not None
    padding = "=" * (-len(first.continuation) % 4)
    state = json.loads(base64.urlsafe_b64decode(first.continuation + padding).decode("utf-8"))
    state["head_seq"] = 2
    tampered = (
        base64.urlsafe_b64encode(json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    with pytest.raises(SegmentedEventStoreError, match="continuation"):
        store.query(limit=2, continuation=tampered, strict_integrity=True)

    segment = Path(store.segment_absolute_path(1))
    lines = segment.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["payload"]["provider_request_id"] = "tampered-after-cut"
    lines[0] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(SegmentedEventStoreError):
        store.query(limit=2, continuation=first.continuation, strict_integrity=True)


def test_tail_fast_validation_checks_previous_segment_link_and_full_seal(tmp_path: Path) -> None:
    import json

    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=1)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(2):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-anchor-{index}"},
            idempotency_key=f"req-anchor-{index}:start",
            durability="fsync",
        )
    tail = Path(store.segment_absolute_path(1))
    lines = tail.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["previous_event_hash"] = "f" * 64
    event["event_hash"] = store._hash_mapping({key: value for key, value in event.items() if key != "event_hash"})
    seal = json.loads(lines[1])
    seal["head_hash"] = event["event_hash"]
    seal["seal_hash"] = store._hash_mapping({key: value for key, value in seal.items() if key != "seal_hash"})
    tail.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in (event, seal)) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SegmentedEventStoreError):
        store.head(strict_integrity=False)


def test_strict_rebuild_rejects_duplicate_idempotency_keys_even_when_semantics_match(tmp_path: Path) -> None:
    import json

    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=3)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    for index in range(2):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": "same-semantics"},
            idempotency_key=f"duplicate-{index}:start",
            durability="fsync",
        )
    segment = Path(store.segment_absolute_path(0))
    lines = segment.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["idempotency_key"] = "duplicate-0:start"
    second["event_hash"] = store._hash_mapping({key: value for key, value in second.items() if key != "event_hash"})
    lines[1] = json.dumps(second, ensure_ascii=False, separators=(",", ":"))
    segment.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(SegmentedEventStoreError, match="duplicate idempotency"):
        store.ensure()


def test_full_rebuild_removes_stale_unused_locator_manifest(tmp_path: Path) -> None:
    import hashlib
    import json

    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=2)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "existing"},
        idempotency_key="existing:start",
        durability="fsync",
    )
    existing_shard = hashlib.sha256(b"existing:start").hexdigest()[:2]
    stale_shard = next(f"{value:02x}" for value in range(256) if f"{value:02x}" != existing_shard)
    stale_path = store._kernel_fs.resolve_path(store._locator_manifest_logical_path(stale_shard))
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    stale_path.write_text(
        json.dumps(
            {
                "schema": "kernelone.segmented_locator_manifest.v1",
                "logical_stream": store.logical_stream,
                "shard": stale_shard,
                "digests": [stale_shard + "0" * 62],
            }
        ),
        encoding="utf-8",
    )
    candidate = next(
        f"new-key-{index}"
        for index in range(10000)
        if hashlib.sha256(f"new-key-{index}".encode()).hexdigest()[:2] == stale_shard
    )

    store.ensure()
    appended = store.append(
        event_type="provider_attempt.started",
        source="roles.kernel",
        payload={"provider_request_id": "new-after-rebuild"},
        idempotency_key=candidate,
        durability="fsync",
    )
    assert appended.global_seq == 2


def test_more_than_4096_healthy_appends_never_full_scan_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SegmentedJsonlEventStore(str(tmp_path), segment_max_events=511)
    _enroll_control_path(tmp_path, store)
    store.ensure()
    monkeypatch.setattr(
        store,
        "_full_scan_and_rebuild_locked",
        lambda: (_ for _ in ()).throw(AssertionError("healthy append rescanned prefix")),
    )
    for index in range(4097):
        store.append(
            event_type="provider_attempt.started",
            source="roles.kernel",
            payload={"provider_request_id": f"req-bounded-{index}"},
            idempotency_key=f"req-bounded-{index}:start",
            durability="buffered",
        )
    assert store.head(strict_integrity=False).total_count == 4097
