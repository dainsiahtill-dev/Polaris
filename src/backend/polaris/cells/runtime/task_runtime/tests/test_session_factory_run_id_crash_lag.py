from __future__ import annotations

from polaris.cells.runtime.task_runtime.internal.execution_session import TaskExecutionSession
from polaris.cells.runtime.task_runtime.internal.service._mixin_directed_effect import (
    _DirectedEffectMixin,
)


def test_session_factory_run_id_uses_owner_row_when_claim_attempt_lags() -> None:
    """L2-12 SIGSEGV: session persisted, TaskBoard claim_attempt lagged."""

    session = TaskExecutionSession.create(
        task_id=271,
        role_id="director",
        worker_id="director",
        run_id="director-1eb8571d4858",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="claim",
        selection_source="task_id_lookup",
        external_task_id="TASK-3-source-modules",
        metadata={},
    )
    row = {
        "id": 271,
        "status": "in_progress",
        "metadata": {"factory_run_id": "factory_a1b49b0460f2"},
    }

    owner = _DirectedEffectMixin._session_factory_run_id(session, row)

    assert owner == "factory_a1b49b0460f2"
