"""Local Factory command capture: canned role port and synthetic cutoff, no provider wire."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public.tests.test_recipe_diagnosis_scoped_projection import _recipe_case
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _bootstrap_fact_stream_workspace,
    _characterization_authority_port,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _capture_repair_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Real Factory objective/schema producer and TaskRuntime claim; provider/PM cutoff are test ports."""
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    _bootstrap_fact_stream_workspace(tmp_path)
    executor = OrchestrationStageExecutor(tmp_path)
    source, candidate, diagnosis = _recipe_case(tmp_path)
    row = deepcopy(candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"][1])
    row["command"] = "python -m src.main"
    patch = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "entrypoint_upserts": [row],
    }
    commands = []

    class RecordingRolePort:
        async def execute_role_task(self, command: Any) -> RoleExecutionResultV1:
            commands.append(command)
            return RoleExecutionResultV1(
                ok=True,
                status="completed",
                role="chief_engineer",
                workspace=str(tmp_path),
                run_id=command.run_id,
                task_id=command.task_id,
                metadata={"structured_output": patch, "provider_id": "canned-provider", "model": "canned-model"},
            )

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", RecordingRolePort)
    port = _characterization_authority_port()
    binding = port.mint_authority_binding("chief_engineer")
    run = FactoryRun(
        id="run-intent",
        config=FactoryConfig(name="project-intent"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-20T00:00:00+00:00",
    )
    prior = RoleExecutionResultV1(
        ok=False,
        status="failed",
        role="chief_engineer",
        workspace=str(tmp_path),
        error_code="output_validation_failed",
        error_message="invalid recipe",
    )
    deadline = SimpleNamespace(timeout_seconds=120, to_dict=lambda: {"timeout_seconds": 120})
    result = asyncio.run(
        executor._run_chief_engineer_schema_repair(
            run=run,
            authority_port=port,
            authority_binding=binding,
            prior_result=prior,
            portfolio_context=executor._chief_engineer_portfolio_context(
                [
                    {
                        "id": source.tasks[0].task_id,
                        "goal": source.tasks[0].objective,
                        "schema_version": "pm.task_intent.v1",
                        "product_goal": source.tasks[0].objective,
                        "required_requirements": [],
                        "product_acceptance": list(source.tasks[0].observable_acceptance),
                        "acceptance": list(source.tasks[0].observable_acceptance),
                        "metadata": {
                            "planning_contract_version": "pm.task_intent.v1",
                            "topology_authority": "chief_engineer",
                            "verification_authority": "chief_engineer",
                        },
                        "target_files": list(source.tasks[0].target_files),
                    }
                ],
                run_id=run.id,
            ),
            portfolio_task_ids=candidate.task_ids,
            portfolio_tasks=source.tasks,
            deadline_decision=deadline,
            semantic_candidate=candidate,
            semantic_diagnosis=diagnosis,
            authority_carrier=source.authority_carrier,
            prompt_profile_identity={
                "language": "python",
                "task_type": "implement",
                "prompt_stage": "blueprint",
                "artifact": "cli",
            },
        )
    )
    assert len(commands) == 1
    assert result.ok, result.error_message
    return commands[0], result


def test_factory_guidance_reuses_bound_recipe_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    command, _result = _capture_repair_command(tmp_path, monkeypatch)
    context = command.context["chief_engineer_semantic_repair_provider_context"]
    assert context["entrypoint_replacement_rule"] in command.objective
    assert "provide a same-owner, same-kind entrypoint_upsert atomically" not in command.objective
    assert (
        "If an immutable field differs, use a new unique obligation_id; never repurpose an old ID."
        not in command.objective
    )
    assert command.stream is False
    assert command.structured_output_contract is not None
    ids = command.structured_output_contract.json_schema["properties"]["entrypoint_upserts"]["items"]["properties"][
        "obligation_id"
    ]["enum"]
    assert ids == ["broken-cli"]
    assert (
        command.structured_output_contract.json_schema["properties"]["entrypoint_remove_obligation_ids"]["maxItems"]
        == 0
    )
