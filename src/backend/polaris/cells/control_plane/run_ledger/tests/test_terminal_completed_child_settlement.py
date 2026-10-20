from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import query_factory_settlement_barrier
from polaris.cells.control_plane.run_ledger.tests.test_terminal_failure_cleanup import _failed_read_batch


def test_completed_child_with_canonical_denied_dispatch_can_close_failed_project_resources(tmp_path: Path) -> None:
    _failed_read_batch(tmp_path, status="completed")
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.passed is False
    assert result.missing_required_modalities
    assert result.release_allowed is True


@pytest.mark.parametrize(
    "defect",
    [
        {"status": "in_progress"},
        {"pending": 1},
        {"include_batch": False},
        {"result_status": "success"},
        {"tool": "unknown_tool"},
        {"tool": "write_file"},
    ],
)
def test_completed_child_cleanup_requires_real_terminal_dispatch_proof(tmp_path: Path, defect: dict) -> None:
    _failed_read_batch(tmp_path, **{"status": "completed", **defect})
    result = query_factory_settlement_barrier(tmp_path, "factory-cleanup")
    assert result.release_allowed is False
    assert result.passed is False


@pytest.mark.parametrize("tamper", ["ledger_absent", "append_id", "content_id", "receipt", "duplicate", "summary_only"])
def test_completed_child_cleanup_cannot_be_activated_by_counts_or_foreign_failure_fact(
    tmp_path: Path, tamper: str
) -> None:
    from copy import deepcopy

    from polaris.cells.control_plane.run_ledger.public import service
    from polaris.cells.control_plane.run_ledger.public.settlement_barrier import _terminal_failure_has_closed_dispatches

    _failed_read_batch(tmp_path, status="completed")
    tasks = service._read_task_runtime_execution_facts(workspace=tmp_path, factory_run_id="factory-cleanup")
    facts = service._read_execution_control_plane_facts(workspace=tmp_path, run_ids=("director-cleanup",))
    projection = service.build_run_ledger_projection(
        [*facts, *service._tool_lifecycle_requirement_events_from_task_runtime(tasks)]
    )
    from polaris.cells.control_plane.run_ledger.public.settlement_barrier import _latest_task_facts, _task_state

    assert all(_task_state(row) == "completed" for row in _latest_task_facts(tasks).values())
    # No owner effect observation is supplied: only the genuine canonical
    # denied-read outcome can open the terminal cleanup path here.
    assert _terminal_failure_has_closed_dispatches(tasks, projection, facts, workspace=str(tmp_path)) is True
    altered = deepcopy(facts)
    failures = [f for f in altered if f.get("event_type") == "tool_call_lifecycle"]
    assert len(failures) == 1
    failure = failures[0]
    if tamper == "ledger_absent":
        altered.remove(failure)
    elif tamper == "append_id":
        failure["append_id"] = "foreign"
    elif tamper == "content_id":
        failure["content_id"] = "foreign"
    elif tamper == "receipt":
        failure["tool_call_lifecycle_receipt"]["run_id"] = "foreign"
    elif tamper == "duplicate":
        altered.append(deepcopy(failure))
    else:
        for event in projection["tool_lifecycle"]["events"]:
            event["failed"] = False
        projection["tool_lifecycle"]["failed_count"] = 999
    assert _terminal_failure_has_closed_dispatches(tasks, projection, altered, workspace=str(tmp_path)) is False


def _cleanup_witness_inputs(tmp_path: Path, *, second_dispatch: bool = False, same_turn: bool = False):
    from polaris.cells.control_plane.run_ledger.public import service

    _failed_read_batch(tmp_path, status="completed")
    run_ids = ["director-cleanup"]
    if second_dispatch:
        from polaris.cells.control_plane.run_ledger.public import (
            AppendRunLedgerEventCommandV1,
            AppendToolCallLifecycleEventCommandV1,
            append_run_ledger_event,
            append_tool_call_lifecycle_event,
            build_tool_call_lifecycle_receipt,
        )

        original = service._read_execution_control_plane_facts(workspace=tmp_path, run_ids=tuple(run_ids))
        gate = next(row for row in original if row.get("event_type") == "gate_evaluated")
        batch = deepcopy(gate["physical_evidence"]["batch_receipt"])
        batch.update(batch_id="other-opaque-batch", turn_id=batch["turn_id"] if same_turn else "other-opaque-turn")
        for row in (*batch["results"], *batch["raw_results"]):
            row["call_id"] = "other-opaque-call"
        receipt = build_tool_call_lifecycle_receipt(
            run_id="director-cleanup",
            task_id="TASK-1",
            turn_id=batch["turn_id"],
            role="director",
            native_tool_calls_count=1,
            decoded_tool_calls_count=1,
            dispatched_tool_calls_count=1,
            receipts=[batch],
        ).to_dict()
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=str(tmp_path),
                run_id="director-cleanup",
                task_id="TASK-1",
                turn_id=batch["turn_id"],
                role="director",
                project_id="project-1",
                lifecycle_receipt=receipt,
            )
        )
        event = {
            key: deepcopy(gate[key])
            for key in ("event_type", "run_id", "stage", "gate", "job_token", "physical_evidence", "quiescent")
        }
        event["physical_evidence"]["batch_receipt"] = batch
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id="director-cleanup",
                event=event,
            )
        )
    tasks = service._read_task_runtime_execution_facts(workspace=tmp_path, factory_run_id="factory-cleanup")
    facts = service._read_execution_control_plane_facts(workspace=tmp_path, run_ids=tuple(run_ids))
    projection = service.build_run_ledger_projection(
        [*facts, *service._tool_lifecycle_requirement_events_from_task_runtime(tasks)]
    )
    return tasks, projection, facts


def _witness_closes(tmp_path: Path, tasks, projection, facts) -> bool:
    from polaris.cells.control_plane.run_ledger.public.settlement_barrier import _terminal_failure_has_closed_dispatches

    return _terminal_failure_has_closed_dispatches(tasks, projection, facts, workspace=str(tmp_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("failed", False),
        ("ok", True),
        ("status", "dropped"),
        ("native_tool_calls_count", 999),
        ("native_tool_calls_count", True),
        ("decoded_tool_calls_count", 999),
        ("dispatched_tool_calls_count", True),
        ("tool_result_count", 999),
        ("effect_receipt_count", False),
        ("dropped", True),
        ("provider_response_hash", "f" * 64),
        ("batch_receipt_hash", "e" * 64),
        ("batch_receipt_refs", []),
        ("effect_receipt_refs", [{"receipt_hash": "foreign"}]),
        ("native_tool_call_names", ["execute_command"]),
        ("turn_id", "foreign-turn"),
        ("task_key", "foreign-task-key"),
        ("task_identity_source", "foreign"),
        ("reason", "foreign reason"),
        ("failure_class", "foreign failure"),
        ("failure_evidence", "forged evidence"),
    ],
)
def test_cleanup_rejects_every_explicit_projection_contradiction(tmp_path: Path, field: str, value: Any) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path)
    assert _witness_closes(tmp_path, tasks, projection, facts)
    projection["tool_lifecycle"]["events"][0][field] = value
    assert not _witness_closes(tmp_path, tasks, projection, facts)


@pytest.mark.parametrize("missing", ["failed", "dispatched_tool_calls_count", "derived_fields"])
def test_cleanup_rederives_absent_row_fields_from_exact_canonical_receipt(tmp_path: Path, missing: str) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path)
    event = projection["tool_lifecycle"]["events"][0]
    if missing == "derived_fields":
        projection["tool_lifecycle"]["events"][0] = {
            key: event[key] for key in ("append_id", "content_id", "run_id", "task_id", "receipt")
        }
    else:
        del event[missing]
    assert _witness_closes(tmp_path, tasks, projection, facts)


def test_cleanup_summary_totals_do_not_authorize_or_block_canonical_closeout(tmp_path: Path) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path)
    projection["tool_lifecycle"]["failed_count"] = 999
    assert _witness_closes(tmp_path, tasks, projection, facts)


@pytest.mark.parametrize(
    "defect",
    ["missing", "duplicate", "unprojected_dispatch", "borrowed_scope", "removed_anchors", "missing_event_receipt"],
)
def test_cleanup_requires_total_unique_canonical_lifecycle_inventory(tmp_path: Path, defect: str) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path, second_dispatch=defect == "unprojected_dispatch")
    assert _witness_closes(tmp_path, tasks, projection, facts)
    events = projection["tool_lifecycle"]["events"]
    if defect == "missing":
        events.clear()
    elif defect == "duplicate":
        events.append(deepcopy(events[0]))
    elif defect == "unprojected_dispatch":
        assert len(events) == 2
        events.pop()
    elif defect == "removed_anchors":
        events[0].pop("append_id")
        events[0].pop("content_id")
    elif defect == "missing_event_receipt":
        events[0].pop("receipt")
    else:
        event = events[0]
        fact = next(row for row in facts if row.get("append_id") == event["append_id"])
        fact["run_id"] = event["run_id"] = "foreign-run"
    assert not _witness_closes(tmp_path, tasks, projection, facts)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ok", "false"),
        ("ok", 0),
        ("effect_receipt_count", False),
        ("effect_receipt_count", "0"),
        ("effect_receipt_count", -1),
        ("effect_receipt_count", "invalid"),
        ("run_id", 7),
        ("schema_version", None),
    ],
)
def test_cleanup_rejects_malformed_present_canonical_receipt_inputs(tmp_path: Path, field: str, value: Any) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path)
    event = projection["tool_lifecycle"]["events"][0]
    fact = next(row for row in facts if row.get("append_id") == event["append_id"])
    fact["tool_call_lifecycle_receipt"][field] = value
    event["receipt"][field] = value
    if field in event:
        event[field] = value
    assert not _witness_closes(tmp_path, tasks, projection, facts)


@pytest.mark.parametrize("missing", ["schema_version", "ok", "effect_receipt_count"])
def test_cleanup_missing_raw_witness_inputs_cannot_become_zero_obligations(tmp_path: Path, missing: str) -> None:
    tasks, projection, facts = _cleanup_witness_inputs(tmp_path)
    event = projection["tool_lifecycle"]["events"][0]
    fact = next(row for row in facts if row.get("append_id") == event["append_id"])
    fact["tool_call_lifecycle_receipt"].pop(missing)
    event["receipt"].pop(missing, None)
    assert not _witness_closes(tmp_path, tasks, projection, facts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["compact_exact", "compact_ref_annotation", "canonical_then_compact", "compact_then_canonical"]
)
async def test_compact_readonly_owner_proof_cannot_be_counted_twice(tmp_path: Path, variant: str) -> None:
    from polaris.cells.control_plane.run_ledger.public import service
    from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import query_committed_turn_receipts
    from polaris.cells.control_plane.run_ledger.public.settlement_barrier import _terminal_failure_has_closed_dispatches
    from polaris.cells.control_plane.run_ledger.tests.test_failed_product_resource_settlement import (
        _readonly_failed_product,
    )

    attempt, batch, lifecycle = await _readonly_failed_product(tmp_path)
    tasks = service._read_task_runtime_execution_facts(workspace=tmp_path, factory_run_id="factory-failed-product")
    owners = service._terminal_execution_effect_observations(str(tmp_path), tasks)
    proof = query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle])
    assert len(proof) == 1 and proof[0].status == "observed"
    receipt = {
        "run_id": attempt.run_id,
        "task_id": attempt.external_task_id,
        "turn_id": batch["turn_id"],
        "batch_receipt_refs": deepcopy(lifecycle["batch_receipt_refs"]),
    }
    compact = {"status": "ok", "dispatched_tool_calls_count": 1, "receipt": receipt}
    facts = []
    rows = [compact]
    if variant in {"canonical_then_compact", "compact_then_canonical"}:
        facts = service._read_execution_control_plane_facts(workspace=tmp_path, run_ids=(attempt.run_id,))
        rows = service.build_run_ledger_projection(facts)["tool_lifecycle"]["events"]
        assert len(rows) == 1
    projection = {"tool_lifecycle": {"events": rows}}
    assert _terminal_failure_has_closed_dispatches(
        tasks, projection, facts, owners, committed_turn_receipts=proof, workspace=str(tmp_path)
    )
    duplicate = deepcopy(compact)
    if variant == "compact_ref_annotation":
        duplicate["receipt"]["batch_receipt_refs"][0]["non_authoritative_note"] = "same batch/hash"
    if variant == "compact_then_canonical":
        rows.insert(0, duplicate)
    else:
        rows.append(duplicate)
    assert not _terminal_failure_has_closed_dispatches(
        tasks, projection, facts, owners, committed_turn_receipts=proof, workspace=str(tmp_path)
    )


def test_distinct_canonical_refs_are_not_collapsed_by_equal_turn_id(tmp_path: Path) -> None:
    from polaris.cells.control_plane.run_ledger.public.settlement_barrier import _canonical_cleanup_lifecycle_events

    _, projection, facts = _cleanup_witness_inputs(tmp_path, second_dispatch=True, same_turn=True)
    events = projection["tool_lifecycle"]["events"]
    assert len(events) == 2 and events[0]["turn_id"] == events[1]["turn_id"]
    assert events[0]["batch_receipt_refs"] != events[1]["batch_receipt_refs"]
    # This isolates projection/inventory binding. Existing downstream batch and
    # TaskRuntime guards still decide whether a complete resource closeout exists.
    bound = _canonical_cleanup_lifecycle_events(events, facts, committed_turn_receipts=(), workspace=str(tmp_path))
    assert bound is not None and len(bound) == 2
