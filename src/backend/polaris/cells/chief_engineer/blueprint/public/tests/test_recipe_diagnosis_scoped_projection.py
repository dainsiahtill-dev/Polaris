"""Real public compiler/compose with a synthetic trusted issuer; no native replay."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    build_chief_engineer_blueprint_portfolio,
    build_chief_engineer_semantic_repair_patch_schema,
    chief_engineer_semantic_repair_task_set_hash,
    compose_chief_engineer_semantic_repair,
    project_chief_engineer_semantic_repair_provider_context,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _command,
    _payload,
)


def _recipe_case(tmp_path: Path, collection: str = "entrypoints") -> tuple[Any, Any, Any]:
    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    if collection == "entrypoints":
        bad = deepcopy(obligations["entrypoints"][0])
        bad.update(obligation_id="broken-cli", command="npm run build")
        obligations["entrypoints"].append(bad)
    else:
        obligations["verification"][1]["argv"] = ["npm", "run", "test:check"]
    command = _command(tmp_path, payload)
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(command)
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-intent",
        run_id="run-intent",
        pm_contract_hash="a" * 64,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    operation = "entrypoint_upsert" if collection == "entrypoints" else "verification_recipe_upsert"
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=(
            "chief_engineer.shared_behavior_contract.invalid",
            "chief_engineer.entrypoint_contract.invalid",
        ),
        allowed_operations=(operation, "behavior_invariant_upsert", "task_behavior_ref_replace"),
        verification_recipe_diagnostics=tuple(caught.value.details["verification_recipe_diagnostics"]),
    )
    return command, candidate, diagnosis


def _wire(candidate: Any, diagnosis: Any, **operations: Any) -> dict[str, Any]:
    return {"base_candidate_hash": candidate.candidate_hash, "diagnosis_hash": diagnosis.diagnosis_hash, **operations}


@pytest.mark.parametrize("collection,expected", [("entrypoints", ["broken-cli"]), ("verification", ["check"])])
def test_mixed_behavior_context_keeps_only_diagnosed_mutable_recipes(
    tmp_path: Path,
    collection: str,
    expected: list[str],
) -> None:
    command, candidate, diagnosis = _recipe_case(tmp_path, collection)
    context = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    assert [row["obligation_id"] for row in context["current"][collection]] == expected
    # Readonly artifact identities remain available for behavior references.
    assert {"source", "tests", "manifest"}.issubset(context["allowed_completion_obligation_ids"])
    assert (
        context["allowed_completion_obligation_ids"]
        == context["effective_obligation_view"]["retained_completion_obligation_ids"]
    )
    assert context["effective_obligation_view"]["final_roster_complete"] is False


@pytest.mark.parametrize("defect", ["sibling", "remove"])
def test_entrypoint_recipe_schema_enforces_the_same_scope_as_composer(tmp_path: Path, defect: str) -> None:
    _command_value, candidate, diagnosis = _recipe_case(tmp_path)
    schema = build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)
    Draft202012Validator.check_schema(schema)
    rows = candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"]
    if defect == "sibling":
        payload = _wire(candidate, diagnosis, entrypoint_upserts=[deepcopy(rows[0])])
    else:
        payload = _wire(candidate, diagnosis, entrypoint_remove_obligation_ids=["broken-cli"])
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(payload)


def test_no_entrypoint_diagnosed_has_valid_empty_only_schema(tmp_path: Path) -> None:
    _command_value, candidate, original = _recipe_case(tmp_path, "verification")
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=original.diagnostic_codes,
        allowed_operations=(*original.allowed_operations, "entrypoint_upsert"),
        verification_recipe_diagnostics=original.verification_recipe_diagnostics,
    )
    schema = build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_wire(candidate, diagnosis, entrypoint_upserts=[]))
    sibling = candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"][0]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(_wire(candidate, diagnosis, entrypoint_upserts=[sibling]))


def test_diagnosed_patch_fully_normalizes_and_preserves_untouched_rows(tmp_path: Path) -> None:
    command, candidate, diagnosis = _recipe_case(tmp_path)
    before = deepcopy(candidate.candidate)
    row = deepcopy(before["project_completion_contract"]["obligations"]["entrypoints"][1])
    row["command"] = "python -m src.main"
    wire = _wire(candidate, diagnosis, entrypoint_upserts=[row])
    Draft202012Validator(build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)).validate(wire)
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(wire, allowed_operations=diagnosis.allowed_operations)
    after, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    expected = deepcopy(before)
    expected["project_completion_contract"]["obligations"]["entrypoints"][1] = row
    assert after.candidate == expected
    assert candidate.candidate == before
    assert receipt.changed_semantic_ids == ("broken-cli",)
    built = build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(after.candidate)))
    assert built.project_completion_contract.pm_contract_hash == candidate.pm_contract_hash
    assert built.execution_authorized is False


@pytest.mark.parametrize("defect", ["echo", "changed_sibling", "foreign_owner", "CAS"])
def test_existing_composer_guards_remain_strict(tmp_path: Path, defect: str) -> None:
    command, candidate, diagnosis = _recipe_case(tmp_path)
    rows = candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"]
    row = deepcopy(rows[0] if defect in {"echo", "changed_sibling"} else rows[1])
    if defect in {"changed_sibling", "foreign_owner", "CAS"}:
        row["command"] = "python -m src.main"
    if defect == "foreign_owner":
        row["owner_task_id"] = "FOREIGN"
    wire = _wire(candidate, diagnosis, entrypoint_upserts=[row])
    if defect == "CAS":
        wire["base_candidate_hash"] = "0" * 64
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(wire, allowed_operations=diagnosis.allowed_operations)
    with pytest.raises(ValueError):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=command.tasks,
            authority_carrier=command.authority_carrier,
        )


def test_nonrecipe_replacement_schema_keeps_legacy_semantics(tmp_path: Path) -> None:
    _command_value, candidate, _ = _recipe_case(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
    )
    replacement = deepcopy(candidate.candidate["project_completion_contract"]["obligations"]["entrypoints"][0])
    replacement["obligation_id"] = "replacement-cli"
    schema = build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(
        _wire(candidate, diagnosis, entrypoint_upserts=[replacement], entrypoint_remove_obligation_ids=["cli"]),
    )
