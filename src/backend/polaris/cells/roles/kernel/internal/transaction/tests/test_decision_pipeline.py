from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import (
    native_tool_call_facts_from_sources,
    project_native_tool_call_facts_to_metadata,
)
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    native_tool_calls_from_response,
    provider_response_hash,
)
from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import build_tool_dispatch_dropped_anomaly


def test_native_tool_call_facts_prefers_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])
    metadata = {
        "native_tool_call_envelopes": [
            {"envelope_id": "tool-envelope-1"},
            {"envelope_id": "tool-envelope-2"},
        ],
    }

    facts = native_tool_call_facts_from_sources(metadata, native_tool_calls_from_response(response))
    assert facts["native_tool_calls_count"] == 2


def test_native_tool_call_facts_accepts_lifecycle_receipt_envelopes() -> None:
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

    facts = native_tool_call_facts_from_sources(metadata, native_tool_calls_from_response(response))
    assert facts["native_tool_calls_count"] == 2


def test_native_tool_call_facts_falls_back_to_raw_calls() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[{"function": {"name": "write_file"}}],
    )

    facts = native_tool_call_facts_from_sources({}, native_tool_calls_from_response(response))
    assert facts["native_tool_calls_count"] == 1


def test_project_native_tool_call_facts_overwrites_stale_projection() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[
            {"function": {"name": "write_file"}},
            {"function": {"name": "execute_command"}},
        ],
    )
    metadata = {"native_tool_calls_count": 9, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(
        metadata,
        native_tool_call_facts_from_sources({}, native_tool_calls_from_response(response)),
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["write_file", "execute_command"]


def test_provider_response_hash_includes_metadata_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])

    without_envelope = provider_response_hash(response, {})
    with_envelope = provider_response_hash(
        response,
        {"native_tool_call_envelopes": [{"envelope_id": "tool-envelope-1"}]},
    )

    assert with_envelope != without_envelope


def test_provider_response_hash_includes_lifecycle_receipt_envelopes() -> None:
    response = SimpleNamespace(content="", model="gpt-test", native_tool_calls=[])

    without_envelope = provider_response_hash(response, {})
    with_envelope = provider_response_hash(
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
    failure_evidence = anomaly["failure_evidence"][0]
    assert failure_evidence["schema_version"] == "failure_evidence.v1"
    assert failure_evidence["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert failure_evidence["responsible_layer"] == "execution_control_plane"
    assert failure_evidence["reason"] == "provider_emitted_tool_calls_but_no_decoded_tool_batch"
    assert f"provider_response:{lifecycle['provider_response_hash']}" in failure_evidence["evidence_refs"]
    assert "native_tool_call:tool-envelope-1" in failure_evidence["evidence_refs"]
    assert "native_tool_call:tool-envelope-2" in failure_evidence["evidence_refs"]
    assert any(str(item).startswith("dropped_tool_call:") for item in failure_evidence["evidence_refs"])
    assert failure_evidence["metadata"] == {
        "source": "tool_call_lifecycle_receipt.v1",
        "dispatch_status": "dropped",
        "native_tool_calls_count": 2,
        "decoded_tool_calls_count": 2,
        "dispatched_tool_calls_count": 0,
        "tool_result_count": 0,
        "effect_receipt_count": 0,
        "dropped_tool_calls": [
            {
                "tool_name": "write_file",
                "envelope_id": "tool-envelope-1",
                "reason": "tool_dispatch_dropped",
            },
            {
                "tool_name": "execute_command",
                "envelope_id": "tool-envelope-2",
                "reason": "tool_dispatch_dropped",
            },
        ],
    }


def test_build_tool_dispatch_dropped_anomaly_builds_envelopes_from_raw_response() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"file": "src/main.py"}},
            },
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "execute_command", "arguments": {"cmd": "pytest"}},
            },
        ],
    )
    metadata = {
        "run_id": "run-1",
        "task_id": "TASK-1",
        "role": "director",
        "tool_call_provider": "openai",
    }

    anomaly = build_tool_dispatch_dropped_anomaly(
        response=response,
        metadata=metadata,
        turn_id="turn-1",
    )

    lifecycle = anomaly["tool_call_lifecycle_receipt"]
    assert anomaly["native_tool_calls_count"] == 2
    assert [item["tool_name"] for item in anomaly["native_tool_call_envelopes"]] == [
        "write_file",
        "execute_command",
    ]
    assert [item["call_id"] for item in anomaly["native_tool_call_envelopes"]] == ["call-1", "call-2"]
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["decoded_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == anomaly["native_tool_call_envelopes"]
    assert lifecycle["dropped_tool_calls"] == [
        {
            "tool_name": "write_file",
            "envelope_id": anomaly["native_tool_call_envelopes"][0]["envelope_id"],
            "reason": "tool_dispatch_dropped",
        },
        {
            "tool_name": "execute_command",
            "envelope_id": anomaly["native_tool_call_envelopes"][1]["envelope_id"],
            "reason": "tool_dispatch_dropped",
        },
    ]
