"""Regressions for CE reference identity versus PM delivery authority."""

from copy import deepcopy
from dataclasses import replace

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBehaviorExampleV1,
    ChiefEngineerBehaviorInvariantV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    EntrypointObligationV1,
    chief_engineer_semantic_repair_task_set_hash,
    compose_chief_engineer_semantic_repair,
    project_chief_engineer_effective_obligation_view,
    project_chief_engineer_portfolio_delivery_depth_feasibility,
    project_chief_engineer_semantic_repair_provider_context,
)
from polaris.cells.chief_engineer.blueprint.public.service import _portfolio
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _application_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_semantic_repair import _candidate, _tasks
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor


def test_provider_behavior_allowlist_excludes_scope_denied_config(tmp_path):
    seed = _candidate(tmp_path)
    payload = deepcopy(seed.candidate)
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "OBL-3",
            "path": "tsconfig.test.json",
            "semantic_role": "config",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        }
    )
    candidate = replace(seed, candidate=payload)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("behavior.invalid_reference",),
        allowed_operations=("behavior_invariant_upsert",),
    )
    before = deepcopy(candidate.candidate)
    context = project_chief_engineer_semantic_repair_provider_context(
        candidate, diagnosis, tasks=_tasks(delegated=True)
    )
    assert "OBL-3" not in context["allowed_completion_obligation_ids"]
    assert context["effective_obligation_view"]["final_roster_complete"] is False
    assert context["effective_obligation_view"]["missing_authority_evidence"] == [
        "authenticated_portfolio_authority_carrier"
    ]
    excluded = {row["obligation_id"]: row for row in context["excluded_completion_obligations"]}
    assert excluded["OBL-3"]["path"] == "tsconfig.test.json"
    assert excluded["OBL-3"]["reason"] == "outside_immutable_pm_scope_and_delegated_topology"
    assert candidate.candidate == before


def test_early_factory_reference_check_rejects_scope_denied_config(tmp_path):
    payload = deepcopy(_candidate(tmp_path).candidate)
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "OBL-3",
            "path": "tsconfig.test.json",
            "semantic_role": "config",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        }
    )
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        {
            "invariant_id": "INV-1",
            "statement": "Tests preserve the command behavior.",
            "owner_task_id": "TASK-1",
            "consumer_task_ids": ["TASK-2"],
            "covered_obligation_ids": ["artifact-main", "OBL-3"],
            "verification_examples": [{"given": "input", "when": "executed", "then": "same result"}],
        }
    ]
    for plan in payload["construction_plan"]["task_plans"].values():
        plan["behavior_invariant_refs"] = ["INV-1"]
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(payload, tasks=_tasks(delegated=True))
    assert any("OBL-3" in error and "completion obligations" in error for error in errors), errors


def _bound_candidate(tmp_path):
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-A",
            objective="Implement CLI",
            target_files=("src/main.py", "README.md"),
            scope_paths=("src", "README.md"),
            entrypoint_targets=("src/main.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules", "entrypoint"),
            primary_language="python",
            allowed_source_suffixes=(".py",),
            entrypoint_kind_authority="cli",
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-B",
            objective="Verify CLI",
            target_files=("tests/test_main.py",),
            scope_paths=("tests",),
            dependencies=("TASK-A",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
    )
    carrier = _portfolio_command_authority(tasks=tasks, workspace=tmp_path)["authority_carrier"]
    payload = {
        "construction_plan": {
            "task_plans": {
                "TASK-A": {"behavior_invariant_refs": ["INV-1"]},
                "TASK-B": {"behavior_invariant_refs": ["INV-1"]},
            },
            "project_interface_contract": {},
            "shared_behavior_contract": {
                "invariants": [_behavior(("artifact-main", "artifact-tests", "OBL-3")).to_dict()]
            },
        },
        "project_completion_contract": _application_completion_requirements(),
        "risk_flags": [],
    }
    payload["project_completion_contract"]["obligations"]["artifacts"].extend(
        [
            {
                "obligation_id": "OBL-3",
                "path": "tsconfig.test.json",
                "semantic_role": "config",
                "applicability": "required",
                "owner_task_id": "TASK-A",
            },
            {
                "obligation_id": "OBL-7",
                "path": "src/optional.py",
                "semantic_role": "source",
                "applicability": "optional",
                "owner_task_id": "TASK-A",
            },
            {
                "obligation_id": "OBL-8",
                "path": "future/noop.py",
                "semantic_role": "source",
                "applicability": "not_applicable",
                "owner_task_id": None,
            },
        ]
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id=carrier.project_id,
        run_id=carrier.run_id,
        pm_contract_hash=carrier.pm_contract_hash,
        task_ids=("TASK-A", "TASK-B"),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-A", "TASK-B")),
        candidate=payload,
    )
    return candidate, tasks, carrier


def _behavior(ids, statement="Authored behavior remains intact."):
    return ChiefEngineerBehaviorInvariantV1(
        invariant_id="INV-1",
        statement=statement,
        owner_task_id="TASK-A",
        consumer_task_ids=("TASK-B",),
        covered_obligation_ids=ids,
        verification_examples=(ChiefEngineerBehaviorExampleV1(given="input", when="run", then="same result"),),
    )


def _diagnosis(candidate, operations=("behavior_invariant_upsert",)):
    return ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.invalid",),
        allowed_operations=operations,
    )


def test_authenticated_view_matches_real_final_normalizer_and_is_pure(tmp_path, monkeypatch):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id=carrier.run_id,
        tasks=tasks,
        authority_carrier=carrier,
        llm_blueprint=dict(candidate.candidate),
    )
    final = _portfolio._build_portfolio_completion_contract(command, candidate.candidate["project_completion_contract"])
    before = deepcopy(candidate.to_dict())
    before_tasks = [task.to_dict() for task in tasks]

    def forbidden(*args, **kwargs):
        raise AssertionError("pure view attempted live authority revalidation or catalog I/O")

    monkeypatch.setattr(_portfolio, "_revalidate_portfolio_authority_carrier", forbidden)
    monkeypatch.setattr(_portfolio, "_read_portfolio_catalog_snapshot", forbidden)
    context = project_chief_engineer_semantic_repair_provider_context(
        candidate, _diagnosis(candidate), tasks=tasks, authority_carrier=carrier
    )
    view = context["effective_obligation_view"]
    assert view["final_roster_complete"] is True
    assert view["execution_authority"] is False
    assert view["obligations"] == final.obligations.to_dict()
    assert "OBL-3" not in context["allowed_completion_obligation_ids"]
    assert "OBL-7" in context["allowed_completion_obligation_ids"]
    assert "OBL-8" in context["allowed_completion_obligation_ids"]
    assert "artifact-pm-001" in context["allowed_completion_obligation_ids"]
    implicit = next(row for row in view["obligations"]["artifacts"] if row["obligation_id"] == "artifact-pm-001")
    assert (implicit["path"], implicit["owner_task_id"]) == ("README.md", "TASK-A")
    depth = project_chief_engineer_portfolio_delivery_depth_feasibility(candidate.candidate, tasks=tasks)
    assert "OBL-7" not in depth["authorized_artifact_obligation_ids"]
    assert "OBL-8" not in depth["authorized_artifact_obligation_ids"]
    again = project_chief_engineer_effective_obligation_view(
        candidate.candidate, tasks=tasks, authority_carrier=carrier
    )
    assert again["basis_hash"] == view["basis_hash"]
    assert candidate.to_dict() == before
    assert [task.to_dict() for task in tasks] == before_tasks


@pytest.mark.parametrize("reference", ["OBL-3", "NEVER-DECLARED"])
def test_composer_rejects_denied_and_unknown_behavior_references(tmp_path, reference):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    diagnosis = _diagnosis(candidate)
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        behavior_invariant_upserts=(_behavior(("artifact-main", "artifact-tests", reference)),),
    )
    with pytest.raises(ValueError, match="references unknown completion obligations"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks, authority_carrier=carrier)
    assert (
        candidate.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"][
            -1
        ]
        == "OBL-3"
    )


def test_typed_replacement_preserves_authored_behavior_and_reprojects_legal_upsert(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    diagnosis = _diagnosis(candidate, ("artifact_upsert", "behavior_invariant_upsert"))
    statement = "Caller and verifier preserve argument order without relying on an extra config."
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="NEW-SOURCE",
                path="src/extra.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-A",
            ),
        ),
        behavior_invariant_upserts=(
            _behavior(("artifact-main", "artifact-tests", "OBL-7", "OBL-8", "NEW-SOURCE"), statement),
        ),
    )
    after, receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, patch, tasks=tasks, authority_carrier=carrier
    )
    assert after.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0]["statement"] == statement
    assert after.pm_contract_hash == candidate.pm_contract_hash
    assert receipt.before_candidate_hash == candidate.candidate_hash
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        after.candidate, tasks=tasks, authority_carrier=carrier
    )
    assert errors == []
    view = project_chief_engineer_effective_obligation_view(after.candidate, tasks=tasks, authority_carrier=carrier)
    assert "NEW-SOURCE" in view["retained_completion_obligation_ids"]
    assert "OBL-3" not in view["retained_completion_obligation_ids"]


def test_authenticated_view_rejects_surrogate_and_cross_candidate_authority(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    with pytest.raises(ValueError, match="exact Factory-issued"):
        project_chief_engineer_effective_obligation_view(
            candidate.candidate, tasks=tasks, authority_carrier={"pm_contract_hash": candidate.pm_contract_hash}
        )
    wrong = replace(candidate, pm_contract_hash="b" * 64)
    with pytest.raises(ValueError, match="pm_contract_hash does not match candidate"):
        project_chief_engineer_semantic_repair_provider_context(
            wrong, _diagnosis(wrong), tasks=tasks, authority_carrier=carrier
        )


def test_composer_rejects_implicit_pm_id_collision(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    payload["project_completion_contract"]["obligations"]["artifacts"][0]["obligation_id"] = "artifact-pm-001"
    candidate = replace(candidate, candidate=payload)
    diagnosis = _diagnosis(candidate)
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        behavior_invariant_upserts=(_behavior(("artifact-pm-001", "artifact-tests")),),
    )
    with pytest.raises(ValueError, match="effective completion normalization"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks, authority_carrier=carrier)


def test_implicit_pm_artifact_requires_unambiguous_owner(tmp_path):
    candidate, tasks, _carrier = _bound_candidate(tmp_path)
    tasks = (tasks[0], replace(tasks[1], target_files=("tests/test_main.py", "README.md"), dependencies=()))
    carrier = _portfolio_command_authority(tasks=tasks, workspace=tmp_path)["authority_carrier"]
    with pytest.raises(ValueError, match="unique authorized terminal owner"):
        project_chief_engineer_effective_obligation_view(candidate.candidate, tasks=tasks, authority_carrier=carrier)


def test_entrypoint_and_verifier_ids_come_from_real_normalization(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["entrypoints"] = []
    obligations["verification"] = [row for row in obligations["verification"] if row["modality"] != "entrypoint"]
    view = project_chief_engineer_effective_obligation_view(payload, tasks=tasks, authority_carrier=carrier)
    assert view["final_roster_complete"] is True
    assert "entrypoint-pm-001" in view["retained_completion_obligation_ids"]
    entrypoint = view["obligations"]["entrypoints"][0]
    assert (entrypoint["owner_task_id"], entrypoint["command"]) == ("TASK-A", "python -m src.main")
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path), run_id=carrier.run_id, tasks=tasks, authority_carrier=carrier, llm_blueprint=payload
    )
    final = _portfolio._build_portfolio_completion_contract(command, payload["project_completion_contract"])
    assert view["obligations"] == final.obligations.to_dict()
    assert any(
        row["modality"] == "entrypoint" and row["covers_obligation_ids"] == ["entrypoint-pm-001"]
        for row in view["obligations"]["verification"]
    )


@pytest.mark.parametrize("role", ["source", "test", "entrypoint"])
def test_role_spoof_cannot_delegate_root_config(tmp_path, role):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    payload["project_completion_contract"]["obligations"]["artifacts"][2]["semantic_role"] = role
    view = project_chief_engineer_effective_obligation_view(payload, tasks=tasks, authority_carrier=carrier)
    assert "OBL-3" not in view["retained_completion_obligation_ids"]
    assert view["excluded_completion_obligations"][0]["reason"] == "outside_immutable_pm_scope_and_delegated_topology"


@pytest.mark.parametrize("path", ["src/main.py", "src/entrypoint.py"])
def test_immutable_owner_failure_cannot_acquire_entrypoint_repair_from_path(tmp_path, path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    artifact = payload["project_completion_contract"]["obligations"]["artifacts"][0]
    artifact.update(path=path, owner_task_id="TASK-FOREIGN", obligation_id="artifact-entrypoint-owner")
    candidate = replace(candidate, candidate=payload)
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=tasks, authority_carrier=carrier
    )
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as raised:
        OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(candidate=candidate, output_errors=errors)
    assert raised.value.code == "invalid_project_completion_contract"
    assert "owner" in str(raised.value)


def test_collision_id_vocabulary_cannot_acquire_entrypoint_repair(tmp_path):
    candidate, _tasks, _carrier = _bound_candidate(tmp_path)
    errors = [
        "invalid project completion contract: duplicate obligation_id across project completion obligations; "
        "obligation_id='entrypoint-collision'"
    ]
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as raised:
        OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(candidate=candidate, output_errors=errors)
    assert raised.value.code == "invalid_project_completion_contract"


def test_real_collision_diagnosis_retains_immutable_failure(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    rows = payload["project_completion_contract"]["obligations"]["artifacts"]
    rows[0]["obligation_id"] = rows[1]["obligation_id"] = "entrypoint-collision"
    candidate = replace(candidate, candidate=payload)
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=tasks, authority_carrier=carrier
    )
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as raised:
        OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(candidate=candidate, output_errors=errors)
    assert raised.value.code == "invalid_project_completion_contract"


def test_scoped_entrypoint_semantic_failure_still_authorizes_typed_repair(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        _behavior(("artifact-main", "artifact-tests")).to_dict()
    ]
    entrypoint = payload["project_completion_contract"]["obligations"]["entrypoints"][0]
    entrypoint["command"] = "python -m src.main --help"
    candidate = replace(candidate, candidate=payload)
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=tasks, authority_carrier=carrier
    )
    diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate, output_errors=errors
    )
    assert diagnosis.allowed_operations == ("entrypoint_upsert",)
    assert diagnosis.diagnostic_codes == ("chief_engineer.entrypoint_contract.invalid",)


def test_typed_entrypoint_shape_finding_retains_repair_despite_canonical_error_code(tmp_path):
    candidate, tasks, carrier = _bound_candidate(tmp_path)
    payload = deepcopy(candidate.candidate)
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        _behavior(("artifact-main", "artifact-tests")).to_dict()
    ]
    entrypoint = payload["project_completion_contract"]["obligations"]["entrypoints"][0]
    entrypoint["applicability"] = "not_applicable"
    candidate = replace(candidate, candidate=payload)
    view = project_chief_engineer_effective_obligation_view(payload, tasks=tasks, authority_carrier=carrier)
    assert view["normalization_findings"][0]["code"] == "invalid_project_completion_contract"
    assert view["normalization_findings"][0]["repair_operation"] == "entrypoint_upsert"
    errors = OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
        payload, tasks=tasks, authority_carrier=carrier
    )
    diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate, output_errors=errors
    )
    assert diagnosis.allowed_operations == ("entrypoint_upsert",)
    corrected = {**entrypoint, "applicability": "required"}
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_upserts=(EntrypointObligationV1(**corrected),),
    )
    after, _receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, patch, tasks=tasks, authority_carrier=carrier
    )
    assert (
        OrchestrationStageExecutor._chief_engineer_portfolio_output_errors(
            after.candidate, tasks=tasks, authority_carrier=carrier
        )
        == []
    )
