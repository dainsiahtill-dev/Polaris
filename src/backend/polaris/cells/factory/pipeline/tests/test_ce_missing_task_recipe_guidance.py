"""Factory binds real CE missing-owner findings into its scoped repair protocol."""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from polaris.cells.chief_engineer.blueprint.public import (
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    VerificationCommandAuthorityV1,
    generate_task_blueprint,
    get_blueprint_status,
    project_chief_engineer_task_blueprint,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import ChiefEngineerSemanticRepairCandidateV1
from polaris.cells.chief_engineer.blueprint.public.service import (
    BlueprintPersistence,
    bind_chief_engineer_semantic_repair_provider_patch,
    build_chief_engineer_blueprint_portfolio,
    build_chief_engineer_semantic_repair_patch_schema,
    compose_chief_engineer_semantic_repair,
    load_chief_engineer_semantic_repair_candidate,
    persist_chief_engineer_semantic_repair_candidate,
    project_chief_engineer_effective_obligation_view,
    project_chief_engineer_semantic_repair_provider_context,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_semantic_task_recipe_ownership import _ownership_case
from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _command
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _bootstrap_fact_stream_workspace,
    _characterization_authority_port,
    _executor,
    _factory_stage_context,
)
from polaris.cells.factory.pipeline.tests.test_semantic_pm_ce_handoff import _intent
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def _bound_case(
    tmp_path: Path, *, mixed: bool = False, missing_environment: bool = False, explicit_command: bool = False
) -> tuple[Any, ChiefEngineerSemanticRepairCandidateV1, Any]:
    source, payload = _ownership_case(tmp_path)
    recipes = payload["project_completion_contract"]["obligations"]["verification"]
    for identity, modality, argv, covers in (
        ("optional-test", "test", ["pytest", "-q"], ["tests"]),
        ("optional-entry", "entrypoint", ["python", "-m", "src.main", "--color"], ["source", "cli"]),
        ("optional-env", "environment_prep", ["python", "-m", "pip", "install", "."], ["manifest"]),
    ):
        recipes.append(
            {
                "obligation_id": identity,
                "owner_task_id": "TASK-A",
                "modality": modality,
                "argv": argv,
                "cwd": ".",
                "command_authority_hash": None,
                "applicability": "optional",
                "covers_obligation_ids": covers,
            }
        )
    if mixed:
        recipes[1]["modality"] = "build"
        construction = payload["construction_plan"]
        construction["shared_behavior_contract"]["invariants"] = [
            {
                "invariant_id": "INV-DOMAIN",
                "statement": "The helper keeps deterministic domain results",
                "owner_task_id": "TASK-A",
                "consumer_task_ids": ["TASK-B"],
                "covered_obligation_ids": ["source", "UNKNOWN-OBLIGATION"],
                "verification_examples": [{"given": "same input", "when": "the helper runs", "then": "same result"}],
            }
        ]
        for task_plan in construction["task_plans"].values():
            task_plan["behavior_invariant_refs"] = ["INV-DOMAIN"]
    if missing_environment:
        recipes[:] = [row for row in recipes if row["obligation_id"] not in {"environment", "optional-env"}]
    if explicit_command:
        commands = (
            VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("python", "-m", "pytest", "-q")),
        )
        source = _command(tmp_path, payload, tasks=source.tasks, commands=commands)
    else:
        source = replace(source, llm_blueprint=payload)
    carrier = source.authority_carrier
    assert carrier is not None
    task_ids = tuple(task.task_id for task in source.tasks)
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=source.workspace,
        project_id=carrier.project_id,
        run_id=source.run_id,
        pm_contract_hash=carrier.pm_contract_hash,
        task_ids=task_ids,
        task_set_hash=hashlib.sha256(json.dumps(list(task_ids), separators=(",", ":")).encode("utf-8")).hexdigest(),
        candidate=payload,
    )
    view = project_chief_engineer_effective_obligation_view(payload, tasks=source.tasks, authority_carrier=carrier)
    assert view["final_roster_complete"] is False
    errors = [
        "chief_engineer.completion_normalization:" + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in view["normalization_findings"]
    ]
    if mixed:
        errors.append("shared_behavior_contract behavior invariant has an unknown obligation reference")
    diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate, output_errors=errors
    )
    return source, candidate, diagnosis


def _recipe_patch(
    candidate: ChiefEngineerSemanticRepairCandidateV1, diagnosis: Any, *, mixed: bool = False
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "base_candidate_hash": candidate.candidate_hash,
        "diagnosis_hash": diagnosis.diagnosis_hash,
        "verification_recipe_upserts": [
            {
                "obligation_id": "check-domain-helper",
                "owner_task_id": "TASK-B",
                "modality": "test",
                "argv": ["python", "-m", "pytest", "-q"],
                "cwd": ".",
                "applicability": "optional",
                "covers_obligation_ids": ["domain-helper"],
            }
        ],
    }
    if mixed:
        old = candidate.candidate["project_completion_contract"]["obligations"]["verification"][1]
        corrected = {key: deepcopy(value) for key, value in old.items() if key != "command_authority_hash"}
        corrected["modality"] = "test"
        patch["verification_recipe_upserts"].append(corrected)
        behavior = deepcopy(candidate.candidate["construction_plan"]["shared_behavior_contract"]["invariants"][0])
        behavior["covered_obligation_ids"] = ["source", "domain-helper"]
        patch["behavior_invariant_upserts"] = [behavior]
    return patch


def test_factory_binds_owner_issued_missing_row_without_inventing_an_obligation_id(tmp_path: Path) -> None:
    _source, candidate, diagnosis = _bound_case(tmp_path)
    assert diagnosis is not None
    assert diagnosis.candidate_hash == candidate.candidate_hash
    assert diagnosis.allowed_operations == ("verification_recipe_upsert",)
    finding = diagnosis.verification_recipe_diagnostics[0]
    assert finding["source_path"] == ["obligations", "verification"]
    assert finding["owner_task_id"] == "TASK-B"
    assert "obligation_id" not in finding


@pytest.mark.parametrize("mixed", [False, True])
def test_missing_owner_patch_compiles_and_hands_off_without_borrowing_sibling_recipes(
    tmp_path: Path, mixed: bool
) -> None:
    source, candidate, diagnosis = _bound_case(tmp_path, mixed=mixed)
    assert diagnosis is not None
    context = project_chief_engineer_semantic_repair_provider_context(
        candidate, diagnosis, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    schema = build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)
    raw_patch = _recipe_patch(candidate, diagnosis, mixed=mixed)
    Draft202012Validator(schema).validate(raw_patch)
    patch, _binding = bind_chief_engineer_semantic_repair_provider_patch(
        raw_patch, candidate=candidate, diagnosis=diagnosis
    )
    after, receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, patch, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    assert receipt.after_candidate_hash == after.candidate_hash
    old_rows = candidate.candidate["project_completion_contract"]["obligations"]["verification"]
    after_rows = after.candidate["project_completion_contract"]["obligations"]["verification"]
    assert len(old_rows) == 5 and len(after_rows) == 6
    for old in old_rows:
        new = next(row for row in after_rows if row["obligation_id"] == old["obligation_id"])
        expected = deepcopy(old)
        if mixed and old["obligation_id"] == "check":
            expected["modality"] = "test"
        assert new == expected
        assert new["owner_task_id"] == "TASK-A"
    missing = next(row for row in context["verification_recipe_diagnostics"] if row.get("missing_task_recipe"))
    assert missing["owner_task_id"] == "TASK-B" and missing["modality"] is None
    assert {row["obligation_id"] for row in missing["required_artifacts"]} == {"domain-helper"}
    if mixed:
        assert "behavior_invariant_upsert" in diagnosis.allowed_operations
        assert context["invalid_behavior_obligation_references"]
        assert {row["obligation_id"] for row in context["current"]["verification"]} == {"check"}

    persist_chief_engineer_semantic_repair_candidate(after)
    cold = load_chief_engineer_semantic_repair_candidate(
        workspace=source.workspace,
        project_id=after.project_id,
        run_id=after.run_id,
        candidate_hash=after.candidate_hash,
    )
    assert cold == after
    portfolio = build_chief_engineer_blueprint_portfolio(replace(source, llm_blueprint=cold.candidate))
    own = [
        row for row in portfolio.project_completion_contract.verification_command_authority if row.task_id == "TASK-B"
    ]
    assert len(own) == 1 and own[0].modality == "test"
    task = source.tasks[1]
    pm_task = {
        "id": task.task_id,
        "title": task.objective,
        "goal": task.objective,
        "acceptance": list(task.observable_acceptance),
        "metadata": {"planning_contract_version": "pm.task_intent.v1"},
    }
    blueprint = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            workspace=source.workspace,
            task_id=task.task_id,
            run_id=source.run_id,
            objective=task.objective,
            context={**portfolio.to_task_blueprint_context(), "pm_task_contract": pm_task},
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
        )
    )
    stored = BlueprintPersistence(source.workspace, ensure_directory=False).load(blueprint.blueprint_id)
    assert stored["handoff_ready"] is True
    assert stored["allowed_commands"] == ["python -m pytest -q"]
    assert stored["verification_commands"] == []  # optional capability never becomes a new required gate
    assert stored["job_token"]["allowed_write_paths"] == ["src/domain.py"]
    status = get_blueprint_status(GetBlueprintStatusQueryV1(task_id=task.task_id, workspace=source.workspace))
    assert status.target_files == ("src/domain.py",)


def test_existing_missing_environment_and_missing_task_recipe_are_repaired_atomically(tmp_path: Path) -> None:
    source, candidate, diagnosis = _bound_case(tmp_path, missing_environment=True)
    assert diagnosis is not None
    findings = diagnosis.verification_recipe_diagnostics
    assert any(row.get("modality") == "environment_prep" and not row.get("missing_task_recipe") for row in findings)
    assert any(row.get("missing_task_recipe") for row in findings)
    raw = _recipe_patch(candidate, diagnosis)
    raw["verification_recipe_upserts"].append(
        {
            "obligation_id": "restore-environment",
            "modality": "environment_prep",
            "owner_task_id": "TASK-A",
            "argv": ["python", "-m", "pip", "install", "-e", "."],
            "cwd": ".",
            "applicability": "required",
            "covers_obligation_ids": ["manifest"],
        }
    )
    Draft202012Validator(build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)).validate(raw)
    patch, _binding = bind_chief_engineer_semantic_repair_provider_patch(raw, candidate=candidate, diagnosis=diagnosis)
    after, _receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, patch, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    view = project_chief_engineer_effective_obligation_view(
        after.candidate, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    assert view["final_roster_complete"] is True
    assert not view["normalization_errors"]


@pytest.mark.parametrize(
    "tamper",
    [
        "foreign_owner",
        "foreign_covers",
        "empty_covers",
        "reuse_id",
        "old_owner",
        "old_applicability",
        "old_covers",
        "undiagnosed",
    ],
)
def test_schema_and_composer_reject_owner_scope_and_existing_identity_drift(tmp_path: Path, tamper: str) -> None:
    source, candidate, diagnosis = _bound_case(tmp_path, mixed=True)
    raw = _recipe_patch(candidate, diagnosis, mixed=True)
    new, old = raw["verification_recipe_upserts"]
    if tamper == "foreign_owner":
        new["owner_task_id"] = "FOREIGN"
    elif tamper == "foreign_covers":
        new["covers_obligation_ids"] = ["source"]
    elif tamper == "empty_covers":
        new["covers_obligation_ids"] = []
    elif tamper == "reuse_id":
        new["obligation_id"] = "optional-test"
    elif tamper == "old_owner":
        old["owner_task_id"] = "TASK-B"
    elif tamper == "old_applicability":
        old["applicability"] = "optional"
    elif tamper == "old_covers":
        old["covers_obligation_ids"] = ["domain-helper"]
    else:
        old["obligation_id"] = "optional-test"
    schema_errors = list(
        Draft202012Validator(build_chief_engineer_semantic_repair_patch_schema(diagnosis=diagnosis)).iter_errors(raw)
    )
    # Owner/scope/new-ID restrictions are expressible at the provider boundary;
    # exact previous applicability/covers stay enforced by the existing composer.
    if tamper not in {"old_applicability", "old_covers"}:
        assert schema_errors
    patch, _binding = bind_chief_engineer_semantic_repair_provider_patch(raw, candidate=candidate, diagnosis=diagnosis)
    with pytest.raises(ValueError):
        compose_chief_engineer_semantic_repair(
            candidate, diagnosis, patch, tasks=source.tasks, authority_carrier=source.authority_carrier
        )


@pytest.mark.parametrize("field", ["base_candidate_hash", "diagnosis_hash"])
def test_typed_stale_patch_is_not_rebound_by_the_composer(tmp_path: Path, field: str) -> None:
    source, candidate, diagnosis = _bound_case(tmp_path)
    patch, _binding = bind_chief_engineer_semantic_repair_provider_patch(
        _recipe_patch(candidate, diagnosis), candidate=candidate, diagnosis=diagnosis
    )
    stale = replace(patch, **{field: "f" * 64})
    with pytest.raises(ValueError, match="does not match"):
        compose_chief_engineer_semantic_repair(
            candidate, diagnosis, stale, tasks=source.tasks, authority_carrier=source.authority_carrier
        )


def test_missing_owner_patch_cannot_replace_an_authenticated_sibling_command(tmp_path: Path) -> None:
    source, candidate, diagnosis = _bound_case(tmp_path, explicit_command=True)
    raw = _recipe_patch(candidate, diagnosis)
    legal, _binding = bind_chief_engineer_semantic_repair_provider_patch(raw, candidate=candidate, diagnosis=diagnosis)
    after, _receipt = compose_chief_engineer_semantic_repair(
        candidate, diagnosis, legal, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    portfolio = build_chief_engineer_blueprint_portfolio(replace(source, llm_blueprint=after.candidate))
    assert (
        source.authority_carrier.verification_command_authority[0]
        in portfolio.project_completion_contract.verification_command_authority
    )
    old = deepcopy(candidate.candidate["project_completion_contract"]["obligations"]["verification"][1])
    old.pop("command_authority_hash")
    old["argv"] = ["pytest", "-q"]
    raw["verification_recipe_upserts"].append(old)
    patch, _binding = bind_chief_engineer_semantic_repair_provider_patch(raw, candidate=candidate, diagnosis=diagnosis)
    with pytest.raises(ValueError, match="not diagnosed"):
        compose_chief_engineer_semantic_repair(
            candidate, diagnosis, patch, tasks=source.tasks, authority_carrier=source.authority_carrier
        )


@pytest.mark.parametrize(
    "change", ["invented_id", "foreign_owner", "empty_delegation", "bad_modality", "authority_claim"]
)
def test_missing_row_binder_rejects_untrusted_or_noncanonical_shape(tmp_path: Path, change: str) -> None:
    source, candidate, _diagnosis = _bound_case(tmp_path)
    view = project_chief_engineer_effective_obligation_view(
        candidate.candidate, tasks=source.tasks, authority_carrier=source.authority_carrier
    )
    finding = deepcopy(view["normalization_findings"][0])
    row = finding["verification_recipe_diagnostics"][0]
    if change == "invented_id":
        row["obligation_id"] = "invented"
    elif change == "foreign_owner":
        row["delegated_owner_task_ids"] = ["FOREIGN"]
    elif change == "empty_delegation":
        row["delegated_owner_task_ids"] = []
    elif change == "bad_modality":
        row["allowed_recipe_modalities"] = ["unsafe"]
    else:
        row["execution_authority"] = True
    diagnosis = OrchestrationStageExecutor._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate, output_errors=["chief_engineer.completion_normalization:" + json.dumps(finding)]
    )
    assert diagnosis is None


def test_actual_factory_repair_command_uses_the_missing_owner_schema_and_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real Factory command/TaskRuntime/compose; canned provider and PM-cutoff ports, no physical request."""
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    _bootstrap_fact_stream_workspace(tmp_path)
    source, candidate, diagnosis = _bound_case(tmp_path)
    raw = _recipe_patch(candidate, diagnosis)
    commands = []

    class RecordingRolePort:
        async def execute_role_task(self, command: Any) -> RoleExecutionResultV1:
            commands.append(command)
            return RoleExecutionResultV1(
                ok=True,
                status="completed",
                role="chief_engineer",
                workspace=str(tmp_path),
                run_id=command.run_id,
                task_id=command.task_id,
                metadata={"structured_output": raw, "provider_id": "canned", "model": "canned"},
            )

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", RecordingRolePort)
    executor = OrchestrationStageExecutor(tmp_path)
    port = _characterization_authority_port()
    run = FactoryRun(
        id=source.run_id,
        config=FactoryConfig(name="project-intent"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-20T00:00:00+00:00",
    )
    prior = RoleExecutionResultV1(
        ok=False,
        status="failed",
        role="chief_engineer",
        workspace=str(tmp_path),
        error_code="output_validation_failed",
        error_message="missing own recipe",
    )
    tasks = [
        {
            "id": task.task_id,
            "goal": task.objective,
            "schema_version": "pm.task_intent.v1",
            "product_goal": task.objective,
            "required_requirements": [],
            "product_acceptance": list(task.observable_acceptance),
            "acceptance": list(task.observable_acceptance),
            "metadata": {
                "planning_contract_version": "pm.task_intent.v1",
                "topology_authority": "chief_engineer",
                "verification_authority": "chief_engineer",
            },
            "target_files": [],
        }
        for task in source.tasks
    ]
    result = asyncio.run(
        executor._run_chief_engineer_schema_repair(
            run=run,
            authority_port=port,
            authority_binding=port.mint_authority_binding("chief_engineer"),
            prior_result=prior,
            portfolio_context=executor._chief_engineer_portfolio_context(tasks, run_id=run.id),
            portfolio_task_ids=candidate.task_ids,
            portfolio_tasks=source.tasks,
            deadline_decision=SimpleNamespace(timeout_seconds=120, to_dict=lambda: {"timeout_seconds": 120}),
            semantic_candidate=candidate,
            semantic_diagnosis=diagnosis,
            authority_carrier=source.authority_carrier,
            prompt_profile_identity={
                "language": "python",
                "task_type": "implement",
                "prompt_stage": "blueprint",
                "artifact": "cli",
            },
        )
    )
    assert result.ok, result.error_message
    assert len(commands) == 1
    captured = commands[0]
    assert "Each semantic PM task needs at least one of its own" in captured.objective
    assert "missing_task_recipe" in captured.objective
    assert captured.structured_output_contract is not None
    Draft202012Validator(captured.structured_output_contract.json_schema).validate(raw)
    context = captured.context["chief_engineer_semantic_repair_provider_context"]
    assert context["verification_recipe_diagnostics"][0]["owner_task_id"] == "TASK-B"


def test_actual_initial_factory_command_exposes_the_same_task_recipe_ownership_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual initial command capture; PM/cutoff lookup and provider are characterization ports."""
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")
    _bootstrap_fact_stream_workspace(tmp_path)
    executor = _executor(tmp_path)
    executor._write_json_artifact("tasks/plan.json", {"tasks": [_intent()]})
    commands = []

    class CapturingRolePort:
        async def execute_role_task(self, command: Any) -> RoleExecutionResultV1:
            commands.append(command)
            return RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="chief_engineer",
                workspace=str(tmp_path),
                run_id=command.run_id,
                task_id=command.task_id,
                error_code="canned_capture_stop",
                error_message="private initial command capture complete",
            )

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", CapturingRolePort)
    run = FactoryRun(
        id="initial-command-private",
        config=FactoryConfig(name="private-project"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-20T00:00:00+00:00",
    )
    result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))
    assert result.status == "failed"  # The local capture intentionally has no model output or success claim.
    assert len(commands) == 1
    command = commands[0]
    assert command.context.get("chief_engineer_schema_repair_required") is not True
    assert command.structured_output_contract is not None
    assert command.structured_output_contract.schema_name == "chief_engineer_blueprint_portfolio"
    assert (
        "Each semantic PM task that delegates verification to Chief Engineer must own at least one valid command recipe"
        in command.objective
    )
    assert "in verification or entrypoints, with required or optional applicability" in command.objective
    assert "any supported modality" in command.objective
    assert "never borrow a sibling task's command authority" in command.objective
