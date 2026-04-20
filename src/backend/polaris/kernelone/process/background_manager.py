"""Background command manager v2 with queue and concurrency control.

Phase 2 implementation from learn-claude-code integration.
Adds: QUEUED state, max_concurrent limit, wait with on_timeout decision.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from queue import Queue as ThreadQueue
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS, MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.fs.text_ops import append_text_atomic, write_json_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.storage import resolve_runtime_path

if TYPE_CHECKING:
    import builtins

logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}
_PROCESS_REGISTRY: dict[str, subprocess.Popen] = {}
_PROCESS_LOCK = threading.RLock()


class BackgroundTaskState(Enum):
    """Task lifecycle states."""

    QUEUED = "queued"  # Waiting for concurrency slot
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """Background task with full metadata."""

    id: str
    command: str
    status: BackgroundTaskState
    created_at: float
    cwd: str = "."
    timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    output_preview: str = ""  # First 1000 chars for quick view
    queue_position: int = 0  # Position when queued
    pid: int | None = None
    process: subprocess.Popen | None = field(default=None, repr=False)


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload)


def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
    append_text_atomic(path, json.dumps(payload, ensure_ascii=False) + "\n")


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Background manager could not read state file: path=%s error=%s", path, exc)
        return {}


class BackgroundManagerV2:
    """Enhanced background manager with queue and concurrency control."""

    MAX_TIMEOUT_SECONDS = MAX_WORKFLOW_TIMEOUT_SECONDS
    DEFAULT_TIMEOUT_SECONDS = DEFAULT_OPERATION_TIMEOUT_SECONDS
    DEFAULT_MAX_CONCURRENT = 2
    OUTPUT_PREVIEW_LENGTH = 1000

    def __init__(
        self,
        workspace_full: str,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        *,
        auto_start: bool = True,
        load_state: bool = True,
    ) -> None:
        self.workspace_full = os.path.abspath(workspace_full)
        self.max_concurrent = max(1, min(max_concurrent, 10))  # 1-10 range
        self._command_executor = CommandExecutionService(self.workspace_full)

        self.state_path = resolve_runtime_path(self.workspace_full, "runtime/state/background_tasks_v2.state.json")
        self.events_path = resolve_runtime_path(self.workspace_full, "runtime/events/background_v2.events.jsonl")

        # In-memory structures（有界队列防止内存泄漏）
        self._lock = threading.RLock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._queue: ThreadQueue = ThreadQueue(maxsize=500)  # Queue for pending task IDs
        self._running_count: int = 0
        self._processor_thread: threading.Thread | None = None
        self._closed = False

        # Shutdown event for background thread
        self._shutdown_event = threading.Event()

        # Load persisted state and resume tracking
        if load_state:
            self._load_state()
        if auto_start:
            self._start_queue_processor()

    def _start_queue_processor(self) -> None:
        """Start background thread to process queued tasks."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Background manager is closed")
            if self._processor_thread is not None and self._processor_thread.is_alive():
                return
            processor = threading.Thread(
                target=self._queue_processor_loop,
                daemon=True,
                name="background-queue-processor",
            )
            self._processor_thread = processor
        processor.start()

    def _queue_processor_loop(self) -> None:
        """Background thread: promote QUEUED tasks to RUNNING when slot available."""
        while not self._shutdown_event.is_set():
            try:
                with self._lock:
                    running = sum(1 for t in self._tasks.values() if t.status == BackgroundTaskState.RUNNING)
                    slots_available = self.max_concurrent - running

                    promoted = 0
                    for _ in range(slots_available):
                        # Find next queued task
                        queued_tasks = [
                            (tid, t) for tid, t in self._tasks.items() if t.status == BackgroundTaskState.QUEUED
                        ]
                        queued_tasks.sort(key=lambda x: x[1].created_at)

                        if queued_tasks:
                            tid, task = queued_tasks[0]
                            self._start_task_execution(tid, task)
                            promoted += 1

                if promoted == 0:
                    self._shutdown_event.wait(0.5)  # Wait with shutdown check
            except (RuntimeError, ValueError) as e:
                logger.exception("Background queue processor loop failed: %s", e)
                self._emit_event("queue_processor_error", payload={"error": str(e)})
                self._shutdown_event.wait(1)  # Wait before retry

    def _start_task_execution(self, task_id: str, task: BackgroundTask) -> None:
        """Actually start a subprocess for a task."""
        import shlex

        try:
            from polaris.kernelone.process.command_executor import CommandRequest

            # Parse command and build spec directly
            tokens = shlex.split(task.command)
            if not tokens:
                raise ValueError("Empty command")
            request = CommandRequest(
                executable=tokens[0],
                args=tokens[1:],
                cwd=task.cwd or ".",
                timeout_seconds=task.timeout,
            )
            spec = self._command_executor.build_subprocess_spec(request)
            process = subprocess.Popen(
                list(spec["argv"]),
                shell=False,
                cwd=str(spec["cwd"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=dict(spec["env"]),
            )

            with _PROCESS_LOCK:
                _PROCESS_REGISTRY[task_id] = process

            task.status = BackgroundTaskState.RUNNING
            task.started_at = time.time()
            task.process = process
            task.pid = process.pid
            self._start_process_monitor(task_id)

            self._emit_event(
                "background_promoted_to_running", task_id=task_id, payload={"command": task.command, "pid": task.pid}
            )
        except (RuntimeError, ValueError) as e:
            logger.warning("Background task failed to start: task_id=%s error=%s", task_id, e)
            task.status = BackgroundTaskState.FAILED
            task.stderr = str(e)
            task.finished_at = time.time()
            self._emit_event("background_start_failed", task_id=task_id, payload={"error": str(e)})

    def _start_process_monitor(self, task_id: str) -> None:
        """Drain one child process in a dedicated daemon thread."""
        monitor = threading.Thread(
            target=self._monitor_process_output,
            args=(task_id,),
            daemon=True,
            name=f"background-monitor-{task_id}",
        )
        monitor.start()

    def _monitor_process_output(self, task_id: str) -> None:
        """Wait for process completion while continuously draining stdio pipes."""
        with self._lock:
            task = self._tasks.get(task_id)
            process = task.process if task else None
            started_at = float(task.started_at or time.time()) if task else time.time()

        if task is None or process is None:
            return

        try:
            stdout_text, stderr_text = process.communicate()
        except (RuntimeError, ValueError) as exc:
            logger.warning("Background process monitor failed: task_id=%s error=%s", task_id, exc)
            stdout_text, stderr_text = "", str(exc)

        finished_at = time.time()
        status_payload: dict[str, Any] | None = None
        should_save = False

        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                return

            stdout_value = str(stdout_text or "")[:80000]
            stderr_value = str(stderr_text or "")[:80000]
            if stdout_value and not current.stdout:
                current.stdout = stdout_value
            if stderr_value and not current.stderr:
                current.stderr = stderr_value
            if not current.output_preview:
                current.output_preview = (current.stdout + current.stderr)[: self.OUTPUT_PREVIEW_LENGTH]

            if current.status == BackgroundTaskState.RUNNING:
                return_code = int(process.returncode or 0)
                current.status = BackgroundTaskState.SUCCESS if return_code == 0 else BackgroundTaskState.FAILED
                current.exit_code = return_code
                current.finished_at = finished_at
                current.output_preview = (current.stdout + current.stderr)[: self.OUTPUT_PREVIEW_LENGTH]
                status_payload = {
                    "status": current.status.value,
                    "exit_code": current.exit_code,
                    "duration_ms": int((finished_at - started_at) * 1000),
                }
                should_save = True
            elif current.status in {BackgroundTaskState.TIMEOUT, BackgroundTaskState.CANCELLED}:
                if current.finished_at is None:
                    current.finished_at = finished_at
                    should_save = True

        with _PROCESS_LOCK:
            _PROCESS_REGISTRY.pop(task_id, None)

        if status_payload is not None:
            self._emit_event(
                "background_completed",
                task_id=task_id,
                payload=status_payload,
            )
        if should_save:
            self._save_state()

    def _load_state(self) -> None:
        """Load persisted state."""
        lock_path = f"{self.state_path}.lock"
        with file_lock(lock_path, timeout_sec=5.0) as acquired:
            if not acquired:
                raise TimeoutError(f"Timed out loading background state: {self.state_path}")
            data = _read_json(self.state_path)
        tasks_data = data.get("tasks", {})

        for tid, tdata in tasks_data.items():
            try:
                task = BackgroundTask(
                    id=tdata["id"],
                    command=tdata["command"],
                    status=BackgroundTaskState(tdata.get("status", "queued")),
                    created_at=tdata["created_at"],
                    cwd=tdata.get("cwd", "."),
                    timeout=tdata.get("timeout", DEFAULT_OPERATION_TIMEOUT_SECONDS),
                    started_at=tdata.get("started_at"),
                    finished_at=tdata.get("finished_at"),
                    exit_code=tdata.get("exit_code"),
                    stdout=tdata.get("stdout", "")[:80000],
                    stderr=tdata.get("stderr", "")[:80000],
                    output_preview=tdata.get("output_preview", "")[: self.OUTPUT_PREVIEW_LENGTH],
                )
                self._tasks[tid] = task
                # Re-queue if was queued or running
                if task.status in (BackgroundTaskState.QUEUED, BackgroundTaskState.RUNNING):
                    task.status = BackgroundTaskState.QUEUED  # Reset to queued
            except (KeyError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "Skipping corrupt background task state record: task_id=%s error=%s",
                    tid,
                    exc,
                )

    def _save_state(self) -> None:
        """Persist current state."""
        with self._lock:
            local_tasks = {
                tid: {
                    "id": t.id,
                    "command": t.command,
                    "status": t.status.value,
                    "created_at": t.created_at,
                    "cwd": t.cwd,
                    "timeout": t.timeout,
                    "started_at": t.started_at,
                    "finished_at": t.finished_at,
                    "exit_code": t.exit_code,
                    "stdout": t.stdout[:80000],
                    "stderr": t.stderr[:80000],
                    "output_preview": t.output_preview,
                }
                for tid, t in self._tasks.items()
            }
            data: dict[str, Any] = {
                "schema_version": 2,
                "updated_at": time.time(),
                "max_concurrent": self.max_concurrent,
                "tasks": local_tasks,
            }
        lock_path = f"{self.state_path}.lock"
        with file_lock(lock_path, timeout_sec=5.0) as acquired:
            if not acquired:
                raise TimeoutError(f"Timed out saving background state: {self.state_path}")
            current = _read_json(self.state_path)
            persisted_tasks = current.get("tasks", {}) if isinstance(current.get("tasks"), dict) else {}
            merged_tasks = dict(persisted_tasks)
            merged_tasks.update(data["tasks"])
            payload = {
                **current,
                "schema_version": 2,
                "updated_at": data["updated_at"],
                "max_concurrent": self.max_concurrent,
                "tasks": merged_tasks,
            }
            write_json_atomic(self.state_path, payload, lock_timeout_sec=None)

    def close(self, *, cancel_running: bool = False, join_timeout: float = 1.0) -> None:
        """Stop background processing and optionally cancel active tasks."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._processor_thread
            task_ids = [
                task.id
                for task in self._tasks.values()
                if task.status in {BackgroundTaskState.QUEUED, BackgroundTaskState.RUNNING}
            ]
        self._shutdown_event.set()

        if cancel_running:
            for task_id in task_ids:
                try:
                    self.cancel(task_id)
                except (RuntimeError, ValueError) as exc:
                    logger.debug(
                        "Background manager close skipped task cancellation: task_id=%s error=%s", task_id, exc
                    )

        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(join_timeout)))

        with self._lock:
            self._processor_thread = None

    def __enter__(self) -> BackgroundManagerV2:
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        del exc_type, exc, exc_tb
        self.close(cancel_running=True)

    def _emit_event(self, event: str, *, task_id: str = "", payload: dict[str, Any] | None = None) -> None:
        _append_jsonl(
            self.events_path,
            {
                "schema_version": 2,
                "event": event,
                "task_id": task_id,
                "timestamp_epoch": time.time(),
                "payload": payload if isinstance(payload, dict) else {},
            },
        )

    def _normalize_timeout(self, timeout: Any) -> int:
        try:
            parsed = int(timeout)
        except (TypeError, ValueError):
            parsed = self.DEFAULT_TIMEOUT_SECONDS
        parsed = max(1, parsed)
        return min(parsed, self.MAX_TIMEOUT_SECONDS)

    def _resolve_cwd(self, raw_cwd: Any) -> str:
        token = str(raw_cwd or "").strip() or "."
        candidate = token
        if not os.path.isabs(candidate):
            candidate = os.path.join(self.workspace_full, candidate)
        candidate = os.path.abspath(candidate)
        if os.path.commonpath([self.workspace_full, candidate]) != self.workspace_full:
            return self.workspace_full
        if not os.path.isdir(candidate):
            return self.workspace_full
        return candidate

    def _is_forbidden_command(self, command: str) -> bool:
        lowered = str(command or "").lower()
        has_redirect = ">" in lowered or "1>" in lowered or "2>" in lowered
        # Check both legacy paths (for backward compatibility) and current runtime paths
        blocked_target = "/runtime/state/" in lowered or "/runtime/events/" in lowered or "background_tasks" in lowered
        return has_redirect and blocked_target

    def submit(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        command_id: str = "",
        wait_for_slot: bool = False,
    ) -> dict[str, Any]:
        """Submit a background task. May be queued if concurrency limit reached."""
        cmd = str(command or "").strip()
        if not cmd:
            return {"ok": False, "error": "missing_command", "task_id": ""}
        with self._lock:
            if self._closed:
                return {"ok": False, "error": "manager_closed", "task_id": ""}
        if self._is_forbidden_command(cmd):
            return {
                "ok": False,
                "error": "forbidden_shell_redirect_to_runtime_state",
                "task_id": "",
            }

        with self._lock:
            task_id = str(command_id or "").strip() or f"bg-{uuid.uuid4().hex[:8]}"
            task_timeout = self._normalize_timeout(timeout)

            # Determine initial status based on concurrency
            running_count = sum(1 for t in self._tasks.values() if t.status == BackgroundTaskState.RUNNING)

            if running_count >= self.max_concurrent and not wait_for_slot:
                initial_status = BackgroundTaskState.QUEUED
                queue_position = sum(1 for t in self._tasks.values() if t.status == BackgroundTaskState.QUEUED) + 1
            else:
                initial_status = BackgroundTaskState.QUEUED  # Will be promoted by processor
                queue_position = 0

            task = BackgroundTask(
                id=task_id,
                command=cmd,
                status=initial_status,
                created_at=time.time(),
                cwd=cwd,
                timeout=task_timeout,
                queue_position=queue_position,
            )
            self._tasks[task_id] = task
            self._save_state()

            self._emit_event(
                "background_submitted",
                task_id=task_id,
                payload={
                    "command": cmd,
                    "cwd": cwd,
                    "timeout": task_timeout,
                    "initial_status": initial_status.value,
                    "queue_position": queue_position,
                },
            )

            # If wait_for_slot and at capacity, wait
            if wait_for_slot and running_count >= self.max_concurrent:
                return {
                    "ok": True,
                    "task_id": task_id,
                    "status": "queued",
                    "queue_position": queue_position,
                    "message": "Task queued, waiting for concurrency slot",
                }

            return {
                "ok": True,
                "task_id": task_id,
                "status": initial_status.value,
                "queue_position": queue_position if initial_status == BackgroundTaskState.QUEUED else 0,
                "max_concurrent": self.max_concurrent,
                "currently_running": running_count,
            }

    def _poll_task(self, task_id: str, task: BackgroundTask) -> bool:
        """Poll a running task. Returns True if state changed."""
        if task.status != BackgroundTaskState.RUNNING:
            return False

        if task.process is None:
            return False

        now = time.time()
        started = task.started_at or now
        timeout = self._normalize_timeout(task.timeout)

        # Check timeout
        if now - started > timeout:
            try:
                task.process.kill()
            except (RuntimeError, ValueError) as exc:
                logger.debug(
                    "Background process kill on timeout failed: task_id=%s error=%s",
                    task_id,
                    exc,
                )

            task.status = BackgroundTaskState.TIMEOUT
            task.exit_code = -1
            if not task.stderr:
                task.stderr = f"Timed out after {timeout}s"
            task.finished_at = now
            task.output_preview = (task.stdout + task.stderr)[: self.OUTPUT_PREVIEW_LENGTH]

            self._emit_event(
                "background_timeout",
                task_id=task_id,
                payload={"timeout": timeout},
            )
            return True

        # Check completion
        return_code = task.process.poll()
        if return_code is None:
            return False
        del return_code
        return False

    # 最大保留任务数，防止内存无限增长
    MAX_RETAINED_TASKS = 1000

    def _cleanup_finished_tasks(self) -> None:
        """Clean up old finished tasks to prevent memory leak.

        Keeps only MAX_RETAINED_TASKS most recent finished tasks.
        Always retains QUEUED and RUNNING tasks.
        """
        with self._lock:
            # Separate active and finished tasks
            active_tasks = {}
            finished_tasks = []

            for tid, task in self._tasks.items():
                if task.status in (BackgroundTaskState.QUEUED, BackgroundTaskState.RUNNING):
                    active_tasks[tid] = task
                else:
                    finished_tasks.append((tid, task))

            # Sort finished tasks by finished_at (or created_at if not finished), newest first
            finished_tasks.sort(key=lambda x: x[1].finished_at or x[1].created_at, reverse=True)

            # Keep only the most recent MAX_RETAINED_TASKS
            retained_finished = finished_tasks[: self.MAX_RETAINED_TASKS]

            # Rebuild _tasks dict with active + retained finished
            self._tasks = active_tasks
            for tid, task in retained_finished:
                self._tasks[tid] = task

    def poll_all(self) -> None:
        """Poll all running tasks and update state."""
        changed = False
        with self._lock:
            for task_id, task in list(self._tasks.items()):
                if self._poll_task(task_id, task):
                    changed = True
            # Periodically cleanup old tasks to prevent memory leak
            if len(self._tasks) > self.MAX_RETAINED_TASKS:
                self._cleanup_finished_tasks()
        if changed:
            self._save_state()

    def check(self, task_id: str) -> dict[str, Any]:
        """Check task status."""
        self.poll_all()
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"error": "task_not_found"}

            # Update queue positions for queued tasks
            if task.status == BackgroundTaskState.QUEUED:
                queued_ahead = sum(
                    1
                    for t in self._tasks.values()
                    if t.status == BackgroundTaskState.QUEUED and t.created_at < task.created_at
                )
                task.queue_position = queued_ahead + 1

            return {
                "id": task.id,
                "status": task.status.value,
                "command": task.command,
                "created_at": task.created_at,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "exit_code": task.exit_code,
                "stdout_preview": task.output_preview[:500],
                "stderr_preview": task.stderr[:500] if task.stderr else "",
                "queue_position": task.queue_position if task.status == BackgroundTaskState.QUEUED else 0,
            }

    def list(
        self,
        *,
        status: str = "",
        include_output: bool = False,
    ) -> builtins.list[dict[str, Any]]:
        """List tasks with optional filtering."""
        self.poll_all()
        with self._lock:
            result = []
            for task in self._tasks.values():
                if status and task.status.value != status.lower():
                    continue

                item = {
                    "id": task.id,
                    "status": task.status.value,
                    "command": task.command[:100],
                    "created_at": task.created_at,
                    "started_at": task.started_at,
                    "finished_at": task.finished_at,
                    "exit_code": task.exit_code,
                    "queue_position": task.queue_position if task.status == BackgroundTaskState.QUEUED else 0,
                }
                if include_output:
                    item["stdout"] = task.stdout[:5000]
                    item["stderr"] = task.stderr[:5000]
                result.append(item)

            result.sort(key=lambda x: (x["created_at"] is not None, x["created_at"]), reverse=True)
            return result

    def cancel(self, task_id: str) -> dict[str, Any]:
        """Cancel a queued or running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"ok": False, "error": "task_not_found", "task_id": task_id}

            if task.status == BackgroundTaskState.CANCELLED:
                return {"ok": False, "error": "already_cancelled", "task_id": task_id}

            if task.status in _TERMINAL_STATUSES:
                return {"ok": False, "error": "already_terminal", "task_id": task_id, "status": task.status.value}

            # Kill if running
            previous_status = task.status.value
            if task.status == BackgroundTaskState.RUNNING and task.process:
                try:
                    task.process.kill()
                except (RuntimeError, ValueError) as exc:
                    logger.debug(
                        "Background process kill on cancel failed: task_id=%s error=%s",
                        task_id,
                        exc,
                    )

            task.status = BackgroundTaskState.CANCELLED
            task.finished_at = time.time()
            task.exit_code = -1
            task.stderr = "Cancelled by user"

            self._save_state()
            self._emit_event("background_cancelled", task_id=task_id, payload={"previous_status": previous_status})

            return {"ok": True, "task_id": task_id, "status": "cancelled"}

    def wait(
        self,
        task_ids: builtins.list[str],
        timeout: int = 120,
        on_timeout: str = "needs_continue",
    ) -> dict[str, Any]:
        """Wait for tasks with timeout decision policy.

        Args:
            task_ids: List of task IDs to wait for
            timeout: Max seconds to wait
            on_timeout: "continue" | "needs_continue" | "fail"

        Returns:
            Dict with waited, resolved, timeout, decision
        """
        if not isinstance(task_ids, list) or not task_ids:
            return {"ok": False, "error": "missing_task_ids"}

        timeout = self._normalize_timeout(timeout)
        start_time = time.time()

        resolved: list[dict[str, Any]] = []
        timed_out_ids: list[str] = []

        while time.time() - start_time < timeout:
            self.poll_all()

            all_terminal = True
            for tid in task_ids:
                task = self._tasks.get(tid)
                if not task:
                    timed_out_ids.append(tid)
                    continue
                if task.status.value not in _TERMINAL_STATUSES:
                    all_terminal = False
                    break
                # Add to resolved if terminal and not already recorded
                if tid not in [r["id"] for r in resolved]:
                    resolved.append(
                        {
                            "id": task.id,
                            "status": task.status.value,
                            "exit_code": task.exit_code,
                            "stdout_preview": task.output_preview[:500],
                        }
                    )

            if all_terminal:
                break

            time.sleep(0.5)

        # Final check
        self.poll_all()
        for tid in task_ids:
            if tid in [r["id"] for r in resolved] or tid in timed_out_ids:
                continue
            task = self._tasks.get(tid)
            if task and task.status.value in _TERMINAL_STATUSES:
                resolved.append(
                    {
                        "id": task.id,
                        "status": task.status.value,
                        "exit_code": task.exit_code,
                        "stdout_preview": task.output_preview[:500],
                    }
                )
            else:
                timed_out_ids.append(tid)

        # Determine decision
        if not timed_out_ids:
            decision = "continue"
        elif on_timeout == "fail":
            decision = "fail"
        elif on_timeout == "continue":
            decision = "continue"
        else:
            decision = "needs_continue"

        return {
            "ok": True,
            "waited": len(resolved) > 0,
            "resolved": resolved,
            "timeout": timed_out_ids,
            "decision": decision,
            "elapsed_seconds": int(time.time() - start_time),
        }

    def get_queue_status(self) -> dict[str, Any]:
        """Get current queue status."""
        with self._lock:
            running = sum(1 for t in self._tasks.values() if t.status == BackgroundTaskState.RUNNING)
            queued = sum(1 for t in self._tasks.values() if t.status == BackgroundTaskState.QUEUED)

            return {
                "max_concurrent": self.max_concurrent,
                "running": running,
                "queued": queued,
                "available_slots": max(0, self.max_concurrent - running),
            }


# Backwards compatibility: BackgroundManager inherits from V2
class BackgroundManager(BackgroundManagerV2):
    """Backwards-compatible background manager."""

    def run(
        self, *, command: str, cwd: str = ".", timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS, command_id: str = ""
    ) -> dict[str, Any]:
        """Legacy run() method - delegates to submit()."""
        return self.submit(
            command=command,
            cwd=cwd,
            timeout=timeout,
            command_id=command_id,
        )

    def drain_completed(self) -> list[dict[str, Any]]:
        """Legacy drain method."""
        self.poll_all()
        drained = []
        with self._lock:
            for task in list(self._tasks.values()):
                if task.status.value in _TERMINAL_STATUSES:
                    drained.append(
                        {
                            "id": task.id,
                            "command": task.command,
                            "status": task.status.value,
                            "exit_code": task.exit_code,
                            "stdout": task.stdout[:10000],
                            "stderr": task.stderr[:10000],
                        }
                    )
        return drained
