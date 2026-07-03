from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import (
    _native_tool_call_count,
    _provider_response_hash,
    build_tool_dispatch_dropped_anomaly,
)


def test_native_tool_call_count_prefers_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])
    metadata = {
        "native_tool_call_envelopes": [
            {"envelope_id": "tool-envelope-1"},
            {"envelope_id": "tool-envelope-2"},
        ],
    }

    assert _native_tool_call_count(response, metadata) == 2


def test_native_tool_call_count_accepts_lifecycle_receipt_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])
    metadata = {
        "tool_call_lifecycle_receipt": {
            "schema_version": "tool_call_lifecycle_receipt.v1",
            "native_tool_call_envelope_refs": [
                {"envelope_id": "tool-envelope-1"},
                {"envelope_id": "tool-envelope-2"},
            ],
        }
    }

    assert _native_tool_call_count(response, metadata) == 2


def test_native_tool_call_count_falls_back_to_raw_calls() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[{"function": {"name": "write_file"}}],
    )

    assert _native_tool_call_count(response, {}) == 1


def test_provider_response_hash_includes_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])

    without_envelope = _provider_response_hash(response, {})
    with_envelope = _provider_response_hash(
        response,
        {"native_tool_call_envelopes": [{"envelope_id": "tool-envelope-1"}]},
    )

    assert with_envelope != without_envelope


def test_provider_response_hash_includes_lifecycle_receipt_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])

    without_envelope = _provider_response_hash(response, {})
    with_envelope = _provider_response_hash(
        response,
        {
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [{"envelope_id": "tool-envelope-1"}],
            }
        },
    )

    assert with_envelope != without_envelope


def test_build_tool_dispatch_dropped_anomaly_derives_lifecycle_from_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])
    metadata = {
        "run_id": "run-1",
        "task_id": "TASK-1",
        "role": "director",
        "native_tool_call_envelopes": [
            {"envelope_id": "tool-envelope-1", "tool_name": "write_file"},
            {"envelope_id": "tool-envelope-2", "tool_name": "execute_command"},
        ],
    }

    anomaly = build_tool_dispatch_dropped_anomaly(
        response=response,
        metadata=metadata,
        turn_id="turn-1",
        streaming=True,
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["type"] == "TOOL_DISPATCH_DROPPED"
    assert anomaly["streaming"] is True
    assert anomaly["native_tool_calls_count"] == 2
    assert anomaly["native_tool_call_envelopes"] == metadata["native_tool_call_envelopes"]
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["dispatched_tool_calls_count"] == 0
    assert lifecycle["dispatch_status"] == "dropped"
    assert lifecycle["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert lifecycle["run_id"] == "run-1"
    assert lifecycle["task_id"] == "TASK-1"
    assert lifecycle["role"] == "director"
