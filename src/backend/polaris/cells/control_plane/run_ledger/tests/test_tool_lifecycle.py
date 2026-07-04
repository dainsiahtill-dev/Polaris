from __future__ import annotations

from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public import tool_lifecycle
from polaris.cells.control_plane.run_ledger.public.failure_evidence import FailureClassV1
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    build_missing_dispatch_lifecycle_receipt,
    build_tool_batch_lifecycle_receipt,
    build_tool_batch_lifecycle_receipt_from_sources,
    build_tool_call_lifecycle_receipt,
    build_tool_call_lifecycle_run_ledger_event,
    build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt,
    build_tool_dispatch_dropped_anomaly_from_sources,
    build_tool_dispatch_dropped_anomaly_projection,
    build_tool_dispatch_dropped_lifecycle_from_anomaly_flags,
    build_tool_dispatch_dropped_lifecycle_from_observed_calls,
    empty_tool_lifecycle_summary,
    failure_evidence_from_lifecycle_receipt,
    merge_tool_lifecycle_summaries,
    native_tool_call_count_from_facts,
    native_tool_call_count_from_metadata,
    native_tool_call_envelope_refs_from_metadata,
    native_tool_call_facts_from_lifecycle_receipt,
    native_tool_call_facts_from_metadata,
    native_tool_call_facts_from_raw_calls,
    native_tool_call_facts_from_sources,
    native_tool_call_names_from_facts,
    normalize_native_tool_call_envelope_refs,
    normalize_tool_call_lifecycle_receipt,
    observed_tool_call_names_from_sources,
    project_completion_audit_evidence_to_metadata,
    project_completion_dispatch_evidence_to_metadata,
    project_lifecycle_failure_evidence_to_metadata,
    project_native_tool_call_envelopes_to_metadata,
    project_native_tool_call_facts_from_evidence_to_metadata,
    project_native_tool_call_facts_to_metadata,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    project_tool_lifecycle_metadata,
    project_tool_lifecycle_receipt_to_metadata,
    project_tool_lifecycle_summary,
    summarize_tool_lifecycle_events,
    task_boundary_tool_dispatch_from_lifecycle_metadata,
    task_boundary_tool_dispatch_from_lifecycle_receipt,
    tool_call_lifecycle_receipts_from_metadata,
)


def test_tool_lifecycle_all_exports_source_projection_helpers() -> None:
    required_exports = {
        "build_tool_batch_lifecycle_receipt_from_sources",
        "build_tool_dispatch_dropped_anomaly_from_sources",
        "native_tool_call_names_from_facts",
        "observed_tool_call_names_from_sources",
        "project_tool_lifecycle_failure_status",
        "project_tool_lifecycle_summary",
        "task_boundary_tool_dispatch_from_lifecycle_receipt",
    }

    assert required_exports <= set(tool_lifecycle.__all__)
    for name in required_exports:
        assert hasattr(tool_lifecycle, name)


def test_tool_lifecycle_receipt_links_batch_and_effect_refs() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "operation": "write_file:create",
                            "file": "src/index.js",
                            "before_hash": "",
                            "after_hash": "after",
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["provider_response_hash"] == "provider-response-hash"
    assert receipt["batch_receipt_hash"]
    assert receipt["batch_receipt_refs"][0]["batch_id"] == "batch-1"
    assert receipt["effect_receipt_count"] == 1
    assert receipt["effect_receipt_refs"][0]["file"] == "src/index.js"
    assert receipt["effect_receipt_refs"][0]["tool_name"] == "write_file"
    assert failure_evidence_from_lifecycle_receipt(receipt) == {}


def test_project_native_tool_call_facts_to_metadata_can_emit_decision_caller_compat_count() -> None:
    metadata: dict[str, object] = {}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file", "write_file"],
        },
        project_decision_caller_count=True,
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["decision_caller_native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["read_file", "write_file"]


def test_project_tool_lifecycle_summary_owns_read_model_shape() -> None:
    summary = {
        "ok": False,
        "event_count": "2",
        "native_tool_calls_count": "3",
        "decoded_tool_calls_count": "2",
        "dispatched_tool_calls_count": "1",
        "tool_result_count": "1",
        "effect_receipt_count": "1",
        "native_tool_call_names": ["write_file", "write_file", "execute_command"],
        "dropped_count": "1",
        "failed_count": "0",
        "failure_evidence": [{"failure_class": "TOOL_DISPATCH_DROPPED"}],
        "events": [{"status": "dropped"}],
    }

    projection = project_tool_lifecycle_summary(summary)

    assert projection == {
        "ok": False,
        "event_count": 2,
        "native_tool_calls_count": 3,
        "decoded_tool_calls_count": 2,
        "dispatched_tool_calls_count": 1,
        "tool_result_count": 1,
        "effect_receipt_count": 1,
        "native_tool_call_names": ["write_file", "execute_command"],
        "dropped_count": 1,
        "failed_count": 0,
        "failure_evidence": [{"failure_class": "TOOL_DISPATCH_DROPPED"}],
        "events": [{"status": "dropped"}],
    }


def test_normalize_native_tool_call_envelope_refs_filters_and_deduplicates() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-1",
    }
    without_id = {
        "schema_version": "native_tool_call_envelope.v1",
        "provider": "openai",
        "tool_name": "execute_command",
        "call_id": "call-2",
        "raw_call_hash": "a" * 64,
        "arguments_hash": "b" * 64,
    }

    refs = normalize_native_tool_call_envelope_refs(
        [
            envelope,
            dict(envelope),
            "not-an-envelope",
            without_id,
            dict(without_id),
        ]
    )

    assert refs == (envelope, without_id)


def test_tool_lifecycle_receipt_derives_dispatched_count_from_batch_receipts() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "read_file",
                        "status": "success",
                        "result": {"ok": True},
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["dispatched_tool_calls_count"] == 1
    assert receipt["tool_result_count"] == 1
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_receipt_detects_missing_batch_receipt() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["failure_class"] == "MISSING_BATCH_RECEIPT"


def test_tool_batch_lifecycle_receipt_classifies_decoded_batch_without_receipt() -> None:
    receipt = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        decoded_tool_calls_count=2,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert receipt["native_tool_calls_count"] == 2
    assert receipt["decoded_tool_calls_count"] == 2
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 2,
            "reason": "decoded_tool_batch_without_authoritative_receipt",
        }
    ]
    assert receipt["reason"] == "decoded_tool_batch_produced_no_authoritative_batch_receipt"


def test_tool_batch_lifecycle_receipt_from_sources_owns_native_fact_projection() -> None:
    receipt = build_tool_batch_lifecycle_receipt_from_sources(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        metadata={
            "native_tool_call_envelopes": [
                {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
                {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
            ],
        },
        native_tool_calls=[
            {"function": {"name": "write_file"}},
            {"function": {"name": "execute_command"}},
        ],
        decoded_tool_calls_count=2,
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["native_tool_calls_count"] == 2
    assert receipt["decoded_tool_calls_count"] == 2
    assert receipt["native_tool_call_envelope_refs"] == [
        {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
        {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
    ]


def test_tool_batch_lifecycle_receipt_keeps_authoritative_receipt_dispatched() -> None:
    receipt = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        decoded_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "read_file",
                        "status": "success",
                        "result": {"ok": True},
                    }
                ],
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""
    assert receipt["reason"] == ""


def test_build_dropped_lifecycle_from_anomaly_flags_preserves_legacy_envelopes() -> None:
    envelopes = [
        {"envelope_id": "native-read", "tool_name": "read_file"},
        {"envelope_id": "native-write", "tool_name": "write_file"},
    ]

    lifecycle = build_tool_dispatch_dropped_lifecycle_from_anomaly_flags(
        anomaly_flags=[
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "native_tool_calls_count": 99,
                "native_tool_call_envelopes": envelopes,
                "provider_response_hash": "hash-1",
            }
        ],
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        reason="tool dispatch dropped",
    )

    assert lifecycle["provider_response_hash"] == "hash-1"
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == envelopes
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "read_file", "envelope_id": "native-read", "reason": "tool_dispatch_dropped"},
        {"tool_name": "write_file", "envelope_id": "native-write", "reason": "tool_dispatch_dropped"},
    ]


def test_build_dropped_lifecycle_from_anomaly_flags_prefers_lifecycle_receipt() -> None:
    envelope = {"envelope_id": "native-receipt-write", "tool_name": "write_file"}

    lifecycle = build_tool_dispatch_dropped_lifecycle_from_anomaly_flags(
        anomaly_flags=[
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "native_tool_calls_count": 99,
                "provider_response_hash": "legacy-hash",
                "tool_call_lifecycle_receipt": {
                    "schema_version": "tool_call_lifecycle_receipt.v1",
                    "provider_response_hash": "receipt-hash",
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 1,
                    "dispatched_tool_calls_count": 0,
                    "native_tool_call_envelope_refs": [envelope, "invalid-ref", dict(envelope)],
                    "dropped_tool_calls": [
                        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"}
                    ],
                    "dispatch_status": "dropped",
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                },
            }
        ],
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        reason="tool dispatch dropped",
    )

    assert lifecycle["provider_response_hash"] == "receipt-hash"
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["decoded_tool_calls_count"] == 1
    assert lifecycle["native_tool_call_envelope_refs"] == [envelope]
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"}
    ]


def test_build_dropped_lifecycle_from_observed_calls_owns_dropped_refs() -> None:
    lifecycle = build_tool_dispatch_dropped_lifecycle_from_observed_calls(
        tool_names=["write_file", "write_file", "execute_command", ""],
        reason="observed calls had no result receipt",
    )

    assert lifecycle["dispatch_status"] == "dropped"
    assert lifecycle["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
        {"tool_name": "execute_command", "reason": "tool_dispatch_dropped"},
    ]
    assert lifecycle["reason"] == "observed calls had no result receipt"


def test_build_dropped_lifecycle_from_observed_calls_prefers_native_envelopes() -> None:
    lifecycle = build_tool_dispatch_dropped_lifecycle_from_observed_calls(
        tool_names=["ignored"],
        native_tool_call_envelopes=[
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
    )

    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["native_tool_call_envelope_refs"] == [
        {"envelope_id": "native-1", "tool_name": "write_file"}
    ]
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "envelope_id": "native-1", "reason": "tool_dispatch_dropped"}
    ]


def test_observed_tool_call_names_from_sources_owns_runtime_aliases() -> None:
    tool_calls = [
        {"name": "write_file"},
        {"tool": "read_file"},
        {"function": {"name": "execute_command"}},
        {"functionName": "repo_tree"},
        {"other": "ignored"},
        "not-a-mapping",
    ]

    assert observed_tool_call_names_from_sources(tool_calls) == (
        "write_file",
        "read_file",
        "execute_command",
        "repo_tree",
    )


def test_observed_tool_call_names_from_sources_falls_back_to_lifecycle_metadata() -> None:
    metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
    }

    assert observed_tool_call_names_from_sources([], metadata) == ("write_file",)


def test_tool_lifecycle_receipt_preserves_dropped_tool_details() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        dropped_tool_calls=["write_file"],
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]
    failure_evidence = failure_evidence_from_lifecycle_receipt(receipt)
    assert failure_evidence["schema_version"] == "failure_evidence.v1"
    assert failure_evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert failure_evidence["responsible_layer"] == "execution_control_plane"
    assert failure_evidence["reason"] == "dropped"
    assert failure_evidence["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"
    assert failure_evidence["metadata"]["dropped_tool_calls"] == receipt["dropped_tool_calls"]
    assert failure_evidence["evidence_refs"][0].startswith("dropped_tool_call:")


def test_tool_dispatch_dropped_anomaly_projection_builds_lifecycle_and_failure_evidence() -> None:
    anomaly = build_tool_dispatch_dropped_anomaly_projection(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=2,
        native_tool_call_envelopes=[
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
        streaming=True,
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["type"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert anomaly["streaming"] is True
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["native_tool_call_envelopes"] == lifecycle["native_tool_call_envelope_refs"]
    assert anomaly["provider_response_hash"] == "provider-hash"
    assert lifecycle["dispatch_status"] == "dropped"
    assert lifecycle["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["dispatched_tool_calls_count"] == 0
    failure_evidence = anomaly["failure_evidence"][0]
    assert failure_evidence["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert "provider_response:provider-hash" in failure_evidence["evidence_refs"]
    assert "native_tool_call:tool-envelope-1" in failure_evidence["evidence_refs"]


def test_tool_dispatch_dropped_anomaly_from_sources_owns_native_fact_projection() -> None:
    anomaly = build_tool_dispatch_dropped_anomaly_from_sources(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        metadata={},
        native_tool_calls=[
            {"function": {"name": "write_file"}},
            {"function": {"name": "execute_command"}},
        ],
        native_tool_call_envelopes=[
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["native_tool_call_envelopes"] == lifecycle["native_tool_call_envelope_refs"]
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["dispatched_tool_calls_count"] == 0


def test_tool_dispatch_dropped_anomaly_from_lifecycle_receipt_projects_counts() -> None:
    lifecycle = build_tool_batch_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-response-hash",
        decoded_tool_calls_count=2,
        receipts=[],
        dropped_tool_calls=[
            {"tool_name": "write_file", "call_id": "call-1"},
            {"tool_name": "execute_command", "call_id": "call-2"},
        ],
        missing_receipt_reason="decoded_tool_batch_produced_no_authoritative_batch_receipt",
    ).to_dict()

    anomaly = build_tool_dispatch_dropped_anomaly_from_lifecycle_receipt(lifecycle)

    assert anomaly["type"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert anomaly["turn_id"] == "turn-1"
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["decoded_tool_calls_count"] == 2
    assert anomaly["dispatched_tool_calls_count"] == 0
    assert anomaly["provider_response_hash"] == "provider-response-hash"
    assert anomaly["tool_call_lifecycle_receipt"] == lifecycle
    assert anomaly["failure_evidence"][0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_receipt_derives_dropped_status_from_native_without_dispatch() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        dispatch_status="success",
        receipts=[],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_project_tool_lifecycle_event_centralizes_projection_shape() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()

    event = project_tool_lifecycle_event(receipt, append_id="append-1", content_id="event-1")

    assert event["status"] == "dropped"
    assert event["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert event["failed"] is True
    assert event["dropped"] is True
    assert event["native_tool_calls_count"] == 1
    assert event["decoded_tool_calls_count"] == 1
    assert event["dispatched_tool_calls_count"] == 0
    assert event["provider_response_hash"] == "provider-hash"
    assert event["append_id"] == "append-1"
    assert event["content_id"] == "event-1"
    assert event["failure_evidence"]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert "provider_response:provider-hash" in event["failure_evidence"]["evidence_refs"]
    assert event["receipt"]["schema_version"] == "tool_call_lifecycle_receipt.v1"


def test_summarize_tool_lifecycle_events_centralizes_projection_totals() -> None:
    dropped_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        provider_response_hash="provider-hash",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=[
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native-tool-1",
                "tool_name": "write_file",
            }
        ],
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()
    dispatched_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "execute_command",
                        "status": "success",
                    }
                ],
            }
        ],
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [
            project_tool_lifecycle_event(dropped_receipt, append_id="append-1", content_id="event-1"),
            project_tool_lifecycle_event(dispatched_receipt, append_id="append-2", content_id="event-2"),
        ]
    )

    assert summary["ok"] is False
    assert summary["event_count"] == 2
    assert summary["native_tool_calls_count"] == 2
    assert summary["decoded_tool_calls_count"] == 2
    assert summary["dispatched_tool_calls_count"] == 1
    assert summary["tool_result_count"] == 1
    assert summary["native_tool_call_names"] == ["write_file"]
    assert summary["dropped_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["failure_evidence"][0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert [event["content_id"] for event in summary["events"]] == ["event-1", "event-2"]


def test_project_tool_lifecycle_failure_status_centralizes_failure_precedence() -> None:
    dropped_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=0,
        receipts=[],
        reason="native calls had no dispatch receipt",
    ).to_dict()
    missing_effect_receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-2",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
            }
        ],
        reason="write result had no effect receipt",
    ).to_dict()

    summary = summarize_tool_lifecycle_events(
        [
            project_tool_lifecycle_event(missing_effect_receipt, content_id="event-1"),
            project_tool_lifecycle_event(dropped_receipt, content_id="event-2"),
        ]
    )

    failure_status = project_tool_lifecycle_failure_status(summary)

    assert failure_status == {
        "failed": True,
        "status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native calls had no dispatch receipt",
    }


def test_project_tool_lifecycle_failure_status_reports_non_dropped_failure() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
            }
        ],
        reason="write result had no effect receipt",
    ).to_dict()

    summary = summarize_tool_lifecycle_events([project_tool_lifecycle_event(receipt)])

    failure_status = project_tool_lifecycle_failure_status(summary)

    assert failure_status == {
        "failed": True,
        "status": "blocked",
        "failure_class": FailureClassV1.MISSING_EFFECT_RECEIPT.value,
        "reason": "write result had no effect receipt",
    }
    assert project_tool_lifecycle_failure_status(empty_tool_lifecycle_summary()) == {
        "failed": False,
        "status": "",
        "failure_class": "",
        "reason": "",
    }


def test_merge_tool_lifecycle_summaries_centralizes_multi_project_projection() -> None:
    failure_evidence = {"failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value}
    merged = merge_tool_lifecycle_summaries(
        [
            {
                "tool_lifecycle": {
                    "ok": True,
                    "event_count": 1,
                    "native_tool_calls_count": 1,
                    "decoded_tool_calls_count": 1,
                    "dispatched_tool_calls_count": 1,
                    "tool_result_count": 1,
                    "effect_receipt_count": 1,
                    "native_tool_call_names": ["read_file"],
                    "dropped_count": 0,
                    "failed_count": 0,
                    "failure_evidence": [],
                    "events": [{"content_id": "event-1"}],
                }
            },
            {
                "tool_lifecycle": {
                    "ok": False,
                    "event_count": 1,
                    "native_tool_calls_count": 2,
                    "decoded_tool_calls_count": 2,
                    "dispatched_tool_calls_count": 0,
                    "tool_result_count": 0,
                    "effect_receipt_count": 0,
                    "native_tool_call_names": ["write_file", "read_file"],
                    "dropped_count": 1,
                    "failed_count": 1,
                    "failure_evidence": [failure_evidence],
                    "events": [{"content_id": "event-2"}],
                }
            },
        ]
    )

    assert merged["ok"] is False
    assert merged["event_count"] == 2
    assert merged["native_tool_calls_count"] == 3
    assert merged["decoded_tool_calls_count"] == 3
    assert merged["dispatched_tool_calls_count"] == 1
    assert merged["tool_result_count"] == 1
    assert merged["effect_receipt_count"] == 1
    assert merged["native_tool_call_names"] == ["read_file", "write_file"]
    assert merged["dropped_count"] == 1
    assert merged["failed_count"] == 1
    assert merged["failure_evidence"] == [failure_evidence]
    assert [event["content_id"] for event in merged["events"]] == ["event-1", "event-2"]


def test_empty_tool_lifecycle_summary_matches_public_projection_shape() -> None:
    assert empty_tool_lifecycle_summary() == {
        "ok": True,
        "event_count": 0,
        "native_tool_calls_count": 0,
        "decoded_tool_calls_count": 0,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "effect_receipt_count": 0,
        "native_tool_call_names": [],
        "dropped_count": 0,
        "failed_count": 0,
        "failure_evidence": [],
        "events": [],
    }


def test_tool_lifecycle_receipt_derives_counts_from_dropped_tool_details() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        dispatched_tool_calls_count=0,
        dropped_tool_calls=[{"tool_name": "write_file", "reason": "tool_dispatch_dropped"}],
        receipts=[],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["decoded_tool_calls_count"] == 1
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_receipt_derives_counts_from_count_only_dropped_ref() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        dispatched_tool_calls_count=0,
        dropped_tool_calls=[{"count": 4, "reason": "native_tool_calls_without_dispatch"}],
        receipts=[],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 4
    assert receipt["decoded_tool_calls_count"] == 4
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 4,
            "reason": "native_tool_calls_without_dispatch",
        }
    ]


def test_build_missing_dispatch_lifecycle_receipt_projects_required_write_tool() -> None:
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=["read_file", "write_file", "write_file"],
        metadata_candidates=(),
        tool_results=[],
        batch_receipt=None,
    )

    assert receipt is not None
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["native_tool_calls_count"] == 1
    assert receipt["decoded_tool_calls_count"] == 1
    assert receipt["dispatched_tool_calls_count"] == 0
    assert receipt["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
    ]
    assert receipt["reason"] == "required_write_tool_without_dispatch_evidence"


def test_build_missing_dispatch_lifecycle_receipt_prefers_native_envelope_metadata() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:provider:0:call-1:hash",
        "tool_name": "write_file",
    }
    receipt = build_missing_dispatch_lifecycle_receipt(
        required_write_tools=["write_file"],
        metadata_candidates=({"native_tool_call_envelope_refs": [envelope]},),
        tool_results=[],
        batch_receipt=None,
    )

    assert receipt is not None
    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
            "envelope_id": "native_tool_call:provider:0:call-1:hash",
        },
    ]


def test_build_missing_dispatch_lifecycle_receipt_skips_existing_dispatch_evidence() -> None:
    assert (
        build_missing_dispatch_lifecycle_receipt(
            required_write_tools=["write_file"],
            tool_results=[{"tool": "write_file", "ok": True}],
            batch_receipt=None,
        )
        is None
    )
    assert (
        build_missing_dispatch_lifecycle_receipt(
            required_write_tools=["write_file"],
            tool_results=[],
            batch_receipt={"results": [{"tool": "write_file", "ok": True}]},
        )
        is None
    )


def test_build_tool_call_lifecycle_run_ledger_event_normalizes_receipt_and_job_token() -> None:
    event = build_tool_call_lifecycle_run_ledger_event(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        lifecycle_receipt={
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dropped",
            "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        },
        stage="director_tool_dispatch",
        ok=False,
    )

    assert event["event_type"] == "tool_call_lifecycle"
    assert event["stage"] == "director_tool_dispatch"
    assert event["run_id"] == "run-1"
    assert event["task_id"] == "TASK-1"
    assert event["job_token"] == {
        "run_id": "run-1",
        "task_id": "TASK-1",
        "project_id": "TASK-1",
        "capability_audit": {"ok": True, "issues": []},
        "gate_policy": {},
    }
    receipt = event["tool_call_lifecycle_receipt"]
    assert receipt["run_id"] == "run-1"
    assert receipt["task_id"] == "TASK-1"
    assert receipt["turn_id"] == "turn-1"
    assert receipt["role"] == "director"
    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_build_tool_call_lifecycle_run_ledger_event_preserves_supplied_job_token() -> None:
    event = build_tool_call_lifecycle_run_ledger_event(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        lifecycle_receipt={"schema_version": "tool_call_lifecycle_receipt.v1", "ok": True},
        stage="tool_batch",
        job_token={
            "schema_version": 1,
            "source": "control_plane.job_token",
            "token_id": "token-1",
            "run_id": "",
            "task_id": "",
            "project_id": "",
            "stage": "",
            "contract_hash": "contract-hash",
            "blueprint_hash": "blueprint-hash",
            "execution_envelope_hash": "envelope-hash",
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {"enabled_evidence_modalities": ["tool_receipt"]},
        },
    )

    assert event["stage"] == "tool_batch"
    assert event["job_token"]["token_id"] == "token-1"
    assert event["job_token"]["run_id"] == "run-1"
    assert event["job_token"]["task_id"] == "TASK-1"
    assert event["job_token"]["project_id"] == "TASK-1"
    assert event["job_token"]["stage"] == "tool_batch"
    assert event["job_token"]["contract_hash"] == "contract-hash"
    assert event["job_token"]["blueprint_hash"] == "blueprint-hash"
    assert event["job_token"]["execution_envelope_hash"] == "envelope-hash"


def test_native_tool_call_facts_from_lifecycle_receipt_prefers_envelope_names() -> None:
    facts = native_tool_call_facts_from_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
                    "tool_name": "write_file",
                },
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
                    "tool_name": "execute_command",
                },
            ],
            "dispatched_tool_calls_count": 0,
        }
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_facts_from_lifecycle_receipt_uses_dropped_tool_names() -> None:
    facts = native_tool_call_facts_from_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dropped_tool_calls": ["write_file", {"tool_name": "write_file"}, {"tool_name": "edit_file"}],
        }
    )

    assert facts == {
        "native_tool_calls_count": 3,
        "native_tool_call_names": ["write_file", "edit_file"],
    }


def test_native_tool_call_facts_from_raw_calls_owns_provider_aliases() -> None:
    facts = native_tool_call_facts_from_raw_calls(
        [
            {"function": {"name": "write_file", "arguments": {"file": "src/index.js"}}},
            {"toolName": "execute_command"},
            {"function_name": "repo_tree"},
            {"tool_name": ""},
            "not-a-call",
        ]
    )

    assert facts == {
        "native_tool_calls_count": 4,
        "native_tool_call_names": ["write_file", "execute_command", "repo_tree"],
    }


def test_native_tool_call_facts_from_sources_prefers_metadata() -> None:
    facts = native_tool_call_facts_from_sources(
        {
            "native_tool_call_envelopes": [
                {"envelope_id": "native-1", "tool_name": "write_file"},
                {"envelope_id": "native-2", "tool_name": "execute_command"},
            ],
        },
        [{"function": {"name": "ignored_raw_tool"}}],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_facts_from_sources_falls_back_to_raw_calls() -> None:
    facts = native_tool_call_facts_from_sources(
        {},
        [
            {"function": {"name": "write_file"}},
            {"toolName": "repo_tree"},
            "not-a-call",
        ],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "repo_tree"],
    }


def test_native_tool_call_facts_from_sources_accepts_legacy_numeric_metadata() -> None:
    facts = native_tool_call_facts_from_sources(
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file", "write_file"],
        },
        [],
    )

    assert facts == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["read_file", "write_file"],
    }


def test_project_native_tool_call_envelopes_to_metadata_projects_count_and_names() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-write",
        "tool_name": "write_file",
    }
    command_envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-run",
        "tool_name": "execute_command",
    }
    metadata: dict[str, object] = {"native_tool_calls_count": 99, "native_tool_call_names": ["stale"]}

    project_native_tool_call_envelopes_to_metadata(
        metadata,
        [envelope, dict(envelope), command_envelope, "not-an-envelope"],
    )

    assert metadata == {
        "native_tool_call_envelopes": [envelope, command_envelope],
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_project_native_tool_call_facts_to_metadata_overwrites_stale_projection() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["write_file", "", "execute_command"],
        },
    )

    assert metadata == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["write_file", "execute_command"],
    }


def test_native_tool_call_names_from_facts_owns_name_coercion() -> None:
    assert native_tool_call_names_from_facts(
        {
            "native_tool_calls_count": 3,
            "native_tool_call_names": ["", " write_file ", None, "execute_command"],
        }
    ) == ["write_file", "execute_command"]
    assert native_tool_call_names_from_facts({}, fallback=(" repo_tree ", "", None)) == ["repo_tree"]


def test_project_native_tool_call_facts_to_metadata_can_preserve_names() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(
        metadata,
        {"native_tool_calls_count": 0, "native_tool_call_names": []},
        project_names=False,
    )

    assert metadata == {
        "native_tool_calls_count": 0,
        "native_tool_call_names": ["stale_tool"],
    }


def test_project_native_tool_call_facts_from_evidence_to_metadata_uses_lifecycle_evidence() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}
    evidence = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
            ],
        }
    }

    project_native_tool_call_facts_from_evidence_to_metadata(metadata, evidence)

    assert metadata == {
        "native_tool_calls_count": 2,
        "native_tool_call_names": ["read_file", "write_file"],
    }


def test_project_native_tool_call_facts_from_evidence_to_metadata_ignores_missing_evidence() -> None:
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_from_evidence_to_metadata(metadata, {})

    assert metadata == {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}


def test_project_completion_audit_evidence_to_metadata_projects_lifecycle_facts() -> None:
    metadata: dict[str, object] = {}
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-completion-write",
        "tool_name": "write_file",
    }

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "required_tools": ["write_file"],
            "native_tool_calls_count": 9,
            "native_tool_call_names": ["stale_tool"],
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [envelope],
            },
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    assert lifecycle["native_tool_call_envelope_refs"] == [envelope]
    assert failure_evidence_rows[0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_project_completion_audit_evidence_to_metadata_preserves_direct_failure_evidence() -> None:
    metadata: dict[str, object] = {}
    failure_evidence = [
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_RESULT_FAILED",
            "responsible_layer": "tool_executor",
        }
    ]

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 2,
            "native_tool_call_names": ["read_file"],
            "failure_evidence": failure_evidence,
            "failure_evidence_summary": {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"},
        },
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["read_file"]
    assert metadata["failure_evidence"] == failure_evidence
    assert metadata["failure_evidence_summary"] == {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"}


def test_project_completion_audit_evidence_to_metadata_can_overwrite_stale_native_facts() -> None:
    metadata: dict[str, object] = {
        "native_tool_calls_count": 9,
        "native_tool_call_names": ["stale_tool"],
    }

    project_completion_audit_evidence_to_metadata(
        metadata,
        {
            "native_tool_calls_count": 1,
            "native_tool_call_names": ["write_file"],
        },
        overwrite_native_facts=True,
    )

    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]


def test_project_completion_dispatch_evidence_keeps_native_envelope_refs() -> None:
    metadata: dict[str, object] = {
        "native_tool_call_envelopes": ["bad legacy projection"],
    }
    decision_metadata = {
        "native_tool_call_envelope_refs": [
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                "tool_name": "write_file",
            },
            {
                "schema_version": "native_tool_call_envelope.v1",
                "envelope_id": "native_tool_call:openai:1:call-2:abcdef",
                "tool_name": "execute_command",
            },
        ],
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "dropped",
            "failure_class": "tool_dispatch_dropped",
            "native_tool_calls_count": 2,
        },
    }
    usage_metadata = {
        "final_request_context_audit": {"schema_version": "llm.final_request_context_audit.v1"},
        "required_tools": ["write_file"],
    }

    project_completion_dispatch_evidence_to_metadata(metadata, decision_metadata, usage_metadata)

    assert metadata["native_tool_call_envelope_refs"] == decision_metadata["native_tool_call_envelope_refs"]
    assert metadata["tool_call_lifecycle_receipt"] == decision_metadata["tool_call_lifecycle_receipt"]
    assert metadata["final_request_context_audit"] == usage_metadata["final_request_context_audit"]
    assert metadata["required_tools"] == ["write_file"]


def test_project_completion_dispatch_evidence_derives_refs_from_lifecycle_receipt() -> None:
    metadata: dict[str, object] = {}
    usage_metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
                    "tool_name": "write_file",
                }
            ],
        }
    }

    project_completion_dispatch_evidence_to_metadata(metadata, usage_metadata)

    assert metadata["native_tool_call_envelope_refs"] == [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
            "tool_name": "write_file",
        }
    ]


def test_project_lifecycle_failure_evidence_to_metadata_appends_failed_lifecycle() -> None:
    metadata = {
        "failure_evidence": [
            {
                "schema_version": "failure_evidence.v1",
                "failure_class": "TOOL_RESULT_FAILED",
                "responsible_layer": "tool_executor",
            }
        ],
        "failure_evidence_summary": {"source": "previous_projection", "count": 1},
    }

    rows = project_lifecycle_failure_evidence_to_metadata(
        metadata,
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dropped",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "reason": "provider emitted tool calls but none were dispatched",
        },
    )

    assert rows[-1]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert metadata["failure_evidence_summary"] == {
        "source": "previous_projection",
        "count": 2,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


def test_project_lifecycle_failure_evidence_to_metadata_skips_success_lifecycle() -> None:
    metadata: dict[str, object] = {}

    rows = project_lifecycle_failure_evidence_to_metadata(
        metadata,
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "ok": True,
            "dispatch_status": "dispatched",
        },
    )

    assert rows == []
    assert metadata == {}


def test_project_tool_lifecycle_metadata_projects_canonical_receipt_failure_and_native_facts() -> None:
    metadata = {
        "tool_call_lifecycle_receipts": [
            {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 1,
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "dropped",
                "failure_class": "TOOL_DISPATCH_DROPPED",
                "reason": "provider emitted tool calls but none were dispatched",
                "native_tool_call_envelope_refs": [
                    {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"}
                ],
            }
        ],
    }

    project_tool_lifecycle_metadata(metadata)

    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    failure_summary = cast(dict[str, Any], metadata["failure_evidence_summary"])
    assert lifecycle["dispatch_status"] == "dropped"
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert failure_evidence_rows[-1]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert failure_summary["latest_failure_class"] == "TOOL_DISPATCH_DROPPED"


def test_project_tool_lifecycle_receipt_to_metadata_owns_canonical_and_compat_keys() -> None:
    metadata: dict[str, object] = {}
    receipt = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "envelope_id": "native-write", "tool_name": "write_file"},
        ],
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
    }

    project_tool_lifecycle_receipt_to_metadata(metadata, receipt)

    assert metadata["tool_call_lifecycle_receipt"] == metadata["tool_call_lifecycle"]
    lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
    failure_evidence_rows = cast(list[dict[str, Any]], metadata["failure_evidence"])
    assert lifecycle["native_tool_calls_count"] == 1
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert failure_evidence_rows[0]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_normalizer_canonicalizes_legacy_dropped_tool_names() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "dropped",
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "dropped_tool_calls": ["write_file"],
        }
    )

    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_normalizer_canonicalizes_failure_class_alias() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "blocked",
            "failure_class": "missing-effect-receipt",
        }
    )

    assert receipt["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value


def test_tool_lifecycle_normalizer_canonicalizes_dispatch_status_alias() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "dispatch_status": "tool-dispatch-dropped",
            "failure_class": "tool_dispatch_dropped",
            "dropped_tool_calls": ["write_file"],
        }
    )

    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_normalizer_derives_counts_from_native_envelopes() -> None:
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": f"native_tool_call:openai:{index}:call-{index}:abcdef",
            "provider": "openai",
            "tool_name": "write_file",
            "call_id": f"call-{index}",
        }
        for index in range(2)
    ]

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "decoded_tool_calls_count": 2,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": envelopes,
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 2
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        },
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
            "reason": "tool_dispatch_dropped",
        },
    ]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        },
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:1:call-1:abcdef",
            "reason": "tool_dispatch_dropped",
        },
    ]


def test_tool_lifecycle_normalizer_preserves_count_only_dropped_evidence() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 6,
            "decoded_tool_calls_count": 0,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 6
    assert receipt["decoded_tool_calls_count"] == 6
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "count": 6,
            "reason": "native_tool_calls_without_dispatch",
        }
    ]


def test_tool_lifecycle_normalizer_falls_back_to_valid_legacy_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": ["bad lifecycle projection"],
            "native_tool_call_envelopes": [envelope],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_normalizer_deduplicates_native_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 9,
            "dispatched_tool_calls_count": 0,
            "native_tool_call_envelope_refs": [envelope, dict(envelope)],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]


def test_tool_lifecycle_normalizer_derives_counts_from_lifecycle_refs() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 7,
            "dispatched_tool_calls_count": 0,
            "tool_result_count": 1,
            "effect_receipt_count": 0,
            "native_tool_call_envelope_refs": [
                {
                    "schema_version": "native_tool_call_envelope.v1",
                    "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
                    "provider": "openai",
                    "tool_name": "write_file",
                    "call_id": "call-0",
                }
            ],
            "batch_receipt_refs": [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}],
            "effect_receipt_refs": [
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
                {"receipt_hash": "effect-2", "operation": "edit_file", "file": "src/index.js"},
            ],
            "dispatch_status": "",
            "failure_class": "",
        }
    )

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["dispatched_tool_calls_count"] == 1
    assert receipt["tool_result_count"] == 1
    assert receipt["effect_receipt_count"] == 2
    assert receipt["batch_receipt_refs"] == [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}]
    assert [item["receipt_hash"] for item in receipt["effect_receipt_refs"]] == ["effect-1", "effect-2"]
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["ok"] is True


def test_tool_lifecycle_normalizer_deduplicates_batch_and_effect_refs() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "batch_receipt_refs": [
                {"batch_id": "batch-1", "receipt_hash": "batch-hash"},
                {"batch_id": "batch-1", "receipt_hash": "batch-hash"},
            ],
            "effect_receipt_refs": [
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
                {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"},
            ],
            "dispatch_status": "dispatched",
            "failure_class": "",
        }
    )

    assert receipt["batch_receipt_refs"] == [{"batch_id": "batch-1", "receipt_hash": "batch-hash"}]
    assert receipt["effect_receipt_refs"] == [
        {"receipt_hash": "effect-1", "operation": "write_file:create", "file": "src/index.js"}
    ]
    assert receipt["effect_receipt_count"] == 1


def test_tool_lifecycle_normalizer_projects_raw_dispatched_payload_as_ok() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "effect_receipt_count": 1,
            "dispatch_status": "dispatched",
            "failure_class": "",
        }
    )

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_normalizer_does_not_mark_dispatched_without_failure_as_unknown() -> None:
    receipt = normalize_tool_call_lifecycle_receipt(
        {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 1,
            "decoded_tool_calls_count": 1,
            "dispatched_tool_calls_count": 1,
            "tool_result_count": 1,
            "dispatch_status": "dispatched",
        }
    )

    assert receipt["ok"] is True
    assert receipt["dispatch_status"] == "dispatched"
    assert receipt["failure_class"] == ""


def test_tool_lifecycle_receipt_preserves_native_tool_call_envelopes() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-1",
        "raw_call_hash": "a" * 64,
        "arguments_hash": "b" * 64,
    }
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        native_tool_call_envelopes=[envelope],
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                        "effect_receipt": {
                            "operation": "write_file:create",
                            "file": "src/index.js",
                        },
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is True
    assert receipt["native_tool_call_envelope_refs"] == [envelope]


def test_tool_lifecycle_receipt_derives_native_count_from_envelopes() -> None:
    envelopes = [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": f"native_tool_call:openai:{index}:call-{index}:abcdef",
            "provider": "openai",
            "tool_name": "write_file",
            "call_id": f"call-{index}",
            "raw_call_hash": "a" * 64,
            "arguments_hash": "b" * 64,
        }
        for index in range(2)
    ]

    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=7,
        decoded_tool_calls_count=2,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=envelopes,
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 2
    assert receipt["dispatch_status"] == "dropped"
    assert receipt["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value


def test_tool_lifecycle_projects_task_boundary_dispatch_from_metadata() -> None:
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    dispatch = task_boundary_tool_dispatch_from_lifecycle_metadata(
        {"tool_call_lifecycle_receipt": lifecycle},
    )

    assert dispatch == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "reason": "native_tool_calls_without_dispatch",
    }


def test_tool_lifecycle_projects_task_boundary_dispatch_from_receipt() -> None:
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-1", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    assert task_boundary_tool_dispatch_from_lifecycle_receipt(lifecycle) == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/hash",
        "reason": "native_tool_calls_without_dispatch",
    }


def test_tool_lifecycle_projects_task_boundary_dispatch_from_plural_receipts() -> None:
    dispatched = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_calls_count": 1,
        "dispatched_tool_calls_count": 1,
        "tool_result_count": 1,
        "dispatch_status": "dispatched",
        "failure_class": "",
    }
    dropped = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"envelope_id": "native-write", "tool_name": "write_file"},
        ],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/plural",
        "dispatch_status": "dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "reason": "native_tool_calls_without_dispatch",
    }

    dispatch = task_boundary_tool_dispatch_from_lifecycle_metadata(
        {"tool_call_lifecycle_receipts": [dispatched, dropped]},
    )

    assert dispatch == {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "provider/plural",
        "reason": "native_tool_calls_without_dispatch",
    }


def test_tool_lifecycle_receipts_from_metadata_deduplicates_aliases() -> None:
    receipt = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
    }
    metadata = {
        "tool_call_lifecycle_receipt": receipt,
        "tool_call_lifecycle": dict(receipt),
        "tool_call_lifecycle_receipts": [dict(receipt)],
    }

    receipts = tool_call_lifecycle_receipts_from_metadata(metadata)

    assert len(receipts) == 1
    assert receipts[0]["native_tool_calls_count"] == 1
    assert receipts[0]["native_tool_call_envelope_refs"] == [
        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
    ]


def test_native_tool_call_facts_from_metadata_prefers_top_level_envelopes() -> None:
    top_level = [
        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
    ]
    metadata = {
        "native_tool_call_envelopes": top_level,
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
            ],
        },
    }

    assert native_tool_call_envelope_refs_from_metadata(metadata) == tuple(top_level)
    assert native_tool_call_facts_from_metadata(metadata) == {
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
    }


def test_native_tool_call_facts_from_metadata_treats_lifecycle_zero_as_authoritative() -> None:
    metadata = {
        "native_tool_calls_count": 99,
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_calls_count": 0,
            "decoded_tool_calls_count": 0,
            "dispatched_tool_calls_count": 0,
            "dispatch_status": "dispatched",
        },
    }

    assert native_tool_call_envelope_refs_from_metadata(metadata) == ()
    assert native_tool_call_facts_from_metadata(metadata) == {
        "native_tool_calls_count": 0,
        "native_tool_call_names": [],
    }


def test_native_tool_call_count_from_metadata_uses_envelopes_before_numeric_fallback() -> None:
    metadata = {
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
        "native_tool_calls_count": 99,
    }

    assert native_tool_call_count_from_metadata(metadata, fallback=1) == 2
    assert native_tool_call_count_from_metadata({"native_tool_calls_count": 3}, fallback=1) == 3
    assert native_tool_call_count_from_metadata({}, fallback=2) == 2


def test_native_tool_call_count_from_facts_owns_fact_count_coercion() -> None:
    assert native_tool_call_count_from_facts({"native_tool_calls_count": 2}, fallback=1) == 2
    assert native_tool_call_count_from_facts({"native_tool_calls_count": 0}, fallback=3) == 3
    assert native_tool_call_count_from_facts({"native_tool_calls_count": "bad"}, fallback=4) == 4
    assert native_tool_call_count_from_facts({}, fallback=5) == 5


def test_tool_lifecycle_receipt_deduplicates_native_envelopes_by_envelope_id() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
        "provider": "openai",
        "tool_name": "write_file",
        "call_id": "call-0",
    }

    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=9,
        decoded_tool_calls_count=9,
        dispatched_tool_calls_count=0,
        native_tool_call_envelopes=[envelope, dict(envelope)],
    ).to_dict()

    assert receipt["native_tool_calls_count"] == 1
    assert receipt["native_tool_call_envelope_refs"] == [envelope]
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": "native_tool_call:openai:0:call-0:abcdef",
            "reason": "tool_dispatch_dropped",
        }
    ]


def test_tool_lifecycle_receipt_blocks_successful_write_without_effect_receipt() -> None:
    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()

    assert receipt["ok"] is False
    assert receipt["dispatch_status"] == "blocked"
    assert receipt["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert receipt["effect_receipt_count"] == 0
    assert receipt["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "call_id": "call-1",
            "reason": "successful_write_tool_without_effect_receipt",
        }
    ]
