"""Stage continuation orchestration units; no provider or physical qualification."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import (
    factory_materialization_impl as materialization,
    factory_workspace_quality_impl as quality,
)
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture as _entrypoint_fixture
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import authored as authored

_BOUND_DIRECTOR = ContextVar("materialization_unit_bound_director", default=False)


async def _entrypoint_source_effect(
    workspace: Path, path: str, content: str, turn_id: str, *, attempt: Any = None
) -> tuple[Any, list[dict[str, Any]]]:
    """Use actual Director lifecycle, mutation, CAS and committed effect receipts."""
    from polaris.tests.integration import test_deo_2c_production_receipt_wiring as production

    if attempt is None:
        attempt = production._setup_attempt(str(workspace))
    else:
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1, TaskRuntimeService

        owner = TaskRuntimeService(str(workspace))
        owner.reopen_task_row(attempt.task_id, reason="workspace_quality_gate_failed")
        claimed = owner.claim_execution(
            attempt.task_id,
            worker_id="worker",
            role_id="director",
            run_id=attempt.run_id,
            external_task_id=attempt.external_task_id,
            selection_source="test",
        )
        assert claimed["success"], claimed
        attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    arguments = {
        "file": path,
        "search": (workspace / path).read_text(encoding="utf-8"),
        "replace": content,
    }
    candidate = production._candidate(
        attempt,
        ordinal=0,
        normalized_tool_name="edit_file",
        normalized_arguments=tuple(sorted(arguments.items())),
        target_path=path,
        allowed_paths=(path,),
        target_exists=True,
        target_before_content_hash=hashlib.sha256((workspace / path).read_bytes()).hexdigest(),
        turn_id=turn_id,
        batch_id=f"batch-{turn_id}",
    )
    policy = production._WorkspaceObservingPolicyPort(workspace)
    authority = production._authority(attempt)
    lifecycle = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id=turn_id,
        batch_id=f"batch-{turn_id}",
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
            (member.tool_call_id, production._job_restriction_evidence(allowed_paths=(path,))),
        ),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(member.tool_call_id),
        tool_name="edit_file",
        arguments=arguments,
    )
    batches = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId(f"batch-{turn_id}"), invocations=[invocation], serial_writes=[invocation]
        ),
        production.TurnId(turn_id),
    )
    assert batches[0].success_count == 1, batches[0].raw_results
    return prepared.execution_attempt, list(batches[0].raw_results)


@pytest.mark.asyncio
async def test_real_test_repair_retained_before_same_owner_build_config_entrypoint_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )
    from polaris.cells.runtime.execution_broker.public import project_verification as broker

    executor, run, port = _entrypoint_fixture(
        tmp_path,
        monkeypatch,
        failing=True,
        entrypoint=True,
        compiled_entrypoint=True,
        task_id="DEO-LIFECYCLE",
        run_id="run",
    )
    contract = port.completion_contract
    for artifact in contract.obligations.artifacts:
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    initial = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    calls: list[str] = []
    observations = [initial.to_dict()]

    async def director(_executor: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get()
        path, content = (
            ("src/module.py", "value = 8\n") if not calls else ("build.json", '{"output":"dist/entrypoint.pyc"}\n')
        )
        if calls:
            assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 8\n"
            assert "required entrypoint artifact missing" in "\n".join(kwargs["artifact_quality_errors"])
            assert "AssertionError" not in "\n".join(kwargs["artifact_quality_errors"])
            assert "build.json" in executor._workspace_quality_repair_diagnostic_target_files(
                kwargs["artifact_quality_errors"]
            )
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace,
            candidate_id=f"entry-repair-{len(calls)}",
            target_files=[path],
        )
        attempt, rows = await _entrypoint_source_effect(executor.workspace, path, content, f"entry-repair-{len(calls)}")
        await guard.seal_effect()
        calls.append(path)
        return rows, {
            "attempted": True,
            "success": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "mutation_committed": True,
                "candidate_guard": guard,
                "task_completion_projection": _projection(port.blueprint_portfolio, contract),
            },
        }

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    result = await materialization._continue_materialization_verifier_repair(
        executor,
        run=run,
        context={},
        result={"ok": False, "post_commit_diagnostics": list(initial)},
        sessions=[],
        observations=observations,
    )
    assert calls == ["src/module.py", "build.json"]
    assert result["ok"] is True and result["post_commit_diagnostics"] == []
    assert observations[1]["verifier_results"][-1]["exit_code"] is None
    assert observations[1]["verifier_results"][-1]["receipt_ref"] is None
    assert observations[1]["verifier_results"][-1]["repair_diagnostic"]["code"] == "failed_required_entrypoint"
    assert all(row["passed"] and row["receipt_ref"] for row in observations[2]["verifier_results"])
    assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 8\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["authority_denied", "runtime_regression"])
async def test_real_repair_rolls_back_on_security_denial_or_remaining_test_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )
    from polaris.cells.runtime.execution_broker.public import project_verification as broker

    executor, run, port = _entrypoint_fixture(
        tmp_path,
        monkeypatch,
        failing=True,
        entrypoint=True,
        compiled_entrypoint=True,
        task_id="DEO-LIFECYCLE",
        run_id="run",
    )
    contract = port.completion_contract
    for artifact in contract.obligations.artifacts:
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    initial = await materialization._collect_director_stage_materialization_diagnostics(executor, run=run)
    observations = [initial.to_dict()]
    applied = False

    async def director(_executor: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nonlocal applied
        if applied:
            return [], {"attempted": False}
        applied = True
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace,
            candidate_id="rejected-entry-repair",
            target_files=["src/module.py"],
        )
        content = "value = 8\n" + (
            "raise RuntimeError('new runtime regression')\n" if failure == "runtime_regression" else ""
        )
        attempt, rows = await _entrypoint_source_effect(
            executor.workspace, "src/module.py", content, "rejected-entry-repair"
        )
        await guard.seal_effect()
        if failure == "authority_denied":
            port.denied = True
        return rows, {
            "attempted": True,
            "success": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "mutation_committed": True,
                "candidate_guard": guard,
                "task_completion_projection": _projection(port.blueprint_portfolio, contract),
            },
        }

    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    operation = materialization._continue_materialization_verifier_repair(
        executor,
        run=run,
        context={},
        result={"ok": False, "post_commit_diagnostics": list(initial)},
        sessions=[],
        observations=observations,
    )
    if failure == "authority_denied":
        with pytest.raises(materialization._MaterializationVerificationBlockedError, match="command_policy_rejected"):
            await operation
    else:
        result = await operation
        assert result["ok"] is False
        assert any("new runtime regression" in line for line in observations[1]["diagnostics"])
        assert observations[1]["verifier_results"][2]["receipt_ref"]
        assert observations[1]["verifier_results"][2]["passed"] is False
    assert (executor.workspace / "src/module.py").read_text(encoding="utf-8") == "value = 7\n"


@pytest.fixture(autouse=True)
def bound_director_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    port = SimpleNamespace(mint_authority_binding=lambda _role: object())

    async def bound(_port: Any, role: str, operation: Any, **_kwargs: Any) -> Any:
        assert _port is port and role == "director"
        token = _BOUND_DIRECTOR.set(True)
        try:
            return await operation()
        finally:
            _BOUND_DIRECTOR.reset(token)

    monkeypatch.setattr(OrchestrationStageExecutor, "_factory_role_evidence_cutoff_port", staticmethod(lambda _c: port))
    monkeypatch.setattr(OrchestrationStageExecutor, "_call_with_factory_role_evidence_authority", staticmethod(bound))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "residual, recover_on_retry", [([], False), (["npm test still fails"], False), (["tsx dependency missing"], True)]
)
async def test_uncovered_stage_failure_uses_director_and_verifies_before_accepting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, residual: list[str], recover_on_retry: bool
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    source = tmp_path / "package.json"
    source.write_text("before", encoding="utf-8")
    failures = ["artifact_quality_error: npm test failed: package.json scripts.test discovered zero tests"]
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    session = SimpleNamespace(
        before_repair=lambda: None,
        candidate=lambda **_k: None,
        restored=lambda: None,
        close=lambda: None,
    )

    async def deterministic(_executor: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["sessions"].append(session)
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": failures}

    class Guard:
        def accept(self, **_kwargs: Any) -> dict[str, Any]:
            return {"status": "accepted"}

        async def rollback(self, **_kwargs: Any) -> dict[str, Any]:
            source.write_text("before", encoding="utf-8")
            return {"status": "restored"}

    calls = 0

    async def director(_executor: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        nonlocal calls
        calls += 1
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        if recover_on_retry and calls > 1:
            assert _kwargs["context"]["director_quality_repair"]["candidate_rejection_errors"] == residual
        source.write_text("candidate", encoding="utf-8")
        return [{"tool": "edit_file"}], {
            "attempted": True,
            "success": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": "original-owner",
                "task_row_id": "1",
                "execution_attempt": SimpleNamespace(session_id="owned-attempt"),
                "mutation_committed": True,
                "candidate_guard": Guard(),
            },
        }

    async def collect(**_kwargs: Any) -> materialization.MaterializationDiagnostics:
        assert source.read_text(encoding="utf-8") == "candidate"
        return materialization.MaterializationDiagnostics(
            [] if recover_on_retry and calls > 1 else residual,
            static_evidence={"scan_mode": "pure_static", "scan_complete": True},
        )

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", deterministic)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", collect)
    monkeypatch.setattr(executor, "_settle_director_stage_materialization_attempt", lambda **_k: {"success": True})
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    resolved = not residual or recover_on_retry
    assert result["ok"] is resolved
    assert source.read_text(encoding="utf-8") == ("candidate" if resolved else "before")
    assert result["validation_observations"][-1]["diagnostics"] == ([] if resolved else residual)
    assert "_pending_task_runtime_repair_attempt" not in str(result)


@pytest.mark.asyncio
async def test_admission_blocker_does_not_invoke_director(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )

    async def blocked(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "materialization_verification_blocked"}

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unqualified input must not reach provider repair")

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", blocked)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", forbidden)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    assert result["ok"] is False
    assert result["reason"] == "materialization_verification_blocked"


@pytest.mark.asyncio
async def test_provider_success_without_effect_does_not_clear_verifier_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    failures = ["artifact_quality_error: npm test failed: package.json scripts.test discovered zero tests"]

    async def unresolved(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": failures}

    async def no_effect(*_args: Any, **_kwargs: Any) -> tuple[list[Any], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        return [], {"attempted": True, "success": True}

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", unresolved)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", no_effect)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    assert result["ok"] is False
    assert result["post_commit_diagnostics"] == failures


@pytest.mark.asyncio
async def test_deferred_scope_reason_survives_no_provider_or_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    failures = ["test/generated.js: Error [ERR_MODULE_NOT_FOUND]: Cannot find module '../src/value.js'"]

    async def unresolved(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": failures}

    async def deferred(*_args: Any, **_kwargs: Any) -> tuple[list[Any], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get()
        return [], {
            "attempted": True,
            "success": False,
            "stage": "task_boundary_repair_targets_deferred",
            "success_reason": "repair_targets_outside_current_task_target_files",
            "llm_fallback_blocked": True,
            "task_boundary_scope_filter": {
                "reason": "outside_owner",
                "deferred": True,
                "task_declared_write_targets": ["src/value.ts"],
                "out_of_scope_repair_target_files": ["test/generated.js"],
                "scope_authority": {"credential": "must-not-be-projected"},
            },
        }

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", unresolved)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", deferred)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
    )
    assert result["ok"] is False and result["post_commit_diagnostics"] == failures
    assert result["llm_repair"]["success"] is False
    assert result["llm_repair"]["stage"] == "task_boundary_repair_targets_deferred"
    assert result["llm_repair"]["success_reason"] == "repair_targets_outside_current_task_target_files"
    assert result["llm_repair"]["llm_fallback_blocked"] is True
    assert result["llm_repair"]["task_boundary_scope_filter"]["out_of_scope_repair_target_files"] == [
        "test/generated.js"
    ]
    assert "must-not-be-projected" not in str(result)


@pytest.mark.asyncio
async def test_unproven_process_drain_does_not_settle_or_rollback_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.kernelone.process import ProcessTreeDrainError

    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id="continuation-unit",
        config=FactoryConfig(name="continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-12T00:00:00Z",
    )
    session = SimpleNamespace(before_repair=lambda: None, candidate=lambda **_k: None, close=lambda: None)

    async def unresolved(_executor: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["sessions"].append(session)
        return {"ok": False, "reason": "materialization_verifier_residual", "post_commit_diagnostics": ["test failure"]}

    async def director(*_args: Any, **_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get(), "repair provider invocation lacks the Factory Director request binding"
        return [{"tool": "edit_file"}], {"_pending_task_runtime_repair_attempt": {"mutation_committed": True}}

    async def undrained(**_kwargs: Any) -> Any:
        raise ProcessTreeDrainError("physical execution is still active")

    async def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("pending physical execution must not authorize settlement or rollback")

    monkeypatch.setattr(materialization, "_run_materialization_settle_with_session", unresolved)
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    monkeypatch.setattr(quality, "_settle_pending_workspace_quality_repair_attempt", forbidden)
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", undrained)
    with pytest.raises(ProcessTreeDrainError, match="physical execution is still active"):
        await materialization._run_director_stage_materialization_quality_settle(
            executor, run=run, stage_status="failed", error_code="director.canonical_task_boundary_missing"
        )


@pytest.mark.asyncio
async def test_native_edit_blocks_crosses_factory_filter_and_retains_two_gate_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authored: tuple
) -> None:
    """Actual DEO commits and candidate bytes drive both real isolated gates."""
    from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection, _real_effect

    portfolio, contract, _fs = authored
    executor = OrchestrationStageExecutor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="native-source-write"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-14T00:00:00Z",
    )
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    commands = [
        ["/bin/sh", "-c", 'test "$(cat src/a.py)" = after'],
        ["/bin/sh", "-c", 'test "$(cat src/authored.js)" = after'],
    ]
    gate_history = []

    async def collect(**_kwargs: Any) -> materialization.MaterializationDiagnostics:
        rows = [await session.run_command(command, 5) for command in commands]
        gate_history.append([row["exit_code"] for row in rows])
        return materialization.MaterializationDiagnostics(
            [f"source gate {index} failed" for index, row in enumerate(rows) if not row["passed"]],
            static_evidence={"scan_mode": "pure_static", "scan_complete": True},
            verifier_results=rows,
        )

    calls: list[str] = []

    async def director(_executor: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert _BOUND_DIRECTOR.get()
        path = ["src/a.py", "src/authored.js"][len(calls)]
        if calls:
            assert kwargs["artifact_quality_errors"] == ["source gate 1 failed"]
            assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"
        attempt, rows = await _real_effect(
            tmp_path, tool_name="edit_blocks", target_path=path, turn_id=f"native-round-{len(calls)}"
        )
        calls.append(path)
        assert rows[0]["effect_receipt"]["normalized_tool_name"] == "edit_blocks"
        assert rows[0]["effect_receipt_commit"]["state"] == "RECEIPT_COMMITTED"
        return rows, {
            "attempted": True,
            "success": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "mutation_committed": True,
                "task_completion_projection": _projection(portfolio, contract),
            },
        }

    # The provider and final stage-state composition are seams. Physical writes,
    # TaskRuntime claim/commit, artifact receipts, candidate proof and gates are real.
    monkeypatch.setattr(quality, "_apply_workspace_quality_llm_repairs", director)
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", collect)
    monkeypatch.setattr(executor, "_settle_director_stage_materialization_attempt", lambda **_k: {"success": True})
    try:
        initial = await collect()
        observations = [initial.to_dict()]
        result = await materialization._continue_materialization_verifier_repair(
            executor,
            run=run,
            context={},
            result={"ok": False, "post_commit_diagnostics": list(initial)},
            sessions=[session],
            observations=observations,
        )
        assert gate_history == [[1, 1], [0, 1], [0, 0]]
        assert calls == ["src/a.py", "src/authored.js"]
        assert result["ok"] is True
        assert result["post_commit_diagnostics"] == []
        assert result["mutated"] is True
        assert result["llm_repair"]["verifier_effect"] == "resolved"
        assert all(
            row["verification_group"]["drain_status"] == "owner_terminal"
            for row in observations[-1]["verifier_results"]
        )
        assert (tmp_path / "tests/test_a.py").read_text(encoding="utf-8") == "before\n"
    finally:
        session.close()
