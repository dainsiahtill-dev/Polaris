from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import os
from pathlib import Path
from typing import Any

import polaris.bootstrap.project_completion_convergence_runtime as convergence_runtime_module
import pytest
from polaris.bootstrap.project_completion_convergence_runtime import (
    configure_project_completion_convergence_runtime,
)
from polaris.bootstrap.project_completion_task_market_action_owner import (
    TaskMarketProjectCompletionActionOwnerV1,
)
from polaris.cells.factory.pipeline.public.project_completion_notification import (
    FactoryProjectCompletionIdentityV1,
    notify_factory_project_completion,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionActionCommandV1,
    ProjectCompletionDispatchClaimV1,
    ProjectCompletionIdentityV1,
)
from polaris.cells.runtime.task_market.internal.store import get_store
from polaris.cells.runtime.task_market.public import (
    TASK_REQUEUE_RECEIPTS_METADATA_KEY,
    PublishTaskWorkItemCommandV1,
    RequeueTaskCommandV1,
    TaskMarketService,
)
from polaris.kernelone.storage import resolve_runtime_path


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _action(workspace: str) -> ProjectCompletionActionCommandV1:
    identity = ProjectCompletionIdentityV1(
        workspace=workspace,
        project_id="project-1",
        run_id="run-1",
        completion_contract_hash=_hash("contract"),
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
    )


def _claim(command: ProjectCompletionActionCommandV1) -> ProjectCompletionDispatchClaimV1:
    return ProjectCompletionDispatchClaimV1(
        identity=command.identity,
        action_id=command.action_id,
        claim_id=_hash("claim"),
        attempt_ordinal=1,
        lease_expires_at="2026-08-09T12:00:00+00:00",
    )


def _fork_dispatch(workspace: str, barrier: Any, queue: Any) -> None:
    async def run() -> tuple[str, str, str, str]:
        command = _action(workspace)
        claim = _claim(command)
        owner = TaskMarketProjectCompletionActionOwnerV1(TaskMarketService())
        barrier.wait()
        receipt = await owner.dispatch_project_completion_action(command, claim)
        return receipt.lease_id, receipt.settlement_id, receipt.effect_hash, receipt.receipt_hash

    try:
        queue.put(("ok", asyncio.run(run())))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("error", f"{type(exc).__name__}:{exc}"))


def test_production_task_market_action_owner_is_fork_safe_and_exactly_once(tmp_path: Path) -> None:
    os.environ["KERNELONE_TASK_MARKET_STORE"] = "sqlite"
    workspace = str((tmp_path / "workspace").resolve())
    Path(workspace).mkdir(parents=True)
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-1",
            run_id="run-1",
            task_id="task-owner",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": "owner task"},
        )
    )

    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(3)
    queue = context.Queue()
    processes = [
        context.Process(target=_fork_dispatch, args=(workspace, barrier, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    barrier.wait()
    rows = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    assert [kind for kind, _ in rows] == ["ok", "ok"]
    receipts = [payload for _, payload in rows]
    assert len(set(receipts)) == 1
    assert receipts[0][0] == _hash("claim")
    assert all(receipts[0][index] for index in (1, 2, 3))

    store = get_store(workspace)
    transitions = store.load_transitions("task-owner")
    assert [row["event_type"] for row in transitions] == ["published", "requeued"]
    item = store.load_items()["task-owner"]
    receipts_payload = item.metadata[TASK_REQUEUE_RECEIPTS_METADATA_KEY]
    assert tuple(receipts_payload) == (_hash("action"),)


@pytest.mark.asyncio
async def test_action_owner_rejects_receipt_for_wrong_effect_under_same_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_TASK_MARKET_STORE", "sqlite")
    workspace = str((tmp_path / "forged-effect-workspace").resolve())
    Path(workspace).mkdir(parents=True)
    service = TaskMarketService()
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-forged",
            run_id="run-1",
            task_id="task-owner",
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": "owner task"},
        )
    )
    command = _action(workspace)
    claim = _claim(command)
    expected = TaskMarketProjectCompletionActionOwnerV1._requeue_command(
        command,
        claim_id=claim.claim_id,
    )
    forged = RequeueTaskCommandV1(
        workspace=workspace,
        task_id=command.owner_task_id,
        target_stage="pending_exec",
        reason="unrelated forged effect",
        metadata={"source": "unrelated.owner"},
        reopen_policy={
            "allowed_source_prefixes": ["unrelated.owner."],
            "max_reopen_count": 8,
            "requires_failure_report": False,
        },
        idempotency_key=command.action_id,
        idempotency_fingerprint=claim.claim_id,
    )
    assert forged.effect_hash != expected.effect_hash
    assert service.requeue_task(forged).ok is True

    owner = TaskMarketProjectCompletionActionOwnerV1(service)
    with pytest.raises(RuntimeError, match="receipt_effect_mismatch"):
        await owner.query_project_completion_action_receipt(command)


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

    async def record_completion_notification(command: AdvanceProjectCompletionCommandV1) -> None:
        completion_notifications.append(command)

    monkeypatch.setattr(
        convergence_runtime_module,
        "notify_project_completion",
        record_completion_notification,
    )

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
