"""Canonical settlement consumes owner-bound artifact reuse without a provider turn."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_verified_existing_artifact_lifecycle_receipt,
    query_factory_settlement_barrier,
    service as ledger,
    settlement_barrier as barrier,
)
from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import project_tool_lifecycle_event
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import production
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    bind_runtime_task_to_factory_run,
    settle_task_runtime_execution_attempt_typed,
)


def _public_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    attempt = production._setup_attempt(str(tmp_path))
    factory = "private-artifact-reuse"
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(attempt.task_id), factory_run_id=factory
        )
    ).ok
    settled = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(tmp_path), identity=attempt, outcome="failed", summary="private unqualified candidate"
        )
    )
    assert settled.success, settled
    receipt = build_verified_existing_artifact_lifecycle_receipt(
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        artifact_receipt_refs=("execution-broker://project-verification/artifact/" + "a" * 64,),
    ).to_dict()
    assert receipt["turn_id"] == ""
    assert receipt["dispatched_tool_calls_count"] == 0
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id="",
            role="director",
            project_id="private-project",
            lifecycle_receipt=receipt,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": False, "summary": "candidate not accepted"},
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "private-project",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
            },
        )
    )
    captured: dict[str, Any] = {}
    original = ledger.project_factory_settlement_barrier

    def observe(**kwargs: Any) -> Any:
        captured.update({key: deepcopy(value) for key, value in kwargs.items()})
        return original(**kwargs)

    before = _snapshot(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "project_factory_settlement_barrier", observe)
        result = query_factory_settlement_barrier(str(tmp_path), factory)
    assert before == _snapshot(tmp_path)
    return result, captured


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _rejected(inputs: dict[str, Any]) -> None:
    result = barrier.project_factory_settlement_barrier(**inputs)
    assert result.closed is False, result
    assert result.release_allowed is False
    assert result.passed is False


def _receipt(inputs: dict[str, Any]) -> dict[str, Any]:
    return next(
        fact["tool_call_lifecycle_receipt"]
        for fact in inputs["ledger_facts"]
        if fact.get("event_type") == "tool_call_lifecycle"
    )


def _reproject(inputs: dict[str, Any]) -> None:
    fact = next(fact for fact in inputs["ledger_facts"] if fact.get("event_type") == "tool_call_lifecycle")
    inputs["run_projection"]["tool_lifecycle"]["events"] = [
        project_tool_lifecycle_event(
            fact["tool_call_lifecycle_receipt"], append_id=fact["append_id"], content_id=fact["content_id"]
        )
    ]


def test_real_owner_failed_run_safely_closes_public_artifact_reuse_without_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, inputs = _public_case(tmp_path, monkeypatch)
    assert result.closed is True, result
    assert result.release_allowed is True
    assert result.passed is False
    receipt = _receipt(inputs)
    assert receipt["turn_id"] == ""
    assert receipt["dispatched_tool_calls_count"] == 0
    assert len(inputs["effect_closures"]) == 1
    assert inputs["effect_closures"][0].status == "closed"
    assert inputs["effect_closures"][0].execution_authority is False


@pytest.mark.parametrize(
    "defect", ["missing", "active", "unknown", "foreign_run", "foreign_workspace", "stale_session", "duplicate"]
)
def test_reuse_requires_the_same_complete_terminal_owner_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    baseline, inputs = _public_case(tmp_path, monkeypatch)
    assert baseline.closed
    owner = inputs["effect_closures"][0]
    if defect == "missing":
        inputs["effect_closures"] = ()
    elif defect == "duplicate":
        inputs["effect_closures"] = (owner, owner)
    elif defect in {"active", "unknown"}:
        inputs["effect_closures"] = (replace(owner, status=defect),)
    else:
        field = {"foreign_run": "run_id", "foreign_workspace": "workspace", "stale_session": "session_id"}[defect]
        inputs["effect_closures"] = (
            replace(owner, execution_attempt=replace(owner.execution_attempt, **{field: "foreign-private-owner"})),
        )
    _rejected(inputs)


@pytest.mark.parametrize(
    "defect",
    [
        "ordinary_dispatch",
        "nonzero_reuse_dispatch",
        "empty_refs",
        "command_ref",
        "fake_source",
        "duplicate_refs",
        "ref_count",
        "bool_count",
        "provider_hash",
        "batch_refs",
    ],
)
def test_empty_turn_exception_never_accepts_dispatch_or_forged_reuse_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    baseline, inputs = _public_case(tmp_path, monkeypatch)
    assert baseline.closed
    receipt = _receipt(inputs)
    if defect == "ordinary_dispatch":
        receipt.update(compatibility_mode="native", dispatch_status="dispatched")
    elif defect == "nonzero_reuse_dispatch":
        for field in (
            "native_tool_calls_count",
            "decoded_tool_calls_count",
            "dispatched_tool_calls_count",
            "tool_result_count",
        ):
            receipt[field] = 1
    elif defect == "empty_refs":
        receipt.update(effect_receipt_refs=[], effect_receipt_count=0)
    elif defect == "command_ref":
        receipt["effect_receipt_refs"][0]["receipt_ref"] = "execution-broker://project-verification/command/" + "a" * 64
    elif defect == "fake_source":
        receipt["effect_receipt_refs"][0]["source"] = "caller.claimed"
    elif defect == "duplicate_refs":
        receipt["effect_receipt_refs"].append(deepcopy(receipt["effect_receipt_refs"][0]))
        receipt["effect_receipt_count"] = 2
    elif defect == "ref_count":
        receipt["effect_receipt_count"] = 2
    elif defect == "bool_count":
        receipt["native_tool_calls_count"] = False
    elif defect == "provider_hash":
        receipt["provider_response_hash"] = "f" * 64
    else:
        receipt["batch_receipt_refs"] = [{"batch_id": "phantom", "receipt_hash": "a" * 64}]
    _reproject(inputs)
    _rejected(inputs)


@pytest.mark.parametrize(
    "defect", ["missing_fact", "extra_fact", "duplicate_fact", "duplicate_event", "phantom_turn", "hash_drift"]
)
def test_canonical_reuse_keeps_complete_unique_hash_bound_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    baseline, inputs = _public_case(tmp_path, monkeypatch)
    assert baseline.closed
    facts = inputs["ledger_facts"]
    fact = next(value for value in facts if value.get("event_type") == "tool_call_lifecycle")
    events = inputs["run_projection"]["tool_lifecycle"]["events"]
    if defect == "missing_fact":
        facts.remove(fact)
    elif defect in {"extra_fact", "duplicate_fact"}:
        other = deepcopy(fact)
        if defect == "extra_fact":
            other.update(append_id="private-extra-append", content_id="private-extra-content")
        facts.append(other)
    elif defect == "duplicate_event":
        events.append(deepcopy(events[0]))
    elif defect == "phantom_turn":
        events[0]["receipt"]["turn_id"] = "invented-provider-turn"
    else:
        events[0]["receipt"]["effect_receipt_refs"][0]["receipt_ref"] = (
            "execution-broker://project-verification/artifact/" + "b" * 64
        )
    _rejected(inputs)
