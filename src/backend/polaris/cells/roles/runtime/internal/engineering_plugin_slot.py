"""One in-flight remote plugin call at a time.

三个认知插件各自持有 provider client，但抢的是同一份出口容量和同一段时间预算。
让它们并发出击的实测结果是三个慢调用互相挤掉对方的轮次。插槽让它们排队；
来不及产出卡片的插件这一轮直接被跳过，下一轮再来。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

_slot: asyncio.Semaphore | None = None


def plugin_slot() -> asyncio.Semaphore:
    global _slot
    if _slot is None:
        _slot = asyncio.Semaphore(1)
    return _slot


@contextlib.asynccontextmanager
async def claim_plugin_slot() -> AsyncIterator[None]:
    async with plugin_slot():
        yield
