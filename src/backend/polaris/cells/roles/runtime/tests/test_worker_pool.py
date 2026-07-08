from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.cells.roles.runtime.internal.worker_pool import (
    AsyncWorker,
    AsyncWorkerConfig,
    Worker,
    WorkerConfig,
)

if TYPE_CHECKING:
    from pathlib import Path

def _runtime_task_row(task_id: int) -> dict[str, Any]:
    return {
        "id": task_id,
        "metadata": {
            "command": "python -c \"print('claimed')\"",
            "env": {"WORKER_POOL_TEST": "1"},
            "timeout": 15,
        },
    }

class _AtomicClaimRuntime:
    def __init__(self, task_id: int = 41) -> None:
        self.task_id = task_id
        self.claim_next_calls: list[dict[str, Any]] = []

    def claim_next_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.claim_next_calls.append(dict(kwargs))
        return {
            "success": True,
            "task": _runtime_task_row(self.task_id),
            "session": {"session_id": f"session-{self.task_id}"},
            "attempts": [{"task_id": self.task_id, "success": True}],
            "reason": "",
        }

    def complete_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}

    def fail_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}

class _LegacyReadyRowsRuntime:
    def __init__(self, task_id: int = 53) -> None:
        self.task_id = task_id
        self.ready_row_reads = 0
        self.claim_calls: list[dict[str, Any]] = []

    def list_ready_task_rows(self) -> list[dict[str, Any]]:
        self.ready_row_reads += 1
        return [_runtime_task_row(self.task_id)]

    def claim_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        self.claim_calls.append({"task_id": task_id, **kwargs})
        return {
            "success": True,
            "task": _runtime_task_row(int(task_id)),
            "session": {"session_id": f"session-{task_id}"},
        }

    def complete_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}

    def fail_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}

def test_sync_worker_claims_atomically_without_ready_row_probe(tmp_path: Path) -> None:
    runtime = _AtomicClaimRuntime(task_id=41)
    worker = Worker(
        WorkerConfig(worker_id="sync-worker", work_dir=tmp_path),
        task_runtime=runtime,
    )

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 41
    assert task.session_id == "session-41"
    assert task.work_dir == tmp_path
    assert task.timeout == 15
    assert task.metadata["env"] == {"WORKER_POOL_TEST": "1"}
    assert runtime.claim_next_calls == [
        {
            "worker_id": "sync-worker",
            "role_id": "worker_pool",
            "lease_ttl_seconds": 120,
            "selection_source": "roles.runtime.worker_pool",
            "prefer_resumable": True,
        }
    ]

def test_async_worker_claims_atomically_without_ready_row_probe(tmp_path: Path) -> None:
    runtime = _AtomicClaimRuntime(task_id=47)
    worker = AsyncWorker(
        AsyncWorkerConfig(worker_id="async-worker", work_dir=tmp_path),
        task_runtime=runtime,
    )

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 47
    assert task.session_id == "session-47"
    assert runtime.claim_next_calls == [
        {
            "worker_id": "async-worker",
            "role_id": "async_worker_pool",
            "lease_ttl_seconds": 120,
            "selection_source": "roles.runtime.async_worker_pool",
            "prefer_resumable": True,
        }
    ]

def test_sync_worker_keeps_legacy_ready_row_fallback_when_claim_next_is_absent(tmp_path: Path) -> None:
    runtime = _LegacyReadyRowsRuntime(task_id=53)
    worker = Worker(
        WorkerConfig(worker_id="legacy-worker", work_dir=tmp_path),
        task_runtime=runtime,
    )

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 53
    assert task.session_id == "session-53"
    assert runtime.ready_row_reads == 1
    assert runtime.claim_calls == [
        {
            "task_id": 53,
            "worker_id": "legacy-worker",
            "role_id": "worker_pool",
            "selection_source": "roles.runtime.worker_pool",
        }
    ]
