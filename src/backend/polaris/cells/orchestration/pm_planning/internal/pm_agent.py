"""PM Agent - Autonomous Project Manager Agent.

The PM Agent is responsible for:
- Planning and generating task contracts
- Dispatching tasks to Director
- Evaluating results and making decisions
- Maintaining task history and context
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from polaris.cells.roles.runtime.public.service import (
    AgentMessage,
    MessageType,
    ProtocolFSM,
    ProtocolType,
    RoleAgent,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.domain.entities.task import TaskPriority as TBPriority
from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage import resolve_runtime_path


class PMTask:
    """Represents a task in PM's scope."""

    def __init__(
        self,
        task_id: str,
        title: str,
        goal: str,
        status: str = "todo",
        priority: int = 5,
        context_files: list[str] | None = None,
        target_files: list[str] | None = None,
        scope_paths: list[str] | None = None,
        constraints: list[str] | None = None,
        acceptance: list[str] | None = None,
        assigned_to: str | None = None,
        phase: str | None = None,
        dependencies: list[str] | None = None,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.goal = goal
        self.status = status
        self.priority = priority
        self.context_files = context_files or []
        self.target_files = target_files or []
        self.scope_paths = scope_paths or []
        self.constraints = constraints or []
        self.acceptance = acceptance or []
        self.assigned_to = assigned_to
        self.phase = phase
        self.dependencies = dependencies or []
        self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()
        self.result: dict[str, Any] | None = None
        self.error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "priority": self.priority,
            "context_files": self.context_files,
            "target_files": self.target_files,
            "scope_paths": self.scope_paths,
            "constraints": self.constraints,
            "acceptance": self.acceptance,
            "assigned_to": self.assigned_to,
            "phase": self.phase,
            "dependencies": self.dependencies,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PMTask:
        task = cls(
            task_id=data.get("id", ""),
            title=data.get("title", ""),
            goal=data.get("goal", ""),
            status=data.get("status", "todo"),
            priority=data.get("priority", 5),
            context_files=data.get("context_files"),
            target_files=data.get("target_files"),
            scope_paths=data.get("scope_paths"),
            constraints=data.get("constraints"),
            acceptance=data.get("acceptance"),
            assigned_to=data.get("assigned_to"),
            phase=data.get("phase"),
            dependencies=data.get("dependencies"),
        )
        task.created_at = data.get("created_at", task.created_at)
        task.updated_at = data.get("updated_at", task.updated_at)
        task.result = data.get("result")
        task.error = data.get("error")
        return task

    def update_status(self, status: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.status = status
        self.updated_at = datetime.now().isoformat()
        if result is not None:
            self.result = result
        if error is not None:
            self.error = error


class PMTaskStore:
    """Persistent task store for PM Agent."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._tasks_dir = resolve_runtime_path(workspace, "runtime/state/pm_tasks")
        self._index_file = os.path.join(self._tasks_dir, "index.json")
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        os.makedirs(self._tasks_dir, exist_ok=True)

    def _load_index(self) -> dict[str, dict[str, Any]]:
        if not os.path.exists(self._index_file):
            return {}
        try:
            with open(self._index_file, encoding="utf-8") as f:
                return json.load(f)
        except (RuntimeError, ValueError):
            return {}

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        write_text_atomic(self._index_file, json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    def save_task(self, task: PMTask) -> None:
        task_file = os.path.join(self._tasks_dir, f"{task.task_id}.json")

        index = self._load_index()
        index[task.task_id] = {
            "task_id": task.task_id,
            "status": task.status,
            "updated_at": task.updated_at,
            "file": f"{task.task_id}.json",
        }

        write_text_atomic(task_file, json.dumps(task.to_dict(), ensure_ascii=False, indent=2) + "\n")

        self._save_index(index)

    def load_task(self, task_id: str) -> PMTask | None:
        task_file = os.path.join(self._tasks_dir, f"{task_id}.json")
        if not os.path.exists(task_file):
            return None

        try:
            with open(task_file, encoding="utf-8") as f:
                data = json.load(f)
            return PMTask.from_dict(data)
        except (RuntimeError, ValueError):
            return None

    def list_tasks(self, status: str | None = None) -> list[PMTask]:
        index = self._load_index()
        tasks = []

        for task_id, info in index.items():
            if status and info.get("status") != status:
                continue

            task = self.load_task(task_id)
            if task:
                tasks.append(task)

        return sorted(tasks, key=lambda t: t.priority)

    def get_pending_tasks(self) -> list[PMTask]:
        return self.list_tasks(status="pending_dispatch")

    def get_completed_tasks(self) -> list[PMTask]:
        return self.list_tasks(status="completed")


class PMAgent(RoleAgent):
    """Autonomous Project Manager Agent.

    The PM Agent manages the overall project planning and task dispatch.
    It maintains:
    - Task history and backlog
    - Current planning context
    - Dispatch records to Director
    - Decision history
    """

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace, "PM")
        self._task_store: PMTaskStore | None = None
        self._taskboard: TaskRuntimeService | None = None
        self._protocol_fsm: ProtocolFSM | None = None
        self._current_iteration: int = 0
        self._dispatch_history: list[dict[str, Any]] = []
        self._decisions: list[dict[str, Any]] = []

    @property
    def taskboard(self) -> TaskRuntimeService:
        """Get unified task runtime service for long-term task management."""
        if self._taskboard is None:
            self._taskboard = TaskRuntimeService(self.workspace)
        return self._taskboard

    @property
    def protocol_fsm(self) -> ProtocolFSM:
        """Get Protocol FSM for gate checks."""
        if self._protocol_fsm is None:
            from polaris.cells.roles.runtime.public.service import create_protocol_fsm

            self._protocol_fsm = create_protocol_fsm(self.workspace)
        return self._protocol_fsm

    @property
    def task_store(self) -> PMTaskStore:
        """Get task store (lazy init)."""
        if self._task_store is None:
            self._task_store = PMTaskStore(self.workspace)
        return self._task_store

    def setup_toolbox(self) -> None:
        """Setup PM-specific tools."""
        tb = self.toolbox

        tb.register(
            "create_task",
            self._tool_create_task,
            description="Create a new task",
            parameters={
                "title": "Task title",
                "goal": "Task goal/description",
                "priority": "Priority (1=highest, 5=default)",
                "context_files": "Files to read for context",
                "target_files": "Files to modify",
                "scope_paths": "Scope paths",
                "constraints": "Constraints",
                "acceptance": "Acceptance criteria",
            },
        )

        tb.register(
            "get_task",
            self._tool_get_task,
            description="Get task by ID",
            parameters={"task_id": "Task ID"},
        )

        tb.register(
            "list_tasks",
            self._tool_list_tasks,
            description="List all tasks, optionally filtered by status",
            parameters={"status": "Filter by status (optional)"},
        )

        tb.register(
            "get_pending_tasks",
            self._tool_get_pending_tasks,
            description="Get tasks pending dispatch",
            parameters={},
        )

        tb.register(
            "get_task_history",
            self._tool_get_task_history,
            description="Get task history summaries",
            parameters={"limit": "Max number of summaries"},
        )

        tb.register(
            "dispatch_task",
            self._tool_dispatch_task,
            description="Dispatch a task to Director",
            parameters={"task_id": "Task ID to dispatch"},
        )

        tb.register(
            "update_task_status",
            self._tool_update_task_status,
            description="Update task status",
            parameters={
                "task_id": "Task ID",
                "status": "New status",
                "result": "Result data (optional)",
                "error": "Error message (optional)",
            },
        )

        tb.register(
            "evaluate_result",
            self._tool_evaluate_result,
            description="Evaluate a task result and decide next action",
            parameters={
                "task_id": "Task ID",
                "result": "Task execution result",
            },
        )

        tb.register(
            "get_dispatch_history",
            self._tool_get_dispatch_history,
            description="Get task dispatch history",
            parameters={},
        )

        tb.register(
            "get_decisions",
            self._tool_get_decisions,
            description="Get PM decision history",
            parameters={},
        )

        tb.register(
            "taskboard_create",
            self._tool_taskboard_create,
            description="Create task in long-term TaskBoard",
            parameters={
                "subject": "Task subject/title",
                "description": "Task description",
                "priority": "Priority (low/medium/high/critical)",
                "blocked_by": "List of task IDs this depends on",
            },
        )

        tb.register(
            "taskboard_list_ready",
            self._tool_taskboard_list_ready,
            description="List tasks ready for execution",
            parameters={},
        )

        tb.register(
            "taskboard_stats",
            self._tool_taskboard_stats,
            description="Get TaskBoard statistics",
            parameters={},
        )

        tb.register(
            "request_approval",
            self._tool_request_approval,
            description="Request approval via Protocol FSM",
            parameters={
                "approval_type": "plan_approval, budget_check, policy_check",
                "content": "Content to approve",
                "target_role": "Target role to approve",
            },
        )

        tb.register(
            "check_approval_status",
            self._tool_check_approval_status,
            description="Check approval request status",
            parameters={"request_id": "Request ID to check"},
        )

    def _tool_create_task(self, **kwargs) -> dict[str, Any]:
        """Create a new task."""
        import uuid

        task_id = f"task-{uuid.uuid4().hex[:8]}"

        task = PMTask(
            task_id=task_id,
            title=kwargs.get("title", ""),
            goal=kwargs.get("goal", ""),
            priority=int(kwargs.get("priority", 5)),
            context_files=kwargs.get("context_files"),
            target_files=kwargs.get("target_files"),
            scope_paths=kwargs.get("scope_paths"),
            constraints=kwargs.get("constraints"),
            acceptance=kwargs.get("acceptance"),
            status="todo",
        )

        self.task_store.save_task(task)

        self.memory.append_history(
            {
                "action": "create_task",
                "task_id": task_id,
                "title": task.title,
            }
        )

        return {"ok": True, "task_id": task_id, "task": task.to_dict()}

    def _tool_get_task(self, task_id: str) -> dict[str, Any]:
        """Get task by ID."""
        task = self.task_store.load_task(task_id)
        if not task:
            return {"ok": False, "error": f"Task {task_id} not found"}

        return {"ok": True, "task": task.to_dict()}

    def _tool_list_tasks(self, status: str | None = None) -> dict[str, Any]:
        """List all tasks."""
        tasks = self.task_store.list_tasks(status)
        return {
            "ok": True,
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks),
        }

    def _tool_get_pending_tasks(self) -> dict[str, Any]:
        """Get tasks pending dispatch."""
        tasks = self.task_store.get_pending_tasks()
        return {
            "ok": True,
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks),
        }

    def _tool_get_task_history(self, limit: int = 50) -> dict[str, Any]:
        """Get task history summaries."""
        summaries = self.memory.get_task_summaries(limit=limit)
        return {
            "ok": True,
            "history": summaries,
            "count": len(summaries),
        }

    def _tool_dispatch_task(self, task_id: str) -> dict[str, Any]:
        """Dispatch a task to Director."""
        task = self.task_store.load_task(task_id)
        if not task:
            return {"ok": False, "error": f"Task {task_id} not found"}

        task.update_status("dispatched")
        self.task_store.save_task(task)

        self._current_iteration += 1

        dispatch_record = {
            "task_id": task_id,
            "iteration": self._current_iteration,
            "timestamp": datetime.now().isoformat(),
            "status": "dispatched",
        }
        self._dispatch_history.append(dispatch_record)

        self.memory.append_history(
            {
                "action": "dispatch_task",
                "task_id": task_id,
                "iteration": self._current_iteration,
            }
        )

        message = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="PM",
            receiver="Director",
            payload={
                "task": task.to_dict(),
                "iteration": self._current_iteration,
                "dispatch_timestamp": dispatch_record["timestamp"],
            },
            correlation_id=task_id,
        )
        self.message_queue.send(message)

        if self._state:
            self._state.current_task_id = task_id
            self._state.total_tasks_processed += 1

        return {
            "ok": True,
            "task_id": task_id,
            "iteration": self._current_iteration,
            "message_id": message.id,
        }

    def _tool_update_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Update task status."""
        task = self.task_store.load_task(task_id)
        if not task:
            return {"ok": False, "error": f"Task {task_id} not found"}

        task.update_status(status, result, error)
        self.task_store.save_task(task)

        self.memory.append_history(
            {
                "action": "update_task_status",
                "task_id": task_id,
                "status": status,
                "result": result,
                "error": error,
            }
        )

        return {"ok": True, "task": task.to_dict()}

    def _tool_evaluate_result(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate task result and decide next action."""
        task = self.task_store.load_task(task_id)
        if not task:
            return {"ok": False, "error": f"Task {task_id} not found"}

        status = result.get("status", "unknown")

        if status == "success":
            task.update_status("completed", result)
            decision = "CONTINUE"
        elif status == "fail":
            task.update_status("failed", result, result.get("error"))
            decision = self._decide_on_failure(result)
        elif status == "blocked":
            task.update_status("blocked", result)
            decision = "MANUAL_INTERVENTION"
        else:
            task.update_status("review", result)
            decision = "REVIEW"

        self.task_store.save_task(task)

        decision_record = {
            "task_id": task_id,
            "result_status": status,
            "decision": decision,
            "timestamp": datetime.now().isoformat(),
        }
        self._decisions.append(decision_record)

        self.memory.append_history(
            {
                "action": "evaluate_result",
                "task_id": task_id,
                "result_status": status,
                "decision": decision,
            }
        )

        if self._state:
            if status == "fail":
                self._state.consecutive_failures += 1
            else:
                self._state.consecutive_failures = 0

        return {
            "ok": True,
            "decision": decision,
            "task": task.to_dict(),
            "decision_record": decision_record,
        }

    def _decide_on_failure(self, result: dict[str, Any]) -> str:
        """Decide what to do on task failure."""
        if self._state and self._state.consecutive_failures >= 3:
            return "STOP"

        failure_code = result.get("failure_code", "")

        if failure_code in ("QA_FAIL", "TEST_FAIL"):
            return "RETRY_WITH_FIX"
        elif failure_code in ("RISK_BLOCKED", "CAPABILITY_GATE"):
            return "ADJUST_SCOPE"
        elif failure_code in ("TOOL_TIMEOUT", "RESOURCE_EXHAUSTED"):
            return "RETRY"
        else:
            return "ANALYZE_AND_RETRY"

    def _tool_get_dispatch_history(self) -> dict[str, Any]:
        """Get dispatch history."""
        return {
            "ok": True,
            "history": self._dispatch_history,
            "count": len(self._dispatch_history),
        }

    def _tool_get_decisions(self) -> dict[str, Any]:
        """Get decision history."""
        return {
            "ok": True,
            "decisions": self._decisions,
            "count": len(self._decisions),
        }

    def _tool_taskboard_create(self, **kwargs) -> dict[str, Any]:
        """Create task in long-term TaskBoard."""
        priority_map = {
            "low": TBPriority.LOW,
            "medium": TBPriority.MEDIUM,
            "high": TBPriority.HIGH,
            "critical": TBPriority.CRITICAL,
        }

        task = self.taskboard.create(
            subject=kwargs.get("subject", ""),
            description=kwargs.get("description", ""),
            priority=priority_map.get(kwargs.get("priority", "medium"), TBPriority.MEDIUM),
            owner="PM",
            blocked_by=kwargs.get("blocked_by", []),
        )

        return {
            "ok": True,
            "task_id": task.id,
            "subject": task.subject,
            "status": task.status.value,
        }

    def _tool_taskboard_list_ready(self) -> dict[str, Any]:
        """List tasks ready for execution."""
        ready = self.taskboard.list_ready()
        return {
            "ok": True,
            "tasks": [
                {
                    "id": t.id,
                    "subject": t.subject,
                    "priority": t.priority,
                    "blocked_by": t.blocked_by,
                }
                for t in ready
            ],
            "count": len(ready),
        }

    def _tool_taskboard_stats(self) -> dict[str, Any]:
        """Get TaskBoard statistics."""
        stats = self.taskboard.get_stats()
        return {"ok": True, "stats": stats}

    def _tool_request_approval(self, **kwargs) -> dict[str, Any]:
        """Request approval via Protocol FSM."""
        approval_type = kwargs.get("approval_type", "plan_approval")
        content = kwargs.get("content", {})
        target_role = kwargs.get("target_role", "QA")

        try:
            protocol_type = ProtocolType(approval_type)
        except ValueError:
            return {"ok": False, "error": f"Invalid approval type: {approval_type}"}

        request_id = self.protocol_fsm.create_request(
            protocol_type=protocol_type,
            from_role="PM",
            to_role=target_role,
            content=content,
        )

        return {
            "ok": True,
            "request_id": request_id,
            "status": "pending",
            "target_role": target_role,
        }

    def _tool_check_approval_status(self, request_id: str) -> dict[str, Any]:
        """Check approval request status."""
        status = self.protocol_fsm.get_status(request_id)
        if status is None:
            return {"ok": False, "error": f"Request {request_id} not found"}

        return {"ok": True, "request_id": request_id, "status": status.value}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        """Handle incoming message from Director or other agents."""
        if message.type == MessageType.RESULT:
            payload = message.payload
            task_id = payload.get("task_id")
            result = payload.get("result", {})

            if task_id:
                eval_result = self._tool_evaluate_result(task_id, result)

                return AgentMessage.create(
                    msg_type=MessageType.RESULT,
                    sender="PM",
                    receiver=message.sender,
                    payload={
                        "original_task_id": task_id,
                        "evaluation": eval_result,
                    },
                    correlation_id=message.correlation_id,
                )

        elif message.type == MessageType.COMMAND:
            command = message.payload.get("command")

            if command == "get_status":
                return AgentMessage.create(
                    msg_type=MessageType.EVENT,
                    sender="PM",
                    receiver=message.sender,
                    payload=self.get_status(),
                )

        return None

    def run_cycle(self) -> bool:
        """Main PM processing cycle."""
        message = self.message_queue.receive(block=False)

        if message:
            response = self.handle_message(message)
            if response:
                self.message_queue.send(response)
            return True

        pending = self.task_store.get_pending_tasks()
        if pending:
            task = pending[0]
            self._tool_dispatch_task(task.task_id)
            return True

        return False

    def _load_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Load data from snapshot."""
        self._current_iteration = snapshot.get("current_iteration", 0)
        self._dispatch_history = snapshot.get("dispatch_history", [])
        self._decisions = snapshot.get("decisions", [])

        tasks_data = snapshot.get("tasks", {})
        for _task_id, task_data in tasks_data.items():
            task = PMTask.from_dict(task_data)
            self.task_store.save_task(task)

    def save_snapshot(self) -> dict[str, Any]:
        """Save current state as snapshot."""
        tasks = {}
        for task in self.task_store.list_tasks():
            tasks[task.task_id] = task.to_dict()

        return {
            "current_iteration": self._current_iteration,
            "dispatch_history": self._dispatch_history,
            "decisions": self._decisions,
            "tasks": tasks,
            "saved_at": datetime.now().isoformat(),
        }
