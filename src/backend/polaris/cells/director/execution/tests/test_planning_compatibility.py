from pathlib import Path

from polaris.cells.director.execution.internal.director_agent import DirectorAgent
from polaris.cells.director.planning.public import get_planning_agent


def test_shared_role_retains_execution_metrics_and_same_workspace_history(tmp_path: Path) -> None:
    agent = DirectorAgent(str(tmp_path))
    execution = agent._tool_execute_task("inspect")
    execution_id = execution["execution_id"]
    assert agent._tool_assess_risk(execution_id, ["module.py"], 2, 0)["ok"]
    assert agent._tool_run_qa(execution_id)["qa_result"]["passed"]
    for status in ("started", "assessed", "completed"):
        assert agent.metrics.get_counter("director_execution_total", {"status": status}).get() == 1
    assert agent.quality_tracker.get_qa_history()[0]["execution_id"] == execution_id
    planning = get_planning_agent(str(tmp_path))
    assert planning._tool_get_execution(execution_id)["execution"]["task_id"] == "inspect"
    assert planning._tool_apply_patch("", "module.py")["ok"] is False
