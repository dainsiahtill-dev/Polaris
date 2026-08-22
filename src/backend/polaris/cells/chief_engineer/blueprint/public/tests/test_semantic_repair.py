"""Focused tests for typed CE semantic repair composition and CAS storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    ChiefEngineerBehaviorExampleV1,
    ChiefEngineerBehaviorInvariantV1,
    ChiefEngineerPortfolioStructuralRecoveryV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
    EntrypointObligationV1,
    build_chief_engineer_semantic_repair_patch_schema,
    chief_engineer_semantic_repair_task_set_hash,
    compose_chief_engineer_semantic_repair,
    load_chief_engineer_semantic_repair_candidate,
    normalize_chief_engineer_portfolio_tool_arguments,
    persist_chief_engineer_review_document,
    persist_chief_engineer_semantic_repair_candidate,
    project_chief_engineer_semantic_repair_provider_context,
)
from polaris.kernelone.storage import resolve_logical_path


def _candidate(tmp_path) -> ChiefEngineerSemanticRepairCandidateV1:
    task_ids = ("TASK-1", "TASK-2")
    return ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="project-1",
        run_id="run-1",
        pm_contract_hash="a" * 64,
        task_ids=task_ids,
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
        candidate={
            "construction_plan": {
                "task_plans": {
                    "TASK-1": {"behavior_invariant_refs": []},
                    "TASK-2": {"behavior_invariant_refs": []},
                },
                "project_interface_contract": {"provider_declarations": ["stable"]},
                "shared_behavior_contract": {"invariants": []},
            },
            "project_completion_contract": {
                "obligations": {
                    "artifacts": [
                        {
                            "obligation_id": "artifact-main",
                            "path": "src/main.py",
                            "semantic_role": "source",
                            "applicability": "required",
                            "owner_task_id": "TASK-1",
                        }
                    ],
                    "entrypoints": [],
                    "verification": [{"obligation_id": "verify-build", "modality": "build"}],
                }
            },
            "risk_flags": ["preserve-me"],
        },
    )


def _tasks(
    *,
    expandable: bool = True,
    delegated: bool = False,
    required_source_kinds: tuple[str, ...] | None = None,
) -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
    delegated_kinds = required_source_kinds if required_source_kinds is not None else ("domain_modules",)
    return (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement the production entrypoint.",
            target_files=("src/main.py",),
            scope_paths=(("src",) if expandable else ("src/main.py",)),
            topology_authority="chief_engineer" if delegated else "pm",
            required_source_kinds=delegated_kinds if delegated else (),
            primary_language="python" if delegated else "",
            allowed_source_suffixes=(".py",) if delegated else (),
            delivery_depth_contract={"minimums": {"min_prod_files": 2}},
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project.",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )


def test_artifact_patch_preserves_untouched_sections_and_emits_receipt(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-readme",
                path="README.md",
                semantic_role="docs",
                applicability="required",
                owner_task_id="TASK-2",
            ),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())

    artifacts = repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    assert [row["obligation_id"] for row in artifacts] == ["artifact-main", "artifact-readme"]
    assert repaired.candidate["risk_flags"] == ["preserve-me"]
    assert receipt.before_candidate_hash == candidate.candidate_hash
    assert receipt.after_candidate_hash == repaired.candidate_hash
    assert receipt.changed_semantic_ids == ("artifact-readme",)
    assert set(receipt.unchanged_section_hashes) >= {
        "entrypoints",
        "verification",
        "behavior_invariants",
        "task_behavior_refs",
        "project_interface_contract",
    }


def test_behavior_and_entrypoint_patch_validate_authority_and_refs(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("behavior.missing", "entrypoint.missing"),
        allowed_operations=(
            "entrypoint_upsert",
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        ),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="entry-main",
                kind="cli",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="src/main.py",
                command="python src/main.py",
            ),
        ),
        behavior_invariant_upserts=(
            ChiefEngineerBehaviorInvariantV1(
                invariant_id="behavior-roundtrip",
                statement="Writer and reader preserve values.",
                owner_task_id="TASK-1",
                consumer_task_ids=("TASK-2",),
                covered_obligation_ids=("artifact-main", "entry-main"),
                verification_examples=(
                    ChiefEngineerBehaviorExampleV1(given="value", when="round trip", then="same value"),
                ),
            ),
        ),
        task_behavior_ref_replacements={
            "TASK-1": ("behavior-roundtrip",),
            "TASK-2": ("behavior-roundtrip",),
        },
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())

    construction = repaired.candidate["construction_plan"]
    assert construction["task_plans"]["TASK-2"]["behavior_invariant_refs"] == ["behavior-roundtrip"]
    assert construction["shared_behavior_contract"]["invariants"][0]["owner_task_id"] == "TASK-1"


def test_candidate_store_round_trip_and_exact_cas(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    path = persist_chief_engineer_semantic_repair_candidate(candidate)

    assert path.startswith("runtime/state/blueprints/semantic-repair/")
    loaded = load_chief_engineer_semantic_repair_candidate(
        workspace=str(tmp_path),
        project_id="project-1",
        run_id="run-1",
        candidate_hash=candidate.candidate_hash,
    )
    assert loaded == candidate
    candidate_path = Path(resolve_logical_path(str(tmp_path), path))
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    payload["candidate_hash"] = "b" * 64
    candidate_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="candidate_hash"):
        load_chief_engineer_semantic_repair_candidate(
            workspace=str(tmp_path),
            project_id="project-1",
            run_id="run-1",
            candidate_hash=candidate.candidate_hash,
        )


def test_patch_rejects_unauthorized_operation_and_unknown_owner(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("entrypoint_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-foreign",
                path="src/foreign.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-FOREIGN",
            ),
        ),
    )
    with pytest.raises(ValueError, match="not diagnosis-authorized"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())


def test_authorized_artifact_patch_rejects_unknown_owner(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-foreign",
                path="src/foreign.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-FOREIGN",
            ),
        ),
    )
    with pytest.raises(ValueError, match="outside candidate task set"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())


def test_candidate_allows_deterministically_overlaid_missing_task_plans(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    payload = dict(candidate.candidate)
    payload["construction_plan"] = dict(payload["construction_plan"])
    payload["construction_plan"]["task_plans"] = {}
    rebuilt = ChiefEngineerSemanticRepairCandidateV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        pm_contract_hash=candidate.pm_contract_hash,
        task_ids=candidate.task_ids,
        task_set_hash=candidate.task_set_hash,
        candidate=payload,
    )
    assert rebuilt.candidate["construction_plan"]["task_plans"] == {}


def test_provider_patch_parser_rejects_derived_or_unknown_fields(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [
            {
                "obligation_id": "artifact-extra",
                "path": "src/extra.py",
                "semantic_role": "source",
                "applicability": "required",
                "owner_task_id": "TASK-2",
            }
        ],
        "entrypoint_upserts": [],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }
    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(payload)
    assert patch.operations == ("artifact_upsert",)
    with pytest.raises(ValueError, match="fields are invalid"):
        ChiefEngineerSemanticRepairPatchV1.from_provider_dict({**payload, "patch_hash": patch.patch_hash})


def test_composer_rejects_duplicate_baseline_and_patch_ids(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    payload = dict(candidate.candidate)
    payload["project_completion_contract"] = dict(payload["project_completion_contract"])
    payload["project_completion_contract"]["obligations"] = dict(payload["project_completion_contract"]["obligations"])
    original = payload["project_completion_contract"]["obligations"]["artifacts"][0]
    payload["project_completion_contract"]["obligations"]["artifacts"] = [original, dict(original)]
    duplicate_candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        pm_contract_hash=candidate.pm_contract_hash,
        task_ids=candidate.task_ids,
        task_set_hash=candidate.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=duplicate_candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    artifact = ArtifactObligationV1(
        obligation_id="artifact-extra",
        path="README.md",
        semantic_role="docs",
        applicability="required",
        owner_task_id="TASK-2",
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=duplicate_candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(artifact,),
    )
    with pytest.raises(ValueError, match="duplicate obligation_id"):
        compose_chief_engineer_semantic_repair(duplicate_candidate, diagnosis, patch, tasks=_tasks())

    clean_diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("delivery_depth.prod_files",),
        allowed_operations=("artifact_upsert",),
    )
    duplicate_patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=clean_diagnosis.diagnosis_hash,
        artifact_upserts=(artifact, artifact),
    )
    with pytest.raises(ValueError, match="duplicate obligation_id"):
        compose_chief_engineer_semantic_repair(candidate, clean_diagnosis, duplicate_patch, tasks=_tasks())


def test_patch_schema_has_no_delete_or_freeform_path_surface() -> None:
    schema = build_chief_engineer_semantic_repair_patch_schema()
    rendered = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    assert "delete" not in rendered
    assert "json_pointer" not in rendered
    assert schema["additionalProperties"] is False


def test_provider_context_projects_current_rows_and_task_authority(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(),
    )

    assert projected["base_candidate_hash"] == candidate.candidate_hash
    assert projected["diagnosis_hash"] == diagnosis.diagnosis_hash
    assert projected["allowed_operations"] == ["artifact_upsert"]
    assert projected["current"]["artifacts"][0]["obligation_id"] == "artifact-main"
    assert "entrypoints" not in projected["current"]
    assert projected["task_authority"]["TASK-1"] == {
        "target_files": ["src/main.py"],
        "scope_paths": ["src"],
        "unused_exact_target_paths": [],
        "expandable_scope_paths": ["src"],
        "topology_authority": "pm",
        "required_source_kinds": [],
        "delegated_artifact_roles": [],
    }
    assert projected["repair_feasible"] is True


def test_provider_context_rejects_depth_patch_when_pm_authority_has_no_unused_or_expandable_path(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    payload = json.loads(json.dumps(candidate.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "artifact-readme",
            "path": "README.md",
            "semantic_role": "docs",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        }
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        pm_contract_hash=candidate.pm_contract_hash,
        task_ids=candidate.task_ids,
        task_set_hash=candidate.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(expandable=False),
    )

    assert projected["repair_feasible"] is False
    assert projected["blocker_code"] == "chief_engineer.semantic_repair_authority_infeasible"
    assert projected["available_exact_target_paths"] == []
    assert projected["expandable_scope_paths"] == []


def test_provider_context_rejects_prod_depth_patch_when_only_unused_exact_target_is_docs(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(expandable=False),
    )

    assert projected["available_exact_target_paths"] == ["README.md"]
    assert projected["available_prod_target_paths"] == []
    assert projected["repair_feasible"] is False


def test_provider_context_rejects_test_depth_patch_without_test_authority(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.test_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(expandable=False, delegated=True, required_source_kinds=("domain_modules",)),
    )

    assert projected["repair_feasible"] is False
    assert projected["required_depth_metrics"] == ["test_files"]


def test_provider_context_keeps_delegated_topology_repair_feasible_with_exact_pm_scopes(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(expandable=False, delegated=True),
    )

    assert projected["repair_feasible"] is True
    assert projected["task_authority"]["TASK-1"]["topology_authority"] == "chief_engineer"
    assert projected["task_authority"]["TASK-1"]["required_source_kinds"] == ["domain_modules"]


def test_composer_rejects_artifact_upsert_outside_immutable_pm_authority(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-escape",
                path="src/escape.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"outside immutable PM authority|semantic role does not match path kind",
    ):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=_tasks(expandable=False),
        )


def test_composer_allows_safe_source_artifact_under_delegated_topology_authority(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-support",
                path="src/support.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True),
    )

    paths = {
        row["path"]
        for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    }
    assert "src/support.py" in paths


def test_artifact_patch_preserves_unchanged_legacy_entrypoint_without_rehydrating_it(tmp_path) -> None:
    """Exact r07: typed artifact repair must not reject an untouched root runtime marker."""

    candidate = _candidate(tmp_path)
    payload = json.loads(json.dumps(candidate.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["entrypoints"] = [
        {
            "obligation_id": "entrypoint-cli",
            "kind": "cli",
            "applicability": "required",
            "owner_task_id": "TASK-1",
            "source_path": "src/main.py",
            "runtime_path": ".",
            "command": "python -m src.main",
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=candidate.workspace,
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        pm_contract_hash=candidate.pm_contract_hash,
        task_ids=candidate.task_ids,
        task_set_hash=candidate.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-support",
                path="src/support.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(),
    )

    assert repaired.candidate["project_completion_contract"]["obligations"]["entrypoints"] == payload[
        "project_completion_contract"
    ]["obligations"]["entrypoints"]


@pytest.mark.parametrize("path", ("tests/test_support.py", "README.extra.md", "pyproject.toml"))
def test_composer_rejects_non_source_artifact_under_delegated_topology_authority(tmp_path, path: str) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-unsafe",
                path=path,
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match=r"outside immutable PM authority|semantic role does not match path kind",
    ):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=_tasks(expandable=False, delegated=True),
        )


def test_composer_rejects_source_path_mislabeled_as_test_for_depth_accounting(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.test_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-fake-test",
                path="src/fakecase.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    with pytest.raises(ValueError, match="semantic role does not match path kind"):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=_tasks(expandable=False, delegated=True, required_source_kinds=("domain_modules",)),
        )


def test_composer_allows_real_test_path_only_when_pm_delegates_tests(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.test_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-real-test",
                path="tests/test_support.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True, required_source_kinds=("tests",)),
    )

    assert any(
        row["path"] == "tests/test_support.py"
        for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    )


def test_composer_rejects_mutating_existing_artifact_identity(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-main",
                path="src/main.py",
                semantic_role="entrypoint",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    with pytest.raises(ValueError, match="immutable semantic identity"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())


def test_composer_rejects_foreign_language_source_under_delegated_topology(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-foreign",
                path="src/foreign.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement Go application",
            target_files=("main.go",),
            scope_paths=("src", "main.go"),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="go",
            allowed_source_suffixes=(".go",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )

    with pytest.raises(ValueError, match="semantic role does not match path kind"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks)


def test_composer_accepts_same_language_source_under_delegated_topology(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-engine",
                path="src/engine.go",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement Go application",
            target_files=("main.go",),
            scope_paths=("src", "main.go"),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="go",
            allowed_source_suffixes=(".go",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks)

    assert any(
        row["path"] == "src/engine.go"
        for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    )


def test_candidate_store_identity_rejects_path_traversal(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    with pytest.raises(ValueError, match="safe filename token"):
        load_chief_engineer_semantic_repair_candidate(
            workspace=str(tmp_path),
            project_id="../other-project",
            run_id=candidate.run_id,
            candidate_hash=candidate.candidate_hash,
        )


def test_portfolio_structural_recovery_relocates_only_existing_rows() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {"TASK-1": {"implementation_steps": ["Implement"]}},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
                "item": {"symbol": "Engine", "owner_task_id": "TASK-1", "path": "src/engine.py"},
            },
        },
        "consumer_declarations": [
            {"consumer_task_id": "TASK-1", "provider_symbol": "Engine"},
        ],
        "item": {"consumer_task_id": "TASK-2", "provider_symbol": "Engine"},
        "shared_behavior_contract": {"invariants": [], "examples": []},
        "project_completion_contract": {"obligations": {}},
        "risk_flags": [],
        "scope_for_apply": ["TASK-1"],
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert isinstance(recovery, ChiefEngineerPortfolioStructuralRecoveryV1)
    assert recovery.recovered is True
    assert malformed["construction_plan"]["project_interface_contract"]["provider_declarations"] == []
    assert "item" in malformed["construction_plan"]["project_interface_contract"]
    assert "consumer_declarations" in malformed
    assert set(recovery.repair_codes) == {
        "move_root_consumer_declarations",
        "move_root_shared_behavior_contract",
        "classify_project_interface_item_as_provider",
        "classify_root_item_as_consumer",
    }
    normalized = recovery.payload
    assert set(normalized) == {
        "construction_plan",
        "project_completion_contract",
        "risk_flags",
        "scope_for_apply",
    }
    interface = normalized["construction_plan"]["project_interface_contract"]
    assert interface["provider_declarations"] == [
        {"symbol": "Engine", "owner_task_id": "TASK-1", "path": "src/engine.py"}
    ]
    assert interface["consumer_declarations"] == [
        {"consumer_task_id": "TASK-1", "provider_symbol": "Engine"},
        {"consumer_task_id": "TASK-2", "provider_symbol": "Engine"},
    ]
    assert normalized["construction_plan"]["shared_behavior_contract"] == {
        "invariants": [],
        "examples": [],
    }
    assert recovery.source_hash != recovery.recovered_hash


def test_portfolio_structural_recovery_fails_closed_for_ambiguous_item() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
                "item": {"symbol": "Engine"},
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed
    assert recovery.repair_codes == ()


def test_portfolio_structural_recovery_does_not_invent_empty_payload() -> None:
    recovery = normalize_chief_engineer_portfolio_tool_arguments({})

    assert recovery.recovered is False
    assert recovery.payload == {}
    assert recovery.repair_codes == ()


def test_review_document_persists_through_ce_owner_at_compatibility_path(tmp_path) -> None:
    logical_path = persist_chief_engineer_review_document(
        workspace=str(tmp_path),
        run_id="factory-run-1",
        payload={"schema_version": "factory.chief_engineer_review.v2", "generated_blueprints": 1},
    )
    assert logical_path == "runtime/state/blueprints/factory-run-1.review.json"
    physical_path = Path(resolve_logical_path(str(tmp_path), logical_path))
    assert json.loads(physical_path.read_text(encoding="utf-8"))["generated_blueprints"] == 1
