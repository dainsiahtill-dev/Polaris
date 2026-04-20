from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.cancel import CancellationCoordinator
from polaris.cells.roles.kernel.internal.speculation.chain_speculator import (
    ChainSpeculator,
)
from polaris.cells.roles.kernel.internal.speculation.fingerprints import (
    build_env_fingerprint,
    build_spec_key,
    normalize_args,
)
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import ShadowTaskState, ToolSpecPolicy
from polaris.cells.roles.kernel.internal.speculation.resolver import SpeculationResolver
from polaris.cells.roles.kernel.internal.speculation.write_phases import (
    WriteToolPhases,
)
from polaris.cells.roles.kernel.internal.stream_shadow_engine import StreamShadowEngine
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnLedger,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)


def _make_policy(tool_name: str = "read_file") -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect="readonly",
        cost="cheap",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
        timeout_ms=500,
        cache_ttl_ms=30000,
    )


def _spec_key(tool_name: str, args: dict[str, Any]) -> str:
    return build_spec_key(
        tool_name=tool_name,
        normalized_args=normalize_args(tool_name, args),
        env_fingerprint=build_env_fingerprint(),
    )


@pytest.fixture
def controller(monkeypatch: pytest.MonkeyPatch) -> TurnTransactionController:
    monkeypatch.setenv("ENABLE_SPECULATIVE_EXECUTION", "true")
    llm_provider = AsyncMock()
    tool_runtime = AsyncMock(return_value={"success": True, "result": {"replay": True}})
    return TurnTransactionController(
        llm_provider=llm_provider,
        tool_runtime=tool_runtime,
        config=TransactionConfig(domain="code"),
    )


@pytest.fixture
def shadow_engine(controller: TurnTransactionController) -> StreamShadowEngine:
    engine = cast(StreamShadowEngine, controller._build_stream_shadow_engine(workspace="."))
    assert engine is not None
    return engine


def _setup_state_machine(turn_id: str) -> TurnStateMachine:
    sm = TurnStateMachine(turn_id=turn_id)
    sm.transition_to(TurnState.CONTEXT_BUILT)
    sm.transition_to(TurnState.DECISION_REQUESTED)
    sm.transition_to(TurnState.DECISION_RECEIVED)
    sm.transition_to(TurnState.DECISION_DECODED)
    return sm


@pytest.mark.asyncio
async def test_batch_adopts_completed_shadow_task(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """已完成的 shadow task 应被 ADOPT，省去 canonical 调用，仅保留 1 次 speculative 执行."""
    registry = shadow_engine._registry
    assert registry is not None

    args = {"path": "adopt.py"}
    spec_key = _spec_key("read_file", args)
    record = await registry.start_shadow_task(
        turn_id="t_adopt",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", args),
        spec_key=spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert record.future is not None
    await record.future

    decision = TurnDecision(
        turn_id=TurnId("t_adopt"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments=args,
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_adopt")
    ledger = TurnLedger(turn_id="t_adopt")

    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    assert controller.tool_runtime.await_count == 1  # 1x speculative execution
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_batch_joins_running_shadow_task(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """运行中的 shadow task 应被 JOIN，省去 canonical 调用，仅保留 1 次 speculative 执行."""
    registry = shadow_engine._registry
    assert registry is not None

    block_event: Any = None

    async def blocking_execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal block_event
        if block_event is None:
            block_event = __import__("asyncio").Event()
        await block_event.wait()
        return {"result": "joined_data"}

    shadow_engine._speculative_executor.execute_speculative = blocking_execute  # type: ignore[method-assign]

    args = {"path": "join.py"}
    spec_key = _spec_key("read_file", args)
    await registry.start_shadow_task(
        turn_id="t_join",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", args),
        spec_key=spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )

    decision = TurnDecision(
        turn_id=TurnId("t_join"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments=args,
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_join")
    ledger = TurnLedger(turn_id="t_join")

    import asyncio

    async def run_batch() -> Any:
        return await controller._execute_tool_batch(
            decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
        )

    batch_task = asyncio.create_task(run_batch())
    await asyncio.sleep(0.05)
    assert not batch_task.done()
    assert block_event is not None
    block_event.set()
    result = await batch_task

    assert controller.tool_runtime.await_count == 0  # patched execute_speculative bypasses runtime
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_batch_replays_when_no_shadow_task(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """没有匹配 shadow task 时应回退到 REPLAY，调用 tool_runtime."""
    decision = TurnDecision(
        turn_id=TurnId("t_replay"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments={"path": "replay.py"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_replay")
    ledger = TurnLedger(turn_id="t_replay")

    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    assert controller.tool_runtime.await_count == 1
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_batch_mixed_adopt_and_replay(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """同一批次中部分 ADOPT、部分 REPLAY 应正确拆分执行."""
    registry = shadow_engine._registry
    assert registry is not None

    adopt_args = {"path": "adopt.py"}
    adopt_spec_key = _spec_key("read_file", adopt_args)
    adopt_record = await registry.start_shadow_task(
        turn_id="t_mixed",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", adopt_args),
        spec_key=adopt_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert adopt_record.future is not None
    await adopt_record.future

    decision = TurnDecision(
        turn_id=TurnId("t_mixed"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_adopt"),
                    tool_name="read_file",
                    arguments=adopt_args,
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                ),
                ToolInvocation(
                    call_id=ToolCallId("call_replay"),
                    tool_name="read_file",
                    arguments={"path": "replay.py"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                ),
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_mixed")
    ledger = TurnLedger(turn_id="t_mixed")

    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    # 1x speculative execution for adopt + 1x canonical replay
    assert controller.tool_runtime.await_count == 2
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_stream_shadow_engine_lifecycle_in_controller(controller: TurnTransactionController) -> None:
    """Controller 应在 execute_stream 中创建 shadow engine 并在最后 drain."""

    async def stream_provider(_request_payload: dict[str, Any]):
        yield {"type": "content_chunk", "content": "ok"}
        yield {
            "type": "tool_call",
            "tool": "read_file",
            "args": {"path": "drain.py"},
            "call_id": "call_drain",
            "metadata": {
                "tool_call": {
                    "tool": "read_file",
                    "arguments": {"path": "drain.py"},
                    "call_id": "call_drain",
                }
            },
        }

    controller.llm_provider_stream = stream_provider
    controller.llm_provider = AsyncMock(
        return_value={
            "content": "",
            "tool_calls": [
                {
                    "id": "call_drain",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "drain.py"}',
                    },
                }
            ],
            "model": "test",
            "usage": {},
        }
    )

    events: list[Any] = []
    async for event in controller.execute_stream(
        turn_id="t_drain",
        context=[{"role": "user", "content": "read drain.py"}],
        tool_definitions=[{"name": "read_file", "parameters": {}}],
    ):
        events.append(event)

    assert len(events) > 0


@pytest.mark.asyncio
async def test_param_drift_replay(controller: TurnTransactionController, shadow_engine: StreamShadowEngine) -> None:
    """参数漂移时，旧 shadow 不应被复用，应回退到 REPLAY."""
    registry = shadow_engine._registry
    assert registry is not None

    old_args = {"path": "old.py"}
    old_spec_key = _spec_key("read_file", old_args)
    old_record = await registry.start_shadow_task(
        turn_id="t_drift",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", old_args),
        spec_key=old_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert old_record.future is not None
    await old_record.future

    new_args = {"path": "new.py"}
    decision = TurnDecision(
        turn_id=TurnId("t_drift"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments=new_args,
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_drift")
    ledger = TurnLedger(turn_id="t_drift")

    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    # 1 次 speculative + 1 次 REPLAY（旧 shadow 参数不同无法 ADOPT）
    assert controller.tool_runtime.await_count == 2
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_turn_cancel_no_ghost(shadow_engine: StreamShadowEngine) -> None:
    """turn 级取消后，所有 shadow task 必须处于终止状态，无 ghost task."""
    registry = shadow_engine._registry
    assert registry is not None

    # 1) 先启动并等待一个 completed task（使用原始 fast executor）
    completed_args = {"path": "completed.py"}
    completed_spec_key = _spec_key("read_file", completed_args)
    completed_record = await registry.start_shadow_task(
        turn_id="t_cancel",
        candidate_id="c2",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", completed_args),
        spec_key=completed_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert completed_record.future is not None
    await completed_record.future

    # 2) 再 patch executor 为阻塞版本，启动一个 running task
    block_event = asyncio.Event()

    async def slow_execute(*args: Any, **kwargs: Any) -> Any:
        await block_event.wait()
        return {"result": "slow_data"}

    shadow_engine._speculative_executor.execute_speculative = slow_execute  # type: ignore[method-assign]

    running_args = {"path": "running.py"}
    running_spec_key = _spec_key("read_file", running_args)
    await registry.start_shadow_task(
        turn_id="t_cancel",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", running_args),
        spec_key=running_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )

    await asyncio.sleep(0.05)

    coordinator = CancellationCoordinator()
    task_group = shadow_engine._task_group
    assert task_group is not None
    await coordinator.cancel_turn("t_cancel", registry, task_group, salvage=False)

    records = registry.get_turn_records("t_cancel")
    for record in records:
        assert record.state in {
            ShadowTaskState.CANCELLED,
            ShadowTaskState.ABANDONED,
            ShadowTaskState.ADOPTED,
            ShadowTaskState.FAILED,
        }

    # 无活跃 ghost
    assert registry.exists_active(running_spec_key) is False
    assert registry.exists_active(completed_spec_key) is False

    block_event.set()


@pytest.mark.asyncio
async def test_refusal_abort(shadow_engine: StreamShadowEngine) -> None:
    """refusal abort 后，已完成的 shadow task 应被标记为 ABANDONED，resolver 回退到 REPLAY."""
    registry = shadow_engine._registry
    assert registry is not None

    args = {"path": "refuse.py"}
    spec_key = _spec_key("read_file", args)
    record = await registry.start_shadow_task(
        turn_id="t_refuse",
        candidate_id="c1",
        tool_name="read_file",
        normalized_args=normalize_args("read_file", args),
        spec_key=spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert record.future is not None
    await record.future

    coordinator = CancellationCoordinator()
    task_group = shadow_engine._task_group
    await coordinator.refuse_turn("t_refuse", registry, task_group=task_group)

    records = registry.get_turn_records("t_refuse")
    assert len(records) == 1
    assert records[0].state == ShadowTaskState.ABANDONED

    resolver = SpeculationResolver(registry=registry, metrics=SpeculationMetrics())
    resolution = await resolver.resolve_or_execute(
        turn_id="t_refuse",
        call_id="call_1",
        tool_name="read_file",
        args=args,
    )
    assert resolution["action"] == "replay"


@pytest.mark.asyncio
async def test_retrieval_chain_adopts_prefetch(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """repo_rg shadow 完成后,下游 read_file shadows 在 authoritative 阶段被正确 ADOPT."""
    registry = shadow_engine._registry
    assert registry is not None

    # 手动注入 ChainSpeculator 并模拟上游 shadow 完成
    chain_speculator = ChainSpeculator(registry=registry)
    registry._on_shadow_completed = chain_speculator.on_shadow_completed

    upstream_args = {"query": "auth middleware"}
    upstream_spec_key = _spec_key("repo_rg", upstream_args)
    upstream_record = await registry.start_shadow_task(
        turn_id="t_chain_adopt",
        candidate_id="c1",
        tool_name="repo_rg",
        normalized_args=normalize_args("repo_rg", upstream_args),
        spec_key=upstream_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy("repo_rg"),
    )
    # 模拟 repo_rg 结果并触发 on_shadow_completed
    upstream_record.result = {"matches": [{"path": "src/auth.ts"}]}
    upstream_record.state = ShadowTaskState.COMPLETED
    await chain_speculator.on_shadow_completed(upstream_record)

    # 等待下游 shadow 完成
    await asyncio.sleep(0.05)

    # authoritative batch 请求 read_file(src/auth.ts)
    decision = TurnDecision(
        turn_id=TurnId("t_chain_adopt"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="read_file",
                    arguments={"path": "src/auth.ts"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_chain_adopt")
    ledger = TurnLedger(turn_id="t_chain_adopt")

    before_count = controller.tool_runtime.await_count
    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    # 下游 read_file 被 ADOPT,controller 不再走 tool_runtime replay
    assert controller.tool_runtime.await_count == before_count
    assert result["kind"] == "tool_batch_with_receipt"


@pytest.mark.asyncio
async def test_cascade_cancel_abandons_downstream(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """上游 repo_rg 被 refusal abort 后,所有自动触发的 read_file 也被标记为 ABANDONED."""
    registry = shadow_engine._registry
    assert registry is not None

    chain_speculator = ChainSpeculator(registry=registry)
    registry._on_shadow_completed = chain_speculator.on_shadow_completed

    upstream_args = {"query": "auth"}
    upstream_spec_key = _spec_key("repo_rg", upstream_args)
    upstream_record = await registry.start_shadow_task(
        turn_id="t_chain_cascade",
        candidate_id="c1",
        tool_name="repo_rg",
        normalized_args=normalize_args("repo_rg", upstream_args),
        spec_key=upstream_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy("repo_rg"),
    )
    upstream_record.result = {"matches": [{"path": "src/auth.ts"}]}
    upstream_record.state = ShadowTaskState.COMPLETED
    await chain_speculator.on_shadow_completed(upstream_record)
    await asyncio.sleep(0.05)

    coordinator = CancellationCoordinator()
    task_group = shadow_engine._task_group
    await coordinator.refuse_turn("t_chain_cascade", registry, task_group=task_group)

    records = registry.get_turn_records("t_chain_cascade")
    for record in records:
        assert record.state in {
            ShadowTaskState.ABANDONED,
            ShadowTaskState.CANCELLED,
            ShadowTaskState.ADOPTED,
            ShadowTaskState.FAILED,
        }

    # 所有自动触发的 downstream 也应被清理
    downstream_records = [
        r for r in records if r.tool_name == "read_file" and r.origin_candidate_id.startswith("chain_")
    ]
    for dr in downstream_records:
        assert dr.state in {ShadowTaskState.ABANDONED, ShadowTaskState.CANCELLED}


@pytest.mark.asyncio
async def test_write_tool_commit_never_speculative(
    controller: TurnTransactionController, shadow_engine: StreamShadowEngine
) -> None:
    """write_file 的 commit 阶段不会被 shadow 执行,prepare 可被 adopt 但 commit 必须走 authoritative."""
    registry = shadow_engine._registry
    assert registry is not None

    # 启动 prepare shadow
    prepare_inv = WriteToolPhases.build_prepare_invocation(
        ToolInvocation(
            call_id=ToolCallId("call_1"),
            tool_name="write_file",
            arguments={"path": "src/auth.ts", "content": "hello"},
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
    )
    prepare_spec_key = build_spec_key(
        tool_name=prepare_inv.tool_name,
        normalized_args=normalize_args(prepare_inv.tool_name, prepare_inv.arguments),
        env_fingerprint=build_env_fingerprint(),
    )
    prepare_record = await registry.start_shadow_task(
        turn_id="t_write_commit",
        candidate_id="prepare_call_1",
        tool_name=prepare_inv.tool_name,
        normalized_args=normalize_args(prepare_inv.tool_name, prepare_inv.arguments),
        spec_key=prepare_spec_key,
        env_fingerprint=build_env_fingerprint(),
        policy=_make_policy(),
    )
    assert prepare_record.future is not None
    await prepare_record.future

    decision = TurnDecision(
        turn_id=TurnId("t_write_commit"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        tool_batch=ToolBatch(
            batch_id="batch_1",
            invocations=[
                ToolInvocation(
                    call_id=ToolCallId("call_1"),
                    tool_name="write_file",
                    arguments={"path": "src/auth.ts", "content": "hello"},
                    effect_type=ToolEffectType.WRITE,
                    execution_mode=ToolExecutionMode.WRITE_SERIAL,
                )
            ],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    state_machine = _setup_state_machine("t_write_commit")
    ledger = TurnLedger(turn_id="t_write_commit")

    before_count = controller.tool_runtime.await_count
    result = await controller._execute_tool_batch(
        decision, state_machine, ledger, context=[], stream=False, shadow_engine=shadow_engine
    )

    # prepare 被 adopt,但 commit 仍走了 authoritative tool_runtime(恰好 1 次)
    assert controller.tool_runtime.await_count == before_count + 1
    assert result["kind"] == "tool_batch_with_receipt"
