"""Tests for RoleTurnResult projection helpers."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    role_result_metadata_from_profile,
    role_turn_completion_result,
    role_turn_error_result,
    role_turn_result_from_transaction_result,
    tool_calls_from_batch_receipt,
    tool_results_from_batch_receipt,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult


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


def test_role_result_metadata_projects_tool_lifecycle_and_derived_tool_facts() -> None:
    profile = SimpleNamespace(provider_id="", model="")
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_calls_count": 2,
        "decoded_tool_calls_count": 2,
        "dispatched_tool_calls_count": 0,
        "dropped_tool_calls": [
            {"tool_name": "write_file", "reason": "tool_dispatch_dropped"},
            {"tool_name": "execute_command", "reason": "tool_dispatch_dropped"},
        ],
    }

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={"tool_call_lifecycle": lifecycle},
    )

    assert metadata["tool_call_lifecycle"] == lifecycle
    assert metadata["tool_call_lifecycle_receipt"]["schema_version"] == "tool_call_lifecycle_receipt.v1"
    assert metadata["tool_call_lifecycle_receipt"]["native_tool_calls_count"] == 2
    assert metadata["tool_call_lifecycle_receipt"]["dispatch_status"] == "dropped"
    assert metadata["native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["write_file", "execute_command"]


def test_role_result_metadata_projects_canonical_lifecycle_from_plural_receipts() -> None:
    profile = SimpleNamespace(provider_id="", model="")
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
    }

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={"tool_call_lifecycle_receipts": [lifecycle]},
    )

    assert metadata["tool_call_lifecycle_receipts"] == [lifecycle]
    assert metadata["tool_call_lifecycle_receipt"]["schema_version"] == "tool_call_lifecycle_receipt.v1"
    assert metadata["tool_call_lifecycle_receipt"]["native_tool_calls_count"] == 1
    assert metadata["tool_call_lifecycle_receipt"]["dispatch_status"] == "dropped"
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]


def test_role_result_metadata_prefers_envelope_facts_over_legacy_native_counts() -> None:
    profile = SimpleNamespace(provider_id="", model="")
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
    }

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={
            "native_tool_calls_count": 9,
            "native_tool_call_names": ["stale_tool"],
            "tool_call_lifecycle_receipt": lifecycle,
        },
    )

    assert metadata["native_tool_calls_count"] == 2
    assert metadata["native_tool_call_names"] == ["read_file", "write_file"]


def test_role_result_metadata_treats_zero_lifecycle_as_authoritative() -> None:
    profile = SimpleNamespace(provider_id="", model="")

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={
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


def test_role_turn_error_result_projects_profile_and_copies_evidence() -> None:
    profile = SimpleNamespace(
        version="profile-v2",
        tool_policy=SimpleNamespace(policy_id="policy-v2"),
    )
    fingerprint = SimpleNamespace(full_hash="fingerprint")
    execution_stats = {"transaction_kernel": True, "tool_filter_blocked": True}
    metadata = {"tool_filter_audit": {"status": "conflict"}}

    result = role_turn_error_result(
        error="Tool schema filter conflict",
        profile=profile,
        fingerprint=fingerprint,
        execution_stats=execution_stats,
        metadata=metadata,
    )

    assert result.content == ""
    assert result.error == "Tool schema filter conflict"
    assert result.is_complete is False
    assert result.profile_version == "profile-v2"
    assert result.prompt_fingerprint is fingerprint
    assert result.tool_policy_id == "policy-v2"
    assert result.execution_stats == {
        "transaction_kernel": True,
        "tool_filter_blocked": True,
    }
    assert result.metadata == {"tool_filter_audit": {"status": "conflict"}}
    assert result.execution_stats is not execution_stats
    assert result.metadata is not metadata


def test_role_turn_error_result_supports_pre_profile_setup_failures() -> None:
    result = role_turn_error_result(
        error="角色加载失败: missing profile",
        is_complete=True,
    )

    assert result.error == "角色加载失败: missing profile"
    assert result.is_complete is True
    assert result.profile_version == ""
    assert result.prompt_fingerprint is None
    assert result.tool_policy_id == ""
    assert result.execution_stats == {
        "platform_retry_count": 0,
        "kernel_repair_retry_count": 0,
        "kernel_repair_reasons": [],
        "kernel_repair_exhausted": False,
    }
    assert result.metadata == {}


def test_role_turn_completion_result_projects_committed_turn_facts() -> None:
    profile = SimpleNamespace(
        version="profile-v3",
        tool_policy=SimpleNamespace(policy_id="policy-v3"),
    )
    fingerprint = SimpleNamespace(full_hash="fingerprint")
    structured_output = {"parsed": True}
    tool_calls = [{"tool": "read_file"}]
    tool_results = [{"tool": "read_file", "success": True}]
    batch_receipt = {"results": [{"tool_name": "read_file"}]}
    execution_stats = {"transaction_kernel": True, "llm_calls": 1}
    turn_history = [("assistant", "done")]
    turn_events_metadata = [{"event_id": "evt-2"}]
    metadata = {"context_snapshot_ref": "runtime/contexts/cc/dd.json"}

    result = role_turn_completion_result(
        content="done",
        thinking="internal reasoning",
        structured_output=structured_output,
        tool_calls=tool_calls,
        tool_results=tool_results,
        batch_receipt=batch_receipt,
        profile=profile,
        fingerprint=fingerprint,
        error=None,
        is_complete=True,
        execution_stats=execution_stats,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        metadata=metadata,
    )

    assert result.content == "done"
    assert result.thinking == "internal reasoning"
    assert result.structured_output == {"parsed": True}
    assert result.tool_calls == [{"tool": "read_file"}]
    assert result.tool_results == [{"tool": "read_file", "success": True}]
    assert result.batch_receipt == {"results": [{"tool_name": "read_file"}]}
    assert result.profile_version == "profile-v3"
    assert result.prompt_fingerprint is fingerprint
    assert result.tool_policy_id == "policy-v3"
    assert result.error is None
    assert result.is_complete is True
    assert result.execution_stats == {"transaction_kernel": True, "llm_calls": 1}
    assert result.turn_history == [("assistant", "done")]
    assert result.turn_events_metadata == [{"event_id": "evt-2"}]
    assert result.metadata == {"context_snapshot_ref": "runtime/contexts/cc/dd.json"}

    assert result.structured_output is not structured_output
    assert result.tool_calls is not tool_calls
    assert result.tool_results is not tool_results
    assert result.batch_receipt is not batch_receipt
    assert result.execution_stats is not execution_stats
    assert result.turn_history is not turn_history
    assert result.turn_events_metadata is not turn_events_metadata
    assert result.metadata is not metadata


def test_role_turn_result_from_transaction_result_projects_common_fields() -> None:
    profile = SimpleNamespace(
        version="profile-v1",
        tool_policy=SimpleNamespace(policy_id="policy-v1"),
    )
    fingerprint = SimpleNamespace(full_hash="fingerprint")
    quality_result = SimpleNamespace(quality_score=88.0, suggestions=["tighten scope"])
    transaction_result = RoleTurnResult(
        content="transaction content",
        thinking="analysis",
        tool_calls=[{"tool": "write_file"}],
        tool_results=[{"tool": "write_file", "success": True}],
        batch_receipt={"results": [{"tool_name": "write_file"}]},
        tool_execution_error="tool warning",
        should_retry=True,
        execution_stats={"transaction_kernel": True},
        turn_history=[("user", "hello")],
        turn_events_metadata=[{"event_id": "evt-1"}],
        metadata={"context_snapshot_ref": "runtime/contexts/aa/bb.json"},
    )

    result = role_turn_result_from_transaction_result(
        transaction_result=transaction_result,
        profile=profile,
        fingerprint=fingerprint,
        quality_result=quality_result,
        platform_retry_count=1,
        kernel_repair_retry_count=2,
        kernel_repair_reasons=["attempt_0: validation_failed"],
        kernel_repair_exhausted=True,
        error="validation failed",
        is_complete=False,
        structured_output={"parsed": True},
        content_override="effective content",
    )

    assert result.content == "effective content"
    assert result.thinking == "analysis"
    assert result.structured_output == {"parsed": True}
    assert result.profile_version == "profile-v1"
    assert result.prompt_fingerprint is fingerprint
    assert result.tool_policy_id == "policy-v1"
    assert result.quality_score == 88.0
    assert result.quality_suggestions == ["tighten scope"]
    assert result.error == "validation failed"
    assert result.is_complete is False
    assert result.tool_execution_error == "tool warning"
    assert result.should_retry is True
    assert result.execution_stats == {
        "platform_retry_count": 1,
        "kernel_repair_retry_count": 2,
        "kernel_repair_reasons": ["attempt_0: validation_failed"],
        "kernel_repair_exhausted": True,
        "transaction_kernel": True,
    }
    assert result.batch_receipt == {"results": [{"tool_name": "write_file"}]}
    assert result.tool_calls == [{"tool": "write_file"}]
    assert result.tool_results == [{"tool": "write_file", "success": True}]
    assert result.turn_history == [("user", "hello")]
    assert result.turn_events_metadata == [{"event_id": "evt-1"}]
    assert result.metadata == {"context_snapshot_ref": "runtime/contexts/aa/bb.json"}

    assert result.tool_calls is not transaction_result.tool_calls
    assert result.tool_results is not transaction_result.tool_results
    assert result.execution_stats is not transaction_result.execution_stats
    assert result.metadata is not transaction_result.metadata
