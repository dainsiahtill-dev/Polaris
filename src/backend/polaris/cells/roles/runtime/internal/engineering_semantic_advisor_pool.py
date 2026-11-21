"""Bounded pool of remote semantic advisors with per-endpoint isolation."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import (
    RemoteAdvisorConfig,
    RemoteSemanticAdvisor,
)


class RemoteAdvisorPool:
    """Run independent advisor jobs concurrently, with one job per endpoint."""

    def __init__(self, configs: list[RemoteAdvisorConfig]) -> None:
        if not configs:
            raise ValueError("semantic advisor pool requires at least one endpoint")
        self._advisors = [RemoteSemanticAdvisor(config) for config in configs]
        self._locks = [asyncio.Lock() for _ in configs]

    async def summarize_many(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async def run(index: int, request: dict[str, Any]) -> dict[str, Any]:
            advisor = self._advisors[index % len(self._advisors)]
            started = time.perf_counter()
            async with self._locks[index % len(self._locks)]:
                try:
                    result = await advisor.summarize_node(request)
                    return {
                        "endpoint_index": index % len(self._advisors),
                        "validation_result": "VALID",
                        "result": result,
                        "wall_seconds": time.perf_counter() - started,
                        "usage": advisor.last_usage,
                        "timings": advisor.last_timings,
                        "normalization": advisor.last_normalization,
                    }
                except (ValueError, OSError, asyncio.TimeoutError) as error:
                    return {
                        "endpoint_index": index % len(self._advisors),
                        "validation_result": "REJECTED",
                        "error": f"{type(error).__name__}: {error}",
                    }

        return await asyncio.gather(*(run(index, request) for index, request in enumerate(requests)))

    async def advise_many(self, requests: list[dict[str, Any]], candidates: CandidateRefs) -> list[dict[str, Any]]:
        async def run(index: int, request: dict[str, Any]) -> dict[str, Any]:
            advisor = self._advisors[index % len(self._advisors)]
            started = time.perf_counter()
            async with self._locks[index % len(self._locks)]:
                try:
                    result = await advisor.advise(request, candidates)
                    return {
                        "endpoint_index": index % len(self._advisors),
                        "validation_result": "VALID",
                        "result": result,
                        "wall_seconds": time.perf_counter() - started,
                        "usage": advisor.last_usage,
                        "timings": advisor.last_timings,
                        "normalization": advisor.last_normalization,
                    }
                except (ValueError, OSError, asyncio.TimeoutError) as error:
                    return {
                        "endpoint_index": index % len(self._advisors),
                        "validation_result": "REJECTED",
                        "error": f"{type(error).__name__}: {error}",
                    }

        return await asyncio.gather(*(run(index, request) for index, request in enumerate(requests)))
