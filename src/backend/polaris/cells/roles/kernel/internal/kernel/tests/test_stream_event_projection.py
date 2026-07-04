from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import (
    record_missing_dispatch_lifecycle_receipt,
)
from polaris.cells.roles.profile.public.service import RoleTurnRequest


def test_lift_completion_audit_evidence_preserves_native_tool_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-tool-call-1",
        "tool_name": "write_file",
        "call_id": "call-1",
    }
    metadata: dict[str, object] = {}

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "required_tools": ["write_file"],
            "native_tool_calls_count": 9,
            "native_tool_call_envelopes": [envelope],
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert metadata["native_tool_call_envelopes"] == [envelope]

    lifecycle = record_missing_dispatch_lifecycle_receipt(
        role="director",
        request=RoleTurnRequest(workspace=".", message="implement", run_id="run-1", task_id="TASK-1"),
        kernel=SimpleNamespace(workspace="."),
        turn_id="turn-1",
        metadata=metadata,
        ledger=None,
        tool_results=[],
        batch_receipt=None,
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native-tool-call-1",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_lift_completion_audit_evidence_preserves_lifecycle_envelope_refs() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-tool-call-ref-1",
        "tool_name": "write_file",
        "call_id": "call-ref-1",
    }
    metadata: dict[str, object] = {}

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "required_tools": ["write_file"],
            "native_tool_calls_count": 9,
            "native_tool_call_envelope_refs": [envelope],
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert metadata["native_tool_call_envelope_refs"] == [envelope]

    lifecycle = record_missing_dispatch_lifecycle_receipt(
        role="director",
        request=RoleTurnRequest(workspace=".", message="implement", run_id="run-1", task_id="TASK-1"),
        kernel=SimpleNamespace(workspace="."),
        turn_id="turn-1",
        metadata=metadata,
        ledger=None,
        tool_results=[],
        batch_receipt=None,
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native-tool-call-ref-1",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_lift_completion_audit_evidence_preserves_canonical_lifecycle_receipt() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-tool-call-lifecycle-1",
        "tool_name": "write_file",
        "call_id": "call-lifecycle-1",
    }
    metadata: dict[str, object] = {}

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "required_tools": ["write_file"],
            "tool_call_lifecycle": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [envelope],
            },
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["tool_call_lifecycle"] == {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [envelope],
    }
    canonical_lifecycle = metadata["tool_call_lifecycle_receipt"]
    assert canonical_lifecycle["schema_version"] == "tool_call_lifecycle_receipt.v1"
    assert canonical_lifecycle["native_tool_calls_count"] == 1
    assert canonical_lifecycle["decoded_tool_calls_count"] == 1
    assert canonical_lifecycle["dispatched_tool_calls_count"] == 0
    assert canonical_lifecycle["dispatch_status"] == "dropped"
    assert canonical_lifecycle["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert canonical_lifecycle["native_tool_call_envelope_refs"] == [envelope]
    assert canonical_lifecycle["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native-tool-call-lifecycle-1",
            "reason": "tool_dispatch_dropped",
        }
    ]

    lifecycle = record_missing_dispatch_lifecycle_receipt(
        role="director",
        request=RoleTurnRequest(workspace=".", message="implement", run_id="run-1", task_id="TASK-1"),
        kernel=SimpleNamespace(workspace="."),
        turn_id="turn-1",
        metadata=metadata,
        ledger=None,
        tool_results=[],
        batch_receipt=None,
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native-tool-call-lifecycle-1",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_lift_completion_audit_evidence_treats_zero_lifecycle_as_authoritative() -> None:
    metadata: dict[str, object] = {}

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "native_tool_calls_count": 9,
            "native_tool_call_names": ["stale_tool"],
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 0,
                "decoded_tool_calls_count": 0,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "dispatched",
            },
        },
    )

    assert metadata["native_tool_calls_count"] == 0
    assert metadata["native_tool_call_names"] == []
