"""ADR-0090 I2.3: shadow task failures must be consumed, never left unretrieved.

A failed speculative shadow is an expected miss; before this contract the
``shadow_group:*`` task exceptions were re-raised into futures nobody awaited,
flooding the event loop with "Task exception was never retrieved" noise during
live weak-model runs.
"""

from __future__ import annotations

import asyncio
import gc
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.task_group import TurnScopedTaskGroup


async def _boom() -> None:
    raise RuntimeError("speculative tool failed: error")


class TestShadowTaskExceptionConsumption:
    async def test_failed_task_exception_is_retrieved(self) -> None:
        captured: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: captured.append(dict(context)))
        try:
            group = TurnScopedTaskGroup("turn-1")
            task = group.create_task(_boom(), name="shadow_group:test")
            await asyncio.sleep(0.01)
            assert task.done()

            # Drop the only reference and force the GC pass that would emit
            # "Task exception was never retrieved" for unconsumed exceptions.
            del task
            gc.collect()
            await asyncio.sleep(0)

            unretrieved = [context for context in captured if "never retrieved" in str(context.get("message", ""))]
            assert unretrieved == []
        finally:
            loop.set_exception_handler(previous_handler)

    async def test_successful_task_unaffected(self) -> None:
        group = TurnScopedTaskGroup("turn-2")

        async def _ok() -> str:
            return "adopted"

        task = group.create_task(_ok(), name="shadow_group:ok")
        result = await task

        assert result == "adopted"

    async def test_cancelled_task_does_not_raise_in_callback(self) -> None:
        group = TurnScopedTaskGroup("turn-3")

        async def _slow() -> None:
            await asyncio.sleep(30)

        task = group.create_task(_slow(), name="shadow_group:slow")
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.01)

        assert task.cancelled()
