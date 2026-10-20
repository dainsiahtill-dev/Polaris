"""Real public admission/owner route; provider and process fixtures are synthetic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap import project_completion_diagnostics_owner as bootstrap
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _library_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.control_plane.run_ledger.public import RunLedgerProjectionResultV1
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import (
    bound_director_scope as bound_director_scope,
)
from polaris.cells.factory.verification_guard.public import ProjectCompletionOwnerObservationV1Error
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as broker_owner
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeService,
    create_task_runtime_execution_attempt_authority,
)


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, prior_build: bool = False, manifest_role: str = "manifest"
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    files = {
        "src/module.js": "export const value = 1;\n",
        "tests/module.test.mjs": "import test from 'node:test'; test('value', () => {});\n",
        "package.json": json.dumps({"type": "module", "scripts": {"test": "echo verification is not configured"}})
        + "\n",
    }
    for name, body in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    task = ChiefEngineerPortfolioTaskV1(
        task_id="DEO-LIFECYCLE", objective="Validate library", target_files=tuple(files), scope_paths=tuple(files)
    )
    test = VerificationCommandAuthorityV1(task_id=task.task_id, modality="test", argv=("npm", "test"))
    build = VerificationCommandAuthorityV1(
        task_id=task.task_id, modality="build", argv=("python", "-m", "compileall", "src")
    )
    authorities = (build, test) if prior_build else (test,)
    completion = _library_completion_requirements(
        "src/module.js",
        owner_task_ids=(task.task_id,),
        test_path="tests/module.test.mjs",
        test_owner_task_id=task.task_id,
    )
    completion["obligations"]["artifacts"].append(
        {
            "obligation_id": "manifest",
            "path": "package.json",
            "semantic_role": manifest_role,
            "applicability": "required",
            "owner_task_id": task.task_id,
        }
    )
    for row in completion["obligations"]["verification"]:
        if row["modality"] == "test":
            row["command_authority_hash"] = test.authority_hash
        if row["modality"] == "build":
            row["command_authority_hash"] = build.authority_hash
    if not prior_build:
        completion["obligations"]["verification"] = [
            row for row in completion["obligations"]["verification"] if row["modality"] != "build"
        ]
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id="run",
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,),
                project_kind="library",
                workspace=workspace,
                run_id="run",
                project_id="unit-project",
                command_authority_override=authorities,
            ),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": completion,
                "risk_flags": [],
            },
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None
    executor = OrchestrationStageExecutor(workspace)
    run = FactoryRun(
        id="run",
        config=FactoryConfig(name=contract.project_id),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-19T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
    }
    monkeypatch.setattr(
        broker_owner, "_EXECUTION_AUTHORITY_PORT", bootstrap.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER
    )

    def ledger(query):
        return RunLedgerProjectionResultV1(
            projection={
                "query_scope": {
                    "run_id": query.run_id,
                    "factory_run_id": query.factory_run_id,
                    "project_id": query.project_id,
                },
                "run_projection": {
                    "execution_capability_by_task": {
                        task.task_id: {
                            "ok": True,
                            "issues": [],
                            "latest_contract_hash": "c" * 64,
                            "latest_blueprint_hash": "d" * 64,
                            "job_token_ids": ["fixture-job-token"],
                            "latest_token_id": "fixture-job-token",
                            "gate_policy": {"enabled_evidence_modalities": ["command"]},
                        }
                    }
                },
            }
        )

    monkeypatch.setattr(bootstrap, "read_run_ledger_projection", ledger)
    launched: list[tuple[str, ...]] = []

    def physical(_runner, *, argv, on_launched, **_kwargs):
        launched.append(argv)
        on_launched("synthetic-m15-process", None, None)
        return broker.ProjectVerificationProcessResultV1(
            exit_code=0,
            timed_out=False,
            output_bytes=b"TAP version 13\n1..1\nok 1 - synthetic local fixture\n# tests 1\n# pass 1\n# fail 0\n",
        )

    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", physical)
    return executor, run, contract, portfolio, task, launched


def _original_owner(executor, run, portfolio, task):
    first = _setup_attempt(str(executor.workspace))
    generated = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task.task_id,
            workspace=str(executor.workspace),
            objective=task.objective,
            run_id=run.id,
            context={
                "task": task.to_dict(),
                "target_files": list(task.target_files),
                "scope_paths": list(task.scope_paths),
                "acceptance_criteria": ["The declared test executes"],
                "execution_checklist": ["Run declared verifier"],
                **portfolio.to_task_blueprint_context(),
            },
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
        )
    )
    handoff = validate_director_handoff_from_payload(
        str(executor.workspace), {"task_id": task.task_id, "blueprint_id": generated.blueprint_id}, require_strict=True
    )
    assert handoff["allowed"], handoff
    runtime = TaskRuntimeService(str(executor.workspace))
    runtime.update_task_row(
        first.task_id,
        metadata={
            "factory_run_id": run.id,
            "external_task_id": task.task_id,
            "target_files": list(task.target_files),
            "blueprint_id": generated.blueprint_id,
            "task_completion_projection": handoff["task_completion_projection"],
            "control_plane_job_token": handoff["job_token"],
        },
    )
    settled = create_task_runtime_execution_attempt_authority(first).settle(
        outcome="failed", summary="Verifier admission requires artifact repair"
    )
    assert settled.success
    return first, handoff, generated


@pytest.mark.asyncio
@pytest.mark.parametrize("manifest_role", ["manifest", "source", "config"])
async def test_actual_public_policy_bootstrap_collector_keeps_unexecuted_denial_and_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest_role: str
) -> None:
    executor, run, contract, _portfolio, _task, launched = _fixture(tmp_path, monkeypatch, manifest_role=manifest_role)
    observation = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    assert not observation.blockers, observation.to_dict()
    assert launched == [] and observation._owner_receipts == ()
    row = observation.verifier_results[0]
    assert row["passed"] is False and row["receipt_ref"] is None and row["exit_code"] is None
    assert row["execution_started"] is False
    diagnostic = row["repair_diagnostic"]
    assert diagnostic["path"] == "package.json"
    assert diagnostic["metadata"]["admission_failure"]["completion_contract_hash"] == contract.contract_hash
    assert "non_proving_package_script" in "\n".join(observation)
    assert "package.json" in executor._workspace_quality_repair_diagnostic_target_files(list(observation))
    covered = materialization._materialization_observation_with_coverage(observation)
    assert covered.static_evidence["controlled_checks_complete"] is False
    assert "materialization_collector_coverage_incomplete" in covered.blockers


@pytest.mark.asyncio
async def test_first_verifier_artifact_feedback_reaches_existing_original_owner_before_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, contract, portfolio, task, launched = _fixture(tmp_path, monkeypatch)
    first, handoff, _generated = _original_owner(executor, run, portfolio, task)
    seen = []

    async def provider(_adapter, message, **kwargs):
        seen.append((message, kwargs["context"]))
        return {"content": "Synthetic no-effect provider fixture", "tool_results": []}

    monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", provider)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert len(seen) == 1, result
    message, context = seen[0]
    assert context["task_id"] == task.task_id
    assert context["task_runtime_execution_attempt"].task_id == first.task_id
    assert "non_proving_package_script" in message and "package.json" in message
    assert context["metadata"]["control_plane_job_token"] == handoff["job_token"]
    assert result["ok"] is False and launched == []
    assert contract.verification_command_authority[0].argv == ("npm", "test")


async def _manifest_effect(workspace: Path, attempt: Any, content: str) -> list[dict[str, Any]]:
    """Actual claimed-owner DEO/CAS edit; only provider and process are scripted."""
    from polaris.tests.integration import test_deo_2c_production_receipt_wiring as production

    arguments = {
        "file": "package.json",
        "search": (workspace / "package.json").read_text(encoding="utf-8"),
        "replace": content,
    }
    candidate = production._candidate(
        attempt,
        ordinal=0,
        normalized_tool_name="edit_file",
        normalized_arguments=tuple(sorted(arguments.items())),
        target_path="package.json",
        allowed_paths=("package.json",),
        target_exists=True,
        target_before_content_hash=hashlib.sha256((workspace / "package.json").read_bytes()).hexdigest(),
        turn_id="admission-repair",
        batch_id="batch-admission-repair",
    )
    policy = production._WorkspaceObservingPolicyPort(workspace)
    lifecycle = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=create_task_runtime_execution_attempt_authority(attempt),
        turn_id="admission-repair",
        batch_id="batch-admission-repair",
        candidates=(candidate,),
    )
    assert lifecycle.status == "ready", lifecycle
    prepared = lifecycle.prepared_batch
    assert prepared is not None
    member = prepared.prepared_members[0].member
    fence = production.create_directed_effect_fence_ports()
    runtime = production.ToolBatchRuntime(
        executor=production._reject_non_mutation_execution,
        directed_effect_runtime=production.DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy,
            fence_admin_port=fence.admin,
            mutation_port=production.create_director_directed_effect_mutation_port(
                workspace=str(workspace), policy_snapshot_port=policy, fence_consume_port=fence.consume
            ),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=create_task_runtime_execution_attempt_authority(attempt),
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            (member.tool_call_id, production._job_restriction_evidence(allowed_paths=("package.json",))),
        ),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(member.tool_call_id), tool_name="edit_file", arguments=arguments
    )
    batches = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId("batch-admission-repair"), invocations=[invocation], serial_writes=[invocation]
        ),
        production.TurnId("admission-repair"),
    )
    assert batches[0].success_count == 1, batches[0].raw_results
    return list(batches[0].raw_results)


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", ["proving", "placeholder"])
async def test_current_owner_manifest_edit_revalidates_same_command_with_fresh_owner_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: str
) -> None:
    executor, run, contract, portfolio, task, launched = _fixture(tmp_path, monkeypatch)
    first, _handoff, _generated = _original_owner(executor, run, portfolio, task)
    before = (executor.workspace / "package.json").read_bytes()
    provider_calls = []

    async def provider(_adapter, message, **kwargs):
        provider_calls.append(kwargs["context"])
        assert "non_proving_package_script" in message
        attempt = kwargs["context"]["task_runtime_execution_attempt"]
        content = (
            json.dumps(
                {
                    "type": "module",
                    "scripts": {
                        "test": "node --test tests/module.test.mjs"
                        if candidate == "proving"
                        else "echo changed but still no runner"
                    },
                }
            )
            + "\n"
        )
        rows = await _manifest_effect(executor.workspace, attempt, content)
        return {"content": "Synthetic provider fixture committed one permitted manifest edit", "tool_results": rows}

    monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", provider)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor,
        run=run,
        stage_status="failed",
        error_code="director_execution_failed",
        context={"workspace_quality_repair_max_rounds": 1},
    )
    (tmp_path / "candidate-result.json").write_text(json.dumps(result, default=str, indent=2), encoding="utf-8")
    if candidate == "placeholder":
        assert result["ok"] is False, result
        assert launched == []
        assert (executor.workspace / "package.json").read_bytes() == before
        assert result["llm_repair"]["task_runtime_repair_attempt"]["candidate_accepted"] is False
        return
    assert result["ok"] is True, result
    assert len(provider_calls) == 1
    assert provider_calls[0]["task_runtime_execution_attempt"].task_id == first.task_id
    assert len(launched) == 1
    assert Path(launched[0][0]).name == "bwrap"
    assert "test" in launched[0]
    assert (executor.workspace / "package.json").read_bytes() != before
    observations = result["validation_observations"]
    denial = next(
        row for item in observations for row in item["verifier_results"] if row.get("phase") == "artifact_profile"
    )
    assert denial["receipt_ref"] is None and denial["execution_started"] is False
    after = observations[-1]
    assert after["static_evidence"]["controlled_checks_complete"] is True
    assert all(row["passed"] and row["receipt_ref"] for row in after["verifier_results"])
    assert (
        after["verifier_results"][0]["command_authority_hash"]
        == contract.verification_command_authority[0].authority_hash
    )
    assert after["verifier_results"][0]["input_artifact_hash"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "stale_sha",
        "foreign_task",
        "foreign_run",
        "foreign_workspace",
        "changed_argv",
        "source_outside_scope",
        "generic_error",
    ],
)
async def test_admission_identity_or_source_failures_keep_hard_block_before_owner_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    executor, run, _contract, _portfolio, _task, launched = _fixture(tmp_path, monkeypatch)
    adapter = bootstrap.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER
    resolve = adapter.resolve_project_verification_authority

    def changed(query):
        try:
            return resolve(query)
        except ProjectCompletionOwnerObservationV1Error as error:
            if failure == "generic_error":
                raise ProjectCompletionOwnerObservationV1Error(
                    error.error_code, "same artifact words, no typed witness"
                ) from error
            field, value = {
                "stale_sha": ("source_sha256", "e" * 64),
                "foreign_task": ("owner_task_id", "foreign"),
                "foreign_run": ("run_id", "foreign"),
                "foreign_workspace": ("workspace", str(tmp_path)),
                "changed_argv": ("argv", ("npm", "run", "other")),
                "source_outside_scope": ("source_path", "foreign/package.json"),
            }[failure]
            raise ProjectCompletionOwnerObservationV1Error(
                error.error_code, str(error), admission_failure=replace(error.admission_failure, **{field: value})
            ) from error

    monkeypatch.setattr(adapter, "resolve_project_verification_authority", changed)

    claims = []

    def forbidden(*args, **kwargs):
        claims.append(kwargs)
        raise AssertionError("Hard admission failure must not claim original owner")

    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert result["ok"] is False and result["reason"] == "materialization_verification_blocked"
    assert launched == [] and claims == []
    assert all(
        row["receipt_ref"] is None for item in result["validation_observations"] for row in item["verifier_results"]
    )


@pytest.mark.asyncio
async def test_explicit_empty_original_write_scope_remains_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _contract, portfolio, task, launched = _fixture(tmp_path, monkeypatch)
    _first, _handoff, generated = _original_owner(executor, run, portfolio, task)
    from polaris.kernelone.storage import resolve_logical_path

    # Strict repair authority is reloaded from the CE owner, not row metadata.
    blueprint = executor._read_json_artifact_payload(generated.blueprint_path)
    for name in ("job_token", "capability_token"):
        blueprint[name]["allowed_write_paths"] = []
    blueprint_path = Path(
        resolve_logical_path(str(executor.workspace), f"runtime/blueprints/{generated.blueprint_id}.json")
    )
    blueprint_path.write_text(json.dumps(blueprint), encoding="utf-8")
    denied = validate_director_handoff_from_payload(
        str(executor.workspace), {"task_id": task.task_id, "blueprint_id": generated.blueprint_id}, require_strict=True
    )
    assert denied["allowed"] is False
    before = (executor.workspace / "package.json").read_bytes()

    providers = []

    async def forbidden(*args, **kwargs):
        providers.append(kwargs)
        raise AssertionError("Empty original write scope must not reach provider")

    monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert result["ok"] is False and providers == []
    assert (executor.workspace / "package.json").read_bytes() == before and launched == []


@pytest.mark.asyncio
async def test_placeholder_hash_churn_does_not_change_repair_progress_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _contract, _portfolio, _task, _launched = _fixture(tmp_path, monkeypatch)
    before = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    manifest = executor.workspace / "package.json"
    manifest.write_text(
        json.dumps({"type": "module", "scripts": {"test": "echo still no verification configured"}}), encoding="utf-8"
    )
    after = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    assert not before.blockers and not after.blockers
    assert executor._workspace_quality_diagnostic_signature(
        list(before)
    ) == executor._workspace_quality_diagnostic_signature(list(after))
    assert (
        before.verifier_results[0]["repair_diagnostic"]["metadata"]["admission_failure"]["source_sha256"]
        != after.verifier_results[0]["repair_diagnostic"]["metadata"]["admission_failure"]["source_sha256"]
    )
