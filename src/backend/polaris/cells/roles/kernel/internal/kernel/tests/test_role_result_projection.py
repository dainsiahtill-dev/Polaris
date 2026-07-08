"""Tests for RoleTurnResult projection helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    project_completion_audit_evidence,
    project_task_boundary_failure_to_metadata,
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


def test_batch_receipt_raw_results_fallback_projects_tool_calls_and_results() -> None:
    receipt = {
        "raw_results": [
            {
                "tool_name": "read_file",
                "arguments": {"file": "src/main.py"},
                "call_id": "raw-1",
                "status": "success",
                "result": {"content": "hello"},
                "effect_receipt": {"file": "src/main.py", "operation": "read"},
            }
        ]
    }

    assert tool_calls_from_batch_receipt(receipt) == [
        {"tool": "read_file", "args": {"file": "src/main.py"}, "call_id": "raw-1"}
    ]

    tool_results = tool_results_from_batch_receipt(receipt)

    assert tool_results == [
        {
            "tool": "read_file",
            "tool_name": "read_file",
            "result": {"content": "hello"},
            "success": True,
            "status": "success",
            "call_id": "raw-1",
            "arguments": {"file": "src/main.py"},
            "effect_receipt": {"file": "src/main.py", "operation": "read"},
            "raw_result": receipt["raw_results"][0],
        }
    ]


def test_batch_receipt_results_take_precedence_over_raw_results() -> None:
    receipt = {
        "results": [
            {
                "tool_name": "write_file",
                "arguments": {"file": "src/index.js"},
                "call_id": "result-1",
                "status": "success",
            }
        ],
        "raw_results": [
            {
                "tool_name": "read_file",
                "arguments": {"file": "src/stale.js"},
                "call_id": "raw-stale",
                "status": "success",
            }
        ],
    }

    assert tool_calls_from_batch_receipt(receipt) == [
        {"tool": "write_file", "args": {"file": "src/index.js"}, "call_id": "result-1"}
    ]
    assert tool_results_from_batch_receipt(receipt)[0]["call_id"] == "result-1"


def test_batch_receipt_empty_results_falls_back_to_raw_results() -> None:
    receipt = {
        "results": [],
        "raw_results": [
            {
                "tool_name": "execute_command",
                "arguments": {"cmd": "python -m pytest"},
                "call_id": "raw-command",
                "status": "success",
            }
        ],
    }

    assert tool_calls_from_batch_receipt(receipt) == [
        {"tool": "execute_command", "args": {"cmd": "python -m pytest"}, "call_id": "raw-command"}
    ]


def test_batch_receipt_effect_receipts_are_not_projected_as_tool_calls() -> None:
    receipt = {
        "effect_receipts": [
            {
                "file": "src/index.js",
                "operation": "write",
                "before_hash": "before",
                "after_hash": "after",
            }
        ]
    }

    assert tool_calls_from_batch_receipt(receipt) == []
    assert tool_results_from_batch_receipt(receipt) == []


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
        "failure_evidence": [
            {
                "schema_version": "polaris.failure_evidence.v1",
                "failure_class": "TOOL_RESULT_FAILED",
                "responsible_layer": "platform",
            }
        ],
        "failure_evidence_summary": {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"},
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
    assert metadata["failure_evidence"] == [
        {
            "schema_version": "polaris.failure_evidence.v1",
            "failure_class": "TOOL_RESULT_FAILED",
            "responsible_layer": "platform",
        }
    ]
    assert metadata["failure_evidence_summary"] == {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"}


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
    assert metadata["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert metadata["failure_evidence"][0]["responsible_layer"] == "execution_control_plane"
    assert metadata["failure_evidence"][0]["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"
    assert metadata["failure_evidence_summary"] == {
        "count": 1,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


def test_role_result_metadata_appends_lifecycle_failure_evidence() -> None:
    profile = SimpleNamespace(provider_id="", model="")
    explicit_evidence = [
        {
            "schema_version": "failure_evidence.v1",
            "failure_class": "TOOL_RESULT_FAILED",
            "responsible_layer": "tool_executor",
        }
    ]

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={
            "failure_evidence": explicit_evidence,
            "failure_evidence_summary": {"count": 1, "latest_failure_class": "TOOL_RESULT_FAILED"},
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_calls_count": 1,
                "decoded_tool_calls_count": 1,
                "dispatched_tool_calls_count": 0,
                "dispatch_status": "dropped",
            },
        },
    )

    assert metadata["failure_evidence"][0] == explicit_evidence[0]
    assert metadata["failure_evidence"][1]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert metadata["failure_evidence"][1]["metadata"]["source"] == "tool_call_lifecycle_receipt.v1"
    assert metadata["failure_evidence_summary"] == {
        "count": 2,
        "latest_failure_class": "TOOL_DISPATCH_DROPPED",
    }


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


def test_role_result_metadata_uses_run_ledger_lifecycle_alias_precedence() -> None:
    profile = SimpleNamespace(provider_id="", model="")
    canonical = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
        ],
    }
    stale_plural = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "native_tool_call_envelope_refs": [
            {"schema_version": "native_tool_call_envelope.v1", "tool_name": "read_file"},
        ],
    }

    metadata = role_result_metadata_from_profile(
        profile=profile,
        llm_response_metadata={
            "tool_call_lifecycle_receipt": canonical,
            "tool_call_lifecycle_receipts": [stale_plural],
        },
    )

    assert metadata["tool_call_lifecycle_receipt"]["native_tool_call_envelope_refs"] == [
        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"}
    ]
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


def test_project_completion_audit_evidence_uses_shared_lifecycle_projection() -> None:
    envelope = {
        "schema_version": "native_tool_call_envelope.v1",
        "envelope_id": "native-tool-call-completion-1",
        "tool_name": "write_file",
    }
    metadata: dict[str, Any] = {}

    project_completion_audit_evidence(
        metadata,
        {
            "required_tools": ["write_file"],
            "native_tool_calls_count": 9,
            "tool_call_lifecycle_receipt": {
                "schema_version": "tool_call_lifecycle_receipt.v1",
                "native_tool_call_envelope_refs": [envelope],
            },
        },
    )

    assert metadata["required_tools"] == ["write_file"]
    assert metadata["native_tool_calls_count"] == 1
    assert metadata["native_tool_call_names"] == ["write_file"]
    assert metadata["tool_call_lifecycle_receipt"]["native_tool_call_envelope_refs"] == [envelope]
    assert metadata["failure_evidence"][0]["failure_class"] == "TOOL_DISPATCH_DROPPED"


def test_project_task_boundary_failure_to_metadata_projects_failure_shape() -> None:
    metadata: dict[str, Any] = {}

    error = project_task_boundary_failure_to_metadata(
        metadata,
        {
            "ok": False,
            "task_id": "task-1",
            "run_id": "run-1",
            "status": "missing_entrypoint_target",
            "failure_class": "MISSING_ENTRYPOINT_TARGET",
            "responsible_layer": "task_boundary",
            "reason": "entrypoint is not materialized",
            "missing_entrypoint_targets": ["src/main.ts"],
            "diagnostic_context": {"paths": ("src/main.ts",)},
            "evidence_refs": ["run-ledger://task-1/task-boundary"],
        },
    )

    assert error == "task_boundary_failed:missing_entrypoint_target: entrypoint is not materialized"
    assert metadata["task_boundary_failed"] is True
    assert metadata["task_boundary_failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert metadata["task_boundary_failure_status"] == "missing_entrypoint_target"
    assert metadata["task_boundary_verdict"]["reason"] == "entrypoint is not materialized"
    failure_evidence = metadata["failure_evidence"]
    assert len(failure_evidence) == 1
    evidence_row = failure_evidence[0]
    assert isinstance(evidence_row, dict)
    assert evidence_row["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert evidence_row["responsible_layer"] == "task_boundary"
    assert evidence_row["failure_stage"] == "task_boundary"
    assert evidence_row["root_cause_hint"] == "entrypoint is not materialized"
    assert evidence_row["detail"] == "entrypoint is not materialized"
    assert evidence_row["evidence_refs"] == ["run-ledger://task-1/task-boundary"]
    assert evidence_row["metadata"]["task_boundary_status"] == "missing_entrypoint_target"
    assert evidence_row["metadata"]["task_id"] == "task-1"
    assert evidence_row["metadata"]["run_id"] == "run-1"
    assert evidence_row["metadata"]["missing_entrypoint_targets"] == ["src/main.ts"]
    assert evidence_row["metadata"]["diagnostic_context"] == {"paths": ["src/main.ts"]}
    assert metadata["failure_evidence_summary"] == {
        "count": 1,
        "latest_failure_class": "MISSING_ENTRYPOINT_TARGET",
        "failure_classes": ["MISSING_ENTRYPOINT_TARGET"],
    }


def test_project_task_boundary_failure_to_metadata_preserves_ok_verdict_without_failure() -> None:
    metadata: dict[str, Any] = {}

    error = project_task_boundary_failure_to_metadata(metadata, {"ok": True, "status": "completed_verified"})

    assert error is None
    assert metadata["task_boundary_verdict"] == {"ok": True, "status": "completed_verified"}
    assert "task_boundary_failed" not in metadata
    assert "task_boundary_failure_class" not in metadata
    assert "task_boundary_failure_status" not in metadata
    assert "failure_evidence" not in metadata
    assert "failure_evidence_summary" not in metadata


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
