from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.metrics import SpeculationMetrics
from polaris.cells.roles.kernel.internal.speculation.models import (
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.registry import (
    EphemeralSpecCache,
    ShadowTaskRegistry,
)


def _policy(tool_name: str = "read_file") -> ToolSpecPolicy:
    return ToolSpecPolicy(
        tool_name=tool_name,
        side_effect="readonly",
        cost="cheap",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
    )


@pytest.fixture
def mock_executor() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def registry(mock_executor: AsyncMock) -> ShadowTaskRegistry:
    return ShadowTaskRegistry(
        speculative_executor=mock_executor,
        metrics=SpeculationMetrics(),
        cache=EphemeralSpecCache(),
    )


@pytest.mark.asyncio
async def test_start_shadow_task_creates_record(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    mock_executor.execute_speculative.return_value = {"result": "ok"}
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_1",
        env_fingerprint="fp",
        policy=_policy(),
    )
    assert isinstance(record, ShadowTaskRecord)
    assert record.task_id.startswith("shadow_")
    assert record.state == ShadowTaskState.STARTING
    # wait for background task to complete
    assert record.future is not None
    await record.future
    assert record.state == ShadowTaskState.COMPLETED
    assert record.result == {"result": "ok"}


@pytest.mark.asyncio
async def test_start_shadow_task_deduplicates_same_spec_key(
    registry: ShadowTaskRegistry, mock_executor: AsyncMock
) -> None:
    mock_executor.execute_speculative.return_value = {"result": "ok"}
    record1 = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_dup",
        env_fingerprint="fp",
        policy=_policy(),
    )
    record2 = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_2",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_dup",
        env_fingerprint="fp",
        policy=_policy(),
    )
    assert record1.task_id == record2.task_id


@pytest.mark.asyncio
async def test_adopt_returns_result_and_sets_adopted(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    mock_executor.execute_speculative.return_value = {"result": "adopted_value"}
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_adopt",
        env_fingerprint="fp",
        policy=_policy(),
    )
    assert record.future is not None
    await record.future

    result = await registry.adopt(record.task_id, "call_1")
    assert result == {"result": "adopted_value"}
    assert record.state == ShadowTaskState.ADOPTED
    assert record.adopted_by_call_id == "call_1"


@pytest.mark.asyncio
async def test_adopt_non_completed_raises(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    block_event = asyncio.Event()

    async def slow_execute(*_args: Any, **_kwargs: Any) -> Any:
        await block_event.wait()
        return {"result": "ok"}

    mock_executor.execute_speculative.side_effect = slow_execute
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_adopt_fail",
        env_fingerprint="fp",
        policy=_policy(),
    )
    with pytest.raises(RuntimeError, match="cannot adopt non-completed"):
        await registry.adopt(record.task_id, "call_1")
    block_event.set()
    if record.future is not None:
        await record.future


@pytest.mark.asyncio
async def test_join_waits_for_completion(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    mock_executor.execute_speculative.return_value = {"result": "joined_value"}
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_join",
        env_fingerprint="fp",
        policy=_policy(),
    )
    result = await registry.join(record.task_id, "call_2")
    assert result == {"result": "joined_value"}
    assert record.adopted_by_call_id == "call_2"


@pytest.mark.asyncio
async def test_cancel_requests_cancellation(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    block_event = asyncio.Event()

    async def slow_execute(*_args: Any, **_kwargs: Any) -> Any:
        with __import__("contextlib").suppress(asyncio.TimeoutError):
            await asyncio.wait_for(block_event.wait(), timeout=5.0)
        return {"result": "ok"}

    mock_executor.execute_speculative.side_effect = slow_execute
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_cancel",
        env_fingerprint="fp",
        policy=_policy(),
    )
    await registry.cancel(record.task_id, reason="test_cancel")
    assert record.state == ShadowTaskState.CANCEL_REQUESTED
    if record.future is not None:
        with pytest.raises(asyncio.CancelledError):
            await record.future


@pytest.mark.asyncio
async def test_drain_turn_cancels_running_tasks(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    block_event = asyncio.Event()

    async def slow_execute(*_args: Any, **_kwargs: Any) -> Any:
        await block_event.wait()
        return {"result": "ok"}

    mock_executor.execute_speculative.side_effect = slow_execute
    record = await registry.start_shadow_task(
        turn_id="turn_drain",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_drain",
        env_fingerprint="fp",
        policy=_policy(),
    )
    assert record.state in {ShadowTaskState.STARTING, ShadowTaskState.RUNNING}
    await registry.drain_turn("turn_drain", timeout_s=0.5)
    if record.future is not None:
        with pytest.raises(asyncio.CancelledError):
            await record.future


@pytest.mark.asyncio
async def test_mark_abandoned_removes_from_index(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    mock_executor.execute_speculative.return_value = {"result": "ok"}
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_abandon",
        env_fingerprint="fp",
        policy=_policy(),
    )
    assert registry.exists_active("spec_abandon") is True
    await registry.mark_abandoned(record.task_id, "refused")
    assert registry.exists_active("spec_abandon") is False
    assert record.state == ShadowTaskState.ABANDONED


@pytest.mark.asyncio
async def test_lookup_expired_task_returns_none(registry: ShadowTaskRegistry, mock_executor: AsyncMock) -> None:
    mock_executor.execute_speculative.return_value = {"result": "ok"}
    from polaris.cells.roles.kernel.internal.speculation.models import ToolSpecPolicy

    policy = ToolSpecPolicy(
        tool_name="read_file",
        side_effect="readonly",
        cost="cheap",
        cancellability="cooperative",
        reusability="adoptable",
        speculate_mode="speculative_allowed",
        timeout_ms=500,
        cache_ttl_ms=1,  # 1ms ttl
    )
    record = await registry.start_shadow_task(
        turn_id="turn_1",
        candidate_id="cand_1",
        tool_name="read_file",
        normalized_args={"path": "a.py"},
        spec_key="spec_expire",
        env_fingerprint="fp",
        policy=policy,
    )
    assert record.future is not None
    await record.future
    await asyncio.sleep(0.01)  # wait for ttl to expire
    assert registry.lookup("spec_expire") is None


def test_ephemeral_cache_ttl() -> None:
    cache = EphemeralSpecCache(ttl_ms=1.0)
    import time

    class FakeRecord:
        spec_key = "key1"
        result = "value1"

    asyncio.run(cache.put(FakeRecord()))  # type: ignore[arg-type]
    assert cache.get("key1") == "value1"
    time.sleep(0.01)
    assert cache.get("key1") is None
