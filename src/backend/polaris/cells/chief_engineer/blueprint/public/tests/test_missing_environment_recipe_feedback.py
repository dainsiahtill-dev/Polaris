"""Required environment preparation stays closed with actionable CE feedback."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _command,
    _payload,
    _task,
)


def test_missing_environment_recipe_names_delegated_repair_without_inventing_command(tmp_path: Path) -> None:
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"].pop(0)
    original = deepcopy(payload)
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    error = caught.value
    assert error.code == "invalid_project_completion_contract"
    diagnostic = error.details["verification_recipe_diagnostics"][0]
    assert diagnostic["error_code"] == "missing_required_verification_recipe"
    assert diagnostic["collection"] == "verification"
    assert diagnostic["source_path"] == ["obligations", "verification"]
    assert diagnostic["modality"] == "environment_prep"
    assert diagnostic["applicability"] == "required"
    assert diagnostic["owner_task_ids"] == ["TASK-A"]
    assert diagnostic["delegated_owner_task_ids"] == ["TASK-A"]
    assert diagnostic["required_artifacts"] == [
        {"obligation_id": "source", "path": "src/main.py", "owner_task_id": "TASK-A"},
        {"obligation_id": "tests", "path": "tests/test_main.py", "owner_task_id": "TASK-A"},
        {"obligation_id": "manifest", "path": "pyproject.toml", "owner_task_id": "TASK-A"},
    ]
    assert diagnostic["guidance_only"] is True
    assert diagnostic["execution_authority"] is False
    assert "CE-authored profile-validated" in diagnostic["repair_hint"]
    assert "explicit PM" in diagnostic["repair_hint"]
    assert "command_authority_hash=null" in diagnostic["repair_hint"]
    assert not {"argv", "cwd", "command_authority_hash"} & diagnostic.keys()
    assert error.details["repair_operation"] == "verification_recipe_upsert"
    assert "PM-authorized" not in str(error)
    assert payload == original
    assert not list((tmp_path / ".polaris" / "runtime" / "blueprints").glob("ce_portfolio_*.json"))


def test_required_profile_recipe_restores_admission_with_same_pm_authority(tmp_path: Path) -> None:
    payload = _payload()
    environment = payload["project_completion_contract"]["obligations"]["verification"].pop(0)
    command = _command(tmp_path, payload)
    original_test = deepcopy(payload["project_completion_contract"]["obligations"]["verification"])
    with pytest.raises(ChiefEngineerBlueprintErrorV1):
        build_chief_engineer_blueprint_portfolio(command)
    payload["project_completion_contract"]["obligations"]["verification"].append(environment)
    result = build_chief_engineer_blueprint_portfolio(replace(command, llm_blueprint=payload))
    contract = result.project_completion_contract
    assert contract.pm_contract_hash == "a" * 64
    assert payload["project_completion_contract"]["obligations"]["verification"][:-1] == original_test
    assert any(
        row.modality == "environment_prep" and row.applicability == "required"
        for row in contract.obligations.verification
    )
    assert ("python", "-m", "pip", "install", "-e", ".") in {
        row.argv for row in contract.verification_command_authority
    }
    assert result.execution_authorized is False


@pytest.mark.parametrize("bad_recipe", ["optional", "wrong_owner", "wrong_profile", "wrong_hash"])
def test_missing_environment_recovery_keeps_recipe_guards(tmp_path: Path, bad_recipe: str) -> None:
    payload = _payload()
    environment = payload["project_completion_contract"]["obligations"]["verification"][0]
    if bad_recipe == "optional":
        environment["applicability"] = "optional"
    elif bad_recipe == "wrong_owner":
        environment["owner_task_id"] = "unknown"
    elif bad_recipe == "wrong_profile":
        environment["argv"] = ["python", "-m", "pytest", "-q"]
    else:
        environment["command_authority_hash"] = "0" * 64
    with pytest.raises(ChiefEngineerBlueprintErrorV1):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))


def test_legacy_task_does_not_gain_ce_recipe_authority(tmp_path: Path) -> None:
    legacy = replace(
        _task(),
        planning_contract_version="",
        topology_authority="pm",
        verification_authority="pm",
        target_files=("src/main.py", "tests/test_main.py", "pyproject.toml"),
    )
    with pytest.raises(ChiefEngineerBlueprintErrorV1):
        build_chief_engineer_blueprint_portfolio(
            _command(
                tmp_path,
                _payload(),
                task=legacy,
                commands=(VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("pytest", "-q")),),
            )
        )


def test_missing_environment_finding_reaches_current_full_draft_repair(tmp_path: Path) -> None:
    import json

    from polaris.cells.chief_engineer.blueprint.public import (
        ChiefEngineerSemanticRepairCandidateV1,
        chief_engineer_semantic_repair_task_set_hash,
    )
    from polaris.cells.chief_engineer.blueprint.public.service._portfolio import (
        project_chief_engineer_effective_obligation_view,
    )
    from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor

    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"].pop(0)
    command = _command(tmp_path, payload)
    view = project_chief_engineer_effective_obligation_view(
        payload,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    finding = view["normalization_findings"][0]
    assert finding["verification_recipe_diagnostics"][0]["modality"] == "environment_prep"
    assert view["final_roster_complete"] is False
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-intent",
        run_id="run-intent",
        pm_contract_hash="a" * 64,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    executor = _executor(tmp_path)
    assert (
        executor._chief_engineer_semantic_repair_diagnosis(
            candidate=candidate,
            output_errors=["chief_engineer.completion_normalization:" + json.dumps(finding)],
        )
        is None
    )
    guidance = executor._chief_engineer_trusted_recipe_guidance(
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    assert "missing_required_verification_recipe" in guidance
    assert "required_artifacts" in guidance
    assert "owners without delegation still need committed PM authority" in guidance
