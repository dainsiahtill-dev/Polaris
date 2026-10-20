"""Actual producer admission; private tool IO only, no provider qualification."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.public.turn_contracts import BatchId, ToolBatch, ToolCallId, ToolInvocation, TurnId


def _read(path: str = "declared.txt", call_id: str = "read-1") -> ToolInvocation:
    return ToolInvocation(call_id=ToolCallId(call_id), tool_name="read_file", arguments={"file": path})


@pytest.mark.asyncio
async def test_actual_runtime_rejects_partition_argument_contradiction_before_any_io() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def private_io(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, dict(arguments)))
        return {"content": "private fixture"}

    batch = ToolBatch(
        batch_id=BatchId("contradictory"),
        invocations=[_read()],
        parallel_readonly=[_read("different.txt")],
    )
    with pytest.raises(RuntimeError, match="tool_batch_inventory_member_mismatch"):
        await ToolBatchRuntime(executor=private_io).execute_batch(batch, TurnId("turn"))
    assert calls == []


@pytest.mark.asyncio
async def test_actual_runtime_rejects_inventory_only_instead_of_silent_zero_dispatch() -> None:
    calls: list[str] = []

    async def private_io(name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(name)
        return {"content": "private fixture"}

    with pytest.raises(RuntimeError, match="tool_batch_inventory_execution_groups_missing"):
        await ToolBatchRuntime(executor=private_io).execute_batch(
            ToolBatch(batch_id=BatchId("capture-only"), invocations=[_read()])
        )
    assert calls == []


@pytest.mark.asyncio
async def test_runtime_executes_owned_checked_view_after_original_mutates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    entered, resume = asyncio.Event(), asyncio.Event()

    async def private_io(_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments["file"])
        return {"content": "private fixture"}

    runtime = ToolBatchRuntime(executor=private_io)
    execute = runtime._execute_parallel

    async def delayed(tools: list[ToolInvocation], *args: Any, **kwargs: Any):
        entered.set()
        await resume.wait()
        return await execute(tools, *args, **kwargs)

    monkeypatch.setattr(runtime, "_execute_parallel", delayed)
    invocation = _read()
    batch = ToolBatch(batch_id=BatchId("owned"), invocations=[invocation], parallel_readonly=[invocation])
    running = asyncio.create_task(runtime.execute_batch(batch))
    await entered.wait()
    invocation.arguments["file"] = "mutated.txt"
    resume.set()
    receipts = await running
    assert calls == ["declared.txt"]
    assert receipts[0].success_count == 1


@pytest.mark.asyncio
async def test_actual_runtime_preserves_group_only_execution() -> None:
    calls: list[str] = []

    async def private_io(_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append(arguments["file"])
        return {"content": "private fixture"}

    receipts = await ToolBatchRuntime(executor=private_io).execute_batch(
        ToolBatch(batch_id=BatchId("group-only"), parallel_readonly=[_read()])
    )
    assert calls == ["declared.txt"]
    assert len(receipts) == 1 and receipts[0].success_count == 1


def _executor_fixture(tmp_path: Path):
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor
    from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
    from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _authority, _setup_attempt
    from polaris.cells.roles.kernel.tests.test_tool_batch_runtime import (
        _NoEffectFenceAdmin,
        _NoEffectMutationPort,
        _NoEffectPolicyPort,
    )

    async def private_io(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("private tool IO must not precede admission")

    attempt = _setup_attempt(str(tmp_path))
    executor = ToolBatchExecutor(
        tool_runtime=private_io,
        config=TransactionConfig(mutation_guard_mode="strict"),
        emit_event=lambda _event: None,
        guard_assert_single_tool_batch=lambda **_kwargs: None,
        finalization_handler=None,
        handoff_handler=None,
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=_NoEffectPolicyPort(),
            fence_admin_port=_NoEffectFenceAdmin(),
            mutation_port=_NoEffectMutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=_authority(attempt),
    )
    return executor, attempt


def _effect_owner(tmp_path: Path, attempt: Any):
    from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
        QueryExecutionEffectClosureV1,
        query_execution_effect_closure,
    )

    return query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))


@pytest.mark.asyncio
async def test_main_producer_rejects_before_await_deo_preparation_or_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.tests.test_tool_batch_executor_decision_tree import _build_decoded_state_machine

    executor, attempt = _executor_fixture(tmp_path)
    owner_before = _effect_owner(tmp_path, attempt)
    resource_steps: list[str] = []

    async def forbidden_reset(_turn: str) -> None:
        resource_steps.append("first_await")
        raise AssertionError("contradiction reached runtime reset")

    async def forbidden_prepare(**_kwargs: Any):
        resource_steps.append("deo_prepare")
        raise AssertionError("contradiction reached durable preparation")

    monkeypatch.setattr(executor, "_reset_tool_runtime_turn_boundary", forbidden_reset)
    monkeypatch.setattr(executor, "_prepare_directed_effect_dispatch", forbidden_prepare)
    ledger = TurnLedger(turn_id="main-admission")
    batch = ToolBatch(batch_id=BatchId("main"), invocations=[_read()], parallel_readonly=[_read("changed.txt")])
    with pytest.raises(RuntimeError, match="tool_batch_inventory_member_mismatch"):
        await executor.execute_tool_batch(
            {"turn_id": "main-admission", "tool_batch": batch, "metadata": {"workspace": str(tmp_path)}},
            _build_decoded_state_machine("main-admission"),
            ledger,
            [{"role": "user", "content": "Read the declared file."}],
        )
    assert resource_steps == []
    assert ledger.invocation_batches == []
    assert _effect_owner(tmp_path, attempt) == owner_before
    assert owner_before.receipts == owner_before.terminal_failures == ()


def _followup(*, contradiction: bool = False):
    from polaris.cells.roles.kernel.internal.transaction.deferred_repair_followup import DeferredRepairFollowupV1

    forward = ToolInvocation(
        call_id=ToolCallId("forward"), tool_name="write_file", arguments={"file": "a.txt", "content": "after"}
    )
    rollback = ToolInvocation(
        call_id=ToolCallId("rollback"), tool_name="write_file", arguments={"file": "a.txt", "content": "before"}
    )
    selected = forward.model_copy(deep=True)
    if contradiction:
        selected.arguments["file"] = "changed.txt"
    return DeferredRepairFollowupV1(
        batch_id="followup",
        inventory_invocations=(forward, rollback),
        dispatch_batch=ToolBatch(batch_id=BatchId("followup"), invocations=[selected], serial_writes=[selected]),
        forward_call_ids=("forward",),
        rollback_call_ids=("rollback",),
        rollback_activation_by_call_id=(("rollback", "forward"),),
        effect_bindings_by_call_id=(),
        request_ids=("private-request",),
    )


@pytest.mark.asyncio
async def test_deferred_producer_binds_forward_payload_before_any_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import _executor_core as core

    executor, attempt = _executor_fixture(tmp_path)
    owner_before = _effect_owner(tmp_path, attempt)
    followup = _followup(contradiction=True)
    monkeypatch.setattr(core, "build_deferred_repair_followup", lambda *_args, **_kwargs: followup)
    prepared: list[object] = []

    async def forbidden_prepare(**kwargs: Any):
        prepared.append(kwargs)
        raise AssertionError("contradiction reached durable inventory preparation")

    monkeypatch.setattr(executor, "_prepare_directed_effect_dispatch", forbidden_prepare)
    ledger = TurnLedger(turn_id="deferred-admission")
    with pytest.raises(RuntimeError, match="tool_batch_inventory_member_mismatch"):
        await executor._execute_deferred_repair_followup(
            receipts_as_dicts=[],
            primary_batch_id="primary",
            workspace=str(tmp_path),
            turn_id="deferred-admission",
            ledger=ledger,
            cancel_token=CancelToken(),
        )
    assert prepared == []
    assert ledger.invocation_batches == []
    assert _effect_owner(tmp_path, attempt) == owner_before
    assert owner_before.receipts == owner_before.terminal_failures == ()


@pytest.mark.asyncio
async def test_deferred_owned_forward_and_inactive_rollback_survive_original_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
    from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
    from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import _executor_core as core

    executor, _attempt = _executor_fixture(tmp_path)
    followup = _followup()
    monkeypatch.setattr(core, "build_deferred_repair_followup", lambda *_args, **_kwargs: followup)
    entered, resume = asyncio.Event(), asyncio.Event()
    prepared_paths: list[str] = []

    class PreparationObservedError(Exception):
        pass

    async def observe_before_effect(**kwargs: Any):
        entered.set()
        await resume.wait()
        prepared_paths.extend(item.arguments["file"] for item in kwargs["invocations"])
        raise PreparationObservedError

    monkeypatch.setattr(executor, "_prepare_directed_effect_dispatch", observe_before_effect)
    running = asyncio.create_task(
        executor._execute_deferred_repair_followup(
            receipts_as_dicts=[],
            primary_batch_id="primary",
            workspace=str(tmp_path),
            turn_id="deferred-owned",
            ledger=TurnLedger(turn_id="deferred-owned"),
            cancel_token=CancelToken(),
        )
    )
    await entered.wait()
    followup.inventory_invocations[0].arguments["file"] = "mutated.txt"
    followup.inventory_invocations[1].arguments["file"] = "mutated.txt"
    resume.set()
    with pytest.raises(PreparationObservedError):
        await running
    assert prepared_paths == ["a.txt", "a.txt"]
