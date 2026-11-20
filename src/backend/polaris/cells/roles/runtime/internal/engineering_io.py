"""Cancellation-drained candidate I/O shared by the verifier and service owner."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextvars import copy_context
from typing import TypeVar

from polaris.kernelone.concurrency import get_concurrency_manager

logger = logging.getLogger(__name__)
_ObservationT = TypeVar("_ObservationT")


async def run_owned_io(observation: Callable[[], _ObservationT]) -> _ObservationT:
    """Run candidate I/O off the event loop, keeping ownership until drained."""
    context = copy_context()
    future = asyncio.get_running_loop().run_in_executor(
        get_concurrency_manager().get_io_pool(), context.run, observation
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Cancelling an executor Future cannot stop its running callable. Keep
        # ownership until it finishes; never release a candidate while an old
        # observation is still traversing its inputs.
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - report the completed drain error below.
                break
        if not future.cancelled() and (error := future.exception()) is not None:
            logger.warning("Candidate I/O failed while cancellation drained: %s", type(error).__name__)
        raise
