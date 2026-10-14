from pathlib import Path

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
