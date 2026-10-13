"""CE read-context projection agrees with the canonical capability boundary."""

from dataclasses import replace
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.public import (
    GenerateTaskBlueprintCommandV1,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
)
from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _command,
    _payload,
    _readonly_fixture,
    _task,
)


def test_inferred_ce_inputs_reach_task_overlay_and_public_handoff(tmp_path: Path) -> None:
    task, payload = _readonly_fixture()
    task = replace(task, context_files=())
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append(
        {
            "obligation_id": "verify-test",
            "path": "tests/test_verify.py",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": task.task_id,
        }
    )
    obligations["verification"][-1]["covers_obligation_ids"].append("verify-test")
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    overlay = project_chief_engineer_task_blueprint(portfolio, task.task_id)
    expected_inputs = ["src/main.py", "tests/test_main.py"]
    assert overlay["construction_plan"]["context_files"] == expected_inputs
    assert overlay["construction_plan"]["target_files"] == ["tests/test_verify.py"]
    context = {
        **portfolio.to_task_blueprint_context(),
        "pm_task_contract": {
            "id": task.task_id,
            "title": task.objective,
            "goal": task.objective,
            "acceptance": list(task.observable_acceptance),
            "metadata": {"planning_contract_version": "pm.task_intent.v1"},
        },
    }
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            workspace=str(tmp_path),
            task_id=task.task_id,
            run_id="run-intent",
            objective=task.objective,
            context=context,
            llm_blueprint=overlay,
        )
    )
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert stored["context_files"] == expected_inputs
    assert set(stored["job_token"]["allowed_read_paths"]) == {"tests/test_verify.py", *expected_inputs}
    assert stored["job_token"]["allowed_write_paths"] == ["tests/test_verify.py"]
    assert stored["acceptance_criteria"] == list(task.observable_acceptance)


def test_blank_workspace_creation_handoff_keeps_no_invented_input_authority(tmp_path: Path) -> None:
    task = _task()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload()))
    overlay = project_chief_engineer_task_blueprint(portfolio, task.task_id)
    assert overlay["construction_plan"]["context_files"] == []
    guidance = overlay["construction_plan"]["input_guidance"]
    assert "already supplied" in guidance
    assert "new artifact" in guidance
    assert "authorized" in guidance
    assert "explicit constraints" in guidance
    assert "requirements.md" not in guidance
    context = {
        **portfolio.to_task_blueprint_context(),
        "pm_task_contract": {
            "id": task.task_id,
            "title": task.objective,
            "goal": task.objective,
            "acceptance": list(task.observable_acceptance),
            "explicit_constraints": list(task.explicit_constraints),
            "metadata": {"planning_contract_version": "pm.task_intent.v1"},
        },
    }
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            workspace=str(tmp_path),
            task_id=task.task_id,
            run_id="run-intent",
            objective=task.objective,
            context=context,
            llm_blueprint=overlay,
        )
    )
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert stored["handoff_ready"] is True
    assert stored["context_files"] == []
    assert stored["llm_blueprint"]["construction_plan"]["input_guidance"] == guidance
    assert stored["pm_task"]["explicit_constraints"] == list(task.explicit_constraints)
    assert stored["job_token"]["allowed_read_paths"] == stored["target_files"]
    assert stored["job_token"]["allowed_write_paths"] == stored["target_files"]
    assert not any(
        (tmp_path / path).exists() for path in portfolio.project_interface_contract.task_file_ownership[task.task_id]
    )
