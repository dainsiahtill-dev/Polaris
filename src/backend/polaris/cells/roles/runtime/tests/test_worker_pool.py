from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import Any, Callable

from polaris.cells.roles.runtime.internal.worker_pool import (
    AsyncWorker,
    AsyncWorkerConfig,
    Worker,
    WorkerConfig,
    _claim_legacy_ready_task,
    _claim_next_runtime_task,
    _claim_ready_runtime_task,
)


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


# ---------------------------------------------------------------------------
# AST structural regression tests
# ---------------------------------------------------------------------------


def _ast_call_names(func: Callable[..., Any]) -> set[str]:
    """Extract top-level ``Call`` target names from *func*'s source."""
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _ast_body_call_names(func: Callable[..., Any]) -> set[str]:
    """Extract ``Call`` target names from *func*'s direct body statements only.

    Unlike :func:`_ast_call_names` this does NOT walk into nested ``def`` /
    ``class`` / ``lambda`` bodies, so the returned set reflects only calls
    that the function itself makes at its own indentation level.
    """
    source = textwrap.dedent(inspect.getsource(func))
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        names.add(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        names.add(child.func.attr)
    return names


def test_worker_claim_ready_task_delegates_to_runtime_task() -> None:
    """Worker._claim_ready_task must call _claim_ready_runtime_task."""
    calls = _ast_body_call_names(Worker._claim_ready_task)
    assert "_claim_ready_runtime_task" in calls


def test_async_worker_claim_ready_task_delegates_to_runtime_task() -> None:
    """AsyncWorker._claim_ready_task must call _claim_ready_runtime_task."""
    calls = _ast_body_call_names(AsyncWorker._claim_ready_task)
    assert "_claim_ready_runtime_task" in calls


def test_claim_ready_runtime_task_prioritizes_atomic_claim() -> None:
    """_claim_ready_runtime_task must call _claim_next_runtime_task (atomic path).

    It MUST NOT directly call list_ready_task_rows or claim_execution.
    """
    calls = _ast_body_call_names(_claim_ready_runtime_task)
    assert "_claim_next_runtime_task" in calls
    assert "list_ready_task_rows" not in calls
    assert "claim_execution" not in calls


def test_claim_next_runtime_task_uses_claim_next_execution_only() -> None:
    """_claim_next_runtime_task must call claim_next_execution.

    It MUST NOT call list_ready_task_rows or claim_execution.
    """
    calls = _ast_body_call_names(_claim_next_runtime_task)
    assert "claim_next_execution" in calls
    assert "list_ready_task_rows" not in calls
    assert "claim_execution" not in calls


def test_claim_legacy_ready_task_uses_legacy_pair() -> None:
    """_claim_legacy_ready_task must use list_ready_task_rows + claim_execution."""
    calls = _ast_body_call_names(_claim_legacy_ready_task)
    assert "list_ready_task_rows" in calls
    assert "claim_execution" in calls


# ---------------------------------------------------------------------------
# Behavioral regression tests — spy-doubles record actual call paths
# ---------------------------------------------------------------------------


class _RecordingClaimNextRuntime:
    """Runtime with claim_next_execution; records all method calls in order."""

    def __init__(self, task_id: int = 61) -> None:
        self.task_id = task_id
        self.call_log: list[str] = []

    def claim_next_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.call_log.append("claim_next_execution")
        return {
            "success": True,
            "task": _runtime_task_row(self.task_id),
            "session": {"session_id": f"session-{self.task_id}"},
            "attempts": [{"task_id": self.task_id, "success": True}],
            "reason": "",
        }

    def list_ready_task_rows(self) -> list[dict[str, Any]]:
        self.call_log.append("list_ready_task_rows")
        return []

    def claim_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        self.call_log.append("claim_execution")
        return {"success": False}

    def complete_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}

    def fail_execution(self, task_id: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True}


def test_sync_worker_atomic_path_never_probes_ready_rows(tmp_path: Path) -> None:
    """When claim_next_execution succeeds, Worker must never call
    list_ready_task_rows or claim_execution."""
    runtime = _RecordingClaimNextRuntime(task_id=61)
    worker = Worker(
        WorkerConfig(worker_id="spy-sync", work_dir=tmp_path),
        task_runtime=runtime,
    )

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 61
    assert runtime.call_log == ["claim_next_execution"]
    assert "list_ready_task_rows" not in runtime.call_log
    assert "claim_execution" not in runtime.call_log


def test_async_worker_atomic_path_never_probes_ready_rows(tmp_path: Path) -> None:
    """When claim_next_execution succeeds, AsyncWorker must never call
    list_ready_task_rows or claim_execution."""
    runtime = _RecordingClaimNextRuntime(task_id=63)
    worker = AsyncWorker(
        AsyncWorkerConfig(worker_id="spy-async", work_dir=tmp_path),
        task_runtime=runtime,
    )

    task = worker._claim_ready_task()

    assert task is not None
    assert task.task_id == 63
    assert runtime.call_log == ["claim_next_execution"]
    assert "list_ready_task_rows" not in runtime.call_log
    assert "claim_execution" not in runtime.call_log


def test_claim_ready_runtime_task_falls_back_to_legacy_when_atomic_absent() -> None:
    """_claim_ready_runtime_task must fall back to _claim_legacy_ready_task
    only when claim_next_execution is not present on the runtime."""
    runtime = _LegacyReadyRowsRuntime(task_id=71)
    task = _claim_ready_runtime_task(
        runtime,
        worker_id="unit",
        role_id="worker_pool",
        selection_source="test",
        work_dir=Path("/tmp"),
    )
    assert task is not None
    assert task.task_id == 71
    assert runtime.ready_row_reads == 1
    assert runtime.claim_calls[0]["task_id"] == 71


def test_claim_next_runtime_task_skips_legacy_when_atomic_available() -> None:
    """_claim_next_runtime_task must return (True, ...) when
    claim_next_execution is present, never falling through to legacy."""
    runtime = _RecordingClaimNextRuntime(task_id=73)
    attempted, task = _claim_next_runtime_task(
        runtime,
        worker_id="unit",
        role_id="worker_pool",
        selection_source="test",
        work_dir=Path("/tmp"),
    )
    assert attempted is True
    assert task is not None
    assert task.task_id == 73
    assert runtime.call_log == ["claim_next_execution"]
    assert "list_ready_task_rows" not in runtime.call_log
    assert "claim_execution" not in runtime.call_log


def test_claim_next_runtime_task_returns_false_when_no_atomic_method() -> None:
    """_claim_next_runtime_task must return (False, None) when
    claim_next_execution is absent, signaling the caller to use legacy."""
    runtime = _LegacyReadyRowsRuntime(task_id=77)
    attempted, task = _claim_next_runtime_task(
        runtime,
        worker_id="unit",
        role_id="worker_pool",
        selection_source="test",
        work_dir=Path("/tmp"),
    )
    assert attempted is False
    assert task is None
