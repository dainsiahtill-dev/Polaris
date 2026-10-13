"""Recipe rejection stays complete and repairs preserve frozen CE siblings."""

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    build_chief_engineer_blueprint_portfolio,
    chief_engineer_semantic_repair_task_set_hash,
    compose_chief_engineer_semantic_repair,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import (
    _command,
    _payload,
)


def test_recipe_rejection_reports_all_independent_rows(tmp_path: Path) -> None:
    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["verification"][0]["argv"] = ["install", "--no-audit"]
    obligations["verification"][1]["argv"] = ["npm", "run", "test:check"]
    obligations["entrypoints"][0]["command"] = "npx http-server dist"
    original = deepcopy(payload)
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    diagnostics = caught.value.details["verification_recipe_diagnostics"]
    assert [item["obligation_id"] for item in diagnostics] == ["environment", "check", "cli"]
    assert [item["source_path"] for item in diagnostics] == [
        ["obligations", "verification", 0],
        ["obligations", "verification", 1],
        ["obligations", "entrypoints", 0],
    ]
    assert all(item["profile_feedback"]["error_code"] == "untrusted_verifier_command" for item in diagnostics)
    assert payload == original
    assert not list((tmp_path / ".polaris" / "runtime" / "blueprints").glob("ce_portfolio_*.json"))


def _repair_seed(tmp_path: Path):
    payload = _payload()
    payload["risk_flags"] = ["preserve prose exactly"]
    payload["project_completion_contract"]["obligations"]["verification"][1]["argv"] = ["npm", "run", "test:check"]
    command = _command(tmp_path, payload)
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-intent",
        run_id="run-intent",
        pm_contract_hash="a" * 64,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    diagnostic = {
        "collection": "verification",
        "source_path": ["obligations", "verification", 1],
        "obligation_id": "check",
        "owner_task_id": "TASK-A",
        "modality": "test",
        "argv": ["npm", "run", "test:check"],
        "cwd": ".",
        "profile_feedback": {"error_code": "untrusted_verifier_command"},
    }
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.verification_recipe.invalid",),
        allowed_operations=("verification_recipe_upsert",),
        verification_recipe_diagnostics=(diagnostic,),
    )
    row = deepcopy(payload["project_completion_contract"]["obligations"]["verification"][1])
    row.pop("command_authority_hash")
    row["argv"] = ["python", "-m", "pytest", "-q"]
    return command, candidate, diagnosis, row


def test_local_recipe_patch_preserves_every_untouched_sibling(tmp_path: Path) -> None:
    command, candidate, diagnosis, row = _repair_seed(tmp_path)
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        {
            "base_candidate_hash": candidate.candidate_hash,
            "diagnosis_hash": diagnosis.diagnosis_hash,
            "verification_recipe_upserts": [row],
        },
        allowed_operations=diagnosis.allowed_operations,
    )
    original = deepcopy(candidate.candidate)
    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    expected = deepcopy(original)
    expected["project_completion_contract"]["obligations"]["verification"][1] = {**row, "command_authority_hash": None}
    assert repaired.candidate == expected
    assert candidate.candidate == original
    assert repaired.pm_contract_hash == candidate.pm_contract_hash
    assert receipt.changed_semantic_ids == ("check",)
    assert "artifacts" in receipt.unchanged_section_hashes
    complete = build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(repaired.candidate)))
    assert complete.project_completion_contract.pm_contract_hash == candidate.pm_contract_hash
    assert complete.execution_authorized is False


@pytest.mark.parametrize("defect", ["different_row", "owner", "optional", "coverage", "hash", "bad_profile"])
def test_local_recipe_patch_rejects_diagnosis_or_authority_drift(tmp_path: Path, defect: str) -> None:
    command, candidate, diagnosis, row = _repair_seed(tmp_path)
    if defect == "different_row":
        row["obligation_id"] = "environment"
    elif defect == "owner":
        row["owner_task_id"] = "outside"
    elif defect == "optional":
        row["applicability"] = "optional"
    elif defect == "coverage":
        row["covers_obligation_ids"] = []
    elif defect == "hash":
        row["command_authority_hash"] = "0" * 64
    else:
        row["argv"] = ["npm", "run", "another-untrusted-command"]
    with pytest.raises((TypeError, ValueError)):
        patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
            {
                "base_candidate_hash": candidate.candidate_hash,
                "diagnosis_hash": diagnosis.diagnosis_hash,
                "verification_recipe_upserts": [row],
            },
            allowed_operations=diagnosis.allowed_operations,
        )
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=command.tasks,
            authority_carrier=command.authority_carrier,
        )


def test_behavior_unknown_consumers_name_known_task_ids(tmp_path: Path) -> None:
    payload = _payload()
    construction = payload["construction_plan"]
    construction["task_plans"]["TASK-A"]["behavior_invariant_refs"] = ["INV-1"]
    construction["shared_behavior_contract"]["invariants"] = [
        {
            "invariant_id": "INV-1",
            "statement": "Keep output deterministic",
            "owner_task_id": "TASK-A",
            "consumer_task_ids": ["test_runner", "cli_entrypoint"],
            "covered_obligation_ids": ["source"],
            "verification_examples": [{"given": "valid input", "when": "execute", "then": "stable output"}],
        }
    ]
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    detail = caught.value.details
    assert detail["known_task_ids"] == ["TASK-A"]
    assert detail["unknown_consumer_task_ids"] == ["cli_entrypoint", "test_runner"]
    assert detail["unknown_owner_task_id"] is None
    assert detail["unknown_obligation_ids"] == []
    assert "consumer_task_ids" in detail["repair_hint"]
    assert "consumer_kind" in detail["repair_hint"]


@pytest.mark.parametrize("extra_change", [None, "statement", "coverage", "invariant_id", "task_refs"])
def test_consumer_identity_repair_preserves_valid_obligations(tmp_path: Path, extra_change: str | None) -> None:
    payload = _payload()
    invariant = {
        "invariant_id": "INV-1",
        "statement": "Keep output deterministic",
        "owner_task_id": "TASK-A",
        "consumer_task_ids": ["test_runner"],
        "covered_obligation_ids": ["source"],
        "verification_examples": [{"given": "valid input", "when": "execute", "then": "stable output"}],
    }
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [invariant]
    payload["construction_plan"]["task_plans"]["TASK-A"]["behavior_invariant_refs"] = ["INV-1"]
    command = _command(tmp_path, payload)
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-intent",
        run_id="run-intent",
        pm_contract_hash="a" * 64,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.invalid",),
        allowed_operations=("behavior_invariant_upsert", "task_behavior_ref_replace"),
    )
    repaired_invariant = {**invariant, "consumer_task_ids": []}
    if extra_change == "statement":
        repaired_invariant["statement"] = "silently weaker semantics"
    elif extra_change == "coverage":
        repaired_invariant["covered_obligation_ids"] = ["tests"]
    elif extra_change == "invariant_id":
        repaired_invariant["invariant_id"] = "INV-NEW"
    wire = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "behavior_invariant_upserts": [repaired_invariant],
    }
    if extra_change == "task_refs":
        wire["task_behavior_ref_replacements"] = {"TASK-A": []}
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(wire, allowed_operations=diagnosis.allowed_operations)
    if extra_change is not None:
        with pytest.raises(ValueError):
            compose_chief_engineer_semantic_repair(
                candidate,
                diagnosis,
                patch,
                tasks=command.tasks,
                authority_carrier=command.authority_carrier,
            )
        return
    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    expected = deepcopy(payload)
    expected["construction_plan"]["shared_behavior_contract"]["invariants"][0]["consumer_task_ids"] = []
    assert repaired.candidate == expected
    complete = build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(repaired.candidate)))
    assert complete.execution_authorized is False


def test_missing_environment_recipe_is_added_without_rewriting_test_recipe(tmp_path: Path) -> None:
    payload = _payload()
    environment = payload["project_completion_contract"]["obligations"]["verification"].pop(0)
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
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.verification_recipe.missing",),
        allowed_operations=("verification_recipe_upsert",),
        verification_recipe_diagnostics=tuple(caught.value.details["verification_recipe_diagnostics"]),
    )
    environment.pop("command_authority_hash")
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        {
            "base_candidate_hash": candidate.candidate_hash,
            "diagnosis_hash": diagnosis.diagnosis_hash,
            "verification_recipe_upserts": [environment],
        },
        allowed_operations=diagnosis.allowed_operations,
    )
    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    assert (
        repaired.candidate["project_completion_contract"]["obligations"]["verification"][0]
        == payload["project_completion_contract"]["obligations"]["verification"][0]
    )
    assert receipt.changed_semantic_ids == ("environment",)
    assert build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(repaired.candidate))).handoff_ready is False


def test_diagnosed_entrypoint_repairs_command_without_kind_or_path_changes(tmp_path: Path) -> None:
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["entrypoints"][0]["command"] = "npx http-server dist"
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
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
        verification_recipe_diagnostics=tuple(caught.value.details["verification_recipe_diagnostics"]),
    )
    entrypoint = deepcopy(payload["project_completion_contract"]["obligations"]["entrypoints"][0])
    entrypoint["command"] = "python -m src.main"
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        {
            "base_candidate_hash": candidate.candidate_hash,
            "diagnosis_hash": diagnosis.diagnosis_hash,
            "entrypoint_upserts": [entrypoint],
        },
        allowed_operations=diagnosis.allowed_operations,
    )
    repaired, _ = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    expected = deepcopy(payload)
    expected["project_completion_contract"]["obligations"]["entrypoints"][0] = entrypoint
    assert repaired.candidate == expected
    assert (
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(repaired.candidate))).execution_authorized
        is False
    )


def test_recipe_scoped_schema_forbids_hash_and_undetected_identity(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError
    from polaris.cells.chief_engineer.blueprint.public import build_chief_engineer_semantic_repair_patch_schema

    _command_value, candidate, diagnosis, row = _repair_seed(tmp_path)
    schema = build_chief_engineer_semantic_repair_patch_schema(
        allowed_operations=diagnosis.allowed_operations,
        diagnosis=diagnosis,
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "verification_recipe_upserts": [row],
    }
    Draft202012Validator(schema).validate(payload)
    for changed in [{**row, "obligation_id": "environment"}, {**row, "command_authority_hash": None}]:
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate({**payload, "verification_recipe_upserts": [changed]})


def test_delegated_not_applicable_verifier_does_not_require_model_hash(tmp_path: Path) -> None:
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"].append(
        {
            "obligation_id": "lint-na",
            "modality": "lint",
            "applicability": "not_applicable",
            "owner_task_id": None,
            "covers_obligation_ids": [],
        }
    )
    original = deepcopy(payload)
    complete = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    row = next(
        item
        for item in complete.project_completion_contract.obligations.verification
        if item.obligation_id == "lint-na"
    )
    assert row.command_authority_hash is None
    assert row.command is None
    assert payload == original


def test_recipe_schema_never_advertises_more_than_active_diagnosis(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public import build_chief_engineer_semantic_repair_patch_schema

    _, _, diagnosis, _ = _repair_seed(tmp_path)
    schema = build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)
    assert set(schema["properties"]) == {"base_candidate_hash", "diagnosis_hash", "verification_recipe_upserts"}
    with pytest.raises(ValueError):
        build_chief_engineer_semantic_repair_patch_schema(allowed_operations=("artifact_upsert",), diagnosis=diagnosis)


@pytest.mark.parametrize(
    "new_modality,new_argv,entry_command,accepted",
    [
        ("build", ["npm", "run", "build"], "npm run start", True),
        ("test", ["npm", "run", "build"], "npm run start", False),
        ("build", ["npx", "tsc", "--noEmit"], "npm run start", False),
        ("build", ["npm", "run", "build"], "npm run build", False),
    ],
)
def test_native17_new_argv_is_classified_for_requested_modality(
    tmp_path: Path,
    new_modality: str,
    new_argv: list[str],
    entry_command: str,
    accepted: bool,
) -> None:
    # Exact native17 seq4 commands correct two recipe failures atomically.
    # Seq3/6 left npm run build as an entrypoint, which must still fail.
    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["entrypoints"][0]["command"] = "npm run build"
    obligations["verification"].append(
        {
            "obligation_id": "verify-build-tsc",
            "modality": "build",
            "command_authority_hash": None,
            "argv": ["npx", "tsc", "-p", "tsconfig.json", "--noEmit"],
            "cwd": ".",
            "owner_task_id": "TASK-A",
            "applicability": "required",
            "covers_obligation_ids": ["source", "tests"],
        }
    )
    command = _command(tmp_path, payload)
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        build_chief_engineer_blueprint_portfolio(command)
    diagnostics = deepcopy(caught.value.details["verification_recipe_diagnostics"])
    # Preserve the actual native17 diagnosis: same old argv only classified as lint.
    verification_diagnostic = next(row for row in diagnostics if row["collection"] == "verification")
    verification_diagnostic["allowed_recipe_modalities"] = ["lint"]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-intent",
        run_id="run-intent",
        pm_contract_hash="a" * 64,
        task_ids=("TASK-A",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A",)),
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.verification_recipe.invalid", "chief_engineer.entrypoint_contract.invalid"),
        allowed_operations=("verification_recipe_upsert", "entrypoint_upsert"),
        verification_recipe_diagnostics=tuple(diagnostics),
    )
    entry = {**obligations["entrypoints"][0], "command": entry_command}
    recipe = {**obligations["verification"][-1], "modality": new_modality, "argv": new_argv}
    recipe.pop("command_authority_hash")
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        {
            "base_candidate_hash": candidate.candidate_hash,
            "diagnosis_hash": diagnosis.diagnosis_hash,
            "entrypoint_upserts": [entry],
            "verification_recipe_upserts": [recipe],
        },
        allowed_operations=diagnosis.allowed_operations,
    )
    if not accepted:
        with pytest.raises(ValueError):
            compose_chief_engineer_semantic_repair(
                candidate,
                diagnosis,
                patch,
                tasks=command.tasks,
                authority_carrier=command.authority_carrier,
            )
        return
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_semantic_repair_provider_context

    context = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    visible_diagnostic = next(
        row for row in context["verification_recipe_diagnostics"] if row["collection"] == "verification"
    )
    assert set(visible_diagnostic["allowed_recipe_modalities"]) == {"build", "lint"}
    assert diagnosis.verification_recipe_diagnostics[0]["allowed_recipe_modalities"] == ["lint"]
    repaired, _ = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=command.tasks,
        authority_carrier=command.authority_carrier,
    )
    assert (
        repaired.candidate["project_completion_contract"]["obligations"]["verification"][:2]
        == obligations["verification"][:2]
    )
    complete = build_chief_engineer_blueprint_portfolio(_command(tmp_path, dict(repaired.candidate)))
    assert complete.execution_authorized is False
