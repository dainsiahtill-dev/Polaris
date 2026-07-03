from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import (
    _project_completion_dispatch_evidence,
)


def test_project_completion_dispatch_evidence_keeps_native_envelope_refs() -> None:
    monitoring: dict[str, object] = {
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

    _project_completion_dispatch_evidence(monitoring, decision_metadata, usage_metadata)

    assert monitoring["native_tool_call_envelope_refs"] == decision_metadata["native_tool_call_envelope_refs"]
    assert monitoring["tool_call_lifecycle_receipt"] == decision_metadata["tool_call_lifecycle_receipt"]
    assert monitoring["final_request_context_audit"] == usage_metadata["final_request_context_audit"]
    assert monitoring["required_tools"] == ["write_file"]


def test_project_completion_dispatch_evidence_derives_refs_from_lifecycle_receipt() -> None:
    monitoring: dict[str, object] = {}
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

    _project_completion_dispatch_evidence(monitoring, usage_metadata)

    assert monitoring["native_tool_call_envelope_refs"] == [
        {
            "schema_version": "native_tool_call_envelope.v1",
            "envelope_id": "native_tool_call:openai:0:call-1:abcdef",
            "tool_name": "write_file",
        }
    ]
