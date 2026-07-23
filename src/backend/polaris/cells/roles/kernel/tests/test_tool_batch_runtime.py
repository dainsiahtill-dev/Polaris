"""
Tests for Tool Batch Runtime

验证：
1. 并行执行只读工具
2. 串行执行写工具
3. 异步工具返回pending
4. 超时处理
5. 错误处理
6. 工具分类
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MethodType
from typing import Any
from unittest.mock import AsyncMock

import polaris.cells.roles.kernel.internal.tool_batch_runtime as tool_batch_runtime_module
import pytest
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
    ToolExecutionMode,
)
from polaris.cells.roles.kernel.public import (
    DirectedEffectContextClaimResultV1,
    DirectedEffectExecutionContextV1,
    DirectedEffectFenceRegistrationResultV1,
    DirectedEffectFenceReleaseResultV1,
    DirectedEffectMutationPortResultV1,
    DirectedEffectRuntimeDependenciesV1,
    DirectedEffectToolResultV1,
)
from polaris.cells.roles.kernel.public.directed_effect_service import (
    create_directed_effect_fence_ports,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolInvocation,
    TurnId,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_contracts import (
    _attempt,
    _claim_grant,
    _current_policy_evidence,
    _inventory,
    _member,
    _PolicyPort,
    _prepared_batch,
    _prepared_member,
)
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import (
    _authority,
    _candidate,
    _job_restriction_evidence,
    _RecordingPolicyPort,
    _setup_attempt,
)
from polaris.cells.runtime.task_runtime.public import (
    GetDirectedEffectOperationQueryV1,
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    get_directed_effect_operation,
    heartbeat_task_runtime_execution_attempt,
)

# ============ Fixtures ============


class _NoEffectPolicyPort:
    async def capture_baseline_snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def capture_current_policy_evidence(self, request: Any) -> Any:
        raise AssertionError(request)

    def bind_member(self, request: Any) -> Any:
        raise AssertionError(request)

    async def revalidate(self, request: Any) -> Any:
        raise AssertionError(request)


class _NoEffectFenceAdmin:
    def register(self, context: Any) -> Any:
        raise AssertionError(context)

    def release_batch(self, batch_id: str, execution_attempt: Any) -> Any:
        raise AssertionError((batch_id, execution_attempt))


class _NoEffectMutationPort:
    async def execute_mutation(
        self,
        context: Any,
        normalized_tool_name: str,
        normalized_arguments: Any,
        repair_effect_binding: Any = None,
    ) -> Any:
        raise AssertionError((context, normalized_tool_name, normalized_arguments, repair_effect_binding))


def _required_directed_effect_runtime(
    executor: Any,
) -> ToolBatchRuntime:
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=9,
        external_task_id="DEO-2B-TASK-9",
        session_id="session-task-9",
        attempt=1,
        role_id="director",
        worker_id="worker-task-9",
        run_id="run-task-9",
        lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )
    dependencies = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=_NoEffectPolicyPort(),
        fence_admin_port=_NoEffectFenceAdmin(),
        mutation_port=_NoEffectMutationPort(),
    )
    return ToolBatchRuntime(
        executor=executor,
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(attempt),
    )


@pytest.fixture
def mock_executor():
    """Mock tool executor"""
    executor = AsyncMock()
    return executor


@pytest.fixture
def runtime(mock_executor):
    """Create runtime with mock executor"""
    context = ToolExecutionContext(workspace="/test", timeout_ms=5000)
    return ToolBatchRuntime(executor=mock_executor, context=context)


@pytest.fixture
def sample_batch():
    """Sample tool batch"""
    read1 = ToolInvocation(
        call_id=ToolCallId("call_1"),
        tool_name="read_file",
        arguments={"path": "a.txt"},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )
    read2 = ToolInvocation(
        call_id=ToolCallId("call_2"),
        tool_name="read_file",
        arguments={"path": "b.txt"},
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
    )
    write1 = ToolInvocation(
        call_id=ToolCallId("call_3"),
        tool_name="write_file",
        arguments={"path": "out.txt", "content": "data"},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )
    return ToolBatch(
        batch_id=BatchId("test_batch"),
        invocations=[read1, read2, write1],
        parallel_readonly=[read1, read2],
        serial_writes=[write1],
        async_receipts=[],
    )


@pytest.mark.asyncio
async def test_read_only_skips_deo_preflight_and_canonical_mutation_has_no_raw_fallback() -> None:
    """Required DEO leaves READ raw but rejects every unprepared mutation before raw dispatch."""

    raw_calls: list[tuple[str, dict[str, Any]]] = []

    async def raw_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raw_calls.append((tool_name, arguments))
        return {"success": True, "result": {"path": arguments.get("path", "")}}

    runtime = _required_directed_effect_runtime(raw_executor)
    read = ToolInvocation(
        call_id=ToolCallId("read-1"),
        tool_name="read_file",
        arguments={"path": "src/a.py"},
    )
    read_batch = ToolBatch(
        batch_id=BatchId("read-only-batch"),
        invocations=[read],
        parallel_readonly=[read],
        serial_writes=[],
        async_receipts=[],
    )

    receipts = await runtime.execute_batch(read_batch, TurnId("turn-read"))

    assert receipts[0]["success_count"] == 1
    assert raw_calls == [("read_file", {"path": "src/a.py"})]

    mutation_cases = (
        ("write_file", "write-call", False),
        ("deploy", "async-call", True),
        ("unknown_custom_tool", "unknown-call", False),
    )
    for tool_name, call_id, is_async in mutation_cases:
        mutation = ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name=tool_name,
            arguments={"path": "src/a.py"},
        )
        mutation_batch = ToolBatch(
            batch_id=BatchId(f"{call_id}-batch"),
            invocations=[mutation],
            parallel_readonly=[],
            serial_writes=[] if is_async else [mutation],
            async_receipts=[mutation] if is_async else [],
        )

        with pytest.raises(RuntimeError, match="directed_effect_batch_not_prepared"):
            await runtime.execute_batch(mutation_batch, TurnId(f"turn-{call_id}"))

    assert raw_calls == [("read_file", {"path": "src/a.py"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied_call_id",
    (None, "call-0", "call-1"),
    ids=("all-success", "first-claim-denied", "second-claim-denied"),
)
async def test_prepared_mutations_use_only_jit_claim_and_mutation_port(
    monkeypatch: pytest.MonkeyPatch,
    denied_call_id: str | None,
) -> None:
    """A prepared mixed batch keeps READ raw and routes each mutation through DEO exactly once."""

    prepared = _prepared_batch()
    contexts: dict[str, DirectedEffectExecutionContextV1] = {}
    for prepared_member in prepared.prepared_members:
        member = prepared_member.member
        bound_snapshot = prepared_member.policy_binding.bound_snapshot
        assert bound_snapshot is not None
        grant = _claim_grant(
            prepared.execution_attempt,
            prepared.parent_binding,
            member,
        )
        contexts[member.tool_call_id] = DirectedEffectExecutionContextV1(
            context_id=f"context-{member.tool_call_id}",
            batch_id=prepared.parent_binding.correlation.batch_id,
            creator_pid=os.getpid(),
            tool_call_id=member.tool_call_id,
            normalized_tool_name=member.normalized_tool_name,
            arguments_hash=bound_snapshot.authorization_binding.authorization_evidence.arguments_hash,
            authorization_evidence=bound_snapshot.authorization_binding.authorization_evidence,
            claim_grant=grant,
            bound_snapshot=bound_snapshot,
            current_policy_evidence=_current_policy_evidence(prepared, member, grant),
            current_job_token_restriction_evidence=(),
        )

    claimed: list[str] = []

    class _Lifecycle:
        def __init__(self, *, policy_snapshot_port: object) -> None:
            assert policy_snapshot_port is policy_port

        async def claim_execution_context(self, **kwargs: object) -> DirectedEffectContextClaimResultV1:
            call_id = str(kwargs["tool_call_id"])
            claimed.append(call_id)
            if call_id == denied_call_id:
                return DirectedEffectContextClaimResultV1(
                    status="denied",
                    context=None,
                    error_code="deo_claim_failed",
                    operation_claim_status="not_claimed",
                )
            return DirectedEffectContextClaimResultV1(
                status="claimed",
                context=contexts[call_id],
                error_code=None,
                operation_claim_status="claimed",
            )

    monkeypatch.setattr(
        tool_batch_runtime_module,
        "DirectedEffectLifecycleService",
        _Lifecycle,
    )

    registered: list[str] = []
    released: list[tuple[str, TaskRuntimeExecutionAttemptIdentityV1]] = []

    class _FenceAdmin:
        def register(
            self,
            context: DirectedEffectExecutionContextV1,
        ) -> DirectedEffectFenceRegistrationResultV1:
            registered.append(context.tool_call_id)
            return DirectedEffectFenceRegistrationResultV1(
                ok=True,
                status="registered",
                context_id=context.context_id,
                error_code=None,
            )

        def release_batch(
            self,
            batch_id: str,
            execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        ) -> DirectedEffectFenceReleaseResultV1:
            released.append((batch_id, execution_attempt))
            return DirectedEffectFenceReleaseResultV1(
                ok=True,
                status="released",
                batch_id=batch_id,
                released_count=len(contexts),
                error_code=None,
            )

    mutated: list[str] = []

    class _MutationPort:
        async def execute_mutation(
            self,
            context: DirectedEffectExecutionContextV1,
            normalized_tool_name: str,
            normalized_arguments: DirectedEffectImmutableItemsV1,
            repair_effect_binding: object | None = None,
        ) -> DirectedEffectMutationPortResultV1:
            assert normalized_tool_name == context.normalized_tool_name
            assert normalized_arguments
            mutated.append(context.tool_call_id)
            return DirectedEffectMutationPortResultV1(
                ok=True,
                status="executed",
                tool_result=DirectedEffectToolResultV1(
                    payload=(
                        (
                            "effect_receipt",
                            DirectedEffectImmutableMapV1(items=(("receipt_id", context.tool_call_id),)),
                        ),
                        ("result", DirectedEffectImmutableMapV1(items=(("changed", True),))),
                    )
                ),
                error_code=None,
            )

    policy_port = _NoEffectPolicyPort()
    dependencies = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=policy_port,
        fence_admin_port=_FenceAdmin(),
        mutation_port=_MutationPort(),
    )
    raw_calls: list[str] = []

    async def raw_executor(tool_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        raw_calls.append(tool_name)
        return {"success": True, "result": {"content": "read"}}

    read = ToolInvocation(
        call_id=ToolCallId("read-1"),
        tool_name="read_file",
        arguments={"path": "src/a.py"},
    )
    writes = [
        ToolInvocation(
            call_id=ToolCallId(member.member.tool_call_id),
            tool_name=member.member.normalized_tool_name,
            arguments={"path": "src/a.py"},
        )
        for member in prepared.prepared_members
    ]
    runtime = ToolBatchRuntime(
        executor=raw_executor,
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(prepared.execution_attempt),
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (member.member.tool_call_id, ()) for member in prepared.prepared_members
        ),
    )
    batch = ToolBatch(
        batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
        invocations=[read, *writes],
        parallel_readonly=[read],
        serial_writes=writes,
        async_receipts=[],
    )

    receipts = await runtime.execute_batch(batch, TurnId("turn-1"))

    expected_calls = [member.member.tool_call_id for member in prepared.prepared_members]
    if denied_call_id == "call-0":
        expected_claimed = ["call-0"]
        expected_effects: list[str] = []
    elif denied_call_id == "call-1":
        expected_claimed = expected_calls
        expected_effects = ["call-0"]
    else:
        expected_claimed = expected_calls
        expected_effects = expected_calls
    assert raw_calls == ["read_file"]
    assert claimed == expected_claimed
    assert registered == mutated == expected_effects
    assert len(receipts) == 1 + len(expected_claimed)
    assert sum(receipt["failure_count"] for receipt in receipts) == (0 if denied_call_id is None else 1)
    if denied_call_id is None:
        assert released == [
            (
                prepared.parent_binding.correlation.batch_id,
                prepared.execution_attempt,
            )
        ]
    else:
        # A denied claim leaves durable inventory work unresolved.  Keep the
        # fence for DEO-3 reconciliation instead of erasing local evidence.
        assert released == []


@pytest.mark.asyncio
async def test_deferred_repair_dispatch_executes_forward_and_aborts_unused_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_batch()
    forward_member, rollback_member = prepared.prepared_members
    member = forward_member.member
    bound_snapshot = forward_member.policy_binding.bound_snapshot
    assert bound_snapshot is not None
    grant = _claim_grant(prepared.execution_attempt, prepared.parent_binding, member)
    context = DirectedEffectExecutionContextV1(
        context_id="context-deferred-forward",
        batch_id=prepared.parent_binding.correlation.batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=bound_snapshot.authorization_binding.authorization_evidence.arguments_hash,
        authorization_evidence=bound_snapshot.authorization_binding.authorization_evidence,
        claim_grant=grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=_current_policy_evidence(prepared, member, grant),
        current_job_token_restriction_evidence=(),
    )
    claimed: list[str] = []
    aborted: list[tuple[tuple[str, ...], str]] = []

    class _Lifecycle:
        def __init__(self, *, policy_snapshot_port: object) -> None:
            assert policy_snapshot_port is policy_port

        async def claim_execution_context(self, **kwargs: object) -> DirectedEffectContextClaimResultV1:
            claimed.append(str(kwargs["tool_call_id"]))
            return DirectedEffectContextClaimResultV1(
                status="claimed",
                context=context,
                error_code=None,
                operation_claim_status="claimed",
            )

        def abort_unclaimed_members(self, **kwargs: object) -> tuple[object, ...]:
            aborted.append((kwargs["tool_call_ids"], str(kwargs["reason"])))  # type: ignore[arg-type]
            return (object(),)

    monkeypatch.setattr(tool_batch_runtime_module, "DirectedEffectLifecycleService", _Lifecycle)

    class _FenceAdmin:
        def register(self, _context: DirectedEffectExecutionContextV1) -> DirectedEffectFenceRegistrationResultV1:
            return DirectedEffectFenceRegistrationResultV1(
                ok=True,
                status="registered",
                context_id=context.context_id,
                error_code=None,
            )

        def release_batch(
            self,
            batch_id: str,
            _execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        ) -> DirectedEffectFenceReleaseResultV1:
            return DirectedEffectFenceReleaseResultV1(
                ok=True,
                status="released",
                batch_id=batch_id,
                released_count=1,
                error_code=None,
            )

    class _MutationPort:
        async def execute_mutation(
            self,
            effect_context: DirectedEffectExecutionContextV1,
            _normalized_tool_name: str,
            _normalized_arguments: DirectedEffectImmutableItemsV1,
            repair_effect_binding: object | None = None,
        ) -> DirectedEffectMutationPortResultV1:
            return DirectedEffectMutationPortResultV1(
                ok=True,
                status="executed",
                tool_result=DirectedEffectToolResultV1(
                    payload=(
                        ("effect_receipt", DirectedEffectImmutableMapV1(items=(("receipt_id", "forward"),))),
                        ("result", DirectedEffectImmutableMapV1(items=(("changed", True),))),
                    )
                ),
                error_code=None,
            )

    policy_port = _NoEffectPolicyPort()
    runtime = ToolBatchRuntime(
        executor=AsyncMock(),
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=_FenceAdmin(),
            mutation_port=_MutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(prepared.execution_attempt),
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (item.member.tool_call_id, ()) for item in prepared.prepared_members
        ),
        directed_effect_dispatch_call_ids=(forward_member.member.tool_call_id,),
        directed_effect_abort_call_ids=(rollback_member.member.tool_call_id,),
    )
    forward = ToolInvocation(
        call_id=ToolCallId(forward_member.member.tool_call_id),
        tool_name=forward_member.member.normalized_tool_name,
        arguments={"path": "src/a.py"},
    )
    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=[forward],
            serial_writes=[forward],
        ),
        TurnId("turn-deferred-repair"),
    )

    assert claimed == [forward_member.member.tool_call_id]
    assert aborted == [((rollback_member.member.tool_call_id,), "contingency_not_activated")]
    assert len(receipts) == 1
    assert receipts[0]["success_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_success", (False, True))
async def test_deferred_repair_failure_preserves_only_activated_rollbacks(
    monkeypatch: pytest.MonkeyPatch,
    partial_success: bool,
) -> None:
    attempt = _attempt(run_id=f"rollback-failure-{partial_success}")
    members = tuple(_member(index) for index in range(4))
    inventory = _inventory(attempt, members)
    prepared_members = tuple(
        _prepared_member(member, stream_head=index + 2, execution_attempt=attempt)
        for index, member in enumerate(members)
    )
    prepared = _prepared_batch(
        execution_attempt=attempt,
        inventory=inventory,
        prepared_members=prepared_members,
        call_id_index=tuple((member.tool_call_id, index) for index, member in enumerate(members)),
    )
    forward_ids = tuple(member.tool_call_id for member in members[:2])
    rollback_ids = tuple(member.tool_call_id for member in members[2:])
    aborted: list[tuple[tuple[str, ...], str]] = []

    class _Lifecycle:
        def __init__(self, *, policy_snapshot_port: object) -> None:
            assert policy_snapshot_port is policy_port

        def abort_unclaimed_members(self, **kwargs: object) -> tuple[object, ...]:
            aborted.append((kwargs["tool_call_ids"], str(kwargs["reason"])))  # type: ignore[arg-type]
            return (object(),)

    monkeypatch.setattr(tool_batch_runtime_module, "DirectedEffectLifecycleService", _Lifecycle)

    class _FenceAdmin:
        def register(self, context: object) -> object:
            raise AssertionError(context)

        def release_batch(
            self,
            batch_id: str,
            _execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        ) -> DirectedEffectFenceReleaseResultV1:
            return DirectedEffectFenceReleaseResultV1(
                ok=True,
                status="released",
                batch_id=batch_id,
                released_count=1,
                error_code=None,
            )

    policy_port = _NoEffectPolicyPort()
    runtime = ToolBatchRuntime(
        executor=AsyncMock(),
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=_FenceAdmin(),
            mutation_port=_NoEffectMutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(attempt),
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple((member.tool_call_id, ()) for member in members),
        directed_effect_dispatch_call_ids=forward_ids,
        directed_effect_abort_call_ids=rollback_ids,
    )
    runtime.directed_effect_rollback_activation_by_call_id = (
        (rollback_ids[0], forward_ids[0]),
        (rollback_ids[1], forward_ids[1]),
    )

    def _rollback_invocation(self: ToolBatchRuntime, call_id: str) -> ToolInvocation:
        del self
        return ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name="write_file",
            arguments={"path": "src/a.py", "content": "rollback"},
        )

    runtime._deferred_repair_invocation = MethodType(_rollback_invocation, runtime)
    calls: list[str] = []

    async def _execute(self: ToolBatchRuntime, tool: ToolInvocation):
        call_id = str(tool.call_id)
        calls.append(call_id)
        if partial_success and call_id in {forward_ids[0], rollback_ids[0]}:
            return tool_batch_runtime_module.ToolResult(
                call_id=call_id,
                tool_name=tool.tool_name,
                status=tool_batch_runtime_module.ToolExecutionStatus.SUCCESS,
                effect_receipt={"receipt_id": "success"},
                directed_effect_mutation_status="executed",
                directed_effect_claim_status="claimed",
            )
        return tool_batch_runtime_module.ToolResult(
            call_id=call_id,
            tool_name=tool.tool_name,
            status=tool_batch_runtime_module.ToolExecutionStatus.ERROR,
            error="denied",
            directed_effect_mutation_status="denied",
            directed_effect_claim_status="claimed",
        )

    runtime._execute_directed_effect = MethodType(_execute, runtime)
    forwards = [
        ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name="write_file",
            arguments={"path": f"src/{index}.py"},
        )
        for index, call_id in enumerate(forward_ids)
    ]

    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=forwards,
            serial_writes=forwards,
        ),
        TurnId("turn-deferred-failure"),
    )

    if partial_success:
        assert calls == [*forward_ids, rollback_ids[0]]
        assert aborted == [((rollback_ids[1],), "deferred_repair_forward_failed")]
        assert receipts[1].raw_results[0]["directed_effect_activated_rollback_call_ids"] == [rollback_ids[0]]
        assert receipts[1].raw_results[0]["directed_effect_executed_rollback_call_ids"] == [rollback_ids[0]]
        assert receipts[1].raw_results[0]["directed_effect_aborted_call_ids"] == [rollback_ids[1]]
        assert receipts[1].raw_results[0]["directed_effect_preserved_call_ids"] == [forward_ids[1]]
    else:
        assert calls == [forward_ids[0]]
        assert aborted == [
            ((forward_ids[1],), "deferred_repair_forward_failed"),
            ((rollback_ids[0],), "deferred_repair_forward_failed"),
            ((rollback_ids[1],), "deferred_repair_forward_failed"),
        ]
        assert receipts[-1].raw_results[0]["directed_effect_activated_rollback_call_ids"] == []
        assert receipts[-1].raw_results[0]["directed_effect_aborted_call_ids"] == [
            forward_ids[1],
            rollback_ids[0],
            rollback_ids[1],
        ]
        assert receipts[-1].raw_results[0]["directed_effect_preserved_call_ids"] == [forward_ids[0]]


@pytest.mark.asyncio
async def test_deferred_failure_terminalizes_real_task_runtime_and_executes_claimed_rollback(
    tmp_path: Path,
) -> None:
    """A failed forward uses real TaskRuntime claims and ordered aborts without sequence gaps."""

    attempt = _setup_attempt(str(tmp_path / "workspace"))
    candidates = tuple(_candidate(attempt, ordinal=index) for index in range(4))

    class _ExecutionPolicyPort(_RecordingPolicyPort):
        async def revalidate(self, request: Any) -> Any:
            return await _PolicyPort().revalidate(request)

    policy_port = _ExecutionPolicyPort(events=[])
    authority = _authority(attempt)
    lifecycle = tool_batch_runtime_module.DirectedEffectLifecycleService(
        policy_snapshot_port=policy_port,
    )
    lifecycle_result = lifecycle.prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )
    assert lifecycle_result.status == "ready"
    prepared = lifecycle_result.prepared_batch
    assert prepared is not None

    forward_ids = tuple(member.member.tool_call_id for member in prepared.prepared_members[:2])
    rollback_ids = tuple(member.member.tool_call_id for member in prepared.prepared_members[2:])
    mutation_calls: list[str] = []

    class _MutationPort:
        async def execute_mutation(
            self,
            context: DirectedEffectExecutionContextV1,
            normalized_tool_name: str,
            normalized_arguments: DirectedEffectImmutableItemsV1,
            repair_effect_binding: object | None = None,
        ) -> DirectedEffectMutationPortResultV1:
            del normalized_tool_name, normalized_arguments, repair_effect_binding
            mutation_calls.append(context.tool_call_id)
            if context.tool_call_id == forward_ids[1]:
                return DirectedEffectMutationPortResultV1(
                    ok=False,
                    status="denied",
                    tool_result=None,
                    error_code="deo_claim_failed",
                )
            return DirectedEffectMutationPortResultV1(
                ok=True,
                status="executed",
                tool_result=DirectedEffectToolResultV1(
                    payload=(
                        (
                            "effect_receipt",
                            DirectedEffectImmutableMapV1(items=(("receipt_id", context.tool_call_id),)),
                        ),
                        ("result", DirectedEffectImmutableMapV1(items=(("changed", True),))),
                    )
                ),
                error_code=None,
            )

    fence_ports = create_directed_effect_fence_ports()
    runtime = ToolBatchRuntime(
        executor=AsyncMock(),
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=_MutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (member.member.tool_call_id, _job_restriction_evidence()) for member in prepared.prepared_members
        ),
        directed_effect_dispatch_call_ids=forward_ids,
        directed_effect_abort_call_ids=rollback_ids,
    )
    runtime.directed_effect_rollback_activation_by_call_id = (
        (rollback_ids[0], forward_ids[0]),
        (rollback_ids[1], forward_ids[1]),
    )

    def _rollback_invocation(self: ToolBatchRuntime, call_id: str) -> ToolInvocation:
        del self
        return ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name="write_file",
            arguments={"content": "after\n", "path": "src/a.py"},
        )

    runtime._deferred_repair_invocation = MethodType(_rollback_invocation, runtime)
    forwards = [
        ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name="write_file",
            arguments={"content": "after\n", "path": "src/a.py"},
        )
        for call_id in forward_ids
    ]

    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=forwards,
            serial_writes=forwards,
        ),
        TurnId("turn-real-deferred-failure"),
    )

    assert mutation_calls == [forward_ids[0], forward_ids[1], rollback_ids[0]]
    assert len(receipts) == 3
    expected_states = {
        forward_ids[0]: "EFFECT_STARTED",
        forward_ids[1]: "EFFECT_STARTED",
        rollback_ids[0]: "EFFECT_STARTED",
        rollback_ids[1]: "ABORTED",
    }
    observed_source_heads: set[int] = set()
    for member in prepared.prepared_members:
        result = get_directed_effect_operation(
            GetDirectedEffectOperationQueryV1(
                workspace=prepared.execution_attempt.workspace,
                task_id=prepared.execution_attempt.task_id,
                execution_attempt=prepared.execution_attempt,
                parent_binding=prepared.parent_binding,
                tool_call_id=member.member.tool_call_id,
                effect_id=member.member.effect_id,
            )
        )
        assert result.ok is True
        assert result.state == expected_states[member.member.tool_call_id]
        assert result.version == 2
        assert result.snapshot is not None
        observed_source_heads.add(result.snapshot.source_head_seq)
    assert observed_source_heads == {8}

    retained = fence_ports.admin.release_batch(
        prepared.parent_binding.correlation.batch_id,
        prepared.execution_attempt,
    )
    assert retained.ok is True
    assert retained.status == "released"
    assert retained.released_count == 3


@pytest.mark.asyncio
async def test_preclaim_denial_aborts_real_task_runtime_inventory_without_sequence_gaps(
    tmp_path: Path,
) -> None:
    """A denial before claim aborts the current member and every later member in order."""

    attempt = _setup_attempt(str(tmp_path / "workspace"))
    candidates = tuple(_candidate(attempt, ordinal=index) for index in range(4))

    class _ExecutionPolicyPort(_RecordingPolicyPort):
        async def revalidate(self, request: Any) -> Any:
            return await _PolicyPort().revalidate(request)

    policy_port = _ExecutionPolicyPort(events=[])
    preparation_authority = _authority(attempt)
    lifecycle_result = tool_batch_runtime_module.DirectedEffectLifecycleService(
        policy_snapshot_port=policy_port,
    ).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=preparation_authority,
        turn_id="turn-1",
        batch_id="batch-1",
        candidates=candidates,
    )
    assert lifecycle_result.status == "ready"
    prepared = lifecycle_result.prepared_batch
    assert prepared is not None

    def _deny_only_preclaim_heartbeat(command: Any) -> Any:
        if str(command.context_summary).startswith("directed_effect_pre_claim:"):
            raise RuntimeError("injected pre-claim heartbeat refusal")
        return heartbeat_task_runtime_execution_attempt(command)

    execution_authority = TaskRuntimeExecutionAttemptAuthorityV1(
        prepared.execution_attempt,
        heartbeat=_deny_only_preclaim_heartbeat,
    )
    inventory_ids = tuple(member.member.tool_call_id for member in prepared.prepared_members)
    forward_ids = inventory_ids[:2]
    rollback_ids = inventory_ids[2:]
    fence_ports = create_directed_effect_fence_ports()
    runtime = ToolBatchRuntime(
        executor=AsyncMock(),
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=_NoEffectMutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=execution_authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (member.member.tool_call_id, _job_restriction_evidence()) for member in prepared.prepared_members
        ),
        directed_effect_dispatch_call_ids=forward_ids,
        directed_effect_abort_call_ids=rollback_ids,
    )
    runtime.directed_effect_rollback_activation_by_call_id = (
        (rollback_ids[0], forward_ids[0]),
        (rollback_ids[1], forward_ids[1]),
    )
    forwards = [
        ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name="write_file",
            arguments={"content": "after\n", "path": "src/a.py"},
        )
        for call_id in forward_ids
    ]

    receipts = await runtime.execute_batch(
        ToolBatch(
            batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=forwards,
            serial_writes=forwards,
        ),
        TurnId("turn-preclaim-denial"),
    )

    assert len(receipts) == 1
    raw_result = receipts[0].raw_results[0]
    assert raw_result["directed_effect_claim_status"] == "not_claimed"
    assert raw_result["directed_effect_aborted_call_ids"] == list(inventory_ids)
    assert raw_result["directed_effect_preserved_call_ids"] == []
    authority_snapshot = execution_authority.snapshot()
    assert authority_snapshot.success is True
    assert authority_snapshot.identity is not None
    current_attempt = authority_snapshot.identity
    observed_source_heads: set[int] = set()
    for member in prepared.prepared_members:
        result = get_directed_effect_operation(
            GetDirectedEffectOperationQueryV1(
                workspace=prepared.execution_attempt.workspace,
                task_id=prepared.execution_attempt.task_id,
                execution_attempt=current_attempt,
                parent_binding=prepared.parent_binding,
                tool_call_id=member.member.tool_call_id,
                effect_id=member.member.effect_id,
            )
        )
        assert result.ok is True
        assert result.state == "ABORTED"
        assert result.version == 2
        assert result.snapshot is not None
        observed_source_heads.add(result.snapshot.source_head_seq)
    assert observed_source_heads == {8}
    assert (
        fence_ports.admin.release_batch(
            prepared.parent_binding.correlation.batch_id,
            current_attempt,
        ).status
        == "absent"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", (RuntimeError, asyncio.CancelledError))
async def test_directed_effect_exception_or_cancellation_never_releases_fence(
    failure_type: type[BaseException],
) -> None:
    """Unknown execution outcome keeps the fence for DEO-3 reconciliation."""

    prepared = _prepared_batch()
    released: list[str] = []

    class _FenceAdmin:
        def register(self, context: object) -> object:
            raise AssertionError(context)

        def release_batch(
            self,
            batch_id: str,
            _execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
        ) -> DirectedEffectFenceReleaseResultV1:
            released.append(batch_id)
            return DirectedEffectFenceReleaseResultV1(
                ok=True,
                status="released",
                batch_id=batch_id,
                released_count=1,
                error_code=None,
            )

    runtime = ToolBatchRuntime(
        executor=AsyncMock(),
        directed_effect_runtime=DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=_NoEffectPolicyPort(),
            fence_admin_port=_FenceAdmin(),
            mutation_port=_NoEffectMutationPort(),
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(prepared.execution_attempt),
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=tuple(
            (member.member.tool_call_id, ()) for member in prepared.prepared_members
        ),
        directed_effect_dispatch_call_ids=tuple(member.member.tool_call_id for member in prepared.prepared_members),
    )

    async def _raise(self: ToolBatchRuntime, tool: ToolInvocation) -> Any:
        del self, tool
        raise failure_type("unknown execution outcome")

    runtime._execute_directed_effect = MethodType(_raise, runtime)
    mutations = [
        ToolInvocation(
            call_id=ToolCallId(member.member.tool_call_id),
            tool_name=member.member.normalized_tool_name,
            arguments={"content": "after\n", "path": "src/a.py"},
        )
        for member in prepared.prepared_members
    ]

    with pytest.raises(failure_type, match="unknown execution outcome"):
        await runtime.execute_batch(
            ToolBatch(
                batch_id=BatchId(prepared.parent_binding.correlation.batch_id),
                invocations=mutations,
                serial_writes=mutations,
            ),
            TurnId("turn-execution-unknown"),
        )

    assert released == []


# ============ Test Parallel Execution ============


class TestParallelExecution:
    """测试并行执行"""

    @pytest.mark.asyncio
    async def test_parallel_readonly_tools_execute_concurrently(self, runtime, mock_executor) -> None:
        """只读工具并行执行"""

        # 模拟慢速工具
        async def slow_executor(tool_name, arguments):
            await asyncio.sleep(0.1)
            return {"success": True, "result": f"content of {arguments.get('path')}"}

        runtime.executor = slow_executor

        p1 = ToolInvocation(
            call_id=ToolCallId("p1"),
            tool_name="read_file",
            arguments={"path": "a.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        p2 = ToolInvocation(
            call_id=ToolCallId("p2"),
            tool_name="read_file",
            arguments={"path": "b.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        p3 = ToolInvocation(
            call_id=ToolCallId("p3"),
            tool_name="read_file",
            arguments={"path": "c.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("parallel_batch"),
            invocations=[p1, p2, p3],
            parallel_readonly=[p1, p2, p3],
            serial_writes=[],
            async_receipts=[],
        )

        import time

        start = time.time()
        receipts = await runtime.execute_batch(batch, TurnId("turn_1"))
        elapsed = time.time() - start

        # 3个100ms工具并行应该只需要~100ms，不是300ms
        assert elapsed < 0.2, f"Parallel execution took {elapsed}s, expected < 0.2s"
        assert len(receipts) == 3

        # 所有只读工具成功
        for receipt in receipts:
            assert receipt["success_count"] == 1
            assert receipt["failure_count"] == 0


# ============ Test Serial Execution ============


class TestSerialExecution:
    """测试串行执行"""

    @pytest.mark.asyncio
    async def test_write_tools_execute_serially(self, runtime, mock_executor) -> None:
        """写工具串行执行"""
        execution_order = []

        async def tracking_executor(tool_name, arguments):
            execution_order.append(tool_name)
            await asyncio.sleep(0.05)
            return {"success": True, "result": "done", "effect_receipt": {"tool": tool_name}}

        runtime.executor = tracking_executor

        w1 = ToolInvocation(
            call_id=ToolCallId("w1"),
            tool_name="write_file",
            arguments={"path": "a.txt", "content": "a"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        w2 = ToolInvocation(
            call_id=ToolCallId("w2"),
            tool_name="write_file",
            arguments={"path": "b.txt", "content": "b"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        w3 = ToolInvocation(
            call_id=ToolCallId("w3"),
            tool_name="write_file",
            arguments={"path": "c.txt", "content": "c"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("serial_batch"),
            invocations=[w1, w2, w3],
            parallel_readonly=[],
            serial_writes=[w1, w2, w3],
            async_receipts=[],
        )

        await runtime.execute_batch(batch, TurnId("turn_2"))

        # 验证顺序执行
        assert execution_order == ["write_file", "write_file", "write_file"]


# ============ Test Unregistered Async-Named Tools ============


class TestUnregisteredAsyncNamedTools:
    """测试未注册异步命名工具的保守串行分类。"""

    @pytest.mark.asyncio
    async def test_unregistered_async_named_tool_executes_as_serial_write(self, runtime, mock_executor) -> None:
        """Registry 外异步命名不得获得 async receipt 模式。"""
        mock_executor.return_value = {
            "success": True,
            "result": "created",
            "effect_receipt": {"tool": "create_pull_request"},
        }
        invocation = ToolInvocation(
            call_id=ToolCallId("async_1"),
            tool_name="create_pull_request",
            arguments={"title": "PR"},
        )
        batch = ToolBatch(
            batch_id=BatchId("async_batch"),
            invocations=[invocation],
            parallel_readonly=[],
            serial_writes=[invocation],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_async"))

        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt["success_count"] == 1
        assert receipt["pending_async_count"] == 0
        assert receipt["has_pending_async"] is False


# ============ Test Error Handling ============


class TestErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_tool_error_returns_error_receipt(self, runtime, mock_executor) -> None:
        """工具错误返回错误receipt"""
        mock_executor.side_effect = Exception("File not found")

        err_inv = ToolInvocation(
            call_id=ToolCallId("err_1"),
            tool_name="read_file",
            arguments={"path": "missing.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("error_batch"),
            invocations=[err_inv],
            parallel_readonly=[err_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_error"))

        assert len(receipts) == 1
        assert receipts[0]["failure_count"] == 1
        assert receipts[0]["success_count"] == 0

    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_status(self, runtime, mock_executor) -> None:
        """超时返回timeout状态"""

        # 模拟超时
        async def slow_tool(tool_name, arguments):
            await asyncio.sleep(10)  # 超过5秒超时
            return {"success": True, "result": "done"}

        runtime.executor = slow_tool
        runtime.context.timeout_ms = 100  # 100ms超时

        slow_inv = ToolInvocation(
            call_id=ToolCallId("slow_1"),
            tool_name="grep",
            arguments={"pattern": "test"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("timeout_batch"),
            invocations=[slow_inv],
            parallel_readonly=[slow_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_timeout"))

        assert receipts[0]["results"][0]["status"] == "timeout"


# ============ Test Tool Classification ============


class TestToolClassification:
    """测试工具分类"""

    def test_readonly_tools_classified_correctly(self) -> None:
        """只读工具正确分类"""
        readonly_tools = ["read_file", "list_directory", "grep", "search_code"]
        for tool in readonly_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.READONLY_PARALLEL, f"{tool} should be READONLY_PARALLEL"

    def test_write_tools_classified_correctly(self) -> None:
        """写工具正确分类"""
        write_tools = ["write_file", "edit_file", "delete_file", "bash"]
        for tool in write_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.WRITE_SERIAL, f"{tool} should be WRITE_SERIAL"

    def test_async_tools_classified_correctly(self) -> None:
        """异步工具正确分类"""
        async_tools = ["create_pull_request", "deploy", "trigger_ci"]
        for tool in async_tools:
            mode = ToolBatchRuntime.classify_tool(tool)
            assert mode == ToolExecutionMode.ASYNC_RECEIPT, f"{tool} should be ASYNC_RECEIPT"

    def test_unknown_tools_default_to_write_serial(self) -> None:
        """未知工具默认WRITE_SERIAL（安全优先）"""
        mode = ToolBatchRuntime.classify_tool("unknown_custom_tool")
        assert mode == ToolExecutionMode.WRITE_SERIAL

    def test_classify_batch_groups_correctly(self) -> None:
        """批次分类正确分组"""
        invocations = [
            ToolInvocation(
                call_id=ToolCallId("c1"),
                tool_name="read_file",
                arguments={},
                effect_type=ToolEffectType.READ,
                execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            ),
            ToolInvocation(
                call_id=ToolCallId("c2"),
                tool_name="write_file",
                arguments={},
                effect_type=ToolEffectType.WRITE,
                execution_mode=ToolExecutionMode.WRITE_SERIAL,
            ),
            ToolInvocation(call_id=ToolCallId("c3"), tool_name="create_pull_request", arguments={}),
            ToolInvocation(
                call_id=ToolCallId("c4"),
                tool_name="grep",
                arguments={},
                effect_type=ToolEffectType.READ,
                execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            ),
        ]

        classified = ToolBatchRuntime.classify_batch(invocations)

        assert len(classified["parallel_readonly"]) == 2  # read_file, grep
        assert len(classified["serial_writes"]) == 2  # write_file, create_pull_request
        assert len(classified["async_receipts"]) == 0


# ============ Test Mixed Batch ============


class TestMixedBatch:
    """测试混合批次"""

    @pytest.mark.asyncio
    async def test_mixed_batch_executes_correctly(self, runtime, mock_executor) -> None:
        """混合批次正确执行"""

        async def mixed_executor(tool_name, arguments):
            if tool_name == "write_file":
                return {"success": True, "result": "done", "effect_receipt": {"bytes_written": 1}}
            return {"success": True, "result": "done"}

        mock_executor.side_effect = mixed_executor

        r1 = ToolInvocation(
            call_id=ToolCallId("r1"),
            tool_name="read_file",
            arguments={"path": "a.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        w1 = ToolInvocation(
            call_id=ToolCallId("w1"),
            tool_name="write_file",
            arguments={"path": "out.txt", "content": "x"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("mixed_batch"),
            invocations=[r1, w1],
            parallel_readonly=[r1],
            serial_writes=[w1],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_mixed"))

        # 2个工具，2个receipts
        assert len(receipts) == 2

        # 只读成功
        assert receipts[0]["success_count"] == 1
        # 写成功
        assert receipts[1]["success_count"] == 1

        # 总调用次数
        assert mock_executor.call_count == 2

    @pytest.mark.asyncio
    async def test_nested_effect_receipt_is_promoted(self, runtime) -> None:
        """嵌套在 result 内的 effect_receipt 也应被识别。"""

        async def nested_receipt_executor(tool_name, arguments):
            return {
                "ok": True,
                "result": {
                    "message": "done",
                    "effect_receipt": {"operation": "modify", "file": arguments.get("path", "")},
                },
            }

        runtime.executor = nested_receipt_executor

        write_inv = ToolInvocation(
            call_id=ToolCallId("w_nested"),
            tool_name="write_file",
            arguments={"path": "nested.txt", "content": "x"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("nested_receipt_batch"),
            invocations=[write_inv],
            parallel_readonly=[],
            serial_writes=[write_inv],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_nested_receipt"))
        assert receipts[0]["success_count"] == 1
        assert receipts[0]["results"][0]["effect_receipt"] == {"operation": "modify", "file": "nested.txt"}

    def test_batch_receipt_preserves_effect_receipt_and_task_runtime_commit(self, runtime) -> None:
        """The canonical batch projection must not separate a v2 receipt from its commit."""

        effect_receipt = {
            "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
            "receipt_hash": "a" * 64,
        }
        effect_receipt_commit = {
            "code": "receipt_committed",
            "state": "RECEIPT_COMMITTED",
            "receipt_hash": "a" * 64,
        }
        tool_result = tool_batch_runtime_module.ToolResult(
            call_id="call-write-committed",
            tool_name="write_file",
            status=tool_batch_runtime_module.ToolExecutionStatus.SUCCESS,
            result={
                "effect_receipt": effect_receipt,
                "effect_receipt_commit": effect_receipt_commit,
                "ok": True,
            },
            effect_receipt=effect_receipt,
        )

        receipt = runtime._result_to_receipt([tool_result], TurnId("turn-write-committed"))

        assert receipt.results[0].effect_receipt == effect_receipt
        assert receipt.results[0].effect_receipt_commit == effect_receipt_commit
        assert receipt.raw_results[0]["effect_receipt_commit"] == effect_receipt_commit
        assert receipt.effect_receipts == [effect_receipt]

    @pytest.mark.asyncio
    async def test_write_without_effect_receipt_fails_closed(self, runtime) -> None:
        """成功写工具缺少 effect_receipt 时必须失败闭合。"""

        async def missing_receipt_executor(tool_name, arguments):
            return {"success": True, "result": {"message": "write reported success"}}

        runtime.executor = missing_receipt_executor

        write_inv = ToolInvocation(
            call_id=ToolCallId("w_missing_receipt"),
            tool_name="write_file",
            arguments={"path": "missing_receipt.txt", "content": "x"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
        batch = ToolBatch(
            batch_id=BatchId("missing_receipt_batch"),
            invocations=[write_inv],
            parallel_readonly=[],
            serial_writes=[write_inv],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_missing_receipt"))

        assert receipts[0]["success_count"] == 0
        assert receipts[0]["failure_count"] == 1
        assert receipts[0]["results"][0]["status"] == "error"
        assert receipts[0]["results"][0]["effect_receipt"] is None
        assert receipts[0]["results"][0]["result"]["failure_class"] == FailureClassV1.MISSING_EFFECT_RECEIPT.value
        assert receipts[0]["raw_results"][0]["error"] == (
            "Write tool succeeded without effect_receipt; tool lifecycle receipt is incomplete."
        )

    @pytest.mark.asyncio
    async def test_ok_false_result_maps_to_error_status(self, runtime) -> None:
        """仅返回 ok=false 的结果应被记为 error，而不是 success。"""

        async def failing_executor(tool_name, arguments):
            return {"ok": False, "error": "command failed", "result": {"detail": "boom"}}

        runtime.executor = failing_executor

        read_inv = ToolInvocation(
            call_id=ToolCallId("r_fail"),
            tool_name="read_file",
            arguments={"path": "missing.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("ok_false_batch"),
            invocations=[read_inv],
            parallel_readonly=[read_inv],
            serial_writes=[],
            async_receipts=[],
        )

        receipts = await runtime.execute_batch(batch, TurnId("turn_ok_false"))
        assert receipts[0]["failure_count"] == 1
        assert receipts[0]["results"][0]["status"] == "error"
