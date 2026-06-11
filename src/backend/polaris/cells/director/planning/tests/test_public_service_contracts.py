"""G4-pattern wiring tests: director.planning public contracts → DirectorAgent."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.planning.public.contracts import (
    GetDirectorStatusQueryV1,
    PlanDirectorTaskCommandV1,
)
from polaris.cells.director.planning.public.service import (
    get_director_status,
    get_planning_agent,
    plan_director_task,
    reset_planning_agents,
)


@pytest.fixture(autouse=True)
def _isolated_agents() -> None:
    reset_planning_agents()


class TestPlanDirectorTask:
    def test_plan_registers_execution_via_agent_ingress(self, tmp_path: Path) -> None:
        command = PlanDirectorTaskCommandV1(
            task_id="task-77",
            workspace=str(tmp_path),
            instruction="fix the login bug",
            run_id="run-1",
        )

        result = plan_director_task(command)

        assert result.ok is True
        assert result.task_id == "task-77"
        assert result.status in ("executing", "started")
        assert "execution" in result.plan_summary
        # The same path a PM TASK message takes: the agent recorded the intent.
        agent = get_planning_agent(str(tmp_path))
        history = agent.memory.get_history(limit=10)
        assert any(entry.get("task_id") == "task-77" for entry in history)


class TestGetDirectorStatus:
    def test_status_on_fresh_workspace(self, tmp_path: Path) -> None:
        query = GetDirectorStatusQueryV1(task_id="task-77", workspace=str(tmp_path))

        result = get_director_status(query)

        assert result.ok is True
        assert result.status == "available"
        assert "open_risks=0" in result.plan_summary


class TestAgentCache:
    def test_same_workspace_shares_agent(self, tmp_path: Path) -> None:
        assert get_planning_agent(str(tmp_path)) is get_planning_agent(str(tmp_path))

    def test_reset_drops_cached_agents(self, tmp_path: Path) -> None:
        before = get_planning_agent(str(tmp_path))
        reset_planning_agents()

        assert get_planning_agent(str(tmp_path)) is not before
