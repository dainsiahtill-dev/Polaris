"""Real nonzero CLI effects close through owner negative proofs, never success receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import production
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests.test_historical_execution_effect_closure import _settle


async def _real_failed_cli(root: Path, *, settle: bool = True, empty_error: bool = False) -> tuple[Any, dict[str, Any]]:
    attempt = production._setup_attempt(str(root))
    script = (
        'print("actual verifier failed"); raise SystemExit(2)\n'
        if empty_error
        else 'raise SystemExit("intentional private CLI failure")\n'
    )
    (root / "verify.py").write_text(script, encoding="utf-8")
    command = "python verify.py"
    candidate = production._candidate(
        attempt,
        ordinal=0,
        tool_call_id="failed-cli-call",
        normalized_tool_name="execute_command",
        normalized_arguments=(("command", command), ("timeout", 30)),
        target_path="",
        target_exists=False,
        target_before_content_hash="0" * 64,
        is_no_file_state=True,
        turn_id="turn-failed-cli",
        batch_id="batch-failed-cli",
        allowed_commands=("python",),
        allowed_paths=(),
    )
    policy = production._WorkspaceObservingPolicyPort(root)
    authority = production._authority(attempt)
    prepared_result = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-failed-cli",
        batch_id="batch-failed-cli",
        candidates=(candidate,),
    )
    assert prepared_result.status == "ready", prepared_result
    prepared = prepared_result.prepared_batch
    assert prepared is not None
    fence = production.create_directed_effect_fence_ports()
    mutation = production.create_director_directed_effect_mutation_port(
        workspace=str(root), policy_snapshot_port=policy, fence_consume_port=fence.consume
    )
    runtime = production.ToolBatchRuntime(
        executor=production._reject_non_mutation_execution,
        directed_effect_runtime=production.DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy, fence_admin_port=fence.admin, mutation_port=mutation
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            ("failed-cli-call", production._job_restriction_evidence(allowed_commands=("python",), allowed_paths=())),
        ),
        directed_effect_dispatch_call_ids=("failed-cli-call",),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId("failed-cli-call"),
        tool_name="execute_command",
        arguments={"command": command, "timeout": 30},
    )
    receipts = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId("batch-failed-cli"), invocations=[invocation], serial_writes=[invocation]
        ),
        production.TurnId("turn-failed-cli"),
    )
    batch = receipts[0].to_dict()
    row = batch["raw_results"][0]
    assert row["status"] == "error", row
    assert row["result"]["exit_code"] == (2 if empty_error else 1), json.dumps(row["result"])
    assert row["result"]["effect_recovery"]["state"] == "DEAD_LETTER", row
    assert row["effect_receipt"] is None
    assert row["effect_receipt_commit"] is None
    # Persist the real owner outcome using the public immutable event contract.
    from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
    from polaris.cells.events.fact_stream.public import (
        AppendFactEventCommandV1,
        FactStreamProvenanceV1,
        append_fact_event,
    )
    from polaris.kernelone.storage import resolve_workspace_runtime_identity

    identity = resolve_workspace_runtime_identity(str(root))
    provenance = FactStreamProvenanceV1(
        workspace=str(root),
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id="turn-failed-cli",
        transition_id="failed-cli-transition",
    )
    outcome = {
        "schema_version": "roles.kernel.turn_outcome.v1",
        "run_id": attempt.run_id,
        "turn_id": "turn-failed-cli",
        "decision": {
            "metadata": {
                "native_tool_call_envelopes": [
                    {
                        "call_id": "failed-cli-call",
                        "tool_name": "execute_command",
                        "arguments_hash": stable_hash({"command": command, "timeout": 30}),
                    }
                ]
            }
        },
        "execution": {
            "batch_id": batch["batch_id"],
            "receipt": batch,
            "side_effect_class": "mutating",
            "invocations": [
                {
                    "call_id": "failed-cli-call",
                    "tool_name": "execute_command",
                    "arguments": {"command": command, "timeout": 30},
                }
            ],
        },
    }
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(root),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            source="roles.kernel.transaction",
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            correlation_id="turn-failed-cli",
            provenance=provenance,
            strict_integrity=True,
            idempotency_key=":".join(
                (
                    "roles.kernel.turn_outcomes",
                    identity.token,
                    attempt.run_id,
                    attempt.external_task_id,
                    "turn-failed-cli",
                    "failed-cli-transition",
                )
            ),
            payload={
                "schema_version": "roles.kernel.turn_outcome_fact.v2",
                "run_id": attempt.run_id,
                "task_id": attempt.external_task_id,
                "turn_id": "turn-failed-cli",
                "transition_id": "failed-cli-transition",
                "provenance": provenance.to_record(),
                "storage_identity": identity.to_record(),
                "session_binding": {"session_id": attempt.session_id},
                "outcome": outcome,
                "outcome_hash": stable_hash(outcome),
            },
        )
    )
    if settle:
        _settle(root, prepared.execution_attempt)
    return prepared.execution_attempt, batch


@pytest.mark.asyncio
async def test_nonzero_cli_with_empty_error_closes_as_failure(tmp_path: Path) -> None:
    attempt, batch = await _real_failed_cli(tmp_path, empty_error=True)
    physical = batch["raw_results"][0]["result"]
    assert physical["physical_error"] == ""
    assert physical["exit_code"] == 2
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "closed", observed
    assert observed.receipts == ()
    assert len(observed.terminal_failures) == 1
    assert observed.terminal_failures[0].exit_code == 2
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_real_failed_cli_dead_letter_is_typed_negative_closure_without_success_receipts(tmp_path: Path) -> None:
    attempt, batch = await _real_failed_cli(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert result.status == "closed", result
    assert result.receipts == ()
    assert len(result.terminal_failures) == 1
    failure = result.terminal_failures[0]
    assert failure.exit_code == 1
    assert (
        failure.resolution_evidence_hash
        == batch["raw_results"][0]["result"]["effect_recovery"]["resolution_evidence_hash"]
    )
    assert failure.dead_letter_event_id == batch["raw_results"][0]["result"]["effect_recovery"]["event_id"]
    assert result.execution_authority is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack",
    [
        "active",
        "foreign",
        "binding",
        "physical",
        "mirror",
        "native",
        "resolution",
        "parent",
        "session_binding_null",
        "decision_null",
    ],
)
async def test_negative_closure_rejects_unproven_or_foreign_owner_evidence(tmp_path: Path, attack: str) -> None:
    attempt, _batch = await _real_failed_cli(tmp_path, settle=attack != "active")
    if attack == "foreign":
        from dataclasses import replace

        attempt = replace(attempt, session_id="foreign-session")
    elif attack in {"binding", "resolution", "parent"}:
        from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import (
            DirectedEffectOperationRepository,
        )
        from polaris.cells.runtime.task_runtime.public import DirectedEffectParentRegistryIdentityV1

        registry = DirectedEffectOperationRepository()._load_registry(
            str(tmp_path), DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
        )
        token = (
            registry.stream_token
            if attack == "parent"
            else next(iter(registry.bindings_by_id.values())).operation_stream_token
        )
        path = tmp_path / f".polaris/runtime/events/{token}.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if attack == "parent":
            events = events[:-1]
        else:
            event = events[-1]
            field = "expected_receipt_binding_hash" if attack == "binding" else "resolution_evidence_hash"
            event["payload"]["replay_descriptor"][field] = "0" * 64
            from polaris.kernelone.events.sourcing.models import EventEnvelope

            event["integrity_digest"] = EventEnvelope.integrity_digest_for_record(event)
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    elif attack in {"physical", "mirror", "native", "session_binding_null", "decision_null"}:
        path = tmp_path / ".polaris/runtime/events/roles.kernel.turn_outcomes.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        event = events[-1]
        outcome = event["payload"]["outcome"]
        if attack == "physical":
            outcome["execution"]["receipt"]["results"][0]["result"]["physical_error"] = ""
        elif attack == "mirror":
            outcome["execution"]["receipt"]["raw_results"][0]["result"]["exit_code"] = 2
        elif attack == "native":
            outcome["execution"]["invocations"][0]["call_id"] = "foreign-call"
        elif attack == "session_binding_null":
            event["payload"]["session_binding"] = None
        else:
            outcome["decision"] = None
        from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
        from polaris.kernelone.events.sourcing.models import EventEnvelope

        event["payload"]["outcome_hash"] = stable_hash(outcome)
        event["integrity_digest"] = EventEnvelope.integrity_digest_for_record(event)
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    result = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert result.status != "closed", result
    assert result.receipts == ()
    assert result.terminal_failures == ()
    assert result.execution_authority is False
