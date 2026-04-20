"""Chronos Mirror: 确定性 HTTP 录制与回放系统

非侵入式外部 I/O 录制与 100% 还原重放机制。

Usage:
    async with ShadowReplay(cassette_id="task-123", mode="both") as replay:
        # All httpx.AsyncClient calls are intercepted
        result = await call_llm_api(prompt)  # Recorded
        result = await call_llm_api(prompt)  # Replayed
"""

from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
    CassetteEntry,
    CassetteFormat,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.core import (
    ShadowReplay,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    CassetteNotFoundError,
    ShadowReplayError,
    UnrecordedRequestError,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.player import (
    ShadowPlayer,
)
from polaris.kernelone.benchmark.reproducibility.shadow_replay.recorder import (
    ShadowRecorder,
)

__all__ = [
    "Cassette",
    "CassetteEntry",
    "CassetteFormat",
    "CassetteNotFoundError",
    "ShadowPlayer",
    "ShadowRecorder",
    "ShadowReplay",
    "ShadowReplayError",
    "UnrecordedRequestError",
]
