from __future__ import annotations

import time

from polaris.cells.runtime.task_market.internal.reconciler import TaskReconciliationLoop


class _FakeService:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile_parent_statuses(self, workspace: str, *, limit: int = 5000) -> dict[str, object]:
        self.calls += 1
        return {"workspace": workspace, "updated": 0, "limit": limit}

    def sweep_escalation_timeouts(self, workspace: str) -> dict[str, object]:
        return {"escalated_count": 0}

    def requeue_drifted_items(self, workspace: str) -> dict[str, object]:
        return {"requeued_count": 0}


def test_reconciler_run_once_invokes_service() -> None:
    service = _FakeService()
    loop = TaskReconciliationLoop(service=service, workspace="/tmp/ws", interval_seconds=5.0)

    result = loop.run_once()
    assert service.calls == 1
    assert result["reconciliation"]["workspace"] == "/tmp/ws"


def test_reconciler_start_stop_runs_background_loop() -> None:
    service = _FakeService()
    loop = TaskReconciliationLoop(service=service, workspace="/tmp/ws", interval_seconds=0.05)

    loop.start()
    time.sleep(0.12)
    loop.stop()

    assert service.calls >= 1
