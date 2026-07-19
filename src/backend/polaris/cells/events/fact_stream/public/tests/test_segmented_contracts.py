"""Strict zero-trust segmented FactStream public contract tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendSegmentedFactEventCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    QuerySegmentedFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    SegmentedFactEventAppendedV1,
    SegmentedFactLedgerHeadV1,
    SegmentedFactLedgerReadyV1,
    SegmentedFactQueryResultV1,
)


class _NoopHead(SegmentedFactLedgerHeadV1):
    def __post_init__(self) -> None:
        pass


class _NoopReady(SegmentedFactLedgerReadyV1):
    def __post_init__(self) -> None:
        pass


class _NoopAppended(SegmentedFactEventAppendedV1):
    def __post_init__(self) -> None:
        pass


class _NoopQueryResult(SegmentedFactQueryResultV1):
    def __post_init__(self) -> None:
        pass


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


class _TupleSubclass(tuple):
    pass


class _DictSubclass(dict[str, object]):
    pass


def _head(**overrides: object) -> SegmentedFactLedgerHeadV1:
    values: dict[str, object] = {
        "workspace": "/tmp/workspace",
        "logical_stream": "factory.role_evidence_authority." + "a" * 64,
        "storage_prefix": "events/authority",
        "total_count": 0,
        "segment_count": 0,
        "global_seq": 0,
        "next_expected_global_seq": 1,
        "tail_segment_index": None,
        "tail_local_seq": 0,
        "head_hash": "0" * 64,
        "storage_bytes": 0,
    }
    values.update(overrides)
    return SegmentedFactLedgerHeadV1(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("head_hash", "x"),
        ("total_count", True),
        ("segment_count", True),
        ("global_seq", False),
        ("next_expected_global_seq", True),
        ("tail_segment_index", True),
        ("tail_local_seq", False),
        ("storage_bytes", False),
    ],
)
def test_segmented_head_rejects_invalid_hash_and_bool_counts(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _head(**{field_name: value})


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("workspace", _StrSubclass("/tmp/workspace")),
        ("head_hash", _StrSubclass("0" * 64)),
        ("total_count", _IntSubclass(0)),
        ("next_expected_global_seq", _IntSubclass(1)),
    ],
)
def test_segmented_head_rejects_scalar_subclasses(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _head(**{field_name: value})


def test_segmented_commands_reject_scalar_and_container_subclasses() -> None:
    logical_stream = "factory.role_evidence_authority." + "a" * 64
    with pytest.raises(ValueError, match="workspace"):
        EnsureSegmentedFactLedgerCommandV1(
            workspace=_StrSubclass("/tmp/workspace"),
            logical_stream=logical_stream,
            maintenance_reason="test",
        )
    with pytest.raises(ValueError, match="logical_stream"):
        QuerySegmentedFactLedgerHeadV1(
            workspace="/tmp/workspace",
            logical_stream=_StrSubclass(logical_stream),
        )
    with pytest.raises(ValueError, match="payload"):
        AppendSegmentedFactEventCommandV1(
            workspace="/tmp/workspace",
            logical_stream=logical_stream,
            event_type="test.event",
            source="test",
            payload=_DictSubclass({"ok": True}),
            idempotency_key="test:1",
            expected_global_seq=1,
        )
    with pytest.raises(ValueError, match="payload"):
        AppendSegmentedFactEventCommandV1(
            workspace="/tmp/workspace",
            logical_stream=logical_stream,
            event_type="test.event",
            source="test",
            payload={_StrSubclass("ok"): True},
            idempotency_key="test:1",
            expected_global_seq=1,
        )
    with pytest.raises(ValueError, match="expected_global_seq"):
        AppendSegmentedFactEventCommandV1(
            workspace="/tmp/workspace",
            logical_stream=logical_stream,
            event_type="test.event",
            source="test",
            payload={"ok": True},
            idempotency_key="test:1",
            expected_global_seq=_IntSubclass(1),
        )
    with pytest.raises(ValueError, match="limit"):
        QuerySegmentedFactEventsV1(
            workspace="/tmp/workspace",
            logical_stream=logical_stream,
            limit=_IntSubclass(100),
        )
    with pytest.raises(ValueError, match="continuation"):
        QuerySegmentedFactEventsV1(
            workspace="/tmp/workspace",
            logical_stream=logical_stream,
            continuation=_StrSubclass("authority-offset:1"),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"total_count": 1},
        {"segment_count": 1},
        {"tail_segment_index": 0},
        {"tail_local_seq": 1},
        {"storage_bytes": 1},
        {
            "total_count": 2,
            "global_seq": 2,
            "next_expected_global_seq": 3,
            "segment_count": 1,
            "tail_segment_index": None,
            "tail_local_seq": 2,
            "storage_bytes": 10,
        },
    ],
)
def test_segmented_head_rejects_inconsistent_count_tail_and_bytes(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _head(**overrides)


def test_segmented_ready_requires_identity_token_and_exact_head_identity() -> None:
    head = _head()
    with pytest.raises(ValueError, match="storage_identity_token"):
        SegmentedFactLedgerReadyV1(
            workspace=head.workspace,
            logical_stream=head.logical_stream,
            storage_prefix=head.storage_prefix,
            storage_identity_token="",
            retention="pinned_audit_no_delete",
            head=head,
        )
    with pytest.raises(ValueError, match="identity"):
        SegmentedFactLedgerReadyV1(
            workspace="/tmp/other",
            logical_stream=head.logical_stream,
            storage_prefix=head.storage_prefix,
            storage_identity_token="token",
            retention="pinned_audit_no_delete",
            head=head,
        )
    for field_name, value in (
        ("storage_identity_token", _StrSubclass("token")),
        ("retention", _StrSubclass("pinned_audit_no_delete")),
    ):
        values = {
            "workspace": head.workspace,
            "logical_stream": head.logical_stream,
            "storage_prefix": head.storage_prefix,
            "storage_identity_token": "token",
            "retention": "pinned_audit_no_delete",
            "head": head,
        }
        values[field_name] = value
        with pytest.raises(ValueError):
            SegmentedFactLedgerReadyV1(**values)  # type: ignore[arg-type]


def test_ready_and_query_reject_head_subclass_with_noop_validator() -> None:
    base = _head()
    malicious_head = _NoopHead(**vars(base))

    with pytest.raises(ValueError, match="head must be exact SegmentedFactLedgerHeadV1"):
        SegmentedFactLedgerReadyV1(
            workspace=base.workspace,
            logical_stream=base.logical_stream,
            storage_prefix=base.storage_prefix,
            storage_identity_token="token",
            retention="pinned_audit_no_delete",
            head=malicious_head,
        )
    with pytest.raises(ValueError, match="captured_head must be exact SegmentedFactLedgerHeadV1"):
        SegmentedFactQueryResultV1(
            workspace=base.workspace,
            logical_stream=base.logical_stream,
            events=(),
            captured_head=malicious_head,
        )


def test_segmented_append_ack_rejects_bool_sequence_and_invalid_hash() -> None:
    values = {
        "workspace": "/tmp/workspace",
        "logical_stream": "factory.role_evidence_authority." + "a" * 64,
        "event_id": "event-1",
        "global_seq": 1,
        "segment_index": 0,
        "local_seq": 1,
        "event_hash": "a" * 64,
        "appended_at": "2026-07-18T00:00:00+00:00",
    }
    for overrides in ({"global_seq": True}, {"segment_index": False}, {"event_hash": "x"}):
        with pytest.raises(ValueError):
            SegmentedFactEventAppendedV1(**(values | overrides))  # type: ignore[arg-type]

    for overrides in (
        {"event_id": _StrSubclass("event-1")},
        {"global_seq": _IntSubclass(1)},
        {"event_hash": _StrSubclass("a" * 64)},
    ):
        with pytest.raises(ValueError):
            SegmentedFactEventAppendedV1(**(values | overrides))  # type: ignore[arg-type]


def test_segmented_query_requires_exact_captured_head_identity_and_valid_event_hashes() -> None:
    head = _head(
        total_count=1,
        segment_count=1,
        global_seq=1,
        next_expected_global_seq=2,
        tail_segment_index=0,
        tail_local_seq=1,
        head_hash="a" * 64,
        storage_bytes=100,
    )
    event = {
        "event_id": "event-1",
        "logical_stream": head.logical_stream,
        "global_seq": 1,
        "segment_index": 0,
        "local_seq": 1,
        "event_type": "test.event",
        "source": "test",
        "payload": {"ok": True},
        "idempotency_key": "test:1",
        "occurred_at": "2026-07-18T00:00:00+00:00",
        "previous_event_hash": "0" * 64,
        "event_hash": "a" * 64,
    }
    result = SegmentedFactQueryResultV1(
        workspace=head.workspace,
        logical_stream=head.logical_stream,
        events=(event,),
        captured_head=head,
    )
    assert result.events == (event,)
    with pytest.raises(ValueError, match="event_hash"):
        replace(result, events=({**event, "event_hash": "x"},))
    with pytest.raises(ValueError, match="identity"):
        replace(result, workspace="/tmp/other")

    with pytest.raises(ValueError, match="exact tuple"):
        replace(result, events=_TupleSubclass((event,)))
    with pytest.raises(ValueError, match="exact dicts"):
        replace(result, events=(_DictSubclass(event),))
    forged_event = dict(event)
    event_id = forged_event.pop("event_id")
    forged_event[_StrSubclass("event_id")] = event_id
    with pytest.raises(ValueError, match="exact dicts"):
        replace(result, events=(forged_event,))
    with pytest.raises(ValueError, match="payload"):
        replace(result, events=({**event, "payload": _DictSubclass({"ok": True})},))
    with pytest.raises(ValueError, match="payload"):
        replace(result, events=({**event, "payload": {_StrSubclass("ok"): True}},))
    with pytest.raises(ValueError, match=r"event\.global_seq"):
        replace(result, events=({**event, "global_seq": _IntSubclass(1)},))
    with pytest.raises(ValueError, match=r"event\.event_id"):
        replace(result, events=({**event, "event_id": _StrSubclass("event-1")},))
    with pytest.raises(ValueError, match="continuation"):
        replace(result, continuation=_StrSubclass("authority-offset:1"))
