from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polaris.bootstrap.project_completion_convergence_runtime as convergence_runtime_module
import pytest
from polaris.bootstrap.project_completion_convergence_runtime import (
    configure_project_completion_convergence_runtime,
)
from polaris.bootstrap.project_completion_task_runtime_action_owner import (
    TaskRuntimeProjectCompletionActionOwnerV1,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.public.project_completion_notification import (
    FactoryProjectCompletionIdentityV1,
    notify_factory_project_completion,
)
from polaris.cells.factory.verification_guard.public.contracts import ProjectCompletionDiagnosticV1
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionActionCommandV1,
    ProjectCompletionDispatchClaimV1,
    ProjectCompletionIdentityV1,
)
from polaris.cells.runtime.task_market.public import (
    TaskMarketService,
)
from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService
from polaris.cells.runtime.task_runtime.public import BindRuntimeTaskToFactoryRunCommandV1
from polaris.kernelone.storage import resolve_runtime_path


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _task_runtime(workspace: str) -> TaskRuntimeService:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=workspace,
            maintenance_reason="project-completion-action-owner-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    return TaskRuntimeService(workspace)


def _action(workspace: str) -> ProjectCompletionActionCommandV1:
    identity = ProjectCompletionIdentityV1(
        workspace=workspace,
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=_hash("contract"),
    )
    diagnostic = ProjectCompletionDiagnosticV1(
        diagnostic_id=_hash("diagnostic"),
        archetype="typescript.compiler_error",
        evidence_state="failed",
        primary_module_id="M10",
        obligation_id="obligation-1",
        owner_task_id="task-owner",
        affected_target="src/main.ts",
        owner_evidence_refs=("runtime/qa/workspace-validation.json",),
        retry_class="deterministic_repair",
        allowed_next_action="run_deterministic_repair",
        dependency_ids=(),
        repair_coverage="executable_runtime",
        repair_source_tool="deterministic_typescript_unresolved_identifier_repair",
        repair_coverage_evidence_ref="runtime/repair/coverage.json",
        repair_coverage_evidence_hash=_hash("coverage"),
        required_verifier_ids=("npm.run.build",),
    )
    return ProjectCompletionActionCommandV1(
        identity=identity,
        action_id=_hash("action"),
        diagnostic_id=_hash("diagnostic"),
        obligation_id="obligation-1",
        owner_task_id="task-owner",
        action_kind="run_deterministic_repair",
        owner_snapshot_hash=_hash("snapshot"),
        owner_bundle_hash=_hash("bundle"),
        diagnostic=diagnostic,
    )


def _claim(command: ProjectCompletionActionCommandV1) -> ProjectCompletionDispatchClaimV1:
    return ProjectCompletionDispatchClaimV1(
        identity=command.identity,
        action_id=command.action_id,
        claim_id=_hash("claim"),
        attempt_ordinal=1,
        lease_expires_at="2026-08-09T12:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_production_action_owner_reopens_exact_taskruntime_owner_with_full_diagnostic(
    tmp_path: Path,
) -> None:
    workspace = str((tmp_path / "workspace").resolve())
    Path(workspace).mkdir(parents=True)
    runtime = _task_runtime(workspace)
    owner_row = runtime.ensure_task_row(external_task_id="task-owner", subject="owner")
    unrelated_row = runtime.ensure_task_row(external_task_id="task-other", subject="unrelated")
    for row in (owner_row, unrelated_row):
        assert runtime.bind_task_to_factory_run(
            BindRuntimeTaskToFactoryRunCommandV1(
                workspace=workspace,
                task_id=str(row["id"]),
                factory_run_id="run-1",
            )
        ).ok

    command = _action(workspace)
    claim = _claim(command)
    action_owner = TaskRuntimeProjectCompletionActionOwnerV1()
    receipt = await action_owner.dispatch_project_completion_action(command, claim)
    replayed = await action_owner.query_project_completion_action_receipt(command)

    assert replayed == receipt
    assert receipt.lease_id == claim.claim_id
    owner_after = runtime.get_task("task-owner")
    unrelated_after = runtime.get_task("task-other")
    assert owner_after is not None
    assert owner_after["metadata"]["factory_local_rework"]["action_id"] == command.action_id
    assert owner_after["metadata"]["last_failure"]["affected_target"] == "src/main.ts"
    assert unrelated_after is not None
    assert "factory_local_rework" not in unrelated_after["metadata"]


@pytest.mark.asyncio
async def test_action_owner_rejects_cross_run_owner_identity(tmp_path: Path) -> None:
    workspace = str((tmp_path / "wrong-run-workspace").resolve())
    Path(workspace).mkdir(parents=True)
    runtime = _task_runtime(workspace)
    row = runtime.ensure_task_row(external_task_id="task-owner", subject="owner")
    assert runtime.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=workspace,
            task_id=str(row["id"]),
            factory_run_id="another-run",
        )
    ).ok
    with pytest.raises(RuntimeError, match="task_not_found"):
        await TaskRuntimeProjectCompletionActionOwnerV1().dispatch_project_completion_action(
            _action(workspace),
            _claim(_action(workspace)),
        )


@pytest.mark.asyncio
async def test_production_convergence_runtime_replays_outbox_and_stops_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = str((tmp_path / "runtime-workspace").resolve())
    Path(workspace).mkdir(parents=True)
    relay_calls: list[str] = []
    completion_notifications: list[AdvanceProjectCompletionCommandV1] = []
    original_relay = TaskMarketService.relay_outbox_messages

    def record_relay(
        self: TaskMarketService,
        current_workspace: str,
        *,
        limit: int = 200,
    ) -> dict[str, Any]:
        relay_calls.append(current_workspace)
        return original_relay(self, current_workspace, limit=limit)

    monkeypatch.setattr(TaskMarketService, "relay_outbox_messages", record_relay)

    async def record_completion_notification(command: AdvanceProjectCompletionCommandV1) -> SimpleNamespace:
        completion_notifications.append(command)
        return SimpleNamespace(
            status="waiting",
            reason_codes=("owner_action_receipt_committed",),
            action_id="a" * 64,
            diagnostic_id="diagnostic-1",
            next_action="run_deterministic_repair",
        )

    monkeypatch.setattr(convergence_runtime_module, "advance_project_completion", record_completion_notification)

    runtime = configure_project_completion_convergence_runtime(workspace)
    await runtime.start()
    try:
        assert relay_calls == [workspace]
        assert len(runtime.wake_tasks) == 3
        assert all(not task.done() for task in runtime.wake_tasks)
        await notify_factory_project_completion(
            FactoryProjectCompletionIdentityV1(
                workspace=workspace,
                project_id="project-1",
                run_id="run-1",
                completion_contract_hash=_hash("contract"),
            )
        )
        assert len(completion_notifications) == 1
        assert completion_notifications[0].identity == ProjectCompletionIdentityV1(
            workspace=workspace,
            project_id="project-1",
            run_id="run-1",
            completion_contract_hash=_hash("contract"),
        )
    finally:
        await runtime.stop()

    assert runtime.wake_tasks == ()
    assert Path(
        resolve_runtime_path(
            workspace,
            "runtime/state/project_completion/convergence.sqlite3",
        )
    ).is_file()
