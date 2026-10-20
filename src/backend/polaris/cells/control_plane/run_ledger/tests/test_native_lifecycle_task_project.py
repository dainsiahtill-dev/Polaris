"""Private public-producer fixtures for native task-scoped lifecycle projects."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
)
from polaris.cells.control_plane.run_ledger.tests.test_canonical_task_identity import (
    ALIAS,
    CHILD,
    FACTORY,
    PROJECT,
    _bootstrap,
    _cp,
    _native,
    _query,
)
from polaris.cells.events.fact_stream.public import AppendFactEventCommandV1, append_fact_event


@pytest.mark.parametrize(
    "defect",
    [
        "",
        "foreign_project",
        "foreign_token_task",
        "foreign_token_run",
        "foreign_event_project",
        "compact_only",
        "wrong_event",
    ],
)
def test_public_native_task_lifecycle_scope_requires_exact_captured_owner(tmp_path: Path, defect: str) -> None:
    if defect == "compact_only":
        _bootstrap(tmp_path)
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="created",
                source="runtime.task_runtime",
                run_id=CHILD,
                task_id=ALIAS,
                strict_integrity=True,
                payload={
                    "run_id": CHILD,
                    "task_id": ALIAS,
                    "factory_run_id": FACTORY,
                    "factory_bench_project_id": PROJECT,
                },
            )
        )
    else:
        _native(tmp_path)
    _cp(tmp_path)
    receipt = build_tool_call_lifecycle_receipt(
        run_id=CHILD,
        task_id=ALIAS,
        turn_id="private-task-project-turn",
        role="director",
        dispatch_status="not_requested",
    )
    appended = append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id=CHILD,
            task_id=ALIAS,
            turn_id="private-task-project-turn",
            role="director",
            lifecycle_receipt=receipt.to_dict(),
            project_id=ALIAS,
        )
    )
    if defect and defect != "compact_only":
        event = dict(appended.receipt["event"])
        event["job_token"] = dict(event["job_token"])
        if defect == "foreign_project":
            event["job_token"]["project_id"] = "foreign-project"
        elif defect == "foreign_token_task":
            event["job_token"]["task_id"] = "foreign-task"
        elif defect == "foreign_token_run":
            event["job_token"]["run_id"] = "foreign-run"
        elif defect == "foreign_event_project":
            event["project_id"] = "foreign-project"
        elif defect == "wrong_event":
            event["event_type"] = "gate_evaluated"
            event["gate"] = {"name": "private-gate", "ok": True}
        for key in ("event_id", "content_id", "append_id", "recorded_at"):
            event.pop(key, None)
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=CHILD, event=event))
    if defect:
        with pytest.raises(ValueError, match="canonical_"):
            _query(tmp_path)
    else:
        projection = _query(tmp_path)
        assert projection["canonical_evidence"]["strict_integrity"] is True
        assert projection["consumed_run_ids"] == [FACTORY, CHILD]
        assert projection["ok"] is False
        assert projection["task_boundary"]["latest_by_task"][ALIAS]["ok"] is False


@pytest.mark.parametrize(
    "defect", ["", "foreign_parent", "foreign_project", "foreign_task", "wrong_event", "foreign_event_project"]
)
def test_supplied_factory_token_retains_its_authority_run_beside_child_execution_run(
    tmp_path: Path, defect: str
) -> None:
    _native(tmp_path)
    _cp(tmp_path)
    token = {"run_id": FACTORY, "task_id": ALIAS, "project_id": PROJECT}
    if defect == "foreign_parent":
        token["run_id"] = "foreign-factory"
    elif defect == "foreign_project":
        token["project_id"] = "foreign-project"
    elif defect == "foreign_task":
        token["task_id"] = "foreign-task"
    receipt = build_tool_call_lifecycle_receipt(
        run_id=CHILD,
        task_id=ALIAS,
        turn_id="private-parent-token-turn",
        role="director",
        dispatch_status="not_requested",
    )
    appended = append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(tmp_path),
            run_id=CHILD,
            task_id=ALIAS,
            turn_id="private-parent-token-turn",
            role="director",
            lifecycle_receipt=receipt.to_dict(),
            job_token=token,
        )
    )
    event = appended.receipt["event"]
    assert event["run_id"] == CHILD
    assert event["job_token"]["run_id"] == token["run_id"]
    if defect in {"wrong_event", "foreign_event_project"}:
        event = dict(event)
        if defect == "wrong_event":
            event.update(event_type="gate_evaluated", gate={"ok": True})
        else:
            event["project_id"] = "foreign-project"
        for key in ("event_id", "content_id", "append_id", "recorded_at"):
            event.pop(key, None)
        append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(tmp_path), run_id=CHILD, event=event))
    if defect:
        with pytest.raises(ValueError, match="canonical_"):
            _query(tmp_path)
    else:
        projection = _query(tmp_path)
        assert projection["canonical_evidence"]["strict_integrity"] is True
        assert projection["consumed_run_ids"] == [FACTORY, CHILD]
        assert projection["ok"] is False
