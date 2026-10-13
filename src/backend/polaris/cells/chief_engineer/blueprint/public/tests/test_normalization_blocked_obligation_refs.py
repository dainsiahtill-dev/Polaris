"""Failed CE normalization preserves diagnostic identity without granting authority."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerSemanticRepairCandidateV1,
    build_chief_engineer_blueprint_portfolio,
    chief_engineer_semantic_repair_task_set_hash,
    project_chief_engineer_effective_obligation_view,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _command, _payload
from polaris.cells.factory.pipeline.internal.factory_ce_evidence import chief_engineer_portfolio_output_errors
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

BLOCKED_PREFIX = "chief_engineer.completion_reference_normalization_blocked:"


def _proposal() -> dict[str, Any]:
    payload = _payload()
    entrypoint = payload["project_completion_contract"]["obligations"]["entrypoints"][0]
    entrypoint["obligation_id"] = "OBL-EP-CLI"
    payload["project_completion_contract"]["obligations"]["entrypoints"].append(
        {**entrypoint, "obligation_id": "OBL-EP-VERIFY"}
    )
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        {
            "invariant_id": "INV-1",
            "statement": "CLI output is deterministic and checked by executable tests.",
            "owner_task_id": "TASK-A",
            "consumer_task_ids": [],
            "covered_obligation_ids": ["source", "tests", "OBL-EP-CLI", "OBL-EP-VERIFY"],
            "verification_examples": [{"given": "same input", "when": "CLI runs", "then": "same output"}],
        }
    ]
    payload["construction_plan"]["task_plans"]["TASK-A"]["behavior_invariant_refs"] = ["INV-1"]
    payload["project_completion_contract"]["obligations"]["verification"][0]["command_authority_hash"] = (
        "sha256:" + "f" * 64
    )
    return payload


def test_failed_recipe_normalization_keeps_declared_ids_only_as_blocked_diagnostics(tmp_path: Path) -> None:
    payload = _proposal()
    before = deepcopy(payload)
    command = _command(tmp_path, payload)
    view = project_chief_engineer_effective_obligation_view(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    assert view["final_roster_complete"] is False
    assert view["execution_authority"] is False
    assert view["obligations"]["entrypoints"] == []
    assert view["obligations"]["verification"] == []
    assert {"OBL-EP-CLI", "OBL-EP-VERIFY", "environment", "check"}.issubset(
        view["normalization_blocked_completion_obligation_ids"]
    )
    assert not {"OBL-EP-CLI", "OBL-EP-VERIFY"}.intersection(view["retained_completion_obligation_ids"])
    assert payload == before
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="command_authority_hash"):
        build_chief_engineer_blueprint_portfolio(command)


def test_factory_reports_declared_reference_normalization_block_instead_of_unknown(tmp_path: Path) -> None:
    payload = _proposal()
    command = _command(tmp_path, payload)
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    blocked = [json.loads(error[len(BLOCKED_PREFIX) :]) for error in errors if error.startswith(BLOCKED_PREFIX)]
    assert blocked == [
        {"invariant_index": 0, "obligation_ids": ["OBL-EP-CLI", "OBL-EP-VERIFY"], "execution_authority": False}
    ]
    assert not any("unknown completion obligations" in error for error in errors), errors
    assert any(error.startswith("chief_engineer.completion_normalization:") for error in errors)


@pytest.mark.parametrize("reference", ["NEVER-DECLARED", "DENIED-ARTIFACT"])
def test_missing_and_pm_denied_ids_still_report_unknown(tmp_path: Path, reference: str) -> None:
    payload = _proposal()
    if reference == "DENIED-ARTIFACT":
        payload["project_completion_contract"]["obligations"]["artifacts"].append(
            {
                "obligation_id": reference,
                "path": ".polaris/runtime/forged.json",
                "semantic_role": "config",
                "applicability": "required",
                "owner_task_id": "TASK-A",
            }
        )
    payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"].append(
        reference
    )
    command = _command(tmp_path, payload)
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    assert any(reference in error and "unknown completion obligations" in error for error in errors), errors


@pytest.mark.parametrize("identity_failure", ["unknown-owner", "duplicate-id", "noncanonical-id"])
def test_invalid_declared_identity_cannot_become_blocked_reference(tmp_path: Path, identity_failure: str) -> None:
    payload = _proposal()
    rows = payload["project_completion_contract"]["obligations"]["entrypoints"]
    if identity_failure == "unknown-owner":
        rows[0]["owner_task_id"] = "UNKNOWN"
    elif identity_failure == "duplicate-id":
        rows.append(deepcopy(rows[0]))
    else:
        rows[0]["obligation_id"] = " OBL-EP-CLI "
    command = _command(tmp_path, payload)
    view = project_chief_engineer_effective_obligation_view(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    assert "OBL-EP-CLI" not in view["normalization_blocked_completion_obligation_ids"]
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    assert errors


def test_canonical_recipe_correction_restores_preserved_refs_and_immutable_pm_identity(tmp_path: Path) -> None:
    payload = _proposal()
    command = _command(tmp_path, payload)
    before = deepcopy(payload["construction_plan"])
    payload["project_completion_contract"]["obligations"]["verification"][0]["command_authority_hash"] = None
    view = project_chief_engineer_effective_obligation_view(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    assert view["final_roster_complete"] is True
    assert view["normalization_blocked_completion_obligation_ids"] == []
    assert {"OBL-EP-CLI", "OBL-EP-VERIFY"}.issubset(view["retained_completion_obligation_ids"])
    assert view["pm_contract_hash"] == command.authority_carrier.pm_contract_hash
    result = build_chief_engineer_blueprint_portfolio(command)
    assert result.project_completion_contract is not None
    assert payload["construction_plan"] == before
    assert (
        OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            payload, tasks=command.tasks, authority_carrier=command.authority_carrier
        )
        == []
    )
    assert result.execution_authorized is False
    assert result.handoff_ready is False


def test_caller_cannot_label_an_undeclared_id_as_normalization_blocked() -> None:
    payload = _proposal()
    payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"].append(
        "NEVER-DECLARED"
    )
    errors = chief_engineer_portfolio_output_errors(
        payload,
        task_ids=("TASK-A",),
        effective_completion_obligation_ids=frozenset({"source", "tests"}),
        normalization_blocked_completion_obligation_ids=frozenset({"OBL-EP-CLI", "OBL-EP-VERIFY", "NEVER-DECLARED"}),
    )
    assert any("NEVER-DECLARED" in error and "unknown completion obligations" in error for error in errors)
    assert all("NEVER-DECLARED" not in error for error in errors if error.startswith(BLOCKED_PREFIX))


def test_recipe_diagnostics_survive_projection_without_false_behavior_repair(tmp_path: Path) -> None:
    payload = _proposal()
    command = _command(tmp_path, payload)
    view = project_chief_engineer_effective_obligation_view(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as raised:
        build_chief_engineer_blueprint_portfolio(command)
    diagnostics = view["normalization_findings"][0]["verification_recipe_diagnostics"]
    assert diagnostics == raised.value.details["verification_recipe_diagnostics"]
    assert diagnostics[0]["obligation_id"] == "environment"
    assert diagnostics[0]["profile_feedback"]["execution_authority"] is False
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=command.workspace,
        project_id=command.authority_carrier.project_id,
        run_id=command.run_id,
        pm_contract_hash=command.authority_carrier.pm_contract_hash,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=command.tasks, authority_carrier=command.authority_carrier
    )
    # Recipes use the existing complete-draft correction route. A blocked graph
    # reference must not invent behavior/entrypoint patch authority.
    assert (
        OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(candidate=candidate, output_errors=errors)
        is None
    )
