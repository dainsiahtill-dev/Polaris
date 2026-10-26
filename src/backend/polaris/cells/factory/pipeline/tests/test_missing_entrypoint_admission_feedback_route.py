"""R02-shaped private module fixtures; provider/build processes are synthetic.

CE ownership, admission, typed carrier, collector, Director DEO/CAS edits and
Broker receipts are real. These fixtures never qualify a native delivery.
"""

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
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    validate_director_handoff_from_payload,
)
from polaris.cells.chief_engineer.blueprint.public.tests.library_completion_fixture import (
    build_library_completion_requirements,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _portfolio_command_authority,
)
from polaris.cells.control_plane.run_ledger.public import RunLedgerProjectionResultV1
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_materialization_admission_feedback_route import _original_owner
from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import (
    bound_director_scope as bound_director_scope,
)
from polaris.cells.factory.verification_guard.public import ProjectCompletionOwnerObservationV1Error
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as broker_owner
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import create_task_runtime_execution_attempt_authority


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, script: str = "node dist/cli/main.js"):
    workspace = tmp_path / "project"
    workspace.mkdir()
    files = {
        "src/cli/main.ts": "console.log('synthetic entrypoint');\n",
        "tests/main.test.mjs": "import test from 'node:test'; test('fixture', () => {});\n",
        "package.json": json.dumps(
            {
                "scripts": {"build": "tsc", "test": "node --test tests/main.test.mjs", "start": script},
                "devDependencies": {"typescript": "5.0.0"},
            }
        )
        + "\n",
        "tsconfig.json": json.dumps({"compilerOptions": {"rootDir": ".", "outDir": "dist"}, "include": ["src"]}) + "\n",
    }
    for name, content in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    # Synthetic installed-metadata fixture; no package manager runs in this test.
    (workspace / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"devDependencies": {"typescript": "5.0.0"}},
                    "node_modules/typescript": {"version": "5.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    installed = workspace / "node_modules/typescript/package.json"
    installed.parent.mkdir(parents=True)
    installed.write_text('{"version":"5.0.0"}', encoding="utf-8")
    task = ChiefEngineerPortfolioTaskV1(
        task_id="DEO-LIFECYCLE",
        objective="Build and run declared CLI",
        target_files=tuple(files),
        scope_paths=tuple(files),
    )
    environment = VerificationCommandAuthorityV1(
        task_id=task.task_id, modality="environment_prep", argv=("npm", "install")
    )
    build = VerificationCommandAuthorityV1(task_id=task.task_id, modality="build", argv=("npm", "run", "build"))
    test = VerificationCommandAuthorityV1(task_id=task.task_id, modality="test", argv=("npm", "test"))
    entry = VerificationCommandAuthorityV1(
        task_id=task.task_id, modality="entrypoint", argv=("npm", "run", "start", "--", "--ticks=20")
    )
    completion = build_library_completion_requirements(
        "src/cli/main.ts",
        "package.json",
        "tsconfig.json",
        owner_task_ids=(task.task_id,) * 3,
        test_path="tests/main.test.mjs",
        test_owner_task_id=task.task_id,
    )
    completion["obligations"]["artifacts"][1]["semantic_role"] = "manifest"
    completion["obligations"]["artifacts"][2]["semantic_role"] = "config"
    completion["obligations"]["entrypoints"] = [
        {
            "obligation_id": "entrypoint-cli",
            "kind": "cli",
            "applicability": "required",
            "owner_task_id": task.task_id,
            "source_path": "src/cli/main.ts",
            "runtime_path": "dist/cli/main.js",
            "command": entry.command,
        }
    ]
    for row in completion["obligations"]["verification"]:
        if row["modality"] == "environment_prep":
            row.update(
                command_authority_hash=environment.authority_hash,
                applicability="required",
                covers_obligation_ids=["artifact-2"],
                owner_task_id=task.task_id,
            )
        if row["modality"] in {"build", "test"}:
            authority = build if row["modality"] == "build" else test
            row["command_authority_hash"] = authority.authority_hash
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id="run",
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,),
                project_kind="application",
                workspace=workspace,
                run_id="run",
                project_id="synthetic-entrypoint",
                command_authority_override=(environment, build, test, entry),
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
                            "job_token_ids": ["synthetic-module-job-token"],
                            "latest_token_id": "synthetic-module-job-token",
                            "gate_policy": {"enabled_evidence_modalities": ["command"]},
                        }
                    }
                },
            }
        )

    monkeypatch.setattr(bootstrap, "read_run_ledger_projection", ledger)
    launches: list[tuple[str, ...]] = []

    def physical(_runner, *, argv, on_launched, **_kwargs):
        launches.append(argv)
        on_launched(f"synthetic-entrypoint-process-{len(launches)}", None, None)
        if argv[-2:] == ("run", "build"):
            config = json.loads((workspace / "tsconfig.json").read_text(encoding="utf-8"))
            suffix = "cli/main.js" if config["compilerOptions"]["rootDir"] == "src" else "src/cli/main.js"
            target = workspace / "dist" / suffix
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("console.log('synthetic compiled output');\n", encoding="utf-8")
        return broker.ProjectVerificationProcessResultV1(
            exit_code=0,
            timed_out=False,
            output_bytes=b"synthetic module fixture\nTAP version 13\n1..1\nok 1 - fixture\n# tests 1\n# pass 1\n# fail 0\n",
        )

    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", physical)
    return executor, run, contract, portfolio, task, launches


@pytest.mark.asyncio
async def test_current_missing_ce_entrypoint_survives_policy_carrier_and_collector_as_unexecuted_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, contract, _portfolio, task, launches = _fixture(tmp_path, monkeypatch)
    observation = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    assert not observation.blockers, observation.to_dict()
    row = next(item for item in observation.verifier_results if item.get("failure_kind") == "admission")
    assert row["phase"] == "artifact_profile"
    assert row["execution_started"] is False and row["receipt_ref"] is None and row["exit_code"] is None
    failure = row["repair_diagnostic"]["metadata"]["admission_failure"]
    assert failure["owner_task_id"] == task.task_id
    assert failure["completion_contract_hash"] == contract.contract_hash
    assert failure["cause"] == "package_entrypoint_target_missing"
    assert failure["target_path"] == "dist/cli/main.js"
    assert (executor.workspace / "dist/src/cli/main.js").is_file()
    assert not (executor.workspace / "dist/cli/main.js").exists()
    assert len(launches) == 3  # environment/build/test ran; denied entrypoint did not.


async def _config_effect(workspace: Path, attempt: Any, content: str) -> list[dict[str, Any]]:
    from polaris.tests.integration import test_deo_2c_production_receipt_wiring as production

    arguments = {
        "file": "tsconfig.json",
        "search": (workspace / "tsconfig.json").read_text(encoding="utf-8"),
        "replace": content,
    }
    candidate = production._candidate(
        attempt,
        ordinal=0,
        normalized_tool_name="edit_file",
        normalized_arguments=tuple(sorted(arguments.items())),
        target_path="tsconfig.json",
        allowed_paths=("tsconfig.json",),
        target_exists=True,
        target_before_content_hash=hashlib.sha256((workspace / "tsconfig.json").read_bytes()).hexdigest(),
        turn_id="entrypoint-layout-repair",
        batch_id="entrypoint-layout-repair-batch",
    )
    policy = production._WorkspaceObservingPolicyPort(workspace)
    prepared_result = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=create_task_runtime_execution_attempt_authority(attempt),
        turn_id="entrypoint-layout-repair",
        batch_id="entrypoint-layout-repair-batch",
        candidates=(candidate,),
    )
    assert prepared_result.status == "ready", prepared_result
    prepared = prepared_result.prepared_batch
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
            (member.tool_call_id, production._job_restriction_evidence(allowed_paths=("tsconfig.json",))),
        ),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(member.tool_call_id), tool_name="edit_file", arguments=arguments
    )
    batches = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId("entrypoint-layout-repair-batch"),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        production.TurnId("entrypoint-layout-repair"),
    )
    assert batches[0].success_count == 1, batches[0].raw_results
    return list(batches[0].raw_results)


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate", ["correct_layout", "unchanged_layout"])
async def test_original_owner_config_repair_preserves_ce_entrypoint_and_requires_fresh_broker_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate: str
) -> None:
    executor, run, contract, portfolio, task, launches = _fixture(tmp_path, monkeypatch)
    first, _handoff, _generated = _original_owner(executor, run, portfolio, task)
    before_config = (executor.workspace / "tsconfig.json").read_bytes()
    before_manifest = (executor.workspace / "package.json").read_bytes()
    calls = []

    async def provider(_adapter, message, **kwargs):
        calls.append(kwargs["context"])
        assert "package_entrypoint_target_missing" in message
        assert "dist/cli/main.js" in message
        assert "tsconfig.json" in kwargs["context"]["metadata"]["control_plane_job_token"]["allowed_write_paths"]
        assert "dist/cli/main.js" not in kwargs["context"]["metadata"]["control_plane_job_token"]["allowed_write_paths"]
        attempt = kwargs["context"]["task_runtime_execution_attempt"]
        content = (
            json.dumps(
                {
                    "compilerOptions": {
                        "rootDir": "src" if candidate == "correct_layout" else ".",
                        "outDir": "dist",
                        "strict": True,
                    },
                    "include": ["src"],
                }
            )
            + "\n"
        )
        rows = await _config_effect(executor.workspace, attempt, content)
        return {"content": "Synthetic provider committed permitted source config edit", "tool_results": rows}

    monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", provider)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor,
        run=run,
        stage_status="failed",
        error_code="director_execution_failed",
        context={"workspace_quality_repair_max_rounds": 1},
    )
    assert len(calls) == 1, result
    assert calls[0]["task_runtime_execution_attempt"].task_id == first.task_id
    assert (executor.workspace / "package.json").read_bytes() == before_manifest
    assert contract.obligations.entrypoints[0].runtime_path == "dist/cli/main.js"
    if candidate == "unchanged_layout":
        assert result["ok"] is False, result
        assert (executor.workspace / "tsconfig.json").read_bytes() == before_config
        assert result["llm_repair"]["task_runtime_repair_attempt"]["candidate_accepted"] is False
        assert all("--ticks=20" not in argv for argv in launches)
        return
    assert result["ok"] is True, result
    assert (executor.workspace / "dist/cli/main.js").is_file()
    observations = result["validation_observations"]
    denial = next(
        row for item in observations for row in item["verifier_results"] if row.get("phase") == "artifact_profile"
    )
    assert denial["receipt_ref"] is None and denial["execution_started"] is False
    final = observations[-1]
    entry_row = next(row for row in final["verifier_results"] if "--ticks=20" in row["command"])
    assert entry_row["passed"] is True and entry_row["receipt_ref"] and entry_row["input_artifact_hash"]
    assert sum("--ticks=20" in argv for argv in launches) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["stale_sha", "foreign_target", "foreign_owner", "foreign_run", "source_not_owned"])
async def test_stale_or_foreign_entrypoint_feedback_never_claims_repair_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    executor, run, _contract, _portfolio, _task, launches = _fixture(tmp_path, monkeypatch)
    adapter = bootstrap.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER
    resolve = adapter.resolve_project_verification_authority

    def altered(query):
        try:
            return resolve(query)
        except ProjectCompletionOwnerObservationV1Error as error:
            assert error.admission_failure is not None
            field, value = {
                "stale_sha": ("source_sha256", "e" * 64),
                "foreign_target": ("target_path", "dist/foreign.js"),
                "foreign_owner": ("owner_task_id", "foreign"),
                "foreign_run": ("run_id", "foreign"),
                "source_not_owned": ("source_path", "foreign/package.json"),
            }[failure]
            raise ProjectCompletionOwnerObservationV1Error(
                error.error_code, str(error), admission_failure=replace(error.admission_failure, **{field: value})
            ) from error

    monkeypatch.setattr(adapter, "resolve_project_verification_authority", altered)
    claims = []

    def forbidden(*args, **kwargs):
        claims.append(kwargs)
        raise AssertionError("Invalid admission evidence cannot claim an original owner")

    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert result["ok"] is False and result["reason"] == "materialization_verification_blocked"
    assert claims == [] and all("--ticks=20" not in argv for argv in launches)
    denied = result["validation_observations"][0]["verifier_results"][-1]
    assert denied["execution_started"] is False and denied["receipt_ref"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "script",
    ["node --require ./preload.cjs dist/cli/main.js", "node --watch dist/cli/main.js", "node dist/foreign.js"],
)
async def test_unsafe_entrypoint_denial_never_reaches_director_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    executor, run, _contract, _portfolio, _task, launches = _fixture(tmp_path, monkeypatch, script=script)
    claims = []

    def forbidden(*args, **kwargs):
        claims.append(kwargs)
        raise AssertionError("Security denial cannot authorize a repair model call")

    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert result["ok"] is False and claims == []
    assert all("--ticks=20" not in argv for argv in launches)
    assert result["validation_observations"][0]["verifier_results"][-1]["receipt_ref"] is None


@pytest.mark.asyncio
async def test_empty_original_write_scope_blocks_entrypoint_repair_without_widening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _contract, portfolio, task, launches = _fixture(tmp_path, monkeypatch)
    _first, _handoff, generated = _original_owner(executor, run, portfolio, task)
    from polaris.kernelone.storage import resolve_logical_path

    blueprint = executor._read_json_artifact_payload(generated.blueprint_path)
    for name in ("job_token", "capability_token"):
        blueprint[name]["allowed_write_paths"] = []
    blueprint_path = Path(
        resolve_logical_path(str(executor.workspace), f"runtime/blueprints/{generated.blueprint_id}.json")
    )
    blueprint_path.write_text(json.dumps(blueprint), encoding="utf-8")
    assert (
        validate_director_handoff_from_payload(
            str(executor.workspace),
            {"task_id": task.task_id, "blueprint_id": generated.blueprint_id},
            require_strict=True,
        )["allowed"]
        is False
    )
    before = (executor.workspace / "tsconfig.json").read_bytes()
    providers = []

    async def forbidden(*args, **kwargs):
        providers.append(kwargs)
        raise AssertionError("Empty original scope must not reach a provider")

    monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director_execution_failed", context={}
    )
    assert result["ok"] is False and providers == []
    assert (executor.workspace / "tsconfig.json").read_bytes() == before
    assert all("--ticks=20" not in argv for argv in launches)
