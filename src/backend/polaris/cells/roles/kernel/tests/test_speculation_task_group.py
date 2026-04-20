from __future__ import annotations

import asyncio
import time

import pytest
from polaris.cells.roles.kernel.internal.speculation.models import (
    SalvageDecision,
    ShadowTaskRecord,
    ShadowTaskState,
    ToolSpecPolicy,
)
from polaris.cells.roles.kernel.internal.speculation.salvage import SalvageGovernor
from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup


class TestTurnScopedTaskGroup:
    @pytest.mark.asyncio
    async def test_create_task_and_join_all(self) -> None:
        group = TurnScopedTaskGroup(turn_id="t1")

        async def worker() -> str:
            await asyncio.sleep(0.01)
            return "ok"

        task = group.create_task(worker())
        assert task in group._tasks
        await group.join_all()
        assert task.done()
        assert task.result() == "ok"
        assert task not in group._tasks

    @pytest.mark.asyncio
    async def test_cancel_all_without_salvage(self) -> None:
        group = TurnScopedTaskGroup(turn_id="t1")
        block_event = asyncio.Event()

        async def worker() -> None:
            await block_event.wait()

        task = group.create_task(worker())
        await group.cancel_all(salvage=False)
        block_event.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_cancel_with_salvage_let_finish(self) -> None:
        governor = SalvageGovernor()
        group = TurnScopedTaskGroup(turn_id="t1", salvage_governor=governor)
        completion_event = asyncio.Event()

        async def worker() -> str:
            await completion_event.wait()
            return "finished"

        task = group.create_task(worker())
        # Manually create a record that maps to this task for salvage evaluation
        policy = ToolSpecPolicy(
            tool_name="read_file",
            side_effect="readonly",
            cost="cheap",
            cancellability="cooperative",
            reusability="adoptable",
            speculate_mode="speculative_allowed",
            timeout_ms=1000,
        )
        record = ShadowTaskRecord(
            task_id="task_1",
            origin_turn_id="t1",
            origin_candidate_id="c1",
            tool_name="read_file",
            normalized_args={},
            spec_key="spec_1",
            env_fingerprint="fp",
            policy_snapshot=policy,
            state=ShadowTaskState.RUNNING,
            started_at=0.0,
            future=task,
        )

        decisions = await group.cancel_with_salvage([record])
        assert decisions["task_1"] == SalvageDecision.LET_FINISH_AND_CACHE
        # Task should be detached from the group
        assert task not in group._tasks
        assert task in group._detached

        completion_event.set()
        result = await task
        assert result == "finished"
        assert task not in group._detached

    @pytest.mark.asyncio
    async def test_cancel_with_salvage_cancel_now(self) -> None:
        governor = SalvageGovernor()
        group = TurnScopedTaskGroup(turn_id="t1", salvage_governor=governor)
        block_event = asyncio.Event()

        async def worker() -> str:
            await block_event.wait()
            return "finished"

        task = group.create_task(worker())
        policy = ToolSpecPolicy(
            tool_name="read_file",
            side_effect="readonly",
            cost="cheap",
            cancellability="cooperative",
            reusability="adoptable",
            speculate_mode="speculative_allowed",
            timeout_ms=1000,
        )
        record = ShadowTaskRecord(
            task_id="task_2",
            origin_turn_id="t1",
            origin_candidate_id="c1",
            tool_name="read_file",
            normalized_args={},
            spec_key="spec_2",
            env_fingerprint="fp",
            policy_snapshot=policy,
            state=ShadowTaskState.RUNNING,
            started_at=time.monotonic(),
            future=task,
        )

        decisions = await group.cancel_with_salvage([record])
        assert decisions["task_2"] == SalvageDecision.CANCEL_NOW
        block_event.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_close_prevents_new_tasks(self) -> None:
        group = TurnScopedTaskGroup(turn_id="t1")
        group.close()
        with pytest.raises(RuntimeError, match="closed"):
            group.create_task(asyncio.sleep(0))

    @pytest.mark.asyncio
    async def test_join_all_with_timeout(self) -> None:
        group = TurnScopedTaskGroup(turn_id="t1")
        block_event = asyncio.Event()

        async def slow_worker() -> None:
            await block_event.wait()

        group.create_task(slow_worker())
        await group.join_all(timeout=0.05)
        # Timeout expired, task still running
        block_event.set()
