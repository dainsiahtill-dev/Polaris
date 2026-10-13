"""Factory CE proposals keep machine authority and scoped repair server-owned."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerSemanticRepairCandidateV1,
    chief_engineer_semantic_repair_task_set_hash,
)
from polaris.cells.chief_engineer.blueprint.public.service._delegated_recipes import prepare_delegated_recipes
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor


def test_provider_recipe_schema_accepts_omitted_machine_hash(tmp_path: Path) -> None:
    schema = _executor(tmp_path)._chief_engineer_structured_output_contract(("TASK-1",)).json_schema
    row_schema = schema["properties"]["project_completion_contract"]["properties"]["obligations"]["properties"][
        "verification"
    ]["items"]
    proposal = {
        "obligation_id": "ENV",
        "modality": "environment_prep",
        "argv": ["npm", "install"],
        "cwd": ".",
        "applicability": "required",
        "owner_task_id": "TASK-1",
        "covers_obligation_ids": ["SOURCE"],
    }
    assert list(Draft202012Validator(row_schema).iter_errors(proposal)) == []


def test_policy_recipe_failure_selects_diagnosed_row_patch(tmp_path: Path) -> None:
    task = SimpleNamespace(
        task_id="TASK-1", planning_contract_version="pm.task_intent.v1", verification_authority="chief_engineer"
    )
    requirements = {
        "obligations": {
            "verification": [
                {
                    "obligation_id": "ENV",
                    "modality": "environment_prep",
                    "argv": ["install"],
                    "cwd": ".",
                    "applicability": "required",
                    "owner_task_id": "TASK-1",
                    "covers_obligation_ids": ["SOURCE"],
                    "command_authority_hash": None,
                }
            ],
            "entrypoints": [],
        }
    }
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        prepare_delegated_recipes(
            SimpleNamespace(workspace=str(tmp_path), tasks=(task,)),
            requirements,
            SimpleNamespace(verification_command_authority=()),
        )
    diagnostics = caught.value.details["verification_recipe_diagnostics"]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project",
        run_id="factory",
        pm_contract_hash="1" * 64,
        task_ids=("TASK-1",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-1",)),
        candidate={"construction_plan": {"task_plans": {"TASK-1": {}}}, "project_completion_contract": requirements},
    )
    finding = {
        "code": "invalid_project_completion_contract",
        "repair_operation": "verification_recipe_upsert",
        "detail": str(caught.value),
        "verification_recipe_diagnostics": diagnostics,
    }
    diagnosis = _executor(tmp_path)._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate,
        output_errors=["chief_engineer.completion_normalization:" + json.dumps(finding)],
    )
    assert diagnosis is not None
    assert diagnosis.allowed_operations == ("verification_recipe_upsert",)
    assert diagnosis.verification_recipe_diagnostics == tuple(diagnostics)
