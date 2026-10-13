"""Unaccepted CE drafts can correct model-owned references under unchanged PM authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.tests._characterization_helpers import _single_task_chief_engineer_result
from polaris.cells.factory.pipeline.tests.test_ce_owner_continuation import _run
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService


def _role_result() -> RoleExecutionResultV1:
    source = _single_task_chief_engineer_result()
    return RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace=".",
        output=source.output,
        metadata=source.metadata,
    )


def _wrong_verifier_binding() -> RoleExecutionResultV1:
    result = _role_result()
    rows = result.metadata["structured_output"]["project_completion_contract"]["obligations"]["verification"]
    rows[1]["command_authority_hash"] = rows[0]["command_authority_hash"]
    return result


def test_unclassified_verifier_binding_is_returned_to_model_draft_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[Any] = []
    rejected = _wrong_verifier_binding()
    original = json.loads(json.dumps(rejected.metadata["structured_output"]))

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            return rejected if len(commands) == 1 else _role_result()

    result, review = _run(tmp_path, monkeypatch, Runtime, "factory-ce-verifier-draft-revision")

    assert result.status == "success", result.output
    assert len(commands) == 2
    repair = commands[1]
    assert "project_completion_contract" in repair.structured_output_contract.json_schema["properties"]
    assert "verification" in repair.objective
    assert "unaccepted" in repair.objective
    expected_hash = hashlib.sha256(
        json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert repair.context["chief_engineer_schema_repair_base_candidate_hash"] == expected_hash
    assert repair.context["project_completion_authority"] == commands[0].context["project_completion_authority"]
    assert "chief_engineer_semantic_repair_diagnosis_hash" not in repair.context
    assert "chief_engineer_semantic_repair_receipt" not in repair.context
    assert review["generated_blueprints"] == 1
    for command in commands:
        task = TaskRuntimeService(str(tmp_path)).get_task(command.task_id)
        assert task is not None and task["status"] == "completed"
    assert rejected.metadata["structured_output"] == original


def test_repeated_invalid_draft_does_not_approve_a_new_command_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[Any] = []

    class Runtime:
        async def execute_role_task(self, command: Any) -> Any:
            commands.append(command)
            return _wrong_verifier_binding()

    result, review = _run(tmp_path, monkeypatch, Runtime, "factory-ce-verifier-draft-no-progress")

    assert result.status == "failed"
    assert len(commands) == 2
    assert review["generated_blueprints"] == 0
    assert any("no_progress" in signal["code"] for signal in review["signals"])
