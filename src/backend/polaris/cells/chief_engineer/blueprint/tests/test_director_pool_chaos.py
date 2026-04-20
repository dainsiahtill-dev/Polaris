"""Chaos tests for DirectorPool.

Covers concurrent assignment races, random director failure/recovery,
and full-pool degradation and recovery.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from polaris.cells.chief_engineer.blueprint.internal.director_pool import (
    DirectorPhase,
    DirectorPool,
    DirectorPoolConflictError,
)


class FakeTask:
    """Minimal task stub for DirectorPool tests."""

    def __init__(self, task_id: str, target_files: list[str] | None = None) -> None:
        self.id = task_id
        self.target_files = target_files or []


class FakeBlueprint:
    """Minimal blueprint stub."""

    pass


@pytest.fixture
def pool() -> DirectorPool:
    p = DirectorPool(workspace="/tmp/test", max_directors=3)
    p.initialize_directors()
    p._submit_director_task_workflow = AsyncMock()  # type: ignore[method-assign]
    return p


class TestDirectorPoolChaos:
    """Chaos and resilience tests for DirectorPool."""

    @pytest.mark.anyio
    async def test_concurrent_assign_race_condition(self, pool: DirectorPool) -> None:
        tasks = [FakeTask(f"T-{i}", [f"file_{i % 5}.py"]) for i in range(10)]

        async def assign(task: FakeTask) -> str | DirectorPoolConflictError:
            try:
                return await pool.assign_task(task, FakeBlueprint())
            except DirectorPoolConflictError as exc:
                return exc

        results = await asyncio.gather(*[assign(t) for t in tasks])

        successes = [r for r in results if isinstance(r, str)]
        conflicts = [r for r in results if isinstance(r, DirectorPoolConflictError)]

        assert len(successes) + len(conflicts) == len(tasks)

        active_files: dict[str, str] = {}
        for task, result in zip(tasks, results, strict=False):
            if isinstance(result, str):
                director_id = result
                for f in task.target_files:
                    existing_owner = active_files.get(f)
                    if existing_owner is not None:
                        assert existing_owner == director_id
                    active_files[f] = director_id

    @pytest.mark.anyio
    async def test_random_kill_director_recovery(self, pool: DirectorPool) -> None:
        did = await pool.assign_task(FakeTask("T-1", ["a.py"]), FakeBlueprint())
        assert pool._directors[did].phase != DirectorPhase.IDLE

        decision = pool.handle_failure("T-1", RuntimeError("boom"))
        assert decision.action == "retry"

        assert pool._directors[did].phase == DirectorPhase.IDLE
        assert pool._directors[did].current_task_id is None
        assert pool.get_director_for_task("T-1") is None

        did2 = await pool.assign_task(FakeTask("T-2", ["b.py"]), FakeBlueprint())
        assert did2 is not None
        assert pool._directors[did2].phase == DirectorPhase.PREPARE

    @pytest.mark.anyio
    async def test_all_directors_fail_degrade(self, pool: DirectorPool) -> None:
        dids = []
        for i in range(3):
            did = await pool.assign_task(FakeTask(f"T-{i}", [f"file_{i}.py"]), FakeBlueprint())
            dids.append(did)

        for i in range(3):
            decision = pool.handle_failure(f"T-{i}", TimeoutError("too slow"))
            assert decision.action == "reassign"

        for did in dids:
            assert pool._directors[did].phase == DirectorPhase.IDLE
            assert pool._directors[did].current_task_id is None

        new_did = await pool.assign_task(FakeTask("T-recovery", ["new_file.py"]), FakeBlueprint())
        assert new_did is not None
        assert pool._directors[new_did].phase == DirectorPhase.PREPARE
        assert pool.get_director_for_task("T-recovery") == new_did
