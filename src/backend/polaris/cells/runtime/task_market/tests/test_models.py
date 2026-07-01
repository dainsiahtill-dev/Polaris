from __future__ import annotations

import pytest
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord


def _valid_record_payload() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "trace_id": "trace-1",
        "run_id": "run-1",
        "workspace": "/tmp/ws",
        "stage": "pending_exec",
        "status": "pending_exec",
        "priority": "medium",
        "payload": {"goal": "run"},
    }


@pytest.mark.parametrize(
    "field_name",
    ["task_id", "trace_id", "run_id", "workspace", "stage", "status"],
)
def test_from_dict_requires_core_identity_and_state_fields(field_name: str) -> None:
    payload = _valid_record_payload()
    payload.pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        TaskWorkItemRecord.from_dict(payload)


@pytest.mark.parametrize("stage", ["", "pending_unknown", "resolved"])
def test_from_dict_rejects_invalid_stage(stage: str) -> None:
    payload = _valid_record_payload()
    payload["stage"] = stage

    with pytest.raises(ValueError, match="stage"):
        TaskWorkItemRecord.from_dict(payload)


@pytest.mark.parametrize("status", ["", "pending_unknown", "complete"])
def test_from_dict_rejects_invalid_status(status: str) -> None:
    payload = _valid_record_payload()
    payload["status"] = status

    with pytest.raises(ValueError, match="status"):
        TaskWorkItemRecord.from_dict(payload)


@pytest.mark.parametrize(
    "status",
    ["pending_exec", "in_execution", "resolved", "rejected", "dead_letter"],
)
def test_from_dict_accepts_known_work_item_statuses(status: str) -> None:
    payload = _valid_record_payload()
    payload["status"] = status

    item = TaskWorkItemRecord.from_dict(payload)

    assert item.task_id == "task-1"
    assert item.stage == "pending_exec"
    assert item.status == status
