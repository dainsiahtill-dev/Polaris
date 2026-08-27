"""Focused tests for typed CE semantic repair composition and CAS storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
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
    bind_chief_engineer_semantic_repair_provider_patch,
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
            primary_language="python",
            allowed_source_suffixes=(".py",),
            entrypoint_kind_authority="cli",
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


def test_artifact_patch_preserves_preexisting_artifact_entrypoint_obligation_link(
    tmp_path,
) -> None:
    """Exact L3-23 r05: a shared artifact/entrypoint id is not patch-introduced drift."""

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["entrypoints"] = [
        {
            "obligation_id": "artifact-main",
            "kind": "cli",
            "applicability": "required",
            "owner_task_id": "TASK-1",
            "source_path": "src/main.py",
            "runtime_path": None,
            "command": "python src/main.py",
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
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

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(),
    )

    obligations = repaired.candidate["project_completion_contract"]["obligations"]
    assert obligations["artifacts"][0]["obligation_id"] == "artifact-main"
    assert obligations["entrypoints"][0]["obligation_id"] == "artifact-main"
    assert obligations["artifacts"][1]["obligation_id"] == "artifact-readme"


def test_artifact_patch_rejects_new_cross_collection_obligation_collision(tmp_path) -> None:
    """The incremental guard still rejects a new artifact/verification id collision."""

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
                obligation_id="verify-build",
                path="README.md",
                semantic_role="docs",
                applicability="required",
                owner_task_id="TASK-2",
            ),
        ),
    )

    with pytest.raises(ValueError, match="duplicate obligation ids"):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=_tasks(),
        )


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
                covered_obligation_ids=("artifact-main", "entry-main", "verify-build"),
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


def test_entrypoint_patch_can_remove_diagnosed_obsolete_row_and_add_same_owner_replacement(tmp_path) -> None:
    """Exact L3-24 r35: typed repair must replace, not preserve, an invalid help-only entrypoint."""

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["entrypoints"] = [
        {
            "obligation_id": "TASK-1::cli_help",
            "kind": "cli",
            "applicability": "required",
            "owner_task_id": "TASK-1",
            "source_path": "src/main.py",
            "runtime_path": None,
            "command": "python src/main.py --help",
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_remove_obligation_ids=("TASK-1::cli_help",),
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="TASK-1::cli_demo",
                kind="cli",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="src/main.py",
                command="python src/main.py encode --text hello --key moon",
            ),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())

    entrypoints = repaired.candidate["project_completion_contract"]["obligations"]["entrypoints"]
    assert [row["obligation_id"] for row in entrypoints] == ["TASK-1::cli_demo"]
    assert set(receipt.changed_semantic_ids) >= {"TASK-1::cli_help", "TASK-1::cli_demo"}


def test_entrypoint_patch_rejects_unknown_removal_id(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_remove_obligation_ids=("missing-entrypoint",),
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="TASK-1::cli_demo",
                kind="cli",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="src/main.py",
                command="python src/main.py encode --text hello --key moon",
            ),
        ),
    )

    with pytest.raises(ValueError, match="unknown entrypoint obligation ids"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())


def test_entrypoint_patch_rejects_removal_without_same_owner_replacement(tmp_path) -> None:
    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["entrypoints"] = [
        {
            "obligation_id": "TASK-2::cli_help",
            "kind": "cli",
            "applicability": "required",
            "owner_task_id": "TASK-2",
            "source_path": None,
            "runtime_path": "README.md",
            "command": "README.md --help",
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_remove_obligation_ids=("TASK-2::cli_help",),
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="TASK-1::cli_demo",
                kind="cli",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="src/main.py",
                command="python src/main.py encode --text hello --key moon",
            ),
        ),
    )

    with pytest.raises(ValueError, match="same-owner same-kind replacement"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=_tasks())


def test_atomic_test_artifact_and_behavior_binding_can_reference_same_patch_id(tmp_path) -> None:
    """Exact L3-23 r06: depth and cross-task coverage close in one typed patch."""

    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=(
            "chief_engineer.delivery_depth.test_files_below_minimum",
            "chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing",
        ),
        allowed_operations=(
            "artifact_upsert",
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        ),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        artifact_upserts=(
            ArtifactObligationV1(
                obligation_id="artifact-product-test",
                path="tests/product_test.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-2",
            ),
        ),
        behavior_invariant_upserts=(
            ChiefEngineerBehaviorInvariantV1(
                invariant_id="behavior-product-test",
                statement="Production behavior and product tests share one contract.",
                owner_task_id="TASK-1",
                consumer_task_ids=("TASK-2",),
                covered_obligation_ids=("artifact-main", "artifact-product-test"),
                verification_examples=(
                    ChiefEngineerBehaviorExampleV1(
                        given="a product input",
                        when="the production entrypoint and test execute",
                        then="both observe the same result",
                    ),
                ),
            ),
        ),
        task_behavior_ref_replacements={
            "TASK-1": ("behavior-product-test",),
            "TASK-2": ("behavior-product-test",),
        },
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement production.",
            target_files=("src/main.py",),
            scope_paths=("src",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Verify production.",
            target_files=("tests/product_test.py",),
            scope_paths=("tests",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    obligations = repaired.candidate["project_completion_contract"]["obligations"]
    assert obligations["artifacts"][1]["obligation_id"] == "artifact-product-test"
    invariant = repaired.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["covered_obligation_ids"] == ["artifact-main", "artifact-product-test"]


def test_cpp_task_accepts_pm_authorized_python_test_harness_patch(tmp_path) -> None:
    """Exact L3-24 r21: verifier language may differ from product language."""

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
                obligation_id="artifact-moon-key-test",
                path="tests/test_moon_key.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement and verify the C++ CLI.",
            target_files=("CMakeLists.txt", "tests/test_product.py"),
            scope_paths=("CMakeLists.txt", "tests"),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules", "tests"),
            primary_language="cpp",
            allowed_source_suffixes=(".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx"),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project.",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    artifacts = repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    assert artifacts[-1]["path"] == "tests/test_moon_key.py"
    assert artifacts[-1]["semantic_role"] == "test"
    assert receipt.changed_semantic_ids == ("artifact-moon-key-test",)


def test_behavior_patch_replaces_invalid_baseline_before_strict_rehydration(tmp_path) -> None:
    """Exact r25: semantic repair must be able to replace its diagnosed defect."""

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate))
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        {
            "invariant_id": "behavior-roundtrip",
            "statement": "Writer and reader preserve values.",
            "owner_task_id": "TASK-1",
            "consumer_task_ids": ["TASK-1", "TASK-2"],
            "covered_obligation_ids": ["artifact-main", "verify-build"],
            "verification_examples": [{"given": "value", "when": "round trip", "then": "same value"}],
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.invalid",),
        allowed_operations=("behavior_invariant_upsert", "task_behavior_ref_replace"),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        behavior_invariant_upserts=(
            ChiefEngineerBehaviorInvariantV1(
                invariant_id="behavior-roundtrip",
                statement="Writer and reader preserve values.",
                owner_task_id="TASK-1",
                consumer_task_ids=("TASK-2",),
                covered_obligation_ids=("artifact-main", "verify-build"),
                verification_examples=(
                    ChiefEngineerBehaviorExampleV1(
                        given="value",
                        when="round trip",
                        then="same value",
                    ),
                ),
            ),
        ),
        task_behavior_ref_replacements={
            "TASK-1": ("behavior-roundtrip",),
            "TASK-2": ("behavior-roundtrip",),
        },
    )

    repaired, _receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(),
    )

    invariant = repaired.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["consumer_task_ids"] == ["TASK-2"]


def test_entrypoint_patch_cannot_change_immutable_pm_kind_authority(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("entrypoint.missing",),
        allowed_operations=("entrypoint_upsert",),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="entry-main-go",
                kind="web",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="main.go",
                command="go run .",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement Go CLI",
            target_files=("main.go",),
            scope_paths=("main.go",),
            entrypoint_targets=("main.go",),
            primary_language="go",
            allowed_source_suffixes=(".go",),
            entrypoint_kind_authority="cli",
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )

    with pytest.raises(ValueError, match="entrypoint kind conflicts with immutable PM authority"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks)


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


def test_provider_patch_parser_accepts_diagnosis_scoped_entrypoint_removal(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.entrypoint_contract.invalid",),
        allowed_operations=("entrypoint_upsert",),
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [],
        "entrypoint_upserts": [
            {
                "obligation_id": "TASK-1::cli_demo",
                "kind": "cli",
                "applicability": "required",
                "owner_task_id": "TASK-1",
                "source_path": "src/main.py",
                "runtime_path": None,
                "command": "python src/main.py encode --text hello --key moon",
            }
        ],
        "entrypoint_remove_obligation_ids": ["TASK-1::cli_help"],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }

    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload,
        allowed_operations=diagnosis.allowed_operations,
    )
    schema = build_chief_engineer_semantic_repair_patch_schema(
        allowed_operations=diagnosis.allowed_operations,
    )

    assert patch.entrypoint_remove_obligation_ids == ("TASK-1::cli_help",)
    assert schema["properties"]["entrypoint_remove_obligation_ids"]["items"]["type"] == "string"
    assert "entrypoint_remove_obligation_ids" not in schema["required"]


def test_provider_patch_binding_uses_active_transaction_hashes_and_audits_provider_echo(
    tmp_path: Path,
) -> None:
    """Opaque CAS identity is server authority, not model-authored content.

    Exact L3-23 r14: the provider returned a useful typed patch but copied one
    nibble of ``base_candidate_hash`` incorrectly.  The response belongs to the
    currently awaited semantic-repair turn, so the platform must bind it to the
    active candidate/diagnosis while retaining the mismatched echo as audit
    evidence.  Semantic operation validation remains unchanged and fail-closed.
    """

    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    provider_hash = candidate.candidate_hash[:27] + "e" + candidate.candidate_hash[28:]
    raw_patch = {
        "base_candidate_hash": provider_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [
            {
                "obligation_id": "artifact-domain",
                "path": "src/domain.py",
                "semantic_role": "source",
                "applicability": "required",
                "owner_task_id": "TASK-1",
            }
        ],
        "entrypoint_upserts": [],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }

    patch, binding = bind_chief_engineer_semantic_repair_provider_patch(
        raw_patch,
        candidate=candidate,
        diagnosis=diagnosis,
    )

    assert patch.base_candidate_hash == candidate.candidate_hash
    assert patch.diagnosis_hash == diagnosis.diagnosis_hash
    assert binding == {
        "schema_version": "chief_engineer.semantic_repair_provider_binding.v1",
        "authority_source": "active_semantic_repair_transaction",
        "provider_base_candidate_hash": provider_hash,
        "provider_diagnosis_hash": diagnosis.diagnosis_hash,
        "bound_base_candidate_hash": candidate.candidate_hash,
        "bound_diagnosis_hash": diagnosis.diagnosis_hash,
        "base_candidate_hash_match": False,
        "diagnosis_hash_match": True,
    }


def test_provider_patch_parser_removes_owner_from_nonempty_consumer_set(tmp_path) -> None:
    """Exact L3-22 r23: semantic patch must reuse safe primary-output recovery."""

    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.invalid",),
        allowed_operations=("behavior_invariant_upsert", "task_behavior_ref_replace"),
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [],
        "entrypoint_upserts": [],
        "behavior_invariant_upserts": [
            {
                "invariant_id": "behavior-main",
                "statement": "The producer result is consumed by the downstream task.",
                "owner_task_id": "TASK-1",
                "consumer_task_ids": ["TASK-1", "TASK-2"],
                "covered_obligation_ids": ["artifact-main"],
                "verification_examples": [{"given": "input", "when": "processed", "then": "output"}],
            }
        ],
        "task_behavior_ref_replacements": {"TASK-2": ["behavior-main"]},
    }

    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload,
        allowed_operations=diagnosis.allowed_operations,
    )

    assert patch.behavior_invariant_upserts[0].consumer_task_ids == ("TASK-2",)

    owner_only_payload = json.loads(json.dumps(payload))
    owner_only_payload["behavior_invariant_upserts"][0]["consumer_task_ids"] = ["TASK-1"]
    owner_only_patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        owner_only_payload,
        allowed_operations=diagnosis.allowed_operations,
    )
    assert owner_only_patch.behavior_invariant_upserts[0].consumer_task_ids == ()


def test_provider_patch_parser_ignores_untrusted_groups_outside_diagnosis_authority(tmp_path) -> None:
    """Exact L3-22 r16: irrelevant malformed groups cannot preempt authorized repair."""

    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.prod_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    payload = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "artifact_upserts": [
            {
                "obligation_id": "artifact-support",
                "path": "src/support.py",
                "semantic_role": "source",
                "applicability": "required",
                "owner_task_id": "TASK-1",
            }
        ],
        "entrypoint_upserts": [
            {
                "obligation_id": "entry-invalid-but-unauthorized",
                "kind": "cli",
                "applicability": "required",
                "owner_task_id": "TASK-1",
                "source_path": "src/main.py",
                "runtime_path": ".",
                "command": "python src/main.py",
            }
        ],
        "behavior_invariant_upserts": [
            {
                "invariant_id": "behavior-invalid-but-unauthorized",
                "statement": "Owner is incorrectly also a consumer.",
                "owner_task_id": "TASK-1",
                "consumer_task_ids": ["TASK-1"],
                "covered_obligation_ids": ["artifact-main"],
                "verification_examples": [{"given": "input", "when": "processed", "then": "output"}],
            }
        ],
        "task_behavior_ref_replacements": {},
    }

    with pytest.raises(ValueError, match="runtime_path"):
        ChiefEngineerSemanticRepairPatchV1.from_provider_dict(payload)

    patch = ChiefEngineerSemanticRepairPatchV1.from_provider_dict(
        payload,
        allowed_operations=diagnosis.allowed_operations,
    )
    assert patch.operations == ("artifact_upsert",)
    assert patch.artifact_upserts[0].path == "src/support.py"
    assert patch.entrypoint_upserts == ()
    assert patch.behavior_invariant_upserts == ()


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


def test_composer_normalizes_safe_shared_artifact_group_before_behavior_repair(tmp_path) -> None:
    """Exact L3-23 r01: one group id may describe several same-owner files."""

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"] = [
        {
            "obligation_id": "OBL-RESTAURANT-CORE",
            "path": "src/main.py",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
        {
            "obligation_id": "OBL-RESTAURANT-CORE",
            "path": "src/support.py",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
    ]
    obligations["verification"] = [
        {
            "obligation_id": "verify-build",
            "modality": "build",
            "covers_obligation_ids": ["OBL-RESTAURANT-CORE"],
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=base.task_ids,
        task_set_hash=base.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.cross_task_production_test_coverage_missing",),
        allowed_operations=(
            "behavior_invariant_upsert",
            "task_behavior_ref_replace",
        ),
    )
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        behavior_invariant_upserts=(
            ChiefEngineerBehaviorInvariantV1(
                invariant_id="behavior-restaurant-core",
                statement="Production and tests share the restaurant queue contract.",
                owner_task_id="TASK-1",
                consumer_task_ids=("TASK-2",),
                covered_obligation_ids=("OBL-RESTAURANT-CORE", "verify-build"),
                verification_examples=(
                    ChiefEngineerBehaviorExampleV1(
                        given="a restaurant queue",
                        when="the verifier runs",
                        then="production and tests observe the same ordering",
                    ),
                ),
            ),
        ),
        task_behavior_ref_replacements={
            "TASK-1": ("behavior-restaurant-core",),
            "TASK-2": ("behavior-restaurant-core",),
        },
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True),
    )

    repaired_obligations = repaired.candidate["project_completion_contract"]["obligations"]
    artifact_ids = [row["obligation_id"] for row in repaired_obligations["artifacts"]]
    assert len(artifact_ids) == 2
    assert len(set(artifact_ids)) == 2
    assert {row["path"] for row in repaired_obligations["artifacts"]} == {
        "src/main.py",
        "src/support.py",
    }
    assert repaired_obligations["verification"][0]["covers_obligation_ids"] == artifact_ids
    invariant = repaired.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["covered_obligation_ids"] == [*artifact_ids, "verify-build"]
    minted_ids = set(artifact_ids) - {"OBL-RESTAURANT-CORE"}
    assert minted_ids
    assert minted_ids.issubset(receipt.changed_semantic_ids)


def test_structural_recovery_splits_safe_shared_artifact_group_transactionally(tmp_path) -> None:
    payload = json.loads(json.dumps(_candidate(tmp_path).candidate, ensure_ascii=False))
    payload["construction_plan"]["project_interface_contract"] = {
        "provider_declarations": [],
        "consumer_declarations": [],
    }
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"] = [
        {
            "obligation_id": "artifact-shared-group",
            "path": "src/main.py",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
        {
            "obligation_id": "artifact-shared-group",
            "path": "src/support.py",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
    ]
    obligations["verification"] = [
        {
            "obligation_id": "verify-build",
            "modality": "build",
            "covers_obligation_ids": ["artifact-shared-group"],
        }
    ]
    payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
        {
            "invariant_id": "behavior-shared",
            "statement": "Both files implement one shared behavior.",
            "owner_task_id": "TASK-1",
            "consumer_task_ids": ["TASK-2"],
            "covered_obligation_ids": ["artifact-shared-group", "verify-build"],
            "verification_examples": [{"given": "input", "when": "processed", "then": "shared output"}],
        }
    ]

    recovery = normalize_chief_engineer_portfolio_tool_arguments(payload)

    assert "split_shared_artifact_obligation_ids" in recovery.repair_codes
    repaired = recovery.payload
    repaired_obligations = repaired["project_completion_contract"]["obligations"]
    artifact_ids = [row["obligation_id"] for row in repaired_obligations["artifacts"]]
    assert len(artifact_ids) == len(set(artifact_ids)) == 2
    assert repaired_obligations["verification"][0]["covers_obligation_ids"] == artifact_ids
    invariant = repaired["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["covered_obligation_ids"] == [*artifact_ids, "verify-build"]


def test_structural_recovery_splits_artifact_group_colliding_with_its_covering_verifier(tmp_path) -> None:
    """Exact L3-24: verifier identity survives while grouped test artifacts receive unique ids."""

    payload = json.loads(json.dumps(_candidate(tmp_path).candidate, ensure_ascii=False))
    payload["construction_plan"]["project_interface_contract"] = {
        "provider_declarations": [],
        "consumer_declarations": [],
    }
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"] = [
        {
            "obligation_id": "verify-tests",
            "path": "tests/test_product.py",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        },
        {
            "obligation_id": "verify-tests",
            "path": "tests/test_deterministic.py",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        },
    ]
    obligations["verification"] = [
        {
            "obligation_id": "verify-tests",
            "modality": "test",
            "command_authority_hash": "test-authority",
            "applicability": "required",
            "owner_task_id": "TASK-2",
            "covers_obligation_ids": ["verify-tests"],
        }
    ]

    recovery = normalize_chief_engineer_portfolio_tool_arguments(payload)

    assert recovery.recovered is True
    repaired = recovery.payload["project_completion_contract"]["obligations"]
    artifact_ids = [row["obligation_id"] for row in repaired["artifacts"]]
    assert len(artifact_ids) == len(set(artifact_ids)) == 2
    assert "verify-tests" not in artifact_ids
    assert repaired["verification"][0]["obligation_id"] == "verify-tests"
    assert set(repaired["verification"][0]["covers_obligation_ids"]) == set(artifact_ids)


def test_patch_schema_has_no_delete_or_freeform_path_surface() -> None:
    schema = build_chief_engineer_semantic_repair_patch_schema()
    rendered = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    assert "delete" not in rendered
    assert "json_pointer" not in rendered
    assert schema["additionalProperties"] is False


def test_patch_schema_validates_only_diagnosis_authorized_operation_items() -> None:
    """Exact L3-24 r02: ignored entrypoint noise must not kill artifact repair."""

    schema = build_chief_engineer_semantic_repair_patch_schema(
        allowed_operations=("artifact_upsert",),
    )
    payload = {
        "base_candidate_hash": "a" * 64,
        "diagnosis_hash": "b" * 64,
        "artifact_upserts": [
            {
                "obligation_id": "artifact-test-cpp",
                "path": "tests/test_compile.cpp",
                "semantic_role": "test",
                "applicability": "required",
                "owner_task_id": "TASK-1",
            }
        ],
        "entrypoint_upserts": [{"command": ""}],
        "behavior_invariant_upserts": [],
        "task_behavior_ref_replacements": {},
    }

    Draft202012Validator(schema).validate(payload)
    strict_payload = json.loads(json.dumps(payload))
    strict_payload["artifact_upserts"][0]["path"] = ""
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(strict_payload)


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
    assert projected["upsert_identity_policy"]["artifact_upsert"] == {
        "id_field": "obligation_id",
        "immutable_fields": ["path", "semantic_role", "owner_task_id"],
        "existing_identities": {
            "artifact-main": {
                "path": "src/main.py",
                "semantic_role": "source",
                "owner_task_id": "TASK-1",
            }
        },
        "new_identity_rule": (
            "If the desired path, semantic_role, or owner_task_id differs from an existing obligation, "
            "mint a new unique obligation_id and leave the existing row unchanged."
        ),
    }
    assert "entrypoints" not in projected["current"]
    assert projected["task_authority"]["TASK-1"] == {
        "target_files": ["src/main.py"],
        "scope_paths": ["src"],
        "unused_exact_target_paths": [],
        "expandable_scope_paths": ["src"],
        "topology_authority": "pm",
        "required_source_kinds": [],
        "primary_language": "python",
        "allowed_source_suffixes": [".py"],
        "entrypoint_kind_authority": "cli",
        "delegated_artifact_roles": [],
    }
    assert projected["repair_feasible"] is True


def test_provider_context_counts_cross_language_test_harnesses(tmp_path) -> None:
    """A test-shaped harness may verify a product written in another language."""

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["artifacts"] = [
        {
            "obligation_id": "artifact-engine",
            "path": "src/engine.cpp",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
        *[
            {
                "obligation_id": f"artifact-python-test-{index}",
                "path": path,
                "semantic_role": "test",
                "applicability": "required",
                "owner_task_id": "TASK-1",
            }
            for index, path in enumerate(
                (
                    "tests/test_product.py",
                    "tests/invisible_diary_asserts.py",
                    "tests/invisible_diary_ledger_test.py",
                ),
                start=1,
            )
        ],
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.delivery_depth.test_files_below_minimum",),
        allowed_operations=("artifact_upsert",),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement and test the C++ diary engine.",
            target_files=("src/engine.cpp",),
            scope_paths=("src", "tests"),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules", "tests"),
            primary_language="cpp",
            allowed_source_suffixes=(".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx"),
            delivery_depth_contract={"minimums": {"min_prod_files": 1, "min_test_files": 2}},
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Document the project.",
            target_files=("README.md",),
            scope_paths=("README.md",),
        ),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=tasks,
    )

    assert projected["delivery_depth_feasibility"]["actual"] == {
        "prod_files": 1,
        "test_files": 3,
    }
    assert projected["delivery_depth_feasibility"]["deficits"] == []


def test_behavior_repair_context_includes_allowed_completion_verification_ids(tmp_path) -> None:
    candidate = _candidate(tmp_path)
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.shared_behavior_contract.invalid",),
        allowed_operations=("behavior_invariant_upsert", "task_behavior_ref_replace"),
    )

    projected = project_chief_engineer_semantic_repair_provider_context(
        candidate,
        diagnosis,
        tasks=_tasks(),
    )

    verification = projected["current"]["verification"]
    assert [row["obligation_id"] for row in verification] == ["verify-build"]
    assert projected["allowed_completion_obligation_ids"] == [
        "artifact-main",
        "verify-build",
    ]


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

    paths = {row["path"] for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]}
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

    assert (
        repaired.candidate["project_completion_contract"]["obligations"]["entrypoints"]
        == payload["project_completion_contract"]["obligations"]["entrypoints"]
    )


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


def test_composer_rebinds_new_artifact_to_unique_authorized_task(tmp_path) -> None:
    """Exact L3-22 r13: repair may mislabel owner while PM authority is unique."""

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["construction_plan"]["task_plans"]["TASK-3"] = {"behavior_invariant_refs": []}
    task_ids = ("TASK-1", "TASK-2", "TASK-3")
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=task_ids,
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
        candidate=payload,
    )
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
                obligation_id="artifact-extra-test",
                path="tests/test_extra.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement domain modules",
            target_files=("src/main.py",),
            scope_paths=("src/main.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Implement CLI",
            target_files=("src/cli.py",),
            scope_paths=("src/cli.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("entrypoint",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-3",
            objective="Implement tests",
            target_files=("tests/test_main.py",),
            scope_paths=("tests/test_main.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("tests",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    artifact = next(
        row
        for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
        if row["obligation_id"] == "artifact-extra-test"
    )
    assert artifact["owner_task_id"] == "TASK-3"
    assert receipt.patch_hash != patch.patch_hash


def test_composer_rebinds_existing_artifact_when_frozen_owner_is_uniquely_unauthorized(
    tmp_path,
) -> None:
    """Exact L3-23 r03: deterministic owner correction must survive identity guard."""

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["artifacts"] = [
        {
            "obligation_id": "OBL-LIB-ENTRY",
            "path": "src/lib.rs",
            "semantic_role": "entrypoint",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=base.task_ids,
        task_set_hash=base.task_set_hash,
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
                obligation_id="OBL-LIB-ENTRY",
                path="src/lib.rs",
                semantic_role="entrypoint",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement domain modules",
            target_files=("src/domain.rs",),
            scope_paths=("src/domain.rs",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="rust",
            allowed_source_suffixes=(".rs",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Implement entrypoints",
            target_files=("src/main.rs",),
            scope_paths=("src/main.rs",),
            topology_authority="chief_engineer",
            required_source_kinds=("entrypoint",),
            primary_language="rust",
            allowed_source_suffixes=(".rs",),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    artifact = repaired.candidate["project_completion_contract"]["obligations"]["artifacts"][0]
    assert artifact["owner_task_id"] == "TASK-2"
    assert receipt.changed_semantic_ids == ("OBL-LIB-ENTRY",)
    assert receipt.patch_hash != patch.patch_hash


def test_depth_composer_rebinds_all_uniquely_unauthorized_frozen_test_owners(
    tmp_path: Path,
) -> None:
    """Depth repair must normalize the frozen baseline, not only repeated rows.

    Exact L3-23 r14 froze three ``tests/*.rs`` artifacts under TASK-2 even
    though TASK-2 delegates only domain/entrypoint topology and TASK-3 is the
    unique tests authority.  Requiring the provider to repeat every bad row is
    token waste and left valid test delivery invisible to feasibility checks.
    """

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["construction_plan"]["task_plans"]["TASK-3"] = {"behavior_invariant_refs": []}
    payload["project_completion_contract"]["obligations"]["artifacts"] = [
        {
            "obligation_id": "artifact-main",
            "path": "src/lib.rs",
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        },
        {
            "obligation_id": "artifact-test-a",
            "path": "tests/domain.rs",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        },
        {
            "obligation_id": "artifact-test-b",
            "path": "tests/behavior.rs",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        },
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=("TASK-1", "TASK-2", "TASK-3"),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-1", "TASK-2", "TASK-3")),
        candidate=payload,
    )
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
                obligation_id="artifact-product-test",
                path="tests/product.rs",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-3",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement Rust domain modules",
            target_files=("Cargo.toml",),
            scope_paths=("Cargo.toml",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="rust",
            allowed_source_suffixes=(".rs",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Implement Rust entrypoint",
            target_files=("Cargo.toml",),
            scope_paths=("Cargo.toml",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules", "entrypoint"),
            primary_language="rust",
            allowed_source_suffixes=(".rs",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-3",
            objective="Implement Rust tests",
            target_files=("tests/product.rs",),
            scope_paths=("tests/product.rs",),
            topology_authority="chief_engineer",
            required_source_kinds=("tests",),
            primary_language="rust",
            allowed_source_suffixes=(".rs",),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    test_rows = [
        row
        for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
        if row["semantic_role"] == "test"
    ]
    assert {row["owner_task_id"] for row in test_rows} == {"TASK-3"}
    assert {row["path"] for row in test_rows} == {
        "tests/domain.rs",
        "tests/behavior.rs",
        "tests/product.rs",
    }
    assert {"artifact-test-a", "artifact-test-b"}.issubset(receipt.changed_semantic_ids)


def test_composer_keeps_authorized_patch_subset_and_drops_extra_operation(tmp_path) -> None:
    """Exact L3-22 r14: one extra operation must not discard useful repair work."""

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
        entrypoint_upserts=(
            EntrypointObligationV1(
                obligation_id="entry-extra",
                kind="cli",
                applicability="required",
                owner_task_id="TASK-1",
                source_path="src/main.py",
                command="python src/main.py",
            ),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True),
    )

    obligations = repaired.candidate["project_completion_contract"]["obligations"]
    assert [row["obligation_id"] for row in obligations["artifacts"]] == [
        "artifact-main",
        "artifact-support",
    ]
    assert obligations["entrypoints"] == []
    assert receipt.changed_semantic_ids == ("artifact-support",)
    assert receipt.patch_hash != patch.patch_hash


def test_composer_preserves_frozen_baseline_path_alias_while_adding_unique_artifact(tmp_path) -> None:
    """Exact L3-22 r15: incremental repair must not rejudge untouched baseline aliases."""

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "artifact-main-entrypoint-role",
            "path": "src/main.py",
            "semantic_role": "entrypoint",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        }
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=base.task_ids,
        task_set_hash=base.task_set_hash,
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

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True),
    )

    paths = [row["path"] for row in repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]]
    assert paths.count("src/main.py") == 2
    assert paths.count("src/support.py") == 1
    assert receipt.changed_semantic_ids == ("artifact-support",)


def test_composer_rejects_patch_that_introduces_new_artifact_path_alias(tmp_path) -> None:
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
                obligation_id="artifact-main-alias",
                path="src/main.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    with pytest.raises(ValueError, match="create duplicate artifact paths"):
        compose_chief_engineer_semantic_repair(
            candidate,
            diagnosis,
            patch,
            tasks=_tasks(),
        )


def test_composer_keeps_unique_depth_upserts_and_drops_one_redundant_path_alias(tmp_path) -> None:
    """Exact L3-22 r40: one redundant path must not discard useful depth work."""

    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "artifact-main-entrypoint-role",
            "path": "src/main.py",
            "semantic_role": "entrypoint",
            "applicability": "required",
            "owner_task_id": "TASK-1",
        }
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=base.task_ids,
        task_set_hash=base.task_set_hash,
        candidate=payload,
    )
    diagnosis = ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=(
            "chief_engineer.delivery_depth.prod_files_below_minimum",
            "chief_engineer.delivery_depth.test_files_below_minimum",
        ),
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
            ArtifactObligationV1(
                obligation_id="artifact-main-depth-alias",
                path="src/main.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=_tasks(expandable=False, delegated=True),
    )

    artifacts = repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    paths = [row["path"] for row in artifacts]
    assert paths.count("src/main.py") == 2
    assert "src/support.py" in paths
    assert "artifact-main-depth-alias" not in {row["obligation_id"] for row in artifacts}
    assert receipt.changed_semantic_ids == ("artifact-support",)
    assert receipt.patch_hash != patch.patch_hash


def test_composer_rejects_artifact_owner_rebind_when_authority_is_ambiguous(tmp_path) -> None:
    base = _candidate(tmp_path)
    payload = json.loads(json.dumps(base.candidate, ensure_ascii=False))
    payload["construction_plan"]["task_plans"]["TASK-3"] = {"behavior_invariant_refs": []}
    task_ids = ("TASK-1", "TASK-2", "TASK-3")
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=base.workspace,
        project_id=base.project_id,
        run_id=base.run_id,
        pm_contract_hash=base.pm_contract_hash,
        task_ids=task_ids,
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(task_ids),
        candidate=payload,
    )
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
                obligation_id="artifact-ambiguous-test",
                path="tests/test_extra.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement domain modules",
            target_files=("src/main.py",),
            scope_paths=("src/main.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("domain_modules",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
        *(
            ChiefEngineerPortfolioTaskV1(
                task_id=task_id,
                objective="Implement tests",
                target_files=(target,),
                scope_paths=(target,),
                topology_authority="chief_engineer",
                required_source_kinds=("tests",),
                primary_language="python",
                allowed_source_suffixes=(".py",),
            )
            for task_id, target in (("TASK-2", "tests/test_main.py"), ("TASK-3", "tests/test_cli.py"))
        ),
    )

    with pytest.raises(ValueError, match="outside immutable PM authority"):
        compose_chief_engineer_semantic_repair(candidate, diagnosis, patch, tasks=tasks)


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


def test_depth_repair_splits_reused_artifact_id_and_expands_verifier_refs(tmp_path) -> None:
    """Exact L3-23 r08: provider group-id reuse means split, not identity mutation.

    A depth repair may naturally reuse the existing test obligation id while
    proposing a second same-owner/same-role physical test file.  The platform
    can normalize that shape without weakening immutable identity: preserve the
    original row, mint a deterministic id for the new path, and expand verifier
    coverage atomically.
    """

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append(
        {
            "obligation_id": "artifact-tests",
            "path": "tests/product.py",
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": "TASK-2",
        }
    )
    obligations["verification"] = [
        {
            "obligation_id": "verify-tests",
            "modality": "test",
            "covers_obligation_ids": ["artifact-tests"],
        }
    ]
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
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
                obligation_id="artifact-tests",
                path="tests/engine_decisions.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-2",
            ),
        ),
    )
    tasks = (
        _tasks()[0],
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-2",
            objective="Verify product behavior.",
            target_files=("tests/product.py",),
            scope_paths=("tests/product.py",),
            topology_authority="chief_engineer",
            required_source_kinds=("tests",),
            primary_language="python",
            allowed_source_suffixes=(".py",),
        ),
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    repaired_obligations = repaired.candidate["project_completion_contract"]["obligations"]
    test_rows = [row for row in repaired_obligations["artifacts"] if row["semantic_role"] == "test"]
    rows_by_path = {row["path"]: row for row in test_rows}
    assert set(rows_by_path) == {"tests/product.py", "tests/engine_decisions.py"}
    assert rows_by_path["tests/product.py"]["obligation_id"] == "artifact-tests"
    minted_id = rows_by_path["tests/engine_decisions.py"]["obligation_id"]
    assert minted_id.startswith("artifact-normalized-")
    assert repaired_obligations["verification"][0]["covers_obligation_ids"] == [
        "artifact-tests",
        minted_id,
    ]
    assert minted_id in receipt.changed_semantic_ids


def test_depth_repair_splits_optional_existing_test_when_new_path_is_required(tmp_path) -> None:
    """Exact L3-24 r51: applicability may change when a reused ID is split.

    The frozen CE candidate contained one optional Python harness.  Repair
    reused its obligation ID for a required C++ test at a new authorized path.
    Applicability is mutable payload, not semantic identity: preserve the old
    optional row and mint a new ID for the required physical test.
    """

    seed = _candidate(tmp_path)
    payload = json.loads(json.dumps(seed.candidate, ensure_ascii=False))
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append(
        {
            "obligation_id": "OBL-TEST-EXTRA",
            "path": "tests/test_cli_edge.py",
            "semantic_role": "test",
            "applicability": "optional",
            "owner_task_id": "TASK-1",
        }
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=seed.workspace,
        project_id=seed.project_id,
        run_id=seed.run_id,
        pm_contract_hash=seed.pm_contract_hash,
        task_ids=seed.task_ids,
        task_set_hash=seed.task_set_hash,
        candidate=payload,
    )
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
                obligation_id="OBL-TEST-EXTRA",
                path="tests/test_cli_edge.cpp",
                semantic_role="test",
                applicability="required",
                owner_task_id="TASK-1",
            ),
        ),
    )
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Implement and verify the C++ CLI.",
            target_files=("tests/test_product.py",),
            scope_paths=("tests",),
            topology_authority="chief_engineer",
            required_source_kinds=("tests",),
            primary_language="cpp",
            allowed_source_suffixes=(".cpp",),
        ),
        _tasks()[1],
    )

    repaired, receipt = compose_chief_engineer_semantic_repair(
        candidate,
        diagnosis,
        patch,
        tasks=tasks,
    )

    rows = repaired.candidate["project_completion_contract"]["obligations"]["artifacts"]
    rows_by_path = {row["path"]: row for row in rows}
    assert rows_by_path["tests/test_cli_edge.py"]["applicability"] == "optional"
    new_row = rows_by_path["tests/test_cli_edge.cpp"]
    assert new_row["applicability"] == "required"
    assert new_row["obligation_id"].startswith("artifact-normalized-")
    assert new_row["obligation_id"] in receipt.changed_semantic_ids


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


def test_portfolio_structural_recovery_normalizes_provider_array_wrappers() -> None:
    """Recover preserved CE rows that a provider lifted/wrapped as ``item``."""

    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-3": {
                    "behavior_invariant_refs": ["INV-1", "INV-2", "INV-3"],
                    "scope_for_apply": ["tests/main_test.go", "go.mod"],
                    "risk_flags": ["risk:test_coverage"],
                }
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-3"],
                        "covered_obligation_ids": ["VERIFY-1"],
                        "statement": "Existing invariant",
                        "verification_examples": [],
                    }
                ],
                "item": {
                    "invariant_id": "INV-2",
                    "owner_task_id": "TASK-1",
                    "consumer_task_ids": {"item": ["TASK-3"]},
                    "covered_obligation_ids": {"item": ["VERIFY-1"]},
                    "statement": "Lifted singleton invariant",
                    "verification_examples": {"item": {"given": "x", "when": "y", "then": "z"}},
                },
            },
            "item": [
                {
                    "invariant_id": "INV-3",
                    "owner_task_id": "TASK-1",
                    "consumer_task_ids": {"item": ["TASK-3"]},
                    "covered_obligation_ids": {"item": ["VERIFY-1"]},
                    "statement": "Lifted invariant array",
                    "verification_examples": {"item": []},
                }
            ],
            "TASK-3": {
                "behavior_invariant_refs": {"item": ["INV-1", "INV-2", "INV-3"]},
                "scope_for_apply": {"item": "tests/main_test.go"},
                "risk_flags": {"item": ["risk:test_coverage"]},
            },
        },
        "project_completion_contract": {"obligations": {}},
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert malformed["construction_plan"]["shared_behavior_contract"]["item"]["consumer_task_ids"] == {
        "item": ["TASK-3"]
    }
    construction = recovery.payload["construction_plan"]
    assert set(construction) == {
        "task_plans",
        "project_interface_contract",
        "shared_behavior_contract",
    }
    assert construction["task_plans"]["TASK-3"]["scope_for_apply"] == [
        "tests/main_test.go",
        "go.mod",
    ]
    assert construction["shared_behavior_contract"]["invariants"] == [
        malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0],
        {
            "invariant_id": "INV-2",
            "owner_task_id": "TASK-1",
            "consumer_task_ids": ["TASK-3"],
            "covered_obligation_ids": ["VERIFY-1"],
            "statement": "Lifted singleton invariant",
            "verification_examples": [{"given": "x", "when": "y", "then": "z"}],
        },
        {
            "invariant_id": "INV-3",
            "owner_task_id": "TASK-1",
            "consumer_task_ids": ["TASK-3"],
            "covered_obligation_ids": ["VERIFY-1"],
            "statement": "Lifted invariant array",
            "verification_examples": [],
        },
    ]
    assert set(recovery.repair_codes) >= {
        "move_shared_behavior_item_to_invariants",
        "move_construction_items_to_behavior_invariants",
        "remove_redundant_lifted_task_plan",
        "unwrap_behavior_invariant_array_items",
    }


def test_portfolio_structural_recovery_partitions_mixed_construction_items() -> None:
    """Move proven invariant rows while preserving unrelated lifted plan content."""

    phase = {
        "phase_id": "PHASE-2",
        "files": {"item": ["src/main.cpp"]},
        "deliverables": {"item": ["Build the CLI"]},
    }
    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {"behavior_invariant_refs": ["INV-LOCAL"]},
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
            "item": [
                {
                    "invariant_id": "INV-LOCAL",
                    "owner_task_id": "TASK-1",
                    "consumer_task_ids": {"item": "TASK-1"},
                    "covered_obligation_ids": {"item": "OBL-SOURCE"},
                    "statement": "Task-local behavior",
                    "verification_examples": {"item": []},
                },
                phase,
                "src/main.cpp",
            ],
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(
        malformed,
        authoritative_task_ids=("TASK-1",),
    )

    assert recovery.recovered is True
    assert set(recovery.repair_codes) >= {
        "move_construction_items_to_behavior_invariants",
        "remove_task_local_invariant_from_shared_contract",
    }
    construction = recovery.payload["construction_plan"]
    assert construction["item"] == [phase, "src/main.cpp"]
    assert construction["shared_behavior_contract"]["invariants"] == []
    assert construction["task_plans"]["TASK-1"]["behavior_invariant_refs"] == []


def test_portfolio_structural_recovery_preserves_symbol_item_with_owner_task_id() -> None:
    """A lifted symbol row is not an invariant merely because both carry an owner."""

    symbol_row = {
        "owner_file": "include/invisible_diary/cipher.hpp",
        "owner_task_id": "TASK-1",
        "symbol_kind": "class",
    }
    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {"behavior_invariant_refs": ["INV-LOCAL"]},
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-LOCAL",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-1"],
                        "covered_obligation_ids": ["OBF-SRC-CIPHER"],
                        "statement": "Cipher round trips preserve diary text.",
                        "verification_examples": [],
                    }
                ]
            },
            "item": [symbol_row],
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "OBF-SRC-CIPHER",
                        "path": "src/cipher.cpp",
                        "semantic_role": "source",
                    }
                ],
                "entrypoints": [],
                "verification": [],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(
        malformed,
        authoritative_task_ids=("TASK-1",),
    )

    assert recovery.recovered is True
    assert recovery.payload["construction_plan"]["item"] == [symbol_row]
    assert recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"] == []
    assert recovery.payload["construction_plan"]["task_plans"]["TASK-1"]["behavior_invariant_refs"] == []
    assert recovery.repair_codes == ("remove_task_local_invariant_from_shared_contract",)


def test_portfolio_structural_recovery_refuses_conflicting_lifted_task_plan() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-3": {
                    "behavior_invariant_refs": ["INV-1"],
                    "scope_for_apply": ["tests/main_test.go"],
                    "risk_flags": [],
                }
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
            "TASK-3": {
                "behavior_invariant_refs": {"item": ["INV-CONFLICT"]},
                "scope_for_apply": {"item": ["tests/main_test.go"]},
                "risk_flags": {"item": []},
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed
    assert recovery.repair_codes == ()


def test_portfolio_structural_recovery_moves_missing_lifted_task_plans_before_behavior_repair() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {
                    "behavior_invariant_refs": ["INV-SHARED"],
                }
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-SHARED",
                        "owner_task_id": "TASK-2",
                        "consumer_task_ids": ["TASK-2"],
                        "covered_obligation_ids": ["VERIFY-1"],
                        "statement": "A shared behavior",
                        "verification_examples": [{"given": "x", "when": "y", "then": "z"}],
                    }
                ]
            },
            "TASK-2": {
                "behavior_invariant_refs": {"item": ["INV-SHARED"]},
                "risk_flags": {"item": ["risk:owner"]},
                "implementation_phases": {"item": {"phase_label": "author"}},
            },
            "TASK-3": {
                "behavior_invariant_refs": {"item": ["INV-SHARED"]},
                "scope_for_apply": {"item": ["tests/main_test.go"]},
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    construction = recovery.payload["construction_plan"]
    assert set(construction) == {
        "task_plans",
        "project_interface_contract",
        "shared_behavior_contract",
    }
    assert set(construction["task_plans"]) == {"TASK-1", "TASK-2", "TASK-3"}
    assert construction["task_plans"]["TASK-2"]["behavior_invariant_refs"] == ["INV-SHARED"]
    assert construction["task_plans"]["TASK-2"]["implementation_phases"] == {"item": {"phase_label": "author"}}
    assert construction["shared_behavior_contract"]["invariants"][0]["consumer_task_ids"] == [
        "TASK-1",
        "TASK-3",
    ]
    assert set(recovery.repair_codes) >= {
        "move_lifted_task_plans",
        "rebind_behavior_consumers_from_task_refs",
    }


def test_portfolio_structural_recovery_does_not_invent_empty_payload() -> None:
    recovery = normalize_chief_engineer_portfolio_tool_arguments({})

    assert recovery.recovered is False
    assert recovery.payload == {}
    assert recovery.repair_codes == ()


def test_portfolio_structural_recovery_removes_owner_from_real_consumer_set() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-1", "TASK-2"],
                    }
                ]
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("remove_behavior_owner_from_consumers",)
    invariant = recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["consumer_task_ids"] == ["TASK-2"]
    assert malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0]["consumer_task_ids"] == [
        "TASK-1",
        "TASK-2",
    ]


def test_portfolio_structural_recovery_refuses_owner_only_consumer_set() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-1"],
                    }
                ]
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed
    assert recovery.repair_codes == ()


def test_portfolio_structural_recovery_removes_proven_task_local_invariant_from_shared_contract() -> None:
    """A shared row referenced only by its owner is provably task-local, not cross-task."""

    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {"behavior_invariant_refs": ["INV-LOCAL"]},
                "TASK-2": {"behavior_invariant_refs": []},
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-LOCAL",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-1"],
                    }
                ]
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("remove_task_local_invariant_from_shared_contract",)
    construction = recovery.payload["construction_plan"]
    assert construction["shared_behavior_contract"]["invariants"] == []
    assert construction["task_plans"]["TASK-1"]["behavior_invariant_refs"] == []
    assert malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0]["invariant_id"] == "INV-LOCAL"


def test_portfolio_structural_recovery_rebinds_owner_only_consumer_from_task_refs() -> None:
    """Use existing task-plan evidence; never invent a consumer task."""

    malformed = {
        "construction_plan": {
            "task_plans": {
                "TASK-1": {"behavior_invariant_refs": ["INV-1"]},
                "TASK-2": {"behavior_invariant_refs": []},
            },
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-2",
                        "consumer_task_ids": ["TASK-2"],
                    }
                ]
            },
        }
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("rebind_behavior_consumers_from_task_refs",)
    invariant = recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["consumer_task_ids"] == ["TASK-1"]
    assert malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0]["consumer_task_ids"] == [
        "TASK-2"
    ]


def test_portfolio_structural_recovery_removes_only_unknown_mixed_obligation_refs() -> None:
    """Keep real artifact/verification authority while dropping a hallucinated alias."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-2"],
                        "covered_obligation_ids": [
                            "ART-main.go",
                            "ART-main_test.go",
                            "VER-go-test",
                        ],
                    }
                ]
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-main.go",
                        "path": "main.go",
                        "semantic_role": "entrypoint",
                    }
                ],
                "entrypoints": [],
                "verification": [{"obligation_id": "VER-go-test", "modality": "test"}],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("remove_unknown_behavior_obligation_refs",)
    invariant = recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["covered_obligation_ids"] == ["ART-main.go", "VER-go-test"]
    assert malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"] == [
        "ART-main.go",
        "ART-main_test.go",
        "VER-go-test",
    ]


def test_portfolio_structural_recovery_refuses_unknown_only_obligation_refs() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-2"],
                        "covered_obligation_ids": ["ART-main_test.go"],
                    }
                ]
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-main.go",
                        "path": "main.go",
                        "semantic_role": "entrypoint",
                    }
                ],
                "entrypoints": [],
                "verification": [{"obligation_id": "VER-go-test", "modality": "test"}],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed
    assert recovery.repair_codes == ()


def test_portfolio_structural_recovery_maps_known_verifier_coverage_aliases() -> None:
    """Unknown leaf ids may map only through explicit verifier coverage evidence."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-1",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-2"],
                        "covered_obligation_ids": [
                            "OBL-RULE-GRAVITY",
                            "OBL-RULE-DAMPING",
                        ],
                    }
                ]
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [],
                "entrypoints": [],
                "verification": [
                    {
                        "obligation_id": "OBL-VERIFY-TEST",
                        "modality": "test",
                        "covers_obligation_ids": [
                            "OBL-RULE-GRAVITY",
                            "OBL-RULE-DAMPING",
                        ],
                    }
                ],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("map_behavior_obligation_aliases_to_verification",)
    invariant = recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    assert invariant["covered_obligation_ids"] == ["OBL-VERIFY-TEST"]
    assert malformed["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"] == [
        "OBL-RULE-GRAVITY",
        "OBL-RULE-DAMPING",
    ]


def test_portfolio_structural_recovery_closes_consumed_verifier_aliases() -> None:
    """A behavior alias consumed by mapping must not remain an unknown verifier ref."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {
                "invariants": [
                    {
                        "invariant_id": "INV-MODULE",
                        "owner_task_id": "TASK-1",
                        "consumer_task_ids": ["TASK-2"],
                        "covered_obligation_ids": ["OBL-MODULE-COMPILE"],
                    }
                ]
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-MODULE",
                        "path": "go.mod",
                        "semantic_role": "manifest",
                    }
                ],
                "entrypoints": [],
                "verification": [
                    {
                        "obligation_id": "VER-GO-BUILD",
                        "modality": "build",
                        "covers_obligation_ids": ["OBL-MODULE-COMPILE", "ART-MODULE"],
                    }
                ],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == (
        "map_behavior_obligation_aliases_to_verification",
        "remove_consumed_verifier_coverage_aliases",
    )
    invariant = recovery.payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]
    verifier = recovery.payload["project_completion_contract"]["obligations"]["verification"][0]
    assert invariant["covered_obligation_ids"] == ["VER-GO-BUILD"]
    assert verifier["covers_obligation_ids"] == ["ART-MODULE"]
    assert malformed["project_completion_contract"]["obligations"]["verification"][0]["covers_obligation_ids"] == [
        "OBL-MODULE-COMPILE",
        "ART-MODULE",
    ]


def test_portfolio_structural_recovery_keeps_unconsumed_unknown_verifier_refs() -> None:
    """Arbitrary unknown verifier refs remain fail-closed rather than being erased."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-MODULE",
                        "path": "go.mod",
                        "semantic_role": "manifest",
                    }
                ],
                "entrypoints": [],
                "verification": [
                    {
                        "obligation_id": "VER-GO-BUILD",
                        "modality": "build",
                        "covers_obligation_ids": ["OBL-UNKNOWN", "ART-MODULE"],
                    }
                ],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed


def test_portfolio_structural_recovery_infers_missing_artifact_roles_from_unambiguous_paths() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {"obligation_id": "ART-MANIFEST", "path": "go.mod"},
                    {"obligation_id": "ART-MAIN", "path": "main.go"},
                    {"obligation_id": "ART-SOURCE", "path": "engine/world.go"},
                    {"obligation_id": "ART-TEST", "path": "engine/world_test.go"},
                    {"obligation_id": "ART-DOCS", "path": "README.md"},
                ],
                "entrypoints": [{"source_path": "main.go", "runtime_path": "main.go"}],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("infer_missing_artifact_semantic_roles",)
    artifacts = recovery.payload["project_completion_contract"]["obligations"]["artifacts"]
    assert [row["semantic_role"] for row in artifacts] == [
        "manifest",
        "entrypoint",
        "source",
        "test",
        "docs",
    ]
    assert all(
        "semantic_role" not in row for row in malformed["project_completion_contract"]["obligations"]["artifacts"]
    )


def test_portfolio_structural_recovery_replaces_invalid_artifact_role_from_unambiguous_path() -> None:
    """Exact L3-24 r47: provider alias ``build`` must not strand CMakeLists."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-BUILD",
                        "path": "CMakeLists.txt",
                        "semantic_role": "build",
                    }
                ],
                "entrypoints": [],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is True
    assert recovery.repair_codes == ("infer_invalid_artifact_semantic_roles",)
    artifact = recovery.payload["project_completion_contract"]["obligations"]["artifacts"][0]
    assert artifact["semantic_role"] == "manifest"
    assert malformed["project_completion_contract"]["obligations"]["artifacts"][0]["semantic_role"] == "build"


def test_portfolio_structural_recovery_refuses_ambiguous_invalid_artifact_role() -> None:
    """An unknown provider enum remains fail-closed when the path proves no role."""

    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "ART-UNKNOWN",
                        "path": "payload.data",
                        "semantic_role": "build",
                    }
                ],
                "entrypoints": [],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed


def test_portfolio_structural_recovery_refuses_ambiguous_missing_artifact_role() -> None:
    malformed = {
        "construction_plan": {
            "task_plans": {},
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [{"obligation_id": "ART-UNKNOWN", "path": "tests/payload.data"}],
                "entrypoints": [],
            }
        },
    }

    recovery = normalize_chief_engineer_portfolio_tool_arguments(malformed)

    assert recovery.recovered is False
    assert recovery.payload == malformed
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
