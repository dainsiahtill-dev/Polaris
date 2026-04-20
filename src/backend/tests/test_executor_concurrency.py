from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.llm.engine import WorkspaceExecutorManager


@pytest.mark.asyncio
async def test_concurrent_executor_creation() -> None:
    manager = WorkspaceExecutorManager()
    workspace = "/test/workspace"

    async def create_executor():
        return await manager.get_executor(workspace)

    tasks = [create_executor() for _ in range(100)]
    executors = await asyncio.gather(*tasks)

    first = executors[0]
    assert all(executor is first for executor in executors)
    assert manager._executors[workspace].ref_count == 100


def test_workspace_executors_are_isolated() -> None:
    manager = WorkspaceExecutorManager()

    ws_a = manager.get_executor_sync("/workspace/a")
    ws_b = manager.get_executor_sync("/workspace/b")

    assert ws_a is not ws_b

