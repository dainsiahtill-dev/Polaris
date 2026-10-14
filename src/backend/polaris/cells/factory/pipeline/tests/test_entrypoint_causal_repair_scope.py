"""Missing derived entries must preserve the existing CE owner for causal repair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    validate_director_handoff_from_payload,
)
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    workspace_quality_entrypoint_causal_repair_targets,
)
from polaris.cells.factory.pipeline.tests.test_exact_ce_clean_verification import _record_sources
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.adapters.public import service as adapters
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1, TaskRuntimeService
from polaris.cells.runtime.task_runtime.public.service import create_task_runtime_execution_attempt_authority


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["factory_scope", "director_config", "director_regression"])
async def test_real_compiled_missing_entry_keeps_full_ce_owner_in_director_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    # The public CE producer signs the fixed build/test/entry recipes. The
    # real compile succeeds but emits a different path from the signed entry.
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        entrypoint=True,
        compiled_entrypoint=True,
        stdlib_verifier=True,
        build_script_name="compile_project.py",
        task_id="DEO-LIFECYCLE",
        run_id="run",
        additional_authored_files={"README.md": "Compiled application\n", "src/support.py": "value = 1\n"},
    )
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "system"))
    _record_sources(executor, run, port)
    paths = [
        "src/module.py",
        "tests/test_output.py",
        "package.json",
        "src/entrypoint.py",
        "build.json",
        "compile_project.py",
        "README.md",
        "src/support.py",
    ]
    assert len(paths) == 8
    first = _setup_attempt(str(executor.workspace))
    ce_task = ChiefEngineerPortfolioTaskV1(
        task_id=first.external_task_id,
        objective="Build and test module",
        target_files=tuple(paths),
        scope_paths=tuple(paths),
        entrypoint_targets=("src/entrypoint.py",),
    )
    generated = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=ce_task.task_id,
            workspace=str(executor.workspace),
            objective=ce_task.objective,
            run_id=run.id,
            context={
                "task": ce_task.to_dict(),
                "target_files": paths,
                "scope_paths": paths,
                "acceptance_criteria": ["The signed compiled entry prints ready"],
                "execution_checklist": ["Run the signed build, test and entry commands"],
                **port.blueprint_portfolio.to_task_blueprint_context(),
            },
            llm_blueprint=project_chief_engineer_task_blueprint(port.blueprint_portfolio, ce_task.task_id),
        )
    )
    handoff = validate_director_handoff_from_payload(
        str(executor.workspace),
        {"task_id": ce_task.task_id, "blueprint_id": generated.blueprint_id},
        require_strict=True,
    )
    assert handoff["allowed"], json.dumps(handoff, ensure_ascii=False)
    runtime = TaskRuntimeService(str(executor.workspace))
    runtime.update_task_row(
        first.task_id,
        metadata={
            "factory_run_id": run.id,
            "external_task_id": ce_task.task_id,
            "target_files": paths,
            "blueprint_id": generated.blueprint_id,
            "task_completion_projection": handoff["task_completion_projection"],
            "control_plane_job_token": handoff["job_token"],
        },
    )
    settled = create_task_runtime_execution_attempt_authority(first).settle(
        outcome="failed",
        summary="Compiled entry is missing",
    )
    assert settled.success, settled
    observation = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    errors = list(observation)
    assert not observation.blockers
    assert "required entrypoint artifact missing" in "\n".join(errors)
    # Compiled outputs belong to the private verifier group, never the live
    # authored scope. None is invented or copied to satisfy the missing entry.
    assert not (executor.workspace / "dist/entrypoint.pyc").exists()
    commands_before = tuple(row.command_authority_hash for row in port.completion_contract.obligations.verification)
    seen: list[dict[str, Any]] = []

    async def provider_boundary(_workspace: Path, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        seen.append(kwargs)
        return [], {"attempted": True, "success": False, "error": "read_only_scope_probe"}

    if boundary == "factory_scope":
        monkeypatch.setattr(adapters, "run_director_materialization_quality_repair", provider_boundary)
    else:

        async def actual_director_boundary(_adapter: Any, message: str, **kwargs: Any) -> dict[str, Any]:
            context = kwargs["context"]
            seen.append({"context": context, "original_message": message})
            attempt = context["task_runtime_execution_attempt"]
            if isinstance(attempt, dict):
                attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt)
            config = '{"output":"dist/entrypoint.pyc"}\n'
            if boundary == "director_regression":
                config = '{"output":null}\n'
            rows = await _commit_config_effect(executor.workspace, attempt, paths, config)
            return {"content": "Repair recorded; fixed CE recipes require host validation", "tool_results": rows}

        monkeypatch.setattr(DirectorAdapter, "_invoke_role_dialogue_with_timeout", actual_director_boundary)
    _results, summary = await quality._apply_workspace_quality_llm_repairs(
        executor,
        run=run,
        context={},
        artifact_quality_errors=errors,
        repair_attempt=1,
    )
    assert len(seen) == 1, summary
    call = seen[0]
    context = call["context"]
    assert set(context["target_files"]) == set(paths)
    if boundary == "factory_scope":
        assert set(call["repair_target_files"]) == set(paths)
    assert set(context["director_quality_repair"]["repair_target_files"]) == set(paths)
    assert context["director_quality_repair"].get("write_only_single_target") is None
    assert "build.json" in call["original_message"]
    assert "package.json" in call["original_message"]
    if boundary != "factory_scope":
        assert "EXISTING FAILED TARGET FILES" not in call["original_message"]
    assert context["task_id"] == first.external_task_id
    assert context["task_runtime_execution_attempt"].task_id == first.task_id
    if boundary == "factory_scope":
        assert call["task"]["metadata"]["control_plane_job_token"] == handoff["job_token"]
    assert (
        tuple(row.command_authority_hash for row in port.completion_contract.obligations.verification)
        == commands_before
    )
    if boundary != "factory_scope":
        assert "_pending_task_runtime_repair_attempt" in summary, summary.get("error")
        pending = summary["_pending_task_runtime_repair_attempt"]
        assert pending["task_row_id"] == first.task_id
        assert pending["execution_attempt"].external_task_id == first.external_task_id
        assert summary["write_tool_evidence"] is True
        # Actual source/config CAS effects are followed by fixed, current-input
        # owner-sealed verifiers. A failed build still rolls the candidate back.
        quality._record_workspace_quality_repair_artifact_receipts(pending)
        after = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
        if boundary == "director_config":
            assert not after, after.to_dict()
            assert not after.blockers
            assert all(row["passed"] for row in after.verifier_results)
            entry = next(row for row in after.verifier_results if row["command"] == ["python", "dist/entrypoint.pyc"])
            assert entry["exit_code"] == 0
            assert entry["receipt_ref"]
            assert after._owner_receipts[-1].succeeded
        else:
            assert after
        settled = await quality._settle_pending_workspace_quality_repair_attempt(
            executor,
            pending,
            accepted=boundary == "director_config",
            reason="private_causal_revalidation",
        )
        assert settled and settled["settled"] is True, settled
        assert settled["task_id"] == first.external_task_id
        if boundary == "director_regression":
            assert (executor.workspace / "build.json").read_text(
                encoding="utf-8"
            ) == '{"output":"dist/src/entrypoint.pyc"}'
        if boundary == "director_regression":
            assert not (executor.workspace / "dist/entrypoint.pyc").exists()
    (tmp_path / "causal-scope.json").write_text(
        json.dumps(
            {
                "diagnostics": errors,
                "owner_paths": paths,
                "repair_paths": context["target_files"],
                "task_id": first.task_id,
                "entry_command": port.completion_contract.obligations.entrypoints[0].command,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "errors,owner,expected",
    [
        (
            [
                "artifact_quality_error: required entrypoint artifact missing: output/app.js; expected command: node output/app.js; repair source (source/app.ts); repair source (package.json); repair source (tsconfig.json)"
            ],
            ["source/app.ts", "package.json", "tsconfig.json", "source/dep.ts"],
            ["source/app.ts", "package.json", "tsconfig.json"],
        ),
        (
            [
                "artifact_quality_error: required entrypoint artifact missing: output/app.js; repair source (other/config.json); repair source (../escape.json); repair source (/absolute/config.json)"
            ],
            ["source/app.ts", "package.json"],
            [],
        ),
        (
            [
                "artifact_quality_error: required entrypoint artifact missing: output/app.js; repair source (package.json); repair source (package.json)"
            ],
            ["package.json"],
            ["package.json"],
        ),
        (
            ["source/app.ts(1,1): error TS6059: build failed; repair source (tsconfig.json)"],
            ["source/app.ts", "tsconfig.json"],
            [],
        ),
        (["Error: Cannot find module output/app.js; repair source (package.json)"], ["package.json"], []),
        (
            [
                "artifact_quality_error: required entrypoint artifact missing: output/app.js; repair source (package.json)"
            ],
            [],
            [],
        ),
    ],
)
def test_entry_causal_hints_cannot_grant_foreign_paths_or_replace_other_diagnostics(
    errors: list[str],
    owner: list[str],
    expected: list[str],
) -> None:
    assert workspace_quality_entrypoint_causal_repair_targets(errors, owner) == expected


async def _commit_config_effect(
    workspace: Path,
    attempt: Any,
    owner_paths: list[str],
    content: str,
) -> list[dict[str, Any]]:
    """Execute one genuine DEO/CAS config edit for the existing claimed task."""
    import hashlib

    from polaris.tests.integration import test_deo_2c_production_receipt_wiring as production

    owner_paths = sorted(set(owner_paths))
    arguments = {
        "file": "build.json",
        "search": (workspace / "build.json").read_text(encoding="utf-8"),
        "replace": content,
    }
    candidate = production._candidate(
        attempt,
        ordinal=0,
        normalized_tool_name="edit_file",
        normalized_arguments=tuple(sorted(arguments.items())),
        target_path="build.json",
        allowed_paths=tuple(owner_paths),
        target_exists=True,
        target_before_content_hash=hashlib.sha256((workspace / "build.json").read_bytes()).hexdigest(),
        turn_id="causal-config",
        batch_id="batch-causal-config",
    )
    policy = production._WorkspaceObservingPolicyPort(workspace)
    authority = create_task_runtime_execution_attempt_authority(attempt)
    lifecycle = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="causal-config",
        batch_id="batch-causal-config",
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
                workspace=str(workspace),
                policy_snapshot_port=policy,
                fence_consume_port=fence.consume,
            ),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=(
            (member.tool_call_id, production._job_restriction_evidence(allowed_paths=tuple(owner_paths))),
        ),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(member.tool_call_id), tool_name="edit_file", arguments=arguments
    )
    batches = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId("batch-causal-config"),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        production.TurnId("causal-config"),
    )
    assert batches[0].success_count == 1, batches[0].raw_results
    return list(batches[0].raw_results)


def test_explicit_prompt_scope_is_permission_not_instruction_to_rewrite_all_files(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate import (
        _build_materialization_quality_repair_message,
    )

    paths = [
        "source/main.ts",
        "source/model.ts",
        "source/types.ts",
        "source/utility.ts",
        "package.json",
        "tsconfig.json",
        "README.md",
        "test/model.test.ts",
    ]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if name.endswith(".json") else "export {};\n", encoding="utf-8")
    message = _build_materialization_quality_repair_message(
        original_message="Compile the fixed required entry without weakening acceptance",
        artifact_quality_errors=[
            "artifact_quality_error: required entrypoint artifact missing: output/main.js; repair source (source/main.ts); repair source (tsconfig.json); repair source (package.json)"
        ],
        changed_files=paths,
        repair_target_files=paths,
        workspace_full=str(tmp_path),
    )
    assert "EXISTING AUTHORIZED REPAIR PATHS" in message
    assert "smallest causal subset" in message
    assert "EXISTING FAILED TARGET FILES" not in message
    assert "repair these exact paths NOW" not in message
    assert all(path in message for path in paths)
