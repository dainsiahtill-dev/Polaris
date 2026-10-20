"""Modern PM intent must compile into one complete CE delivery roster.

The existing trusted test issuer supplies a typed, hash-bound carrier; it is
not a persisted Factory PM run. Compiler, recipe policy, composition and
full effective-obligation validation stay real. No provider or commands run.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    ChiefEngineerSemanticRepairCandidateV1,
    ChiefEngineerSemanticRepairDiagnosisV1,
    ChiefEngineerSemanticRepairPatchV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    build_chief_engineer_blueprint_portfolio,
    compose_chief_engineer_semantic_repair,
    project_chief_engineer_effective_obligation_view,
    project_chief_engineer_semantic_repair_provider_context,
)
from polaris.cells.chief_engineer.blueprint.public.service._helpers import _ce_topology_authorizes_artifact
from polaris.cells.chief_engineer.blueprint.public.service._portfolio import _project_portfolio_artifact_rows
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _command
from polaris.cells.control_plane.verifier_policy.public import get_verifier_command_profile_feedback

TASK = "TASK-A"
SOURCES = ("src/types.ts", "src/input.ts", "src/state.ts", "src/output.ts", "src/worker.ts", "src/data.ts")
ASSETS = ("data/first.json", "data/second.json")
REQUIRED_PATHS = {
    "src/types.ts",
    "src/input.ts",
    "src/state.ts",
    "src/output.ts",
    "src/worker.ts",
    "src/data.ts",
    "src/cli.ts",
    "tests/worker.test.ts",
    "data/first.json",
    "data/second.json",
    "package.json",
    "tsconfig.json",
    "README.md",
    ".gitignore",
}
MINIMUMS = {
    "min_behavior_rules": 3,
    "min_behavior_symbols": 6,
    "min_branch_count": 3,
    "min_edge_cases": 2,
    "min_primary_entities": 3,
    "min_prod_files": 3,
    "min_prod_lines": 120,
    "min_test_assertions": 2,
    "min_test_files": 1,
}


def _task() -> ChiefEngineerPortfolioTaskV1:
    return ChiefEngineerPortfolioTaskV1(
        task_id=TASK,
        objective="Deliver a deterministic CLI with two workspace data fixtures",
        target_files=(),
        topology_authority="chief_engineer",
        verification_authority="chief_engineer",
        planning_contract_version="pm.task_intent.v1",
        observable_acceptance=("CLI consumes a declared fixture deterministically",),
        primary_language="typescript",
        allowed_source_suffixes=(".cts", ".mts", ".ts", ".tsx"),
        delivery_depth_contract={"minimums": MINIMUMS},
    )


def _payload(*, correct_kind: bool = False, declared_cli: bool = False) -> dict[str, Any]:
    pairs = [(path, "source") for path in SOURCES] + [
        ("tests/worker.test.ts", "test"),
        (ASSETS[0], "assets"),
        (ASSETS[1], "assets"),
        ("package.json", "manifest"),
        ("tsconfig.json", "config"),
        ("README.md", "docs"),
        (".gitignore", "config"),
    ]
    if declared_cli:
        pairs.append(("src/cli.ts", "entrypoint"))
    artifacts = [
        {"obligation_id": path, "path": path, "semantic_role": role, "applicability": "required", "owner_task_id": TASK}
        for path, role in pairs
    ]

    def recipe(identifier: str, modality: str, argv: list[str], covers: list[str]) -> dict[str, Any]:
        return {
            "obligation_id": identifier,
            "modality": modality,
            "argv": argv,
            "cwd": ".",
            "owner_task_id": TASK,
            "applicability": "required",
            "covers_obligation_ids": covers,
            "command_authority_hash": None,
        }

    return {
        "construction_plan": {
            "task_plans": {
                TASK: {
                    "scope_for_apply": [
                        {"path": path, "rationale": "Declared delivery component"} for path in sorted(REQUIRED_PATHS)
                    ]
                }
            },
            "project_interface_contract": {},
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": artifacts,
                "entrypoints": [
                    {
                        "obligation_id": "entrypoint_cli",
                        "kind": "cli",
                        "applicability": "required",
                        "owner_task_id": TASK,
                        "source_path": "src/cli.ts",
                        "runtime_path": "dist/cli.js",
                        "command": "npm run start -- --fixture first",
                    }
                ],
                "verification": [
                    recipe(
                        "verify_install",
                        "environment_prep",
                        ["npm", "install", "--silent"],
                        ["package.json", "tsconfig.json"],
                    ),
                    recipe(
                        "verify_type",
                        "lint" if correct_kind else "build",
                        ["npx", "tsc", "--noEmit"],
                        [*SOURCES, "src/cli.ts"],
                    ),
                    recipe(
                        "verify_build",
                        "build",
                        ["npm", "run", "build"],
                        ["package.json", "tsconfig.json", *SOURCES, "src/cli.ts"],
                    ),
                    recipe("verify_test", "test", ["npm", "run", "test"], ["tests/worker.test.ts", "src/worker.ts"]),
                    recipe(
                        "verify_entrypoint",
                        "entrypoint",
                        ["npm", "run", "start", "--", "--fixture", "first"],
                        ["entrypoint_cli", "src/cli.ts", ASSETS[0]],
                    ),
                ],
            }
        },
        "risk_flags": [],
    }


def _candidate(command: Any, payload: dict[str, Any]) -> ChiefEngineerSemanticRepairCandidateV1:
    carrier = command.authority_carrier
    task_ids = (TASK,)
    return ChiefEngineerSemanticRepairCandidateV1(
        workspace=command.workspace,
        project_id=carrier.project_id,
        run_id=command.run_id,
        pm_contract_hash=carrier.pm_contract_hash,
        task_ids=task_ids,
        task_set_hash=sha256(json.dumps(list(task_ids), separators=(",", ":")).encode("utf-8")).hexdigest(),
        candidate=payload,
    )


def _diagnosis(candidate: ChiefEngineerSemanticRepairCandidateV1) -> ChiefEngineerSemanticRepairDiagnosisV1:
    row = candidate.candidate["project_completion_contract"]["obligations"]["verification"][1]
    feedback = get_verifier_command_profile_feedback(workspace="", modality="build", argv=row["argv"])
    assert feedback["error_code"] == "verifier_modality_mismatch"
    return ChiefEngineerSemanticRepairDiagnosisV1(
        candidate_hash=candidate.candidate_hash,
        diagnostic_codes=("chief_engineer.verification_recipe.invalid",),
        allowed_operations=("verification_recipe_upsert",),
        verification_recipe_diagnostics=(
            {
                "collection": "verification",
                "source_path": ["obligations", "verification", 1],
                "obligation_id": "verify_type",
                "owner_task_id": TASK,
                "modality": "build",
                "expected_modality": "build",
                "argv": ["npx", "tsc", "--noEmit"],
                "cwd": ".",
                "allowed_recipe_collections": ["verification"],
                "allowed_recipe_modalities": ["lint"],
                "profile_feedback": feedback,
            },
        ),
    )


def test_same_legal_recipe_patch_compiles_complete_modern_roster(tmp_path: Path) -> None:
    task = _task()
    payload = _payload()
    original = deepcopy(payload)
    command = _command(tmp_path, payload, task=task)
    candidate = _candidate(command, payload)
    diagnosis = _diagnosis(candidate)
    raw = payload["project_completion_contract"]["obligations"]["verification"][1]
    recipe = {
        key: deepcopy(raw[key])
        for key in ("obligation_id", "argv", "cwd", "owner_task_id", "applicability", "covers_obligation_ids")
    }
    recipe["modality"] = "lint"
    patch = ChiefEngineerSemanticRepairPatchV1(
        base_candidate_hash=candidate.candidate_hash,
        diagnosis_hash=diagnosis.diagnosis_hash,
        verification_recipe_upserts=(recipe,),
    )
    after, _receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, patch, tasks=(task,), authority_carrier=command.authority_carrier
    )
    view = project_chief_engineer_effective_obligation_view(
        after.candidate, tasks=(task,), authority_carrier=command.authority_carrier
    )
    assert view["final_roster_complete"] is True
    assert view["normalization_errors"] == []
    obligations = view["obligations"]
    assert {row["path"] for row in obligations["artifacts"]} == REQUIRED_PATHS
    assert len(obligations["artifacts"]) == 14 and all(
        row["applicability"] == "required" for row in obligations["artifacts"]
    )
    assert {row["path"] for row in obligations["artifacts"] if row["semantic_role"] == "assets"} == set(ASSETS)
    assert {row["obligation_id"] for row in obligations["verification"]} == {
        "verify_install",
        "verify_type",
        "verify_build",
        "verify_test",
        "verify_entrypoint",
    }
    assert len(obligations["verification"]) == 5 and all(
        row["applicability"] == "required" for row in obligations["verification"]
    )
    assert all(row["owner_task_id"] == TASK for rows in obligations.values() for row in rows)
    assert task.delivery_depth_contract == {"minimums": MINIMUMS}
    assert candidate.pm_contract_hash == after.pm_contract_hash == command.authority_carrier.pm_contract_hash
    assert candidate.task_ids == after.task_ids == (TASK,)
    assert payload == original
    portfolio = build_chief_engineer_blueprint_portfolio(replace(command, llm_blueprint=after.candidate))
    plan = portfolio.task_overlays[TASK]["construction_plan"]
    assert set(plan["target_files"]) == set(plan["scope_paths"]) == REQUIRED_PATHS
    assert set(portfolio.project_interface_contract.task_file_ownership[TASK]) == REQUIRED_PATHS


def test_modern_feedback_uses_the_same_typed_delegation(tmp_path: Path) -> None:
    task = _task()
    payload = _payload()
    command = _command(tmp_path, payload, task=task)
    candidate = _candidate(command, payload)
    diagnosis = _diagnosis(candidate)
    context = project_chief_engineer_semantic_repair_provider_context(
        candidate, diagnosis, tasks=(task,), authority_carrier=command.authority_carrier
    )
    assert context["delegated_topology_task_ids"] == [TASK]
    assert set(context["task_authority"][TASK]["delegated_artifact_roles"]) == {
        "source",
        "entrypoint",
        "test",
        "manifest",
        "config",
        "docs",
        "assets",
    }


@pytest.mark.parametrize("bad_path,bad_owner", [(".polaris/private.json", TASK), ("data/foreign.json", "FOREIGN")])
def test_required_modern_obligations_are_not_silently_pruned(tmp_path: Path, bad_path: str, bad_owner: str) -> None:
    task = _task()
    payload = _payload(correct_kind=True, declared_cli=True)
    payload["project_completion_contract"]["obligations"]["artifacts"].append(
        {
            "obligation_id": "unapproved",
            "path": bad_path,
            "semantic_role": "assets",
            "applicability": "required",
            "owner_task_id": bad_owner,
        }
    )
    command = _command(tmp_path, payload, task=task)
    view = project_chief_engineer_effective_obligation_view(
        payload, tasks=(task,), authority_carrier=command.authority_carrier
    )
    assert view["final_roster_complete"] is False
    assert view["normalization_errors"]
    assert any(
        "scope" in error.lower() or "owner" in error.lower() or "unauthorized" in error.lower()
        for error in view["normalization_errors"]
    )


def test_explicit_pm_scope_and_legacy_authority_remain_restricted() -> None:
    task = _task()
    payload = _payload(correct_kind=True, declared_cli=True)
    obligations = payload["project_completion_contract"]["obligations"]
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="explicit user scope"):
        _project_portfolio_artifact_rows(
            tasks=(replace(task, scope_paths=("owned",)),),
            artifact_rows=tuple(obligations["artifacts"]),
            entrypoint_rows=tuple(obligations["entrypoints"]),
        )
    assert not _ce_topology_authorizes_artifact(
        topology_authority="pm",
        planning_contract_version="",
        required_source_kinds=(),
        allowed_source_suffixes=(".ts",),
        semantic_role="assets",
        path=ASSETS[0],
    )
    assert not _ce_topology_authorizes_artifact(
        topology_authority="chief_engineer",
        planning_contract_version="",
        required_source_kinds=("domain_modules",),
        allowed_source_suffixes=(".ts",),
        semantic_role="assets",
        path=ASSETS[0],
    )


def test_runtime_locator_and_advisory_do_not_create_source_authority() -> None:
    task = _task()
    payload = _payload(correct_kind=True)
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["entrypoints"][0]["source_path"] = None
    payload["construction_plan"]["task_plans"][TASK]["scope_for_apply"].append(
        {"path": "advisory-only.txt", "rationale": "Untrusted advice"}
    )
    projection = _project_portfolio_artifact_rows(
        tasks=(task,), artifact_rows=tuple(obligations["artifacts"]), entrypoint_rows=tuple(obligations["entrypoints"])
    )
    paths = {row["path"] for row in projection.artifact_rows}
    assert "dist/cli.js" not in paths and "advisory-only.txt" not in paths and "src/cli.ts" not in paths
