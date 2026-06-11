"""Public service handlers for `director.planning` cell.

G4-pattern wiring (mirrors `resident.autonomy/public/service.py`): the public
contracts existed with no handler, while the cell's real implementation
(`DirectorAgent` + `RiskRegistry`/`QualityTracker`) was reachable only through
the agent message bus. These handlers delegate 1:1 to that existing machinery:

- ``plan_director_task`` feeds the command through the SAME path a PM TASK
  message takes (`DirectorAgent.handle_message`) — execution intent is
  registered in the agent's memory/history and an execution id is returned.
- ``get_director_status`` reads the cell's own persisted planning state
  (open risks + recent QA history).

No new business logic lives here.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from polaris.cells.director.planning.internal.director_agent import (
    DirectorAgent,
    QualityTracker,
    RiskRegistry,
)
from polaris.cells.director.planning.public.contracts import (
    DirectorPlanningResultV1,
    GetDirectorStatusQueryV1,
    PlanDirectorTaskCommandV1,
)

# Import from the typed service re-export, NOT public.contracts: the package
# exposes TWO same-named message classes (contracts → kernelone shared_contracts
# vs service → internal agent_runtime_base). DirectorAgent.handle_message
# compares against the agent_runtime_base enum, so we must use that one —
# the cross-class enum comparison silently fails otherwise.
from polaris.cells.roles.runtime.public.service import AgentMessage, MessageType

logger = logging.getLogger(__name__)

_agents: dict[str, DirectorAgent] = {}
_agents_lock = threading.Lock()


def get_planning_agent(workspace: str) -> DirectorAgent:
    """Return the shared per-workspace DirectorAgent used by contract handlers."""
    key = str(Path(str(workspace or ".")).resolve())
    with _agents_lock:
        agent = _agents.get(key)
        if agent is None:
            agent = DirectorAgent(key)
            _agents[key] = agent
        return agent


def reset_planning_agents() -> None:
    """Test hook: drop all cached per-workspace agents."""
    with _agents_lock:
        _agents.clear()


def plan_director_task(command: PlanDirectorTaskCommandV1) -> DirectorPlanningResultV1:
    """Handle PlanDirectorTaskCommandV1 via the agent's canonical TASK ingress."""
    try:
        agent = get_planning_agent(command.workspace)
        message = AgentMessage.create(
            msg_type=MessageType.TASK,
            sender="director.planning.public",
            receiver="Director",
            payload={
                "task": {
                    "id": command.task_id,
                    "title": command.instruction,
                    "metadata": dict(command.metadata or {}),
                }
            },
            correlation_id=command.run_id,
        )
        response = agent.handle_message(message)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("plan_director_task failed: %s", exc)
        return DirectorPlanningResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="error",
            run_id=command.run_id,
            error_code="director_planning_failed",
            error_message=str(exc),
        )

    if response is None or not isinstance(response.payload, dict):
        return DirectorPlanningResultV1(
            ok=False,
            task_id=command.task_id,
            workspace=command.workspace,
            status="rejected",
            run_id=command.run_id,
            error_code="director_planning_rejected",
            error_message="director agent did not accept the task message",
        )

    execution_id = str(response.payload.get("execution_id") or "")
    status = str(response.payload.get("status") or "executing")
    return DirectorPlanningResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status=status,
        run_id=command.run_id,
        plan_summary=(f"execution {execution_id} registered for task {command.task_id}"),
    )


def get_director_status(query: GetDirectorStatusQueryV1) -> DirectorPlanningResultV1:
    """Handle GetDirectorStatusQueryV1 from the cell's persisted planning state."""
    try:
        open_risks = RiskRegistry(query.workspace).get_open_risks()
        qa_history = QualityTracker(query.workspace).get_qa_history(limit=10)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("get_director_status failed: %s", exc)
        return DirectorPlanningResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            status="error",
            run_id=query.run_id,
            error_code="director_status_failed",
            error_message=str(exc),
        )

    return DirectorPlanningResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status="available",
        run_id=query.run_id,
        plan_summary=(f"open_risks={len(open_risks)}; recent_qa_records={len(qa_history)}"),
    )


__all__ = [
    "get_director_status",
    "get_planning_agent",
    "plan_director_task",
    "reset_planning_agents",
]
