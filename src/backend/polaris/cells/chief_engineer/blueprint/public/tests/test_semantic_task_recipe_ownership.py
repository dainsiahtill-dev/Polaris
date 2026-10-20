"""Semantic task recipes must be diagnosed before strict task handoff.

The fixture uses the existing synthetic trusted PM-carrier issuer. Carrier
validation, portfolio normalization and policy-profile checks remain real;
this is not a replay of an original Factory PM authority or physical call.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _command,
    _payload,
    _task,
)


def _ownership_case(
    tmp_path: Path, *, own_recipe: bool = False
) -> tuple[BuildChiefEngineerBlueprintPortfolioCommandV1, dict[str, Any]]:
    task_a = _task()
    task_b = replace(task_a, task_id="TASK-B", objective="Deliver the independent domain helper")
    payload = _payload()
    payload["construction_plan"]["task_plans"]["TASK-B"] = {"steps": ["Implement the domain helper"]}
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append(
        {
            "obligation_id": "domain-helper",
            "path": "src/domain.py",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-B",
        }
    )
    if own_recipe:
        obligations["verification"].append(
            {
                "obligation_id": "check-domain-helper",
                "modality": "test",
                "command_authority_hash": None,
                "applicability": "optional",
                "covers_obligation_ids": ["domain-helper"],
                "owner_task_id": "TASK-B",
                "argv": ["python", "-m", "pytest", "-q"],
                "cwd": ".",
            }
        )
    return _command(tmp_path, payload, tasks=(task_a, task_b)), payload


def test_missing_own_recipe_is_diagnosed_by_public_portfolio_owner(tmp_path: Path) -> None:
    """Reject upstream with a repairable owner finding, before per-task failure."""
    command, _payload_before = _ownership_case(tmp_path)

    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(command)

    findings = caught.value.details.get("verification_recipe_diagnostics", [])
    assert any(
        row.get("owner_task_id") == "TASK-B"
        and row.get("collection") == "verification"
        and row.get("source_path") == ["obligations", "verification"]
        for row in findings
    )
    assert caught.value.details.get("repair_operation") == "verification_recipe_upsert"


def test_existing_optional_own_recipe_satisfies_the_existing_owner_predicate(tmp_path: Path) -> None:
    """An existing own capability does not create a new required-modality gate."""
    command, _payload_before = _ownership_case(tmp_path, own_recipe=True)
    portfolio = build_chief_engineer_blueprint_portfolio(command)
    completion = portfolio.project_completion_contract
    assert completion is not None
    own = [row for row in completion.verification_command_authority if row.task_id == "TASK-B"]
    assert len(own) == 1 and own[0].modality == "test"
    row = next(item for item in completion.obligations.verification if item.owner_task_id == "TASK-B")
    assert row.applicability == "optional"
    assert row.command_authority_hash == own[0].authority_hash


def test_malformed_artifact_does_not_mask_the_original_typed_recipe_failure(tmp_path: Path) -> None:
    command, payload = _ownership_case(tmp_path)
    payload["project_completion_contract"]["obligations"]["artifacts"][0] = {}
    payload["project_completion_contract"]["obligations"]["verification"][1]["modality"] = "build"
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="profile") as caught:
        build_chief_engineer_blueprint_portfolio(replace(command, llm_blueprint=payload))
    assert caught.value.code == "invalid_chief_engineer_verification_recipe"


def test_explicit_pm_scope_still_rejects_outside_artifacts(tmp_path: Path) -> None:
    command, payload = _ownership_case(tmp_path)
    tasks = (command.tasks[0], replace(command.tasks[1], scope_paths=("owned",)))
    bound = _command(tmp_path, payload, tasks=tasks)
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="scope"):
        build_chief_engineer_blueprint_portfolio(bound)


def test_legacy_task_is_not_given_a_new_own_recipe_requirement(tmp_path: Path) -> None:
    command, payload = _ownership_case(tmp_path)
    task = replace(
        command.tasks[1],
        planning_contract_version="",
        topology_authority="pm",
        verification_authority="pm",
        target_files=("src/domain.py",),
        scope_paths=("src/domain.py",),
    )
    commands = (
        VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("python", "-m", "pytest", "-q")),
    )
    portfolio = build_chief_engineer_blueprint_portfolio(
        _command(tmp_path, payload, tasks=(command.tasks[0], task), commands=commands)
    )
    assert not any(
        row.task_id == task.task_id for row in portfolio.project_completion_contract.verification_command_authority
    )


def test_legacy_explicit_empty_command_authority_is_not_filled_from_a_ce_proposal(tmp_path: Path) -> None:
    command, payload = _ownership_case(tmp_path, own_recipe=True)
    task = replace(
        command.tasks[1],
        planning_contract_version="",
        topology_authority="pm",
        verification_authority="pm",
        target_files=("src/domain.py",),
        scope_paths=("src/domain.py",),
    )
    # The legacy TASK-B has an explicit empty own command set; TASK-A's known
    # command keeps the synthetic mixed carrier valid without lending it to B.
    commands = (
        VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("python", "-m", "pytest", "-q")),
    )
    bound = _command(tmp_path, payload, tasks=(command.tasks[0], task), commands=commands)
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="delegat"):
        build_chief_engineer_blueprint_portfolio(bound)
