from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import (
    record_missing_dispatch_lifecycle_receipt,
)
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ErrorEvent
from polaris.cells.roles.profile.public.service import RoleTurnRequest


class _Publisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish_stream_event(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))


def _make_stream_completion_projector(
    tmp_path: Path,
    publisher: _Publisher,
) -> projection.StreamEventProjector:
    return projection.StreamEventProjector(
        kernel=SimpleNamespace(workspace=str(tmp_path)),
        role="director",
        profile=SimpleNamespace(role_id="director", model="test-model", provider_id="test-provider"),
        request=RoleTurnRequest(
            workspace=str(tmp_path),
            message="implement",
            run_id="run-1",
            task_id="TASK-1",
            validate_output=False,
        ),
        fingerprint=SimpleNamespace(full_hash="fingerprint"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
        context_result=SimpleNamespace(token_estimate=11),
        stream_run_id="run-1",
        uep_publisher=publisher,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit=None,
    )


def test_error_projection_preserves_final_request_audit(tmp_path: Path) -> None:
    publisher = _Publisher()
    projector = _make_stream_completion_projector(tmp_path, publisher)
    final_request_audit = {
        "prompt_profile_selection": {
            "inferred_language": "go",
            "inferred_task_type": "implement",
            "inferred_stage": "blueprint",
            "inferred_artifact": "cli",
        }
    }

    result = asyncio.run(
        projector.project(
            ErrorEvent(
                turn_id="turn-1",
                error_type="structured_output_payload_schema_mismatch",
                message="entrypoints must be non-empty",
                metadata={
                    "context_snapshot_ref": "abcdef123456abcdef123456",
                    "final_request_context_audit": final_request_audit,
                },
            )
        )
    )

    assert result is not None
    assert result.event["metadata"]["final_request_context_audit"] == final_request_audit
    assert result.event["metadata"]["context_snapshot_ref"] == "abcdef123456abcdef123456"


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
    canonical_lifecycle = cast(dict[str, Any], metadata["tool_call_lifecycle_receipt"])
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

    failure_evidence = cast(list[dict[str, Any]], metadata["failure_evidence"])
    assert failure_evidence[0]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert failure_evidence[0]["responsible_layer"] == "execution_control_plane"
    assert "provider_response:provider-hash-1" in failure_evidence[0]["evidence_refs"]
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
        lambda **kwargs: (
            captured.setdefault("task_boundary", kwargs)
            or {
                "schema_version": "polaris.task_boundary_verdict.v1",
                "ok": False,
                "status": "incomplete_materialization",
                "failure_class": "INCOMPLETE_MATERIALIZATION",
                "reason": "Required target files were not materialized",
            }
        ),
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
            validate_output=True,
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
    expected_dispatch = {
        "status": "dropped",
        "dropped": True,
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
        "decoded_tool_calls_count": 1,
        "dispatched_tool_calls_count": 0,
        "provider_response_hash": "",
        "reason": "required_write_tool_without_dispatch_evidence",
    }
    actual_dispatch = captured["task_boundary"]["tool_dispatch"]
    assert {key: actual_dispatch.get(key) for key in expected_dispatch} == expected_dispatch
    assert publisher.events[-1]["event_type"] == "error"


def test_stream_completion_validates_before_task_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    task_boundary_calls: list[dict[str, Any]] = []

    class _RejectingQualityChecker:
        def validate_output(self, *_args: Any, **_kwargs: Any) -> QualityResult:
            return QualityResult(
                success=False,
                errors=["malformed chief engineer JSON"],
                suggestions=["return one JSON object"],
                data=None,
                quality_score=0.0,
                quality_passed=False,
            )

    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        lambda **kwargs: task_boundary_calls.append(dict(kwargs)),
    )
    publisher = _Publisher()
    projector = projection.StreamEventProjector(
        kernel=SimpleNamespace(
            workspace=str(tmp_path),
            _injected_quality_checker=_RejectingQualityChecker(),
        ),
        role="chief_engineer",
        profile=SimpleNamespace(role_id="chief_engineer", model="test-model", provider_id="test-provider"),
        request=RoleTurnRequest(
            workspace=str(tmp_path),
            message="review",
            run_id="run-1",
            task_id="CE-PORTFOLIO-run-1",
            validate_output=True,
            max_retries=0,
        ),
        fingerprint=SimpleNamespace(full_hash="fingerprint"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
        context_result=SimpleNamespace(token_estimate=11),
        stream_run_id="run-1",
        uep_publisher=publisher,
        runtime_tool_policy_audit={"tool_policy_mode": "none"},
        tool_filter_audit=None,
        accumulated_content=['{"blueprints": [invalid]}'],
    )

    result = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="turn-invalid-output",
                status="success",
                duration_ms=7,
                llm_calls=1,
                tool_calls=0,
                batch_receipt={},
            )
        )
    )

    assert result is not None
    assert result.should_stop is True
    assert result.event["type"] == "error"
    assert result.event["error_type"] == "output_validation_failed"
    assert result.event["metadata"]["output_validation"]["success"] is False
    assert task_boundary_calls == []
    assert all(event["event_type"] != "complete" for event in publisher.events)


def test_stream_completion_projects_validated_structured_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    structured_output: dict[str, Any] = {"blueprints": []}

    class _AcceptingQualityChecker:
        def validate_output(self, *_args: Any, **_kwargs: Any) -> QualityResult:
            return QualityResult(
                success=True,
                errors=[],
                suggestions=[],
                data=structured_output,
                quality_score=100.0,
                quality_passed=True,
            )

    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        lambda **_kwargs: {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "ok": True,
            "status": "complete",
        },
    )
    publisher = _Publisher()
    projector = projection.StreamEventProjector(
        kernel=SimpleNamespace(
            workspace=str(tmp_path),
            _injected_quality_checker=_AcceptingQualityChecker(),
        ),
        role="chief_engineer",
        profile=SimpleNamespace(role_id="chief_engineer", model="test-model", provider_id="test-provider"),
        request=RoleTurnRequest(
            workspace=str(tmp_path),
            message="review",
            run_id="run-1",
            task_id="CE-PORTFOLIO-run-1",
            validate_output=True,
            max_retries=0,
        ),
        fingerprint=SimpleNamespace(full_hash="fingerprint"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
        context_result=SimpleNamespace(token_estimate=11),
        stream_run_id="run-1",
        uep_publisher=publisher,
        runtime_tool_policy_audit={"tool_policy_mode": "none"},
        tool_filter_audit=None,
        accumulated_content=['{"blueprints": []}'],
    )

    result = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="turn-valid-output",
                status="success",
                duration_ms=7,
                llm_calls=1,
                tool_calls=0,
                batch_receipt={},
            )
        )
    )

    assert result is not None
    assert result.should_stop is False
    assert result.event["type"] == "complete"
    assert result.event["result"].structured_output == structured_output
    assert result.event["result"].metadata["output_validation"]["success"] is True


def test_stream_completion_uses_caller_structured_contract_before_role_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A CE semantic patch must not be reinterpreted as a full CE portfolio."""

    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_semantic_repair_patch",
        description="One typed CE semantic repair patch.",
        json_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "base_candidate_hash": {"type": "string"},
                "diagnosis_hash": {"type": "string"},
                "artifact_upserts": {"type": "array"},
                "entrypoint_upserts": {"type": "array"},
                "behavior_invariant_upserts": {"type": "array"},
                "task_behavior_ref_replacements": {"type": "object"},
            },
            "required": [
                "base_candidate_hash",
                "diagnosis_hash",
                "artifact_upserts",
                "entrypoint_upserts",
                "behavior_invariant_upserts",
                "task_behavior_ref_replacements",
            ],
        },
    )
    structured_output = {
        "base_candidate_hash": "a" * 64,
        "diagnosis_hash": "b" * 64,
        "artifact_upserts": [],
        "entrypoint_upserts": [],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }
    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        lambda **_kwargs: {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "ok": True,
            "status": "complete",
        },
    )
    publisher = _Publisher()
    projector = projection.StreamEventProjector(
        kernel=SimpleNamespace(workspace=str(tmp_path)),
        role="chief_engineer",
        profile=SimpleNamespace(role_id="chief_engineer", model="test-model", provider_id="test-provider"),
        request=RoleTurnRequest(
            workspace=str(tmp_path),
            message="repair only the diagnosed CE semantic contract",
            run_id="run-1",
            task_id="CE-PORTFOLIO-run-1-SEMANTIC-PATCH-REPAIR-2",
            context_override={
                STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection(),
            },
            validate_output=True,
            max_retries=0,
        ),
        fingerprint=SimpleNamespace(full_hash="fingerprint"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
        context_result=SimpleNamespace(token_estimate=11),
        stream_run_id="run-1",
        uep_publisher=publisher,
        runtime_tool_policy_audit={"tool_policy_mode": "none"},
        tool_filter_audit=None,
        accumulated_content=[json.dumps(structured_output, sort_keys=True)],
    )

    result = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="turn-ce-semantic-patch",
                status="success",
                duration_ms=7,
                llm_calls=1,
                tool_calls=0,
                batch_receipt={},
            )
        )
    )

    assert result is not None
    assert result.should_stop is False
    assert result.event["type"] == "complete"
    assert result.event["result"].structured_output == structured_output
    assert result.event["result"].metadata["output_validation"] == {
        "success": True,
        "errors": [],
        "suggestions": [],
        "quality_score": 100.0,
        "schema_name": "chief_engineer_semantic_repair_patch",
        "validation_source": "caller_structured_output_contract",
    }


def test_stream_completion_fails_closed_when_task_boundary_ledger_append_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def raise_ledger_write_error(**_: Any) -> dict[str, Any]:
        raise OSError("ledger volume is read-only")

    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        raise_ledger_write_error,
    )

    publisher = _Publisher()
    projector = _make_stream_completion_projector(tmp_path, publisher)
    result = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="turn-ledger-failure",
                status="success",
                duration_ms=7,
                llm_calls=1,
                tool_calls=0,
                batch_receipt={},
            )
        )
    )

    assert result is not None
    assert result.should_stop is True
    assert result.event["type"] == "error"
    assert result.event["error_type"] == "control_plane_failure"
    assert result.event["error"] == (
        "control_plane_failure:run_ledger_append_failed: TaskBoundary verdict could not be committed to the Run Ledger"
    )
    assert "result" not in result.event
    metadata = result.event["metadata"]
    verdict = metadata["task_boundary_verdict"]
    assert verdict["failure_class"] == "RUN_LEDGER_APPEND_FAILED"
    assert verdict["responsible_layer"] == "execution_control_plane"
    assert verdict["exception_evidence"] == {
        "operation": "append_task_boundary_verdict",
        "exception_type": "OSError",
        "message": "ledger volume is read-only",
    }
    assert metadata["failure_evidence"][0]["failure_class"] == "RUN_LEDGER_APPEND_FAILED"
    assert metadata["failure_evidence"][0]["responsible_layer"] == "execution_control_plane"
    assert publisher.events[-1]["event_type"] == "error"
    assert all(event["event_type"] != "complete" for event in publisher.events)


@pytest.mark.parametrize(
    ("batch_receipt", "expected_tool_results_count"),
    [
        pytest.param(
            {
                "raw_results": [
                    {
                        "tool_name": "write_file",
                        "arguments": {"path": "app.py", "content": "print('ok')\n"},
                        "call_id": "call-raw-1",
                        "status": "success",
                        "result": {
                            "ok": True,
                            "effect_receipt": {
                                "operation": "write_file",
                                "file": "app.py",
                                "after_hash": "after-raw-1",
                            },
                        },
                    }
                ]
            },
            1,
            id="raw-results",
        ),
        pytest.param(
            {
                "effect_receipts": [
                    {
                        "operation": "write_file",
                        "file": "app.py",
                        "tool_name": "write_file",
                        "call_id": "call-effect-1",
                        "after_hash": "after-effect-1",
                    }
                ]
            },
            0,
            id="effect-receipts",
        ),
    ],
)
def test_stream_completion_does_not_report_dropped_dispatch_when_batch_has_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    batch_receipt: dict[str, Any],
    expected_tool_results_count: int,
) -> None:
    captured: dict[str, Any] = {}

    def capture_successful_task_boundary(**kwargs: Any) -> dict[str, Any]:
        captured["task_boundary"] = kwargs
        return {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "ok": True,
            "status": "complete",
        }

    monkeypatch.setattr(
        projection,
        "append_role_turn_task_boundary_verdict",
        capture_successful_task_boundary,
    )

    publisher = _Publisher()
    projector = _make_stream_completion_projector(tmp_path, publisher)

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
                batch_receipt=batch_receipt,
            )
        )
    )

    assert result is not None
    assert result.should_stop is False
    assert result.event["type"] == "complete"
    assert "error_type" not in result.event
    assert captured["task_boundary"]["tool_dispatch"] is None

    role_result = result.event["result"]
    assert len(role_result.tool_results) == expected_tool_results_count
    assert "tool_call_lifecycle_receipt" not in role_result.metadata
    assert role_result.metadata["required_tools"] == ["write_file"]
    assert publisher.events[-1]["event_type"] == "complete"
