"""Tests for RoleTurnResult projection helpers."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    role_result_metadata_from_profile,
    tool_calls_from_batch_receipt,
    tool_results_from_batch_receipt,
)


def test_batch_receipt_projects_tool_calls_and_results() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "write_file",
                "arguments": {"file": "src/index.js"},
                "call_id": "call-1",
                "status": "success",
                "result": {"ok": True},
                "effect_receipt": {"file": "src/index.js"},
            },
            {
                "tool_name": "execute_command",
                "arguments": {"cmd": "npm test"},
                "call_id": "call-2",
                "status": "error",
                "result": {"ok": False},
            },
        ]
    }

    tool_calls = tool_calls_from_batch_receipt(receipt)
    tool_results = tool_results_from_batch_receipt(receipt)

    assert tool_calls == [
        {"tool": "write_file", "args": {"file": "src/index.js"}, "call_id": "call-1"},
        {"tool": "execute_command", "args": {"cmd": "npm test"}, "call_id": "call-2"},
    ]
    assert tool_results[0]["success"] is True
    assert tool_results[0]["effect_receipt"] == {"file": "src/index.js"}
    assert tool_results[0]["raw_result"]["tool_name"] == "write_file"
    assert tool_results[1]["success"] is False
    assert tool_results[1]["status"] == "error"


def test_batch_receipt_projection_ignores_invalid_shapes() -> None:
    assert tool_calls_from_batch_receipt(None) == []
    assert tool_calls_from_batch_receipt({"results": "not-a-list"}) == []
    assert tool_results_from_batch_receipt(None) == []
    assert tool_results_from_batch_receipt({"results": ["bad", {"tool_name": "read_file"}]}) == [
        {
            "tool": "read_file",
            "tool_name": "read_file",
            "result": None,
            "success": False,
            "status": None,
            "call_id": "",
            "arguments": None,
            "effect_receipt": None,
            "raw_result": {"tool_name": "read_file"},
        }
    ]


def test_role_result_metadata_projects_profile_and_llm_evidence() -> None:
    profile = SimpleNamespace(provider_id="openai", model="gpt-test")
    llm_metadata = {
        "context_snapshot_ref": "runtime/contexts/ab/cd.json",
        "usage": {"input_tokens": 12},
        "context_os_audit": {"coverage": "ok"},
    }

    metadata = role_result_metadata_from_profile(
        profile=profile,
        tool_filter_audit={"status": "filtered"},
        llm_response_metadata=llm_metadata,
    )

    assert metadata["provider_id"] == "openai"
    assert metadata["model"] == "gpt-test"
    assert metadata["tool_filter_audit"] == {"status": "filtered"}
    assert metadata["context_snapshot_ref"] == "runtime/contexts/ab/cd.json"
    assert metadata["usage"] == {"input_tokens": 12}
    assert metadata["context_os_audit"] == {"coverage": "ok"}


def test_role_result_metadata_uses_monitoring_context_audit_when_not_already_set() -> None:
    profile = SimpleNamespace(provider_id="", model="")

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={"context_os_audit": {"source": "llm"}},
        monitoring={"context_os_audit": {"source": "monitoring"}},
    )
    monitoring_only = role_result_metadata_from_profile(
        profile=profile,
        monitoring={"context_os_audit": {"source": "monitoring"}},
    )

    assert metadata["context_os_audit"] == {"source": "llm"}
    assert monitoring_only["context_os_audit"] == {"source": "monitoring"}
