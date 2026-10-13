"""Model draft revisions keep serial verifier authority and the latest frozen input."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import VerificationCommandAuthorityV1
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _assert_no_chief_engineer_lease_keeper_threads,
    _capture_chief_engineer_lease_keepers,
    _executor,
    _factory_stage_context,
    _library_completion_requirements,
    _single_task_chief_engineer_result,
)
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path

_SOURCE = "TASK-SOURCE"
_TEST = "TASK-TEST"


def _authorities() -> tuple[VerificationCommandAuthorityV1, ...]:
    return (
        VerificationCommandAuthorityV1(task_id=_SOURCE, modality="build", argv=("python", "-m", "compileall", ".")),
        VerificationCommandAuthorityV1(
            task_id=_SOURCE, modality="test", argv=("python", "-m", "pytest", "-q"), cwd="src"
        ),
        VerificationCommandAuthorityV1(
            task_id=_TEST, modality="test", argv=("python", "-m", "pytest", "-q", "tests/test_module.py"), cwd="."
        ),
    )


def _payload() -> dict[str, Any]:
    completion = _library_completion_requirements(
        "src/module.py", owner_task_ids=(_SOURCE,), test_path="tests/test_module.py", test_owner_task_id=_TEST
    )
    rows = completion["obligations"]["verification"]
    rows[0]["command_authority_hash"] = _authorities()[0].authority_hash
    rows[1]["command_authority_hash"] = _authorities()[1].authority_hash
    return {
        "construction_plan": {
            "project_design_intent": "Keep module behavior and its tests in their declared owners.",
            "project_interface_contract": {
                "provider_declarations": [
                    {
                        "path": "src/module.py",
                        "name": "module_value",
                        "symbol_kind": "function",
                        "signature": "module_value(value: int) -> int",
                        "semantic_role": "module public behavior",
                    }
                ],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-MODULE",
                        "owner_task_id": _SOURCE,
                        "consumer_task_ids": [_TEST],
                        "covered_obligation_ids": ["artifact-1", "artifact-test"],
                        "statement": "Module values preserve the declared integer result.",
                        "verification_examples": [
                            {
                                "given": "an integer",
                                "when": "module_value is called",
                                "then": "the result is an integer",
                            }
                        ],
                    }
                ]
            },
            "task_plans": {
                task_id: {
                    "behavior_invariant_refs": ["INV-MODULE"],
                    "implementation": ["Keep declared module behavior"],
                }
                for task_id in (_SOURCE, _TEST)
            },
        },
        "project_completion_contract": completion,
        "scope_for_apply": ["src/module.py", "tests/test_module.py"],
        "risk_flags": [],
    }


def _result(
    payload: dict[str, Any], command: Any, *, carried_base: dict[str, Any] | None = None
) -> RoleExecutionResultV1:
    metadata = dict(_single_task_chief_engineer_result().metadata)
    metadata["structured_output"] = deepcopy(payload)
    if carried_base is not None:
        metadata["chief_engineer_schema_repair_base_candidate"] = deepcopy(carried_base)
    return RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace=command.workspace,
        task_id=command.task_id,
        run_id=command.run_id,
        session_id=command.execution_attempt.session_id,
        output=json.dumps(payload),
        metadata=metadata,
    )


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _run_serial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime: type[Any], run_id: str
) -> tuple[Any, dict[str, Any]]:
    executor = _executor(tmp_path)
    authorities = _authorities()
    tasks = [
        {
            "id": task_id,
            "title": "Implement module" if task_id == _SOURCE else "Test module",
            "goal": "Keep the declared module contract observable.",
            "target_files": ["src/module.py" if task_id == _SOURCE else "tests/test_module.py"],
            "scope_paths": ["src/module.py" if task_id == _SOURCE else "tests/test_module.py"],
            "depends_on": [] if task_id == _SOURCE else [_SOURCE],
            "acceptance_criteria": ["Declared module behavior is covered"],
            "execution_checklist": ["Implement the owned module artifact"],
            "verification_commands": [
                {"modality": row.modality, "argv": list(row.argv), "cwd": row.cwd}
                for row in authorities
                if row.task_id == task_id
            ],
        }
        for task_id in (_SOURCE, _TEST)
    ]
    executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
    original_loader = executor._load_chief_engineer_portfolio_authority

    async def load_authority(*, run: Any, pm_tasks: Any, portfolio_tasks: Any) -> Any:
        authority = await original_loader(run=run, pm_tasks=pm_tasks, portfolio_tasks=portfolio_tasks)
        return replace(
            authority, verification_command_authority=executor._chief_engineer_verification_command_authority(pm_tasks)
        )

    monkeypatch.setattr(executor, "_load_chief_engineer_portfolio_authority", load_authority)
    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", runtime)
    keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
    run = FactoryRun(
        id=run_id,
        config=FactoryConfig(name="unit-serial-module"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00+00:00",
    )
    result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))
    review = json.loads(
        Path(resolve_logical_path(str(tmp_path), f"runtime/state/blueprints/{run_id}.review.json")).read_text(
            encoding="utf-8"
        )
    )
    assert all(not keeper.is_alive for keeper in keepers)
    _assert_no_chief_engineer_lease_keeper_threads()
    return result, review


def _assert_revision(command: Any, base: dict[str, Any], primary: Any) -> None:
    assert "project_completion_contract" in command.structured_output_contract.json_schema["properties"]
    assert command.context["chief_engineer_schema_repair_mode"] == "reconstruction"
    assert command.context["chief_engineer_schema_repair_base_candidate_hash"] == _hash(base)
    assert command.context["project_completion_authority"] == primary.context["project_completion_authority"]
    assert command.context["pm_task_contracts"] == primary.context["pm_task_contracts"]
    assert command.context["target_files"] == primary.context["target_files"]
    assert command.context["scope_paths"] == primary.context["scope_paths"]
    assert "chief_engineer_semantic_repair_receipt" not in command.context
    assert "chief_engineer_semantic_repair_diagnosis_hash" not in command.context
    assert json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")) in command.objective


def test_serial_verifier_revision_selects_existing_later_owner_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _payload()
    corrected = deepcopy(original)
    corrected["project_completion_contract"]["obligations"]["verification"][1]["command_authority_hash"] = (
        _authorities()[2].authority_hash
    )
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            if len(commands) == 1:
                return _result(original, command)
            _assert_revision(command, original, commands[0])
            assert "dependency-complete verifier owner" in command.objective
            assert (
                _authorities()[2].to_dict()
                in command.context["project_completion_authority"]["verification_command_authority"]
            )
            return _result(corrected, command)

    result, review = _run_serial(tmp_path, monkeypatch, Runtime, "factory-serial-verifier-revision")
    assert result.status == "success", result.output
    assert len(commands) == 2
    assert review["generated_blueprints"] == 2
    assert corrected["construction_plan"] == original["construction_plan"]
    assert corrected["scope_for_apply"] == original["scope_for_apply"]
    portfolio = json.loads(
        Path(resolve_logical_path(str(tmp_path), review["portfolio"]["portfolio_path"])).read_text(encoding="utf-8")
    )
    completion = portfolio["project_completion_contract"]
    verifier = next(row for row in completion["obligations"]["verification"] if row["obligation_id"] == "verify-test")
    later_authority = _authorities()[2]
    assert verifier["owner_task_id"] == _TEST
    assert verifier["command_authority_hash"] == later_authority.authority_hash
    assert verifier["command"] == later_authority.command
    assert verifier["covers_obligation_ids"] == ["artifact-test"]
    assert later_authority.to_dict() in completion["verification_command_authority"]
    assert completion["pm_contract_hash"] == commands[0].context["project_completion_authority"]["pm_contract_hash"]
    expected_invariant = original["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    actual_invariant = portfolio["shared_behavior_contract"]["invariants"][0]
    assert {key: actual_invariant[key] for key in expected_invariant} == expected_invariant
    expected_provider = original["construction_plan"]["project_interface_contract"]["provider_declarations"][0]
    actual_provider = portfolio["project_interface_contract"]["provider_declarations"][0]
    assert {key: actual_provider[key] for key in expected_provider} == expected_provider
    task_runtime = TaskRuntimeService(str(tmp_path))
    for command in commands:
        row = task_runtime.get_task(command.task_id)
        assert row is not None and row["status"] == "completed"
    assert commands[0].execution_attempt.session_id != commands[1].execution_attempt.session_id


def test_uncommitted_serial_verifier_proposal_remains_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    initial = _payload()
    uncommitted = deepcopy(initial)
    row = uncommitted["project_completion_contract"]["obligations"]["verification"][1]
    row.update(command_authority_hash="f" * 64, owner_task_id="TASK-UNCOMMITTED", covers_obligation_ids=[])
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            return _result(initial if len(commands) == 1 else uncommitted, command)

    result, review = _run_serial(tmp_path, monkeypatch, Runtime, "factory-serial-verifier-uncommitted")
    assert result.status == "failed"
    assert len(commands) == 3
    assert review["generated_blueprints"] == 0
    assert review["portfolio"] == {}
    assert any("no_progress" in signal["code"] for signal in review["signals"])
    assert "TASK-UNCOMMITTED" not in {
        row["task_id"] for row in commands[-1].context["project_completion_authority"]["verification_command_authority"]
    }


def test_latest_full_revision_and_error_supersede_stale_carried_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _payload()
    intermediate = deepcopy(original)
    intermediate["project_completion_contract"]["obligations"]["verification"][1]["command_authority_hash"] = (
        _authorities()[0].authority_hash
    )
    corrected = deepcopy(intermediate)
    corrected["project_completion_contract"]["obligations"]["verification"][1]["command_authority_hash"] = (
        _authorities()[2].authority_hash
    )
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            if len(commands) == 1:
                return _result(original, command)
            if len(commands) == 2:
                return _result(intermediate, command, carried_base=original)
            _assert_revision(command, intermediate, commands[0])
            assert "command authority modality mismatch" in command.objective
            assert "command authority modality mismatch" in command.context["failure_feedback"]["detail"]
            return _result(corrected, command, carried_base=original)

    result, review = _run_serial(tmp_path, monkeypatch, Runtime, "factory-serial-verifier-latest-base")
    assert result.status == "success", result.output
    assert len(commands) == 3
    assert review["generated_blueprints"] == 2
    assert (
        commands[1].context["chief_engineer_schema_repair_base_candidate_hash"]
        != commands[2].context["chief_engineer_schema_repair_base_candidate_hash"]
    )
    assert corrected["construction_plan"] == original["construction_plan"]
