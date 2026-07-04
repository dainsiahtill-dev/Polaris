from __future__ import annotations

import pytest
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    TaskExecutionSession,
    is_terminal_session_status,
    terminal_task_status_value_for_session_status,
)


def _valid_session_payload() -> dict[str, object]:
    return {
        "session_id": "tx-1",
        "task_id": 7,
        "role_id": "director",
        "worker_id": "worker-1",
        "run_id": "",
        "status": "active",
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
        "lease_expires_at": "2026-01-01T00:02:00+00:00",
    }


@pytest.mark.parametrize(
    "field_name",
    ["session_id", "task_id", "role_id", "worker_id", "status", "lease_expires_at"],
)
def test_from_dict_requires_core_session_fields(field_name: str) -> None:
    payload = _valid_session_payload()
    payload.pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("task_id", [0, -1, "not-an-int"])
def test_from_dict_rejects_invalid_task_id(task_id: object) -> None:
    payload = _valid_session_payload()
    payload["task_id"] = task_id

    with pytest.raises(ValueError, match="task_id"):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("status", ["", "running", "done"])
def test_from_dict_rejects_invalid_status(status: str) -> None:
    payload = _valid_session_payload()
    payload["status"] = status

    with pytest.raises(ValueError, match="status"):
        TaskExecutionSession.from_dict(payload)


@pytest.mark.parametrize("status", ["active", "completed", "failed", "suspended"])
def test_from_dict_accepts_known_statuses(status: str) -> None:
    payload = _valid_session_payload()
    payload["status"] = status

    session = TaskExecutionSession.from_dict(payload)

    assert session.session_id == "tx-1"
    assert session.task_id == 7
    assert session.status == status
    assert session.run_id == ""


@pytest.mark.parametrize(
    ("session_status", "task_status"),
    [
        ("completed", "completed"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_terminal_session_status_projects_task_status(session_status: str, task_status: str) -> None:
    assert terminal_task_status_value_for_session_status(session_status) == task_status
    assert is_terminal_session_status(session_status) is True


@pytest.mark.parametrize("session_status", ["active", "suspended", "", None])
def test_non_terminal_session_status_has_no_task_status_projection(session_status: object) -> None:
    assert terminal_task_status_value_for_session_status(session_status) == ""
    assert is_terminal_session_status(session_status) is False
