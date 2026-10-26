"""Paired regression coverage for observable fact lease expiry.

Boundary:
    These tests only cover the read-only temporal projection of a canonical
    ``task_runtime.execution`` fact.  They never write a durable fact, never
    append an event, never replay an effect, and never write a session file
    as a way of manufacturing expiry.  Expiry is produced by the real public
    owner with a short lease, so the owner-created fact and the observation
    instant correspond.

Paired controls required by the owner ruling:
    1. A still-valid canonical fact lease stays authoritative even when a
       non-authoritative session file was tampered to an expired value.
    2. A genuinely expired canonical fact lease is not counted as running.
    3. The read model does not persist status and does not automatically
       replay an effect; legal owner recovery is checked separately.
    4. Task / run / namespace identity is retained across the projection.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    project_task_row_from_execution_fact_payload,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_runtime_path


def _bootstrap_workspace(workspace: Path) -> None:
    """Establish the private FactStream authority through the public owner."""
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="observable_fact_lease_expiry_test",
        )
    )


def _claim(
    workspace: Path,
    *,
    lease_ttl_seconds: int,
    external_task_id: str,
) -> tuple[TaskRuntimeService, dict[str, object]]:
    """Create and claim one task through the real public owner."""
    service = TaskRuntimeService(str(workspace))
    task = service.create_task_row(
        subject="Lease expiry subject",
        description="Observable fact lease expiry coverage",
        metadata={"pm_task_id": external_task_id},
    )
    claimed = service.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-lease-expiry",
        lease_ttl_seconds=lease_ttl_seconds,
        selection_source="unit",
        external_task_id=external_task_id,
    )
    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    return service, dict(task)


def _session_file(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.session.json"))


def _canonical_fact(service: TaskRuntimeService) -> dict[str, object]:
    """Return the canonical fact payload the read model is projected from."""
    rows = service.list_task_rows_from_execution_facts()
    assert rows
    metadata = rows[0].get("metadata")
    assert isinstance(metadata, dict)
    fact = metadata.get("task_runtime_execution_fact")
    assert isinstance(fact, dict)
    return fact


def test_valid_fact_lease_is_not_overridden_by_tampered_session_file(tmp_path: Path) -> None:
    """A still-valid fact lease stays authoritative over a tampered session file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-valid")

    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    assert fact_lease

    # Control: tamper the NON-authoritative session file to an expired value.
    # The canonical fact must remain the only authority.
    session_path = _session_file(workspace, task["id"])
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["lease_expires_at"] = "2020-01-01T00:00:00+00:00"
    payload["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
    session_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = service.list_observable_task_rows()
    assert len(rows) == 1
    row = rows[0]

    assert row["status"] == "in_progress"
    assert row["running"] is True
    assert row["claimed_by"] == "director"
    assert row["lease_expires_at"] == fact_lease
    metadata = row["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["status_source"] == "task_runtime.execution_fact"
    runtime_execution = metadata["runtime_execution"]
    assert runtime_execution["effective_status"] == "in_progress"


def test_expired_fact_lease_is_not_counted_as_running(tmp_path: Path) -> None:
    """A genuinely expired canonical fact lease must not count as running work."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=1, external_task_id="PM-expired")

    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    assert fact_lease

    # Wait for the owner-created fact's own lease deadline to pass. No file is
    # edited and no durable fact is written; the deadline belongs to the fact.
    deadline = datetime.fromisoformat(fact_lease)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        time.sleep(remaining + 0.2)

    rows = service.list_observable_task_rows()
    assert len(rows) == 1
    row = rows[0]

    assert row["status"] == "pending"
    assert row["running"] is False
    assert row["claimed_by"] == ""
    assert row["resume_state"] == "resumable"
    metadata = row["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["status_source"] == "task_runtime.execution_fact"
    runtime_execution = metadata["runtime_execution"]
    assert runtime_execution["effective_status"] == "pending"
    assert runtime_execution["resume_state"] == "resumable"
    assert runtime_execution["raw_status"] == "in_progress"


def test_expired_fact_lease_retains_task_run_and_namespace_identity(tmp_path: Path) -> None:
    """The temporal projection must not change task, run or namespace identity."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=1, external_task_id="PM-identity")

    before = service.list_observable_task_rows()[0]
    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    deadline = datetime.fromisoformat(fact_lease)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        time.sleep(remaining + 0.2)

    after = service.list_observable_task_rows()[0]

    assert after["task_id"] == before["task_id"]
    assert after["id"] == before["id"]
    assert after["session_id"] == before["session_id"]
    assert after["workflow_run_id"] == before["workflow_run_id"] == "run-lease-expiry"
    assert after.get("external_task_id", True)
    metadata = after["metadata"]
    assert isinstance(metadata, dict)
    fact_metadata = metadata["task_runtime_execution_fact"]
    assert isinstance(fact_metadata, dict)
    assert str(fact_metadata["lease_expires_at"]) == fact_lease
    assert str(fact_metadata["run_id"]) == "run-lease-expiry"
    assert str(fact_metadata["task_id"]) == str(before["task_id"])


def test_expired_fact_projection_does_not_persist_status_or_replay_effect(tmp_path: Path) -> None:
    """Reading an expired fact must not persist status nor replay an effect."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=1, external_task_id="PM-readonly")

    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    deadline = datetime.fromisoformat(fact_lease)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        time.sleep(remaining + 0.2)

    stream_head_before = service._query_execution_fact_stream_head()
    row = service.list_observable_task_rows()[0]
    assert row["running"] is False

    # The read model must not append a fact, and the durable fact stream must
    # keep exactly the owner-created claim fact.
    assert service._query_execution_fact_stream_head() == stream_head_before
    facts = service.list_task_rows_from_execution_facts()
    assert len(facts) == 1
    canonical = facts[0]
    metadata = canonical.get("metadata")
    assert isinstance(metadata, dict)
    fact_metadata = metadata["task_runtime_execution_fact"]
    assert isinstance(fact_metadata, dict)
    assert str(fact_metadata["lease_expires_at"]) == fact_lease
    assert str(fact_metadata["execution_state"]) == "in_progress"


def test_valid_fact_projection_still_reports_running_work(tmp_path: Path) -> None:
    """A valid lease keeps the legal running projection; the fix is not a blanket demotion."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-running")

    row = service.list_observable_task_rows()[0]

    assert row["status"] == "in_progress"
    assert row["running"] is True
    assert row["claimed_by"] == "director"


def test_legal_owner_recovery_after_expiry_is_still_available(tmp_path: Path) -> None:
    """A valid legal advance of the owner remains observable after the projection."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, task = _claim(workspace, lease_ttl_seconds=1, external_task_id="PM-recovery")

    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    deadline = datetime.fromisoformat(fact_lease)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        time.sleep(remaining + 0.2)

    assert service.list_observable_task_rows()[0]["running"] is False

    # The legal owner path can still advance this task: a re-claim is a real
    # owner action, not an automatic replay by the read model.
    reclaimed = service.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-lease-expiry",
        lease_ttl_seconds=600,
        selection_source="unit",
        external_task_id="PM-recovery",
    )
    assert reclaimed["success"] is True

    row = service.list_observable_task_rows()[0]
    assert row["running"] is True
    assert row["status"] == "in_progress"


def test_fact_projection_with_explicit_observation_instant(tmp_path: Path) -> None:
    """An explicit observation instant evaluates the same canonical fact lease."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-explicit")

    fact = _canonical_fact(service)
    fact_lease = str(fact.get("lease_expires_at"))
    lease_deadline = datetime.fromisoformat(fact_lease)

    before_deadline = lease_deadline - timedelta(seconds=30)
    after_deadline = lease_deadline + timedelta(seconds=30)

    still_valid = project_task_row_from_execution_fact_payload(dict(fact), now=before_deadline)
    assert still_valid["status"] == "in_progress"
    assert still_valid["running"] is True

    expired = project_task_row_from_execution_fact_payload(dict(fact), now=after_deadline)
    assert expired["status"] == "pending"
    assert expired["running"] is False
    assert expired["claimed_by"] == ""


@pytest.mark.parametrize(
    "naive_deadline",
    [
        "2026-10-26T11:00:00",
        "2026-10-26 11:00:00",
        "2026-10-26",
    ],
)
def test_naive_fact_deadline_stays_unknown_without_raising(
    tmp_path: Path,
    naive_deadline: str,
) -> None:
    """A deadline without an offset is unknown: no crash, no demotion.

    The canonical owner always writes an aware deadline, but a historical or
    malformed fact must not make the new read path raise.  A naive deadline
    cannot be compared with an absolute instant, and it must never be read in
    the local timezone.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-naive")

    fact = dict(_canonical_fact(service))
    fact["lease_expires_at"] = naive_deadline

    projected = project_task_row_from_execution_fact_payload(
        fact,
        now=datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Unknown deadline: the fact's own status is preserved, nothing is demoted.
    assert projected["status"] == "in_progress"
    assert projected["running"] is True
    assert projected["claimed_by"] == "director"


def test_aware_fact_deadline_still_demotes_after_the_offset_check(
    tmp_path: Path,
) -> None:
    """The comparability check must not break the aware-deadline demotion."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-aware")

    fact = dict(_canonical_fact(service))
    fact["lease_expires_at"] = "2026-10-26T13:00:00+02:00"

    expired = project_task_row_from_execution_fact_payload(
        fact,
        now=datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert expired["status"] == "pending"
    assert expired["running"] is False

    still_valid = project_task_row_from_execution_fact_payload(
        fact,
        now=datetime(2026, 10, 26, 10, 0, 0, tzinfo=timezone.utc),
    )
    assert still_valid["status"] == "in_progress"
    assert still_valid["running"] is True


def test_naive_observation_instant_is_not_read_in_the_local_timezone(
    tmp_path: Path,
) -> None:
    """An explicit observation instant without a timezone refuses the comparison."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-naive-now")

    fact = dict(_canonical_fact(service))
    fact["lease_expires_at"] = "2026-10-26T11:00:00+00:00"

    # A naive observation instant is not an absolute instant. The local
    # timezone must not be silently assumed, so the comparison is refused and
    # the fact's own status is kept.
    projected = project_task_row_from_execution_fact_payload(
        fact,
        now=datetime(2026, 10, 26, 12, 0, 0),
    )

    assert projected["status"] == "in_progress"
    assert projected["running"] is True
    assert projected["claimed_by"] == "director"


@pytest.mark.parametrize("missing_lease", ["", "not-a-timestamp"])
def test_fact_projection_keeps_fact_status_when_deadline_is_unknown(
    tmp_path: Path,
    missing_lease: str,
) -> None:
    """An unknown deadline is unknown, not an assumed expiry."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    service, _task = _claim(workspace, lease_ttl_seconds=600, external_task_id="PM-unknown")

    fact = dict(_canonical_fact(service))
    fact["lease_expires_at"] = missing_lease

    projected = project_task_row_from_execution_fact_payload(
        fact,
        now=datetime.now(timezone.utc) + timedelta(days=365),
    )
    assert projected["status"] == "in_progress"
    assert projected["running"] is True
