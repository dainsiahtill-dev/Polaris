"""CE prompt does not turn inline product intent into missing input files."""

from pathlib import Path

from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.factory.pipeline.tests.test_semantic_pm_ce_handoff import _intent


def test_ce_prompt_keeps_inline_requirements_and_creation_scope_consistent(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    task = _intent()
    message = executor._chief_engineer_portfolio_objective([task])
    assert "already included" in message
    assert "new artifact" in message
    assert "context_files" in message
    assert "Do not invent prerequisite input files" in message
    assert "Implementation choices may evolve" in message
    assert "server" in message
    context = executor._chief_engineer_portfolio_context([task], run_id="run-inline")
    assert context["pm_task_contracts"][0]["required_requirements"] == task["required_requirements"]
    assert context["pm_task_contracts"][0]["explicit_constraints"] == ["Use Python"]
    assert context["target_files"] == []
    assert context["scope_paths"] == []
