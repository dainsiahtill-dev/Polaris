"""Quality repair producer observation tests for the director quality gate.

These tests target the roles.adapters internal producer
(``polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop``)
and therefore live in the roles.adapters cell test boundary. The producer
context output is pinned against the shared test-data sample under
``polaris/tests/fixtures/quality_repair_producer_context.json``.
"""

from __future__ import annotations

import json
from pathlib import Path


def _missing(path: str) -> str:
    return f"Artifact quality scan failed: declared target file missing '{path}'"


def test_static_producer_preserves_readme_owner_identity():
    from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
        _quality_repair_issue_observations,
    )

    row = _quality_repair_issue_observations([_missing("README.md")], ["README.md"])[0]
    assert row["path"] == "README.md"
    assert row["metadata"]["target_file"] == "README.md"


def _capture_producer_observation_context(root: Path, task: dict, errors: list[str], context: dict) -> dict:
    """Execute the real producer context expression, with controlled fixture inputs."""
    import ast
    import inspect

    from polaris.cells.roles.adapters.internal.director.quality_gate import _repair_loop as producer

    tree = ast.parse(inspect.getsource(producer._run_materialization_quality_repair_retry))
    value = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "repair_context" for target in node.targets)
    )
    namespace = dict(producer.__dict__)
    namespace.update(
        task=task,
        context=context,
        workspace_full=str(root),
        run_id="owned-run",
        target_task_id="owned-task",
        original_message=context.get("task_objective", "Implement and verify all owned outputs."),
        prompt_artifact_quality_errors=errors,
        prompt_safe_artifact_quality_errors=errors,
        failed_gate_evidence=context.get("failed_gate_evidence", {"quality_errors": errors}),
        workspace_quality_evidence=context.get(
            "workspace_quality_evidence", {"quality_errors": errors, "all_checks_passed": False}
        ),
        changed_files=[],
        missing_target_files=[],
        runtime_smoke_target_files=[],
        semantic_quality_target_files=[],
        explicit_quality_target_files=[],
        repair_target_files=task["target_files"],
    )
    return eval(compile(ast.fix_missing_locations(ast.Expression(value)), __file__, "eval"), namespace)


def test_static_producer_context_matches_contract_fixture(tmp_path: Path):
    """Run the real producer and pin its context output to the shared contract sample."""
    fixture_path = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "quality_repair_producer_context.json"
    (tmp_path / "answer.py").write_text("VALUE = 1\n", encoding="utf-8")
    task = {"target_files": ["answer.py", ".gitignore"]}
    error = _missing(".gitignore")
    context = {
        "execution_envelope": {"allowed_read_paths": task["target_files"]},
        "target_files": task["target_files"],
        "task_objective": "Implement and verify owned outputs.",
    }
    captured = _capture_producer_observation_context(tmp_path, task, [error], context)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert captured == fixture
    assert ".gitignore" in captured["director_quality_repair"]["declared_target_files"]
    assert ".gitignore" in captured["director_quality_repair"]["scan_relative_paths"]
