from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public.failure_evidence import FailureClassV1
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import (
    build_tool_call_lifecycle_receipt,
    normalize_tool_call_lifecycle_receipt,
)


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
