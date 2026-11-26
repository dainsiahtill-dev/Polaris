"""One in-flight remote plugin call at a time.

三个认知插件各自持有 provider client，但抢的是同一份出口容量和同一段时间预算。
让它们并发出击的实测结果是三个慢调用互相挤掉对方的轮次。插槽让它们排队；
来不及产出卡片的插件这一轮直接被跳过，下一轮再来。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

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


async def request_node_summaries(
    requests: list[dict[str, Any]],
    summarize: Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """An unavailable auxiliary model cannot terminate the owning engineering run."""
    try:
        async with claim_plugin_slot():
            responses = await summarize(requests)
        if not isinstance(responses, list) or len(responses) != len(requests):
            raise ValueError("auxiliary summary batch did not match the requested node count")
    except Exception as error:  # noqa: BLE001 - preserve the optional provider's failure for the role journal
        return [
            {
                "validation_result": "REJECTED",
                "node_id": item["node_id"],
                "snapshot_id": item["snapshot_id"],
                "error": f"{type(error).__name__}: {error}"[:400],
            }
            for item in requests
        ]
    for index, response in enumerate(responses):
        selection = requests[index].get("selection")
        if (
            isinstance(response, dict)
            and response.get("validation_result") == "NOT_REQUESTED"
            and response.get("skip_reason") == "no_observed_receipts"
            and response.get("provider_attempts") == []
            and isinstance(selection, dict)
            and selection.get("observations") == []
        ):
            continue
        if isinstance(response, dict) and (
            response.get("validation_result") == "REJECTED"
            or (response.get("validation_result") == "VALID" and isinstance(response.get("result"), dict))
        ):
            continue
        responses[index] = {
            "validation_result": "REJECTED",
            "node_id": requests[index]["node_id"],
            "snapshot_id": requests[index]["snapshot_id"],
            "error": "malformed auxiliary summary receipt",
        }
    return responses
