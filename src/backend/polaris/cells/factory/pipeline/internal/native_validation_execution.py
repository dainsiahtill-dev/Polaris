"""Await physical verification workers through cancellation and tree drain."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import TypeVar

from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeRunControl

_Result = TypeVar("_Result")


async def await_verification_worker(worker: Callable[[ProcessTreeRunControl], _Result]) -> _Result:
    """Cancellation requests physical stop and waits until the owner returns.

    The worker must use the supplied control in its real process owner. This
    wrapper never issues cleanup permission; the verification group owns that
    decision. In particular, a drain error escapes unchanged, including after
    repeated outer cancellation requests.
    """
    control = ProcessTreeRunControl()
    physical = asyncio.create_task(asyncio.to_thread(worker, control))
    try:
        return await asyncio.shield(physical)
    except asyncio.CancelledError:
        control.cancel()
        while not physical.done():
            try:
                await asyncio.shield(physical)
            except asyncio.CancelledError:
                continue
            except ProcessTreeCancelledError:
                # Only the expected owner-confirmed cancellation is harmless.
                # Drain errors and other failures escape unchanged.
                break
        with suppress(ProcessTreeCancelledError):
            physical.result()
        raise
