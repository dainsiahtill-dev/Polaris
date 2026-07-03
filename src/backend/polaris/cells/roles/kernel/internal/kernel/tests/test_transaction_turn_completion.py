from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    FailureClassV1,
    ReadRunLedgerProjectionQueryV1,
    read_run_ledger_projection,
)
from polaris.cells.roles.kernel.internal.kernel import transaction_turn_completion as completion
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


@dataclass
class _Request:
    workspace: str = "."
    task_id: str = "task-1"
    run_id: str = "run-1"
    context_override: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Profile:
    role_id: str = "director"
    model: str = "test-model"
    provider_id: str = "test-provider"


def test_completion_owner_commits_projection_and_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    committed: dict[str, Any] = {}
    verdict: dict[str, Any] = {}

    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(
        completion,
        "_commit_turn_to_snapshot",
        lambda **kwargs: committed.update(kwargs),
    )
    monkeypatch.setattr(
        completion,
        "append_role_turn_task_boundary_verdict",
        lambda **kwargs: verdict.update(kwargs),
    )

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.42}))
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "final_answer",
            "visible_content": "done",
            "batch_receipt": {},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": {"events": []},
            "llm_response_metadata": {"context_snapshot_ref": "ctx/ref"},
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    assert result.content == "done"
    assert result.error is None
    assert result.execution_stats["duration_ms"] == 7
    assert result.execution_stats["tool_policy_mode"] == "native"
    assert result.metadata["projection_adaptive_weights_after_turn"] == {"route_weight": 0.42}
    assert committed["turn_id"] == "turn-1"
    assert committed["ledger"] == {"events": []}
    assert verdict["role"] == "director"
    assert verdict["run_id"] == "run-1"
    assert verdict["needs_followup_workflow"] is False
    context_gateway.record_projection_outcome.assert_called_once_with(success=True, tokens_used=11)


def test_completion_owner_fails_when_task_boundary_verdict_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(completion, "_commit_turn_to_snapshot", lambda **_: None)
    monkeypatch.setattr(
        completion,
        "append_role_turn_task_boundary_verdict",
        lambda **_: {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "status": "missing_entrypoint_target",
            "ok": False,
            "failure_class": "MISSING_ENTRYPOINT_TARGET",
            "responsible_layer": "task_boundary",
            "reason": "Manifest references local entrypoint files that are neither complete nor declared downstream",
            "missing_entrypoint_targets": ["scripts/build.js"],
        },
    )

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.11}))
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "final_answer",
            "visible_content": "done",
            "batch_receipt": {},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": {"events": []},
            "llm_response_metadata": {"context_snapshot_ref": "ctx/ref"},
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    assert result.is_complete is False
    assert result.error is not None
    assert result.error.startswith("task_boundary_failed:missing_entrypoint_target")
    assert result.metadata["task_boundary_failed"] is True
    assert result.metadata["task_boundary_failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert result.metadata["task_boundary_verdict"]["missing_entrypoint_targets"] == ["scripts/build.js"]
    context_gateway.record_projection_outcome.assert_called_once_with(success=False, tokens_used=11)


def test_completion_owner_fails_closed_when_required_write_has_no_dispatch_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(completion, "_commit_turn_to_snapshot", lambda **_: None)
    monkeypatch.setattr(
        completion,
        "append_role_turn_task_boundary_verdict",
        lambda **_: {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "status": "incomplete_materialization",
            "ok": False,
            "failure_class": "INCOMPLETE_MATERIALIZATION",
            "responsible_layer": "director",
            "reason": "Required target files were not materialized",
        },
    )

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.07}))
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "final_answer",
            "visible_content": "I will call write_file.",
            "batch_receipt": {},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": SimpleNamespace(
                decisions=[
                    {
                        "metadata": {
                            "native_tool_calls_count": 1,
                            "tool_count": 0,
                        }
                    }
                ]
            ),
            "llm_response_metadata": {
                "context_snapshot_ref": "ctx/ref",
                "native_tool_calls_count": 1,
                "final_request_context_audit": {
                    "final_request_evidence_coverage": {
                        "required_tools": ["write_file"],
                    },
                },
            },
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    assert result.is_complete is False
    assert result.error == "tool_dispatch_dropped: required write tool was not dispatched before completion"
    assert result.metadata["tool_call_lifecycle"]["dispatch_status"] == "dropped"
    assert result.metadata["tool_call_lifecycle"]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert result.metadata["tool_call_lifecycle"]["native_tool_calls_count"] == 1
    assert result.metadata["tool_call_lifecycle"]["dropped_tool_calls"] == [
        {"tool_name": "write_file", "reason": "tool_dispatch_dropped"}
    ]
    assert result.metadata["task_boundary_failed"] is True
    context_gateway.record_projection_outcome.assert_called_once_with(success=False, tokens_used=11)


def test_missing_dispatch_lifecycle_prefers_native_tool_call_envelopes() -> None:
    envelopes = [
        {"envelope_id": "native-1", "tool_name": "write_file"},
        {"envelope_id": "native-2", "tool_name": "execute_command"},
    ]

    lifecycle = completion._build_missing_dispatch_lifecycle_receipt(
        metadata={
            "native_tool_calls_count": 0,
            "final_request_context_audit": {
                "final_request_evidence_coverage": {
                    "required_tools": ["write_file"],
                },
            },
        },
        ledger=SimpleNamespace(
            decisions=[
                {
                    "metadata": {
                        "native_tool_calls_count": 0,
                        "native_tool_call_envelopes": envelopes,
                        "tool_count": 0,
                    }
                }
            ]
        ),
        tool_results=[],
        batch_receipt={},
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == envelopes
    assert lifecycle["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "envelope_id": "native-1", "reason": "tool_dispatch_dropped"},
        {"tool_name": "execute_command", "envelope_id": "native-2", "reason": "tool_dispatch_dropped"},
    ]


def test_missing_dispatch_lifecycle_accepts_lifecycle_envelope_refs() -> None:
    envelopes = [
        {"envelope_id": "native-ref-1", "tool_name": "write_file"},
        {"envelope_id": "native-ref-2", "tool_name": "execute_command"},
    ]

    lifecycle = completion._build_missing_dispatch_lifecycle_receipt(
        metadata={
            "native_tool_calls_count": 0,
            "final_request_context_audit": {
                "final_request_evidence_coverage": {
                    "required_tools": ["write_file"],
                },
            },
        },
        ledger=SimpleNamespace(
            decisions=[
                {
                    "metadata": {
                        "native_tool_calls_count": 0,
                        "native_tool_call_envelope_refs": envelopes,
                        "tool_count": 0,
                    }
                }
            ]
        ),
        tool_results=[],
        batch_receipt={},
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == envelopes
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "write_file", "envelope_id": "native-ref-1", "reason": "tool_dispatch_dropped"},
        {"tool_name": "execute_command", "envelope_id": "native-ref-2", "reason": "tool_dispatch_dropped"},
    ]


def test_missing_dispatch_lifecycle_uses_native_envelope_tools_not_required_tools() -> None:
    lifecycle = completion._build_missing_dispatch_lifecycle_receipt(
        metadata={
            "final_request_context_audit": {
                "final_request_evidence_coverage": {
                    "required_tools": ["write_file"],
                },
            },
        },
        ledger=SimpleNamespace(
            decisions=[
                {
                    "metadata": {
                        "native_tool_call_envelopes": [
                            {"envelope_id": "native-read", "tool_name": "read_file"},
                        ],
                    }
                }
            ]
        ),
        tool_results=[],
        batch_receipt={},
    )

    assert lifecycle is not None
    assert lifecycle["native_tool_calls_count"] == 1
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "read_file", "envelope_id": "native-read", "reason": "tool_dispatch_dropped"}
    ]


def test_completion_owner_preserves_suspension_error_and_records_lifecycle_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """ask_user suspension keeps its own error while dropped-dispatch evidence is still recorded."""

    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(completion, "_commit_turn_to_snapshot", lambda **_: None)

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.07}))
    request = _Request(
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-suspended-missing-dispatch",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace=str(tmp_path)),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, request),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "ask_user",
            "visible_content": "Which file should I write?",
            "batch_receipt": {},
            "finalization": {"suspended_reason": "finalization_tool_calls_blocked"},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": SimpleNamespace(
                decisions=[
                    {
                        "metadata": {
                            "native_tool_calls_count": 1,
                            "tool_count": 0,
                        }
                    }
                ]
            ),
            "llm_response_metadata": {
                "context_snapshot_ref": "ctx/ref",
                "native_tool_calls_count": 1,
                "final_request_context_audit": {
                    "final_request_evidence_coverage": {
                        "required_tools": ["write_file"],
                    },
                },
            },
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-suspended-missing-dispatch")
    ).projection

    assert result.is_complete is False
    assert result.error == "finalization_tool_calls_blocked"
    assert result.metadata["tool_call_lifecycle"]["dispatch_status"] == "dropped"
    assert result.metadata["tool_call_lifecycle"]["failure_class"] == FailureClassV1.TOOL_DISPATCH_DROPPED.value
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["events"][0]["status"] == "dropped"


def test_completion_owner_commits_missing_dispatch_lifecycle_to_run_ledger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(completion, "_commit_turn_to_snapshot", lambda **_: None)

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.07}))
    request = _Request(
        workspace=str(tmp_path),
        task_id="TASK-1",
        run_id="run-missing-dispatch",
        context_override={
            "delivery_mode": "materialize_changes",
            "target_files": ["src/index.js"],
        },
    )
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace=str(tmp_path)),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, request),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "final_answer",
            "visible_content": "I will call write_file.",
            "batch_receipt": {},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": SimpleNamespace(
                decisions=[
                    {
                        "metadata": {
                            "native_tool_calls_count": 1,
                            "tool_count": 0,
                        }
                    }
                ]
            ),
            "llm_response_metadata": {
                "context_snapshot_ref": "ctx/ref",
                "native_tool_calls_count": 1,
                "final_request_context_audit": {
                    "final_request_evidence_coverage": {
                        "required_tools": ["write_file"],
                    },
                },
            },
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-missing-dispatch")
    ).projection

    assert result.is_complete is False
    assert result.error == "tool_dispatch_dropped: required write tool was not dispatched before completion"
    assert projection["ok"] is False
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["tool_lifecycle"]["events"][0]["status"] == "dropped"
    assert projection["task_boundary"]["latest"]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert projection["task_boundary"]["latest"]["status"] == "tool_dispatch_dropped"
