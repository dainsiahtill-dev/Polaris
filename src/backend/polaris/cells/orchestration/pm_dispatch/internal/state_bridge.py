"""TaskBoard <-> workflow runtime state bridge.

This module is a concrete Polaris implementation (not a compatibility shim).
It provides:
1. lightweight async task-state sync from TaskBoard notifications
2. unified task status query surface
3. consistency checking between TaskBoard and workflow runtime store
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.utils import _now

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return _now()


def _normalize_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"in_progress", "running"}:
        return "running"
    if token in {"done"}:
        return "completed"
    return token or "pending"


@dataclass(slots=True)
class StateSyncEvent:
    """Internal event used by TaskBoardStateBridge."""

    event_type: str
    task_id: str
    status: str
    workflow_id: str
    task_type: str = "taskboard.task"
    handler_name: str = "task_board"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)


class TaskBoardStateBridge:
    """Async bridge that syncs TaskBoard events into workflow runtime state."""

    def __init__(
        self,
        task_board: Any,
        workflow_store: Any | None = None,
        *,
        default_workflow_id: str = "taskboard-default",
        flush_interval_seconds: float = 0.05,
    ) -> None:
        self._task_board = task_board
        self._workflow_store = workflow_store
        self._default_workflow_id = str(default_workflow_id or "taskboard-default").strip()
        self._flush_interval_seconds = max(0.01, float(flush_interval_seconds))
        self._pending_events: list[StateSyncEvent] = []
        self._pending_lock = threading.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._run_worker(), name="taskboard-state-bridge")

    async def stop(self) -> None:
        self._running = False
        worker = self._worker_task
        self._worker_task = None
        if worker is None:
            return
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    def notify_task_created(
        self,
        task_id: int | str,
        *,
        subject: str = "",
        status: str = "pending",
        blocked_by: list[int] | None = None,
        workflow_id: str | None = None,
    ) -> None:
        self._enqueue(
            StateSyncEvent(
                event_type="task_created",
                task_id=str(task_id),
                status=_normalize_status(status),
                workflow_id=self._workflow_id_or_default(workflow_id),
                metadata={
                    "subject": str(subject or "").strip(),
                    "blocked_by": list(blocked_by or []),
                },
            )
        )

    def notify_task_updated(
        self,
        *,
        task_id: int | str,
        status: str,
        workflow_id: str | None = None,
    ) -> None:
        self._enqueue(
            StateSyncEvent(
                event_type="task_updated",
                task_id=str(task_id),
                status=_normalize_status(status),
                workflow_id=self._workflow_id_or_default(workflow_id),
            )
        )

    def notify_task_completed(
        self,
        *,
        task_id: int | str,
        result_summary: str = "",
        workflow_id: str | None = None,
    ) -> None:
        self._enqueue(
            StateSyncEvent(
                event_type="task_completed",
                task_id=str(task_id),
                status="completed",
                workflow_id=self._workflow_id_or_default(workflow_id),
                metadata={"result_summary": str(result_summary or "").strip()},
            )
        )

    async def get_unified_task_status(
        self,
        task_id: str,
    ) -> dict[str, Any] | None:
        task_token = str(task_id or "").strip()
        if not task_token:
            return None

        task_board_payload: dict[str, Any] | None = None
        if hasattr(self._task_board, "get_task"):
            task_board_payload = self._task_board.get_task(task_token)
        if task_board_payload is None:
            return None

        sources: dict[str, Any] = {"task_board": task_board_payload}
        workflow_payload: dict[str, Any] | None = None

        if self._workflow_store is not None:
            workflow_payload = await self._find_workflow_state(task_token)
            if workflow_payload is not None:
                sources["workflow"] = workflow_payload

        result_status = _normalize_status(task_board_payload.get("status"))
        if workflow_payload is not None:
            workflow_status = _normalize_status(workflow_payload.get("status"))
            if workflow_status:
                result_status = workflow_status

        return {
            "task_id": str(task_board_payload.get("id", task_token)),
            "status": result_status,
            "sources": sources,
        }

    async def _find_workflow_state(self, task_id: str) -> dict[str, Any] | None:
        store = self._workflow_store
        if store is None:
            return None
        try:
            executions = await store.list_workflows(limit=200)
        except (RuntimeError, ValueError) as exc:
            logger.debug("State bridge failed to list workflows: %s", exc)
            return None

        for execution in executions:
            workflow_id = str(getattr(execution, "workflow_id", "") or "").strip()
            if not workflow_id:
                continue
            try:
                states = await store.list_task_states(workflow_id)
            except (RuntimeError, ValueError):
                continue
            for item in states:
                if str(getattr(item, "task_id", "")).strip() == task_id:
                    return {
                        "workflow_id": workflow_id,
                        "status": str(getattr(item, "status", "") or "").strip(),
                        "attempt": int(getattr(item, "attempt", 0) or 0),
                        "max_attempts": int(getattr(item, "max_attempts", 0) or 0),
                        "updated_at": str(getattr(item, "updated_at", "") or "").strip(),
                    }
        return None

    def _workflow_id_or_default(self, workflow_id: str | None) -> str:
        token = str(workflow_id or "").strip()
        return token or self._default_workflow_id

    def _enqueue(self, event: StateSyncEvent) -> None:
        with self._pending_lock:
            self._pending_events.append(event)

    async def _run_worker(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval_seconds)
            await self._flush_once()

    async def _flush_once(self) -> None:
        if self._workflow_store is None:
            with self._pending_lock:
                self._pending_events.clear()
            return

        with self._pending_lock:
            batch = self._pending_events[:]
            self._pending_events.clear()

        if not batch:
            return

        for event in batch:
            try:
                ended_at = event.created_at if event.status in {"completed", "failed", "cancelled"} else None
                await self._workflow_store.upsert_task_state(
                    workflow_id=event.workflow_id,
                    task_id=event.task_id,
                    task_type=event.task_type,
                    handler_name=event.handler_name,
                    status=event.status,
                    attempt=1,
                    max_attempts=1,
                    started_at=event.created_at,
                    ended_at=ended_at,
                    result={"event_type": event.event_type, **event.metadata},
                    error="",
                    metadata={"bridge_event_type": event.event_type, **event.metadata},
                )
            except (RuntimeError, ValueError) as exc:
                logger.debug("State bridge flush failed for task %s: %s", event.task_id, exc)


class StateConsistencyChecker:
    """Checks consistency between TaskBoard and workflow runtime task states."""

    def __init__(self, task_board: Any, workflow_store: Any) -> None:
        self._task_board = task_board
        self._workflow_store = workflow_store

    async def check_consistency(self, workflow_id: str) -> dict[str, Any]:
        board_tasks = list(self._task_board.list_all() or [])
        workflow_states = await self._workflow_store.list_task_states(str(workflow_id))

        board_map: dict[str, Any] = {str(task.id): task for task in board_tasks if hasattr(task, "id")}
        workflow_map: dict[str, Any] = {
            str(getattr(state, "task_id", "")): state for state in workflow_states if getattr(state, "task_id", None)
        }

        inconsistencies: list[dict[str, Any]] = []

        missing_in_workflow = [task_id for task_id in board_map if task_id not in workflow_map]
        for task_id in missing_in_workflow:
            inconsistencies.append({"type": "missing_in_workflow", "task_id": task_id})

        missing_in_task_board = [task_id for task_id in workflow_map if task_id not in board_map]
        for task_id in missing_in_task_board:
            inconsistencies.append({"type": "missing_in_task_board", "task_id": task_id})

        status_mismatch = 0
        for task_id, task in board_map.items():
            state = workflow_map.get(task_id)
            if state is None:
                continue
            board_status = _normalize_status(getattr(task, "status", ""))
            workflow_status = _normalize_status(getattr(state, "status", ""))
            if board_status != workflow_status:
                status_mismatch += 1
                inconsistencies.append(
                    {
                        "type": "status_mismatch",
                        "task_id": task_id,
                        "task_board_status": board_status,
                        "workflow_status": workflow_status,
                    }
                )

        summary = {
            "checked": len(board_map),
            "missing_in_workflow": len(missing_in_workflow),
            "missing_in_task_board": len(missing_in_task_board),
            "status_mismatch": status_mismatch,
        }

        return {
            "consistent": len(inconsistencies) == 0,
            "summary": summary,
            "inconsistencies": inconsistencies,
        }
