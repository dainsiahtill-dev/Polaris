from __future__ import annotations

from polaris.cells.roles.kernel.public.deferred_repair_commit_service import (
    _normalize_capability_token,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1


def _execution_attempt() -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=1,
        external_task_id="TASK-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="director-worker",
        run_id="run-1",
        lease_expires_at="2030-01-01T00:00:00+00:00",
    )


def test_capability_token_cannot_be_synthesized_from_execution_attempt() -> None:
    attempt = _execution_attempt()

    assert _normalize_capability_token(None, execution_attempt=attempt) == {}
    assert (
        _normalize_capability_token(
            {
                "token_id": "job-1",
                "capability_audit": {"ok": True},
            },
            execution_attempt=attempt,
        )
        == {}
    )


def test_audited_capability_token_with_envelope_is_preserved() -> None:
    attempt = _execution_attempt()

    normalized = _normalize_capability_token(
        {
            "token_id": "job-1",
            "execution_envelope_hash": "a" * 64,
            "capability_audit": {"ok": True},
            "allowed_commands": ["cargo check"],
            "source": "control_plane.job_token",
        },
        execution_attempt=attempt,
    )

    assert normalized == {
        "token_id": "job-1",
        "execution_envelope_hash": "a" * 64,
        "capability_audit_ok": True,
        "allowed_commands": ("cargo check",),
        "source": "control_plane.job_token",
        "run_id": "run-1",
        "stage": "director_materialization_deferred",
    }
