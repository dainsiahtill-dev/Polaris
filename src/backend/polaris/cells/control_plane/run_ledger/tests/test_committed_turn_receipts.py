"""Owner-only readonly dispatch observations; no ledger backfill."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamProvenanceV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
)
from polaris.kernelone.storage import resolve_workspace_runtime_identity


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fixture(workspace: Path) -> tuple[dict, dict]:
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="readonly receipt test setup",
            streams=("roles.kernel.turn_outcomes",),
        )
    )
    identity = resolve_workspace_runtime_identity(str(workspace))
    turn_id = "director-run--TASK-1--attempt-transition-0"
    batch_id = turn_id + "_batch"
    raw_results = [
        {"call_id": "call-1", "tool_name": "read_file", "status": "success", "result": {"content": "first"}},
        {"call_id": "call-2", "tool_name": "read_file", "status": "success", "result": {"content": "second"}},
    ]
    results = [
        {
            **copy.deepcopy(row),
            "error": None,
            "execution_time_ms": 0,
            "effect_receipt": None,
            "effect_receipt_commit": None,
            "directed_effect_mutation_status": None,
            "directed_effect_claim_status": None,
        }
        for row in raw_results
    ]
    receipt = {
        "batch_id": batch_id,
        "turn_id": turn_id,
        "results": results,
        "raw_results": raw_results,
        "success_count": 2,
        "failure_count": 0,
        "pending_async_count": 0,
        "has_pending_async": False,
        "effect_receipts": [],
    }
    invocations = []
    envelopes = []
    refs = []
    for index, row in enumerate(results):
        arguments = {"file": "source.py", "start_line": index + 1}
        invocations.append(
            {
                "call_id": row["call_id"],
                "tool_name": "read_file",
                "raw_tool_name": "read_file",
                "arguments": arguments,
                "native_arguments": arguments,
                "effect_type": "read",
                "execution_mode": "readonly_parallel",
                "classification": {
                    "registered": True,
                    "canonical_tool_name": "read_file",
                    "raw_tool_name": "read_file",
                    "effect_type": "read",
                    "execution_mode": "readonly_parallel",
                    "normalization_required": False,
                    "error_code": None,
                },
            }
        )
        envelopes.append(
            {
                "schema_version": "native_tool_call_envelope.v1",
                "call_id": row["call_id"],
                "tool_name": "read_file",
                "arguments_hash": _hash(arguments),
                "index": index,
                "envelope_id": "envelope-" + str(index),
                "raw_call_hash": str(index + 1) * 64,
            }
        )
        legacy_result = {
            key: value
            for key, value in row.items()
            if key not in {"effect_receipt_commit", "directed_effect_mutation_status", "directed_effect_claim_status"}
        }
        original = {key: value for key, value in receipt.items() if key != "effect_receipts"}
        original.update(results=[legacy_result], raw_results=[raw_results[index]], success_count=1)
        refs.append({"batch_id": batch_id, "receipt_hash": _hash(original)})
    outcome = {
        "schema_version": "roles.kernel.turn_outcome.v1",
        "run_id": "director-run",
        "turn_id": turn_id,
        "decision": {
            "kind": "tool_batch",
            "turn_id": turn_id,
            "tool_batch": {"batch_id": batch_id, "invocations": invocations},
            "metadata": {"native_tool_call_envelopes": envelopes, "provider_response_hash": "a" * 64},
        },
        "execution": {
            "batch_id": batch_id,
            "receipt": receipt,
            "invocations": invocations,
            "invocation_batches": [{"batch_id": batch_id, "invocations": invocations}],
            "side_effect_class": "readonly",
        },
        "outcome_status": "needs_continuation",
    }
    provenance = FactStreamProvenanceV1(
        workspace=str(workspace), run_id="director-run", task_id="TASK-1", turn_id=turn_id, transition_id="transition-0"
    ).to_record()
    payload = {
        "schema_version": "roles.kernel.turn_outcome_fact.v2",
        "run_id": "director-run",
        "task_id": "TASK-1",
        "turn_id": turn_id,
        "transition_id": "transition-0",
        "provenance": provenance,
        "storage_identity": identity.to_record(),
        "outcome_hash": _hash(outcome),
        "outcome": outcome,
    }
    lifecycle = {
        "schema_version": "tool_call_lifecycle_receipt.v1",
        "role": "director",
        "run_id": "director-run",
        "task_id": "TASK-1",
        "turn_id": turn_id,
        "batch_receipt_refs": refs,
        "native_tool_call_envelope_refs": envelopes,
        "provider_response_hash": "a" * 64,
        "dispatched_tool_calls_count": 2,
        "tool_result_count": 2,
    }
    return payload, lifecycle


def _append(workspace: Path, payload: dict, *, source: str = "roles.kernel.transaction") -> None:
    provenance = FactStreamProvenanceV1(
        workspace=str(workspace),
        run_id="director-run",
        task_id="TASK-1",
        turn_id=payload["turn_id"],
        transition_id=payload["transition_id"],
    )
    identity = resolve_workspace_runtime_identity(str(workspace))
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="roles.kernel.turn_outcomes",
            event_type="turn_outcome_committed",
            payload=payload,
            source=source,
            run_id=provenance.run_id,
            task_id=provenance.task_id,
            correlation_id=payload["turn_id"],
            provenance=provenance,
            strict_integrity=True,
            idempotency_key=":".join(
                (
                    "roles.kernel.turn_outcomes",
                    identity.token,
                    payload["run_id"],
                    payload["task_id"],
                    payload["turn_id"],
                    payload["transition_id"],
                )
            ),
        )
    )


def _query(workspace: Path, lifecycle: dict):
    try:
        from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import query_committed_turn_receipts
    except ModuleNotFoundError:
        pytest.fail("public committed readonly receipt query unavailable")
    return query_committed_turn_receipts(workspace, lifecycle_receipts=[lifecycle])


def test_missing_ledger_batch_reads_exact_owner_receipt_without_writing(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    runtime = tmp_path / ".polaris" / "runtime"
    before = {str(path.relative_to(runtime)): path.read_bytes() for path in runtime.rglob("*") if path.is_file()}
    observations = _query(tmp_path, lifecycle)
    assert len(observations) == 1
    observed = observations[0]
    assert observed.status == "observed"
    assert observed.run_id == "director-run" and observed.task_id == "TASK-1"
    assert observed.native_call_ids == ("call-1", "call-2")
    assert observed.batch_receipt["success_count"] == 2
    assert observed.batch_receipt["pending_async_count"] == 0
    assert observed.batch_receipt_refs == tuple(
        (ref["batch_id"], ref["receipt_hash"]) for ref in lifecycle["batch_receipt_refs"]
    )
    observed.batch_receipt["success_count"] = 90
    assert observed.batch_receipt["success_count"] == 2
    assert before == {
        str(path.relative_to(runtime)): path.read_bytes() for path in runtime.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize(
    "defect",
    [
        "outcome_hash",
        "owner_source",
        "workspace",
        "run",
        "task",
        "turn",
        "batch",
        "duplicate_call",
        "invocation_arguments",
        "native_envelope",
        "raw_mirror",
        "unknown_result_field",
        "mutation",
        "async",
        "ref_hash",
        "duplicate_ref",
    ],
)
def test_damaged_owner_or_dispatch_binding_remains_unknown(tmp_path: Path, defect: str) -> None:
    payload, lifecycle = _fixture(tmp_path)
    owner = payload["outcome"]
    receipt = owner["execution"]["receipt"]
    source = "roles.kernel.transaction"
    if defect == "outcome_hash":
        payload["outcome_hash"] = "0" * 64
    elif defect == "owner_source":
        source = "caller"
    elif defect == "workspace":
        payload["provenance"]["workspace"] = str(tmp_path / "other")
    elif defect == "run":
        owner["run_id"] = "other"
    elif defect == "task":
        payload["task_id"] = "TASK-2"
    elif defect == "turn":
        receipt["turn_id"] = "other"
    elif defect == "batch":
        receipt["batch_id"] = "other"
    elif defect == "duplicate_call":
        receipt["results"][1]["call_id"] = "call-1"
    elif defect == "invocation_arguments":
        owner["execution"]["invocations"][0]["native_arguments"] = {"file": "other.py"}
    elif defect == "native_envelope":
        owner["decision"]["metadata"]["native_tool_call_envelopes"][0]["arguments_hash"] = "0" * 64
    elif defect == "raw_mirror":
        receipt["raw_results"][0]["result"]["content"] = "different"
    elif defect == "unknown_result_field":
        receipt["results"][0]["unknown"] = None
    elif defect == "mutation":
        owner["execution"]["side_effect_class"] = "local_write"
    elif defect == "async":
        receipt["pending_async_count"] = 1
    elif defect == "ref_hash":
        lifecycle["batch_receipt_refs"][0]["receipt_hash"] = "0" * 64
    elif defect == "duplicate_ref":
        lifecycle["batch_receipt_refs"].append(copy.deepcopy(lifecycle["batch_receipt_refs"][0]))
    if defect != "outcome_hash":
        payload["outcome_hash"] = _hash(owner)
    _append(tmp_path, payload, source=source)
    observations = _query(tmp_path, lifecycle)
    assert len(observations) == 1 and observations[0].status == "unknown"


def test_missing_owner_stays_unknown(tmp_path: Path) -> None:
    _, lifecycle = _fixture(tmp_path)
    observed = _query(tmp_path, lifecycle)
    assert len(observed) == 1 and observed[0].status == "unknown"


def test_aggregate_legacy_receipt_ref_binds_exact_owner(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    receipt = copy.deepcopy(payload["outcome"]["execution"]["receipt"])
    receipt.pop("effect_receipts")
    for row in receipt["results"]:
        for field in ("effect_receipt_commit", "directed_effect_mutation_status", "directed_effect_claim_status"):
            row.pop(field)
    lifecycle["batch_receipt_refs"] = [{"batch_id": receipt["batch_id"], "receipt_hash": _hash(receipt)}]
    _append(tmp_path, payload)
    assert _query(tmp_path, lifecycle)[0].status == "observed"


def test_two_owner_commits_for_same_turn_are_ambiguous(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    second = copy.deepcopy(payload)
    second["transition_id"] = "transition-1"
    second["provenance"]["transition_id"] = "transition-1"
    _append(tmp_path, second)
    observed = _query(tmp_path, lifecycle)
    assert len(observed) == 1 and observed[0].status == "unknown"
    assert observed[0].reason == "owner_turn_missing_or_ambiguous"


def test_strict_owner_integrity_corruption_remains_unknown(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    identity = resolve_workspace_runtime_identity(str(tmp_path))
    path = Path(identity.runtime_root) / "events" / "roles.kernel.turn_outcomes.jsonl"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"first"', '"forged"'), encoding="utf-8")
    observed = _query(tmp_path, lifecycle)
    assert len(observed) == 1 and observed[0].status == "unknown"


def test_non_null_known_effect_field_cannot_be_hidden_by_legacy_serializer(tmp_path: Path) -> None:
    payload, lifecycle = _fixture(tmp_path)
    payload["outcome"]["execution"]["receipt"]["results"][0]["effect_receipt_commit"] = {"event_id": "fake"}
    payload["outcome_hash"] = _hash(payload["outcome"])
    _append(tmp_path, payload)
    assert _query(tmp_path, lifecycle)[0].status == "unknown"
