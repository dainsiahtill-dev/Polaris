"""三个认知插件共用一个出口：同时在途只允许一个。

实测：制图师叙述卡通过率 11.2%，而三个插件各自持有 provider client，
并发出击会互相挤掉对方的轮次。插槽把它们串成队列。
"""

from __future__ import annotations

import asyncio

from polaris.cells.roles.runtime.internal.engineering_cartographer import CartographerRuntime
from polaris.cells.roles.runtime.internal.engineering_plugin_slot import claim_plugin_slot
from polaris.cells.roles.runtime.internal.engineering_tools import _is_placeholder_evidence


class _NeverPool:
    async def draw_many(self, requests: list[dict[str, object]]) -> list[str]:
        await asyncio.sleep(30)
        return [""]


async def test_plugin_slot_serialises_concurrent_plugins() -> None:
    trace: list[tuple[str, str]] = []

    async def worker(name: str) -> None:
        async with claim_plugin_slot():
            trace.append((name, "start"))
            await asyncio.sleep(0.02)
            trace.append((name, "end"))

    await asyncio.gather(worker("cartographer"), worker("devourer"), worker("distiller"))

    # 顺序可变，但绝不能交叠：每个 start 之后必须紧跟同一个 end。
    assert len(trace) == 6
    for index in range(0, len(trace), 2):
        assert trace[index][0] == trace[index + 1][0]
        assert (trace[index][1], trace[index + 1][1]) == ("start", "end")


def test_live_observations_are_not_placeholder_evidence() -> None:
    assert _is_placeholder_evidence(
        "Investigation in progress; no evidence collected yet for this clause."
    )
    assert _is_placeholder_evidence("no approval decision yet")
    assert not _is_placeholder_evidence("已读 src/core/brake.ts：只有接口没有距离断言。")
    assert not _is_placeholder_evidence("Host check verify-1 exit 0.")


async def test_schedule_reports_whether_it_actually_queued() -> None:
    """旧版无条件发 cartographer_scheduled 事件，12 次里只有 4 次真有任务。"""

    runtime = CartographerRuntime(_NeverPool())
    assert runtime.schedule({"a": 1}, fingerprint="one") is True
    # 同一 fingerprint 不重排；在途不重排。
    assert runtime.schedule({"a": 1}, fingerprint="one") is False
    assert runtime.schedule({"a": 2}, fingerprint="two") is False
