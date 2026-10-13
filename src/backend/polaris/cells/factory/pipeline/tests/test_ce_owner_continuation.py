"""CE correction stays with its immutable candidate and existing Factory owner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _assert_no_chief_engineer_lease_keeper_threads,
    _capture_chief_engineer_lease_keepers,
    _executor,
    _factory_stage_context,
    _invalid_chief_engineer_stream_result,
    _single_task_chief_engineer_result,
    _write_minimal_chief_engineer_plan,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path

_PATCH_ERROR = (
    "structured_output_payload_schema_mismatch:task_behavior_ref_replacements.TASK-CANCEL:'' is not of type 'array'"
)


def _dangling_candidate() -> Any:
    result = _single_task_chief_engineer_result()
    construction = result.metadata["structured_output"]["construction_plan"]
    construction["shared_behavior_contract"]["invariants"] = []
    construction["task_plans"]["TASK-CANCEL"]["behavior_invariant_refs"] = ["INV-UNKNOWN"]
    return result


def _invalid_patch() -> Any:
    result = _invalid_chief_engineer_stream_result()
    result.error_message = _PATCH_ERROR
    return result


def _valid_patch(command: Any) -> Any:
    result = _single_task_chief_engineer_result()
    schema = command.structured_output_contract.json_schema
    payload: dict[str, Any] = {
        "base_candidate_hash": command.context["chief_engineer_semantic_repair_base_candidate_hash"],
        "diagnosis_hash": command.context["chief_engineer_semantic_repair_diagnosis_hash"],
        "task_behavior_ref_replacements": {"TASK-CANCEL": []},
    }
    for field in schema["required"]:
        if field not in payload:
            payload[field] = {} if schema["properties"][field].get("type") == "object" else []
    Draft202012Validator(schema).validate(payload)
    result.output = json.dumps(payload)
    result.metadata["structured_output"] = payload
    return result


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime: type[Any], name: str) -> tuple[Any, dict[str, Any]]:
    executor = _executor(tmp_path)
    keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
    _write_minimal_chief_engineer_plan(executor)
    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", runtime)
    run = FactoryRun(
        id=name,
        config=FactoryConfig(name="unit-ce-continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00+00:00",
    )
    result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))
    review = json.loads(
        Path(resolve_logical_path(str(tmp_path), f"runtime/state/blueprints/{run.id}.review.json")).read_text(
            encoding="utf-8"
        )
    )
    assert all(keeper.is_alive is False for keeper in keepers)
    _assert_no_chief_engineer_lease_keeper_threads()
    return result, review


def test_schema_and_semantic_corrections_do_not_exhaust_one_fixed_call_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            if len(commands) == 1:
                return _invalid_chief_engineer_stream_result()
            if len(commands) == 2:
                return _dangling_candidate()
            if len(commands) == 3:
                primary = TaskRuntimeService(str(tmp_path)).get_task(f"CE-PORTFOLIO-{command.run_id}")
                assert primary is not None and primary["status"] != "completed"
                return _invalid_patch()
            return _valid_patch(command)

    result, review = _run(tmp_path, monkeypatch, Runtime, "factory-ce-schema-patch-correction")

    assert result.status == "success", result.output
    assert len(commands) == 4
    assert review["generated_blueprints"] == 1
    assert review["llm_call_count"] == 4
    assert (
        commands[2].context["chief_engineer_semantic_repair_base_candidate_hash"]
        == commands[3].context["chief_engineer_semantic_repair_base_candidate_hash"]
    )
    assert (
        commands[2].context["chief_engineer_semantic_repair_diagnosis_hash"]
        == commands[3].context["chief_engineer_semantic_repair_diagnosis_hash"]
    )
    assert _PATCH_ERROR in commands[3].objective
    assert len({command.task_id for command in commands}) == 2
    assert len({command.execution_attempt.session_id for command in commands}) == 4
    assert [command.execution_attempt.attempt for command in commands[1:]] == [1, 2, 3]
    task_runtime = TaskRuntimeService(str(tmp_path))
    for task_id in (commands[0].task_id, commands[1].task_id):
        row = task_runtime.get_task(task_id)
        assert row is not None and row["status"] == "completed"
    assert commands[3].metadata["chief_engineer_repair_round"] == 3
    assert "entrypoint_remove_obligation_ids" not in commands[3].objective
    assert "may be omitted" in commands[3].objective
    assert not any("provider_calls_capped" in signal for signal in review["signals"])


def test_repeated_actual_patch_failure_stops_without_changing_the_frozen_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            return _dangling_candidate() if len(commands) == 1 else _invalid_patch()

    result, review = _run(tmp_path, monkeypatch, Runtime, "factory-ce-repeated-patch-error")

    assert result.status == "failed"
    assert len(commands) == 3
    assert any("no_progress" in signal["code"] for signal in review["signals"])
    assert (
        commands[1].context["chief_engineer_semantic_repair_base_candidate_hash"]
        == commands[2].context["chief_engineer_semantic_repair_base_candidate_hash"]
    )
    assert _PATCH_ERROR in commands[2].objective


@pytest.mark.parametrize("failure", ["provider_error", "factory_role_execution_authority_revoked"])
def test_fatal_ce_failure_never_enters_owner_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> RoleExecutionResultV1:
            commands.append(command)
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="chief_engineer",
                workspace=str(tmp_path),
                task_id=command.task_id,
                error_code=failure,
                error_message=failure,
                metadata={"provider_id": "fixture", "model": "fixture"},
            )

    result, review = _run(tmp_path, monkeypatch, Runtime, f"factory-ce-fatal-{failure}")

    assert result.status == "failed"
    assert len(commands) == 1
    assert not any("repair_started" in signal["code"] for signal in review["signals"])


@pytest.mark.parametrize("failure", ["provider_error", "factory_role_execution_authority_revoked", "policy_denied"])
def test_fatal_after_frozen_candidate_keeps_its_failure_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            if len(commands) == 1:
                return _invalid_chief_engineer_stream_result()
            if len(commands) == 2:
                return _dangling_candidate()
            metadata = dict(_dangling_candidate().metadata)
            metadata["chief_engineer_semantic_repair_candidate_hash"] = command.context[
                "chief_engineer_semantic_repair_base_candidate_hash"
            ]
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="chief_engineer",
                workspace=str(tmp_path),
                task_id=command.task_id,
                error_code=failure,
                error_message=failure,
                metadata=metadata,
            )

    result, review = _run(tmp_path, monkeypatch, Runtime, f"factory-ce-retained-fatal-{failure}")

    assert result.status == "failed"
    assert len(commands) == 3
    assert result.metadata["error_code"] == "chief_engineer.llm_review_failed"
    assert result.metadata["failure_class"] == "ROLE_LLM_REVIEW_FAILED"
    assert failure in result.metadata["root_cause_hint"]
    assert not any(signal["code"] == "chief_engineer.semantic_repair_exhausted" for signal in review["signals"])
