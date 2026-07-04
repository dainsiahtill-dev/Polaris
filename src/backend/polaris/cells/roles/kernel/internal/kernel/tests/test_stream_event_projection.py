from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import (
    record_missing_dispatch_lifecycle_receipt,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
from polaris.cells.roles.profile.public.service import RoleTurnRequest


class _Publisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish_stream_event(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))


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


def test_lift_completion_audit_evidence_preserves_failure_evidence() -> None:
    metadata: dict[str, object] = {}
    failure_evidence = [
        {
            "schema_version": "polaris.failure_evidence.v1",
            "source": "tool_lifecycle",
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "responsible_layer": "platform",
            "evidence_refs": ["tool_lifecycle:turn-1"],
        }
    ]

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "failure_evidence": failure_evidence,
            "failure_evidence_summary": {
                "count": 1,
                "latest_failure_class": "MISSING_EFFECT_RECEIPT",
            },
        },
    )

    assert metadata["failure_evidence"] == failure_evidence
    assert metadata["failure_evidence_summary"] == {
        "count": 1,
        "latest_failure_class": "MISSING_EFFECT_RECEIPT",
    }


def test_lift_completion_audit_evidence_derives_failure_evidence_from_lifecycle() -> None:
    metadata: dict[str, object] = {}

    projection._lift_completion_audit_evidence(
        metadata,
        {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "provider_response_hash": "provider-hash-1",
                "native_tool_call_envelope_refs": [
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                ],
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "dropped",
            },
        },
    )

    assert metadata["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert metadata["failure_evidence"][0]["responsible_layer"] == "execution_control_plane"
    assert "provider_response:provider-hash-1" in metadata["failure_evidence"][0]["evidence_refs"]
    assert metadata["failure_evidence_summary"] == {
        "count": 1,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


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


def test_stream_completion_fails_closed_on_required_write_without_dispatch(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion._append_tool_call_lifecycle_event",
        lambda **_: None,
    )
    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        lambda **kwargs: captured.setdefault("task_boundary", kwargs)
        or {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "ok": False,
            "status": "incomplete_materialization",
            "failure_class": "INCOMPLETE_MATERIALIZATION",
            "reason": "Required target files were not materialized",
        },
    )

    publisher = _Publisher()
    projector = projection.StreamEventProjector(
        kernel=SimpleNamespace(workspace=str(tmp_path)),
        role="director",
        profile=SimpleNamespace(role_id="director", model="test-model", provider_id="test-provider"),
        request=RoleTurnRequest(
            workspace=str(tmp_path),
            message="implement",
            run_id="run-1",
            task_id="TASK-1",
        ),
        fingerprint=SimpleNamespace(full_hash="fingerprint"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: {"route_weight": 0.17}),
        context_result=SimpleNamespace(token_estimate=11),
        stream_run_id="run-1",
        uep_publisher=publisher,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit=None,
    )

    result = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="turn-1",
                status="success",
                duration_ms=7,
                llm_calls=1,
                tool_calls=0,
                monitoring={
                    "required_tools": ["write_file"],
                    "native_tool_call_envelopes": [
                        {
                            "schema_version": "native_tool_call_envelope.v1",
                            "envelope_id": "native-write-1",
                            "tool_name": "write_file",
                        }
                    ],
                },
                batch_receipt={},
            )
        )
    )

    assert result is not None
    assert result.should_stop is True
    assert result.event["type"] == "error"
    assert result.event["error_type"] == "tool_dispatch_dropped"
    assert result.event["metadata"]["tool_call_lifecycle_receipt"]["dispatch_status"] == "dropped"
    assert result.event["metadata"]["tool_call_lifecycle_receipt"]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert captured["task_boundary"]["tool_dispatch"] == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "",
        "reason": "required_write_tool_without_dispatch_evidence",
    }
    assert publisher.events[-1]["event_type"] == "error"
