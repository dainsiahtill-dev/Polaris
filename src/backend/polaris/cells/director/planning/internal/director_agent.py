"""Director Agent - Autonomous Execution Agent (Cell Implementation).

Migrated from ``polaris.cells.director.execution.internal.director_agent``.

The Director Agent is responsible for:
- Executing tasks from PM
- Managing code changes (apply patches, run commands)
- Risk assessment and management
- Quality verification
- Maintaining execution history
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.agent_runtime_base import (
    AgentMessage,
    MessageType,
    RoleAgent,
)
from polaris.cells.roles.runtime.internal.worker_pool import WorkerPool, WorkerTask
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)


class ExecutionRecord:
    """Record of a task execution."""

    def __init__(
        self,
        execution_id: str,
        task_id: str,
        status: str = "pending",
    ) -> None:
        self.execution_id = execution_id
        self.task_id = task_id
        self.status = status
        self.started_at = datetime.now().isoformat()
        self.completed_at: str | None = None
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.risk_score: float = 0.0
        self.risk_factors: list[str] = []
        self.qa_result: dict[str, Any] | None = None
        self.files_changed: list[str] = []
        self.lines_added: int = 0
        self.lines_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "risk_score": self.risk_score,
            "risk_factors": self.risk_factors,
            "qa_result": self.qa_result,
            "files_changed": self.files_changed,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionRecord:
        rec = cls(
            execution_id=data.get("execution_id", ""),
            task_id=data.get("task_id", ""),
            status=data.get("status", "pending"),
        )
        rec.started_at = data.get("started_at", rec.started_at)
        rec.completed_at = data.get("completed_at")
        rec.result = data.get("result")
        rec.error = data.get("error")
        rec.risk_score = data.get("risk_score", 0.0)
        rec.risk_factors = data.get("risk_factors", [])
        rec.qa_result = data.get("qa_result")
        rec.files_changed = data.get("files_changed", [])
        rec.lines_added = data.get("lines_added", 0)
        rec.lines_removed = data.get("lines_removed", 0)
        return rec

    def complete(
        self,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.completed_at = datetime.now().isoformat()
        if result is not None:
            self.result = result
        if error is not None:
            self.error = error

    def assess_risk(self, files: list[str], lines_added: int, lines_removed: int) -> None:
        """Assess risk of the changes."""
        self.files_changed = files
        self.lines_added = lines_added
        self.lines_removed = lines_removed

        score = 0.0
        factors = []

        if len(files) > 10:
            score += 0.3
            factors.append("many_files_changed")

        if lines_added > 500:
            score += 0.2
            factors.append("large_change")

        auth_files = [f for f in files if "auth" in f.lower() or "security" in f.lower()]
        if auth_files:
            score += 0.4
            factors.append("touches_auth")

        config_files = [f for f in files if f.endswith((".json", ".yaml", ".yml", ".toml"))]
        if config_files:
            score += 0.1
            factors.append("touches_config")

        if any(f.endswith(".py") for f in files):
            factors.append("python_changes")

        self.risk_score = min(score, 1.0)
        self.risk_factors = factors


class RiskRegistry:
    """Registry for tracking identified risks."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._risks_dir = resolve_runtime_path(workspace, "runtime/state/risks")
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        Path(self._risks_dir).mkdir(parents=True, exist_ok=True)

    def record_risk(
        self,
        execution_id: str,
        risk_type: str,
        description: str,
        severity: str = "medium",
    ) -> str:
        risk_id = f"risk-{__import__('uuid').uuid4().hex[:8]}"

        risk_record = {
            "risk_id": risk_id,
            "execution_id": execution_id,
            "risk_type": risk_type,
            "description": description,
            "severity": severity,
            "recorded_at": datetime.now().isoformat(),
            "status": "open",
        }

        risk_file = os.path.join(self._risks_dir, f"{risk_id}.json")
        self._fs.write_json(risk_file, risk_record)
        return risk_id

    def get_open_risks(self) -> list[dict[str, Any]]:
        self._ensure_dirs()
        risks_dir = Path(self._risks_dir)
        if not risks_dir.exists():
            return []

        risks = []
        try:
            for f in risks_dir.iterdir():
                if f.suffix != ".json":
                    continue
                try:
                    risk = self._fs.read_json(str(f))
                    if risk.get("status") == "open":
                        risks.append(risk)
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Failed to read risk JSON %s: %s", f, exc)
                    continue
        except (FileNotFoundError, PermissionError):
            pass
        return risks


class QualityTracker:
    """Track quality metrics for executions."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._qa_dir = resolve_runtime_path(workspace, "runtime/state/qa")
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        Path(self._qa_dir).mkdir(parents=True, exist_ok=True)

    def record_qa_result(
        self,
        execution_id: str,
        task_id: str,
        result: dict[str, Any],
    ) -> None:
        qa_record = {
            "execution_id": execution_id,
            "task_id": task_id,
            "result": result,
            "recorded_at": datetime.now().isoformat(),
        }

        qa_file = os.path.join(self._qa_dir, f"{execution_id}.json")
        self._fs.write_json(qa_file, qa_record)

    def get_qa_history(self, limit: int = 50) -> list[dict[str, Any]]:
        results = []
        qa_dir = Path(self._qa_dir)
        if not qa_dir.exists():
            return []

        files = sorted(
            qa_dir.iterdir(),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for f in files[:limit]:
            if f.suffix != ".json":
                continue
            try:
                results.append(self._fs.read_json(str(f)))
            except (RuntimeError, ValueError) as exc:
                logger.debug("Failed to read QA result JSON %s: %s", f, exc)
                continue

        return results


class DirectorAgent(RoleAgent):
    """Autonomous Director Agent.

    The Director Agent executes tasks from PM and manages the
    execution lifecycle including risk assessment and quality verification.
    """

    def __init__(self, workspace: str, message_bus: Any = None) -> None:
        super().__init__(workspace, "Director")
        self._risk_registry: RiskRegistry | None = None
        self._quality_tracker: QualityTracker | None = None
        self._worker_pool: WorkerPool | None = None
        self._taskboard: TaskRuntimeService | None = None
        self._current_execution: ExecutionRecord | None = None
        self._execution_history: list[dict[str, Any]] = []
        self._message_bus = message_bus
        self._command_executor = CommandExecutionService(workspace)

    @property
    def worker_pool(self) -> WorkerPool:
        """Get Worker pool for parallel execution."""
        if self._worker_pool is None:
            from pathlib import Path

            from polaris.cells.roles.runtime.public.service import create_worker_pool

            work_dir = Path(self.workspace)
            self._worker_pool = create_worker_pool(work_dir, max_workers=4)
        return self._worker_pool

    @property
    def taskboard(self) -> TaskRuntimeService:
        """Get unified task runtime service for task coordination."""
        if self._taskboard is None:
            self._taskboard = TaskRuntimeService(self.workspace)
        return self._taskboard

    @property
    def risk_registry(self) -> RiskRegistry:
        if self._risk_registry is None:
            self._risk_registry = RiskRegistry(self.workspace)
        return self._risk_registry

    @property
    def quality_tracker(self) -> QualityTracker:
        if self._quality_tracker is None:
            self._quality_tracker = QualityTracker(self.workspace)
        return self._quality_tracker

    def setup_toolbox(self) -> None:
        """Setup Director-specific tools."""
        tb = self.toolbox

        tb.register(
            "execute_task",
            self._tool_execute_task,
            description="Execute a task from PM",
            parameters={"task_id": "Task ID to execute"},
        )

        tb.register(
            "get_execution",
            self._tool_get_execution,
            description="Get execution by ID",
            parameters={"execution_id": "Execution ID"},
        )

        tb.register(
            "get_execution_history",
            self._tool_get_execution_history,
            description="Get execution history",
            parameters={"limit": "Max number of records"},
        )

        tb.register(
            "assess_risk",
            self._tool_assess_risk,
            description="Assess risk of current/past execution",
            parameters={
                "execution_id": "Execution ID",
                "files": "List of changed files",
                "lines_added": "Lines added",
                "lines_removed": "Lines removed",
            },
        )

        tb.register(
            "get_open_risks",
            self._tool_get_open_risks,
            description="Get all open risks",
            parameters={},
        )

        tb.register(
            "resolve_risk",
            self._tool_resolve_risk,
            description="Mark a risk as resolved",
            parameters={"risk_id": "Risk ID"},
        )

        tb.register(
            "run_qa",
            self._tool_run_qa,
            description="Run QA checks on execution",
            parameters={"execution_id": "Execution ID"},
        )

        tb.register(
            "get_qa_history",
            self._tool_get_qa_history,
            description="Get QA history",
            parameters={"limit": "Max number of records"},
        )

        tb.register(
            "apply_patch",
            self._tool_apply_patch,
            description="Apply a code patch",
            parameters={
                "patch": "Patch content",
                "target_file": "Target file path",
            },
        )

        tb.register(
            "run_command",
            self._tool_run_command,
            description="Run a shell command",
            parameters={
                "command": "Command to run",
                "cwd": "Working directory",
                "timeout": "Timeout in seconds",
            },
        )

        tb.register(
            "spawn_worker",
            self._tool_spawn_worker,
            description="Spawn a new Worker for parallel execution",
            parameters={"worker_id": "Worker ID (optional)"},
        )

        tb.register(
            "list_workers",
            self._tool_list_workers,
            description="List all workers and their status",
            parameters={},
        )

        tb.register(
            "submit_task_to_worker",
            self._tool_submit_task_to_worker,
            description="Submit task to a specific worker",
            parameters={
                "task_id": "Task ID from TaskBoard",
                "command": "Command to execute",
                "worker_id": "Worker ID (optional, auto-assign if not provided)",
            },
        )

    def _tool_execute_task(self, task_id: str) -> dict[str, Any]:
        """Execute a task."""
        import uuid

        execution_id = f"exec-{uuid.uuid4().hex[:8]}"

        self._current_execution = ExecutionRecord(
            execution_id=execution_id,
            task_id=task_id,
            status="running",
        )

        self.memory.append_history(
            {
                "action": "execute_task",
                "task_id": task_id,
                "execution_id": execution_id,
                "status": "started",
            }
        )

        if self._state:
            self._state.current_task_id = task_id

        self._execution_history.append(self._current_execution.to_dict())
        self._execution_history = self._execution_history[-500:]

        return {
            "ok": True,
            "execution_id": execution_id,
            "task_id": task_id,
            "status": "started",
        }

    def _tool_get_execution(self, execution_id: str) -> dict[str, Any]:
        """Get execution by ID."""
        if self._current_execution and self._current_execution.execution_id == execution_id:
            return {
                "ok": True,
                "execution": self._current_execution.to_dict(),
            }

        history = self.memory.get_history(limit=1000)
        for entry in history:
            if entry.get("execution_id") == execution_id:
                return {
                    "ok": True,
                    "execution": entry,
                }

        return {"ok": False, "error": f"Execution {execution_id} not found"}

    def _tool_get_execution_history(self, limit: int = 50) -> dict[str, Any]:
        """Get execution history."""
        history = self.memory.get_history(limit=limit)
        executions = [h for h in history if h.get("action") == "execute_task"]

        return {
            "ok": True,
            "executions": executions,
            "count": len(executions),
        }

    def _tool_assess_risk(
        self,
        execution_id: str,
        files: list[str],
        lines_added: int,
        lines_removed: int,
    ) -> dict[str, Any]:
        """Assess risk of execution."""
        execution = None

        if self._current_execution and self._current_execution.execution_id == execution_id:
            execution = self._current_execution
        else:
            for rec in self._execution_history:
                if rec.get("execution_id") == execution_id:
                    execution = ExecutionRecord.from_dict(rec)
                    break

        if not execution:
            return {"ok": False, "error": f"Execution {execution_id} not found"}

        execution.assess_risk(files, lines_added, lines_removed)

        if execution.risk_score > 0.5:
            for factor in execution.risk_factors:
                self.risk_registry.record_risk(
                    execution_id=execution_id,
                    risk_type=factor,
                    description=f"Risk factor detected: {factor}",
                    severity="high" if execution.risk_score > 0.7 else "medium",
                )

        return {
            "ok": True,
            "risk_score": execution.risk_score,
            "risk_factors": execution.risk_factors,
        }

    def _tool_get_open_risks(self) -> dict[str, Any]:
        """Get all open risks."""
        risks = self.risk_registry.get_open_risks()
        return {
            "ok": True,
            "risks": risks,
            "count": len(risks),
        }

    def _tool_resolve_risk(self, risk_id: str) -> dict[str, Any]:
        """Mark a risk as resolved."""
        risk_file = resolve_runtime_path(self.workspace, f"runtime/state/risks/{risk_id}.json")

        fs = KernelFileSystem(os.path.dirname(risk_file), get_default_adapter())
        if not fs.workspace_exists(os.path.basename(risk_file)):
            return {"ok": False, "error": f"Risk {risk_id} not found"}

        try:
            content = fs.workspace_read_text(os.path.basename(risk_file), encoding="utf-8")
            risk = json.loads(content)

            risk["status"] = "resolved"
            risk["resolved_at"] = datetime.now().isoformat()

            write_text_atomic(risk_file, json.dumps(risk, ensure_ascii=False, indent=2), encoding="utf-8")

            return {"ok": True, "risk": risk}
        except (RuntimeError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def _tool_run_qa(self, execution_id: str) -> dict[str, Any]:
        """Run QA checks on execution."""
        execution = None

        if self._current_execution and self._current_execution.execution_id == execution_id:
            execution = self._current_execution
        else:
            for rec in self._execution_history:
                if rec.get("execution_id") == execution_id:
                    execution = ExecutionRecord.from_dict(rec)
                    break

        if not execution:
            return {"ok": False, "error": f"Execution {execution_id} not found"}

        qa_result: dict[str, Any] = {
            "passed": True,
            "checks": [],
            "execution_id": execution_id,
        }

        if execution.risk_score > 0.7:
            qa_result["passed"] = False
            checks_list = qa_result["checks"]
            if isinstance(checks_list, list):
                checks_list.append(
                    {
                        "name": "risk_check",
                        "status": "fail",
                        "message": f"High risk score: {execution.risk_score}",
                    }
                )

        if not execution.files_changed:
            checks_list = qa_result["checks"]
            if isinstance(checks_list, list):
                checks_list.append(
                    {
                        "name": "files_changed_check",
                        "status": "warn",
                        "message": "No files changed",
                    }
                )

        execution.qa_result = qa_result
        self.quality_tracker.record_qa_result(
            execution_id=execution_id,
            task_id=execution.task_id,
            result=qa_result,
        )

        return {
            "ok": True,
            "qa_result": qa_result,
        }

    def _tool_get_qa_history(self, limit: int = 50) -> dict[str, Any]:
        """Get QA history."""
        history = self.quality_tracker.get_qa_history(limit=limit)
        return {
            "ok": True,
            "qa_results": history,
            "count": len(history),
        }

    def _tool_apply_patch(self, patch: str, target_file: str) -> dict[str, Any]:
        """Apply a code patch with broadcast support."""
        from polaris.kernelone.events.file_event_broadcaster import apply_patch_with_broadcast

        try:
            # Get current task_id from execution context
            task_id = ""
            if self._current_execution:
                task_id = self._current_execution.task_id

            result = apply_patch_with_broadcast(
                workspace=self.workspace,
                target_file=target_file,
                patch=patch,
                message_bus=self._message_bus,
                worker_id=f"director-{id(self)}",
                task_id=task_id,
            )
            # Track file changes in execution record
            if result.get("ok") and self._current_execution:
                self._current_execution.files_changed.append(target_file)
                # Update line counts from result
                if "added_lines" in result:
                    self._current_execution.lines_added += result["added_lines"]
                if "deleted_lines" in result:
                    self._current_execution.lines_removed += result["deleted_lines"]
            return result
        except (RuntimeError, ValueError) as e:
            return {
                "ok": False,
                "error": str(e),
                "file": target_file,
            }

    def _tool_run_command(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        """Run a validated command without shell expansion."""
        import shlex

        try:
            from polaris.kernelone.process.command_executor import CommandRequest

            # Parse command and execute directly
            tokens = shlex.split(command)
            if not tokens:
                return {"ok": False, "error": "Empty command"}
            request = CommandRequest(
                executable=tokens[0],
                args=tokens[1:],
                cwd=cwd or self.workspace,
                timeout_seconds=max(1, int(timeout or 60)),
            )
            result = self._command_executor.run(request)
            return result
        except (RuntimeError, ValueError) as e:
            return {
                "ok": False,
                "error": str(e),
            }

    def _tool_spawn_worker(self, worker_id: str | None = None) -> dict[str, Any]:
        """Spawn a new Worker."""
        try:
            wid = self.worker_pool.spawn_worker(worker_id)
            return {
                "ok": True,
                "worker_id": wid,
                "message": f"Worker {wid} spawned",
            }
        except (RuntimeError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def _tool_list_workers(self) -> dict[str, Any]:
        """List all workers."""
        status = self.worker_pool.get_status()
        return {"ok": True, "workers": status}

    def _tool_submit_task_to_worker(
        self,
        task_id: int,
        command: str,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit task to worker."""
        from pathlib import Path

        task = WorkerTask(
            task_id=task_id,
            command=command,
            work_dir=Path(self.workspace),
        )

        success = self.worker_pool.submit_task(task, worker_id)
        if success:
            return {
                "ok": True,
                "task_id": task_id,
                "worker_id": worker_id or "auto",
                "message": "Task submitted",
            }
        return {"ok": False, "error": "Failed to submit task"}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming message from PM or other agents."""
        if message.type == MessageType.TASK:
            payload = message.payload
            task = payload.get("task", {})
            task_id = task.get("id")

            if task_id:
                exec_result = self._tool_execute_task(task_id)

                result = {
                    "task_id": task_id,
                    "execution_id": exec_result.get("execution_id"),
                    "status": "executing",
                }

                return AgentMessage.create(
                    msg_type=MessageType.RESULT,
                    sender="Director",
                    receiver="PM",
                    payload=result,
                    correlation_id=message.correlation_id,
                )

        return None

    def run_cycle(self) -> bool:
        """Main Director processing cycle."""
        message = self.message_queue.receive(block=False)

        if message:
            response = self.handle_message(message)
            if response:
                self.message_queue.send(response)
            return True

        return False

    def _load_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Load data from snapshot."""
        self._execution_history = snapshot.get("execution_history", [])

    def save_snapshot(self) -> dict[str, Any]:
        """Save current state as snapshot."""
        current_exec_dict = self._current_execution.to_dict() if self._current_execution else None

        return {
            "execution_history": self._execution_history,
            "current_execution": current_exec_dict,
            "saved_at": datetime.now().isoformat(),
        }
