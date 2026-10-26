"""The production handoff delegates technical recipes without inventing authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import validate
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.orchestration.pm_planning.public.task_intent import normalize_pm_task_intent


def _intent() -> dict:
    return normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Deliver the requested observable CLI behavior",
            "required_requirements": ["A runnable CLI with behavior tests"],
            "optional_requirements": [],
            "acceptance": ["The CLI returns the requested result and rejects invalid input"],
            "explicit_constraints": ["Use Python"],
            "tasks": [
                {
                    "id": "TASK-IMPLEMENT",
                    "title": "Implement a small CLI",
                    "goal": "Deliver the requested observable CLI behavior",
                    "acceptance": ["The CLI returns the requested result and rejects invalid input"],
                    "depends_on": [],
                }
            ],
        }
    )[0]


def test_semantic_handoff_preserves_obligations_without_guessed_topology(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    item = executor._chief_engineer_portfolio_tasks([_intent()])[0]
    assert item.target_files == ()
    assert item.scope_paths == ()
    assert item.planning_contract_version == "pm.task_intent.v1"
    assert item.verification_authority == "chief_engineer"
    assert item.observable_acceptance == tuple(_intent()["acceptance"])
    assert item.explicit_constraints == ("Use Python",)
    assert executor._chief_engineer_verification_command_authority([_intent()]) == ()


def test_task_acceptance_does_not_require_unmaterialized_downstream_product(tmp_path: Path) -> None:
    tasks = normalize_pm_task_intent(
        {
            "schema_version": "pm.task_intent.v1",
            "product_goal": "Deliver a storage CLI",
            "required_requirements": ["Persistent storage and a runnable CLI"],
            "optional_requirements": [],
            "explicit_constraints": [],
            "acceptance": ["The CLI persists records across invocations"],
            "tasks": [
                {
                    "id": "STORE",
                    "title": "Storage",
                    "goal": "Implement storage API",
                    "acceptance": ["Storage API saves and loads records"],
                    "depends_on": [],
                },
                {
                    "id": "CLI",
                    "title": "CLI",
                    "goal": "Expose storage commands",
                    "acceptance": ["CLI commands use the storage API"],
                    "depends_on": ["STORE"],
                },
            ],
        }
    )
    first = _executor(tmp_path)._chief_engineer_portfolio_tasks(tasks)[0]
    assert first.observable_acceptance == ("Storage API saves and loads records",)
    assert tasks[0]["acceptance_criteria"] == ["Storage API saves and loads records"]
    assert tasks[0]["product_acceptance"] == ["The CLI persists records across invocations"]


def test_explicit_commands_survive_semantic_delegation(tmp_path: Path) -> None:
    task = _intent()
    task["verification_commands"] = [{"modality": "test", "argv": ["python", "-m", "unittest", "discover"], "cwd": "."}]
    authority = _executor(tmp_path)._chief_engineer_verification_command_authority([task])
    assert len(authority) == 1
    assert authority[0].argv == ("python", "-m", "unittest", "discover")


def test_legacy_or_partial_delegation_cannot_drop_command_authority(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    for change in ({"planning_contract_version": ""}, {"verification_authority": "pm"}):
        task = deepcopy(_intent())
        task["metadata"].update(change)
        with pytest.raises(RuntimeError):
            executor._chief_engineer_verification_command_authority([task])


def test_final_ce_submission_schema_allows_delegated_recipes(tmp_path: Path) -> None:
    contract = _executor(tmp_path)._chief_engineer_structured_output_contract(("TASK-IMPLEMENT",))
    row = contract.json_schema["properties"]["project_completion_contract"]["properties"]["obligations"]["properties"][
        "verification"
    ]["items"]
    assert row["properties"]["argv"]["type"] == "array"
    assert row["properties"]["cwd"]["type"] == "string"
    assert "argv" not in row["required"]


def test_minimal_ce_plan_matches_actual_submission_schema(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )

    contract = _executor(tmp_path)._chief_engineer_structured_output_contract(("TASK-A",))
    validate(_payload(), contract.json_schema)


def test_invalid_delegated_recipe_returns_correctable_ce_feedback(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _command, _task

    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"][1]["argv"] = ["echo", "pass"]
    command = _command(tmp_path, payload)
    errors = _executor(tmp_path)._chief_engineer_portfolio_output_errors(
        payload, tasks=(_task(),), authority_carrier=command.authority_carrier
    )
    assert any("verification_recipe_upsert" in error for error in errors)


def test_qa_receives_same_run_ce_artifacts_instead_of_empty_pm_paths(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        GenerateTaskBlueprintCommandV1,
        build_chief_engineer_blueprint_portfolio,
        generate_task_blueprint,
        project_chief_engineer_task_blueprint,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _command

    task = _intent()
    task["id"] = "TASK-A"
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload()))
    generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            workspace=str(tmp_path),
            task_id="TASK-A",
            run_id="run-intent",
            objective=task["goal"],
            context={**portfolio.to_task_blueprint_context(), "pm_task_contract": task},
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, "TASK-A"),
        )
    )
    executor = _executor(tmp_path)
    assert set(executor._qa_delivery_targets([task], run_id="run-intent")) == {
        "src/main.py",
        "tests/test_main.py",
        "pyproject.toml",
    }
    assert executor._qa_delivery_targets([task], run_id="foreign-run") == []
