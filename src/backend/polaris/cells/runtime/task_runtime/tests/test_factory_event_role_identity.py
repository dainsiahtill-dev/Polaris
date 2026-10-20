"""Producer regressions for the native R02 CE-as-Director runtime event defect."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_runtime.internal.execution_session import TaskExecutionSession
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

FACTORY_RUN_ID = "factory-role-event-regression"


@pytest.fixture
def producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TaskRuntimeService, list[dict[str, Any]]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="task-runtime-role-event-regression",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    published: list[dict[str, Any]] = []

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "envelope": deepcopy(payload)})
            return True

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(publisher_module, "get_log_jetstream_publisher", lambda: Publisher())
    return TaskRuntimeService(str(workspace)), published


def claim_role(
    producer: tuple[TaskRuntimeService, list[dict[str, Any]]], role: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    service, published = producer
    # The misleading subject/metadata must never override the claimed session role.
    created = service.create_task_row(
        subject="Director task: Chief Engineer portfolio review",
        metadata={"factory_run_id": FACTORY_RUN_ID, "role": "director"},
    )
    claim = service.claim_execution(
        created["id"],
        worker_id="role-event-worker",
        role_id=role,
        run_id="role-execution-run",
        external_task_id="CE-PORTFOLIO-role-event-regression",
        selection_source="role-event-regression",
    )
    assert claim["success"] is True
    envelope = published[-1]["envelope"]
    assert envelope["payload"]["event_type"] == "claimed"
    return claim, envelope


@pytest.mark.parametrize(
    ("role", "stage", "message_prefix"),
    [
        ("chief_engineer", "chief_engineer_review", "Chief Engineer review task"),
        ("director", "director_dispatch", "Director task"),
        ("pm", "pm_planning", "PM task"),
        ("qa", "quality_gate", "QA task"),
    ],
)
def test_claim_producer_projects_bound_execution_role(
    producer: tuple[TaskRuntimeService, list[dict[str, Any]]],
    role: str,
    stage: str,
    message_prefix: str,
) -> None:
    claim, envelope = claim_role(producer, role)
    payload = envelope["payload"]
    assert payload["stage"] == stage
    assert payload["role"] == role
    assert payload["message"] == f"{message_prefix} {claim['session']['task_id']} claimed"
    assert envelope["run_id"] == FACTORY_RUN_ID
    assert envelope["channel"] == f"event.factory:{FACTORY_RUN_ID}"
    assert envelope["cursor"] == claim["execution_event"]["fact_event_seq"]
    assert envelope["event_id"] == claim["execution_event"]["fact_event_id"]
    assert "task_row_snapshot" not in payload
    assert payload["task_row_snapshot_projection"]["status"] == "durable_fact_only"
    if role == "director":
        assert payload["director_run_id"] == "role-execution-run"


def test_sessionless_producer_does_not_infer_director_from_subject_or_metadata(
    producer: tuple[TaskRuntimeService, list[dict[str, Any]]],
) -> None:
    service, published = producer
    created = service.create_task_row(
        subject="Director dispatch claimed",
        metadata={"factory_run_id": FACTORY_RUN_ID, "role": "director"},
    )
    payload = published[-1]["envelope"]["payload"]
    assert created["execution_event"]["ok"] is True
    assert payload["role"] == "unknown"
    assert "stage" not in payload
    assert payload["message"] == f"Runtime task {created['id']} created"


@pytest.mark.parametrize("role", ["unknown", "architect", "director qa", "chief_engineer review"])
def test_unknown_session_roles_publish_neutral_identity(
    producer: tuple[TaskRuntimeService, list[dict[str, Any]]], role: str
) -> None:
    _claim, envelope = claim_role(producer, role)
    payload = envelope["payload"]
    assert payload["role"] == "unknown"
    assert "stage" not in payload
    assert payload["message"].startswith("Runtime task ")


@pytest.mark.parametrize("mismatch", ["task_id", "session_id", "run_id"])
def test_publisher_rejects_role_from_a_different_execution_identity(
    producer: tuple[TaskRuntimeService, list[dict[str, Any]]], mismatch: str
) -> None:
    service, published = producer
    claim, envelope = claim_role(producer, "chief_engineer")
    session = TaskExecutionSession.from_dict(claim["session"])
    payload = deepcopy(envelope["payload"])
    payload[mismatch] = "different-execution"
    payload["role"] = "director"
    payload["message"] = "Director task claimed"
    assert service._publish_factory_execution_event(payload, session=session) is True
    emitted = published[-1]["envelope"]["payload"]
    assert emitted["role"] == "unknown"
    assert "stage" not in emitted
    assert emitted["message"].startswith("Runtime task ")
