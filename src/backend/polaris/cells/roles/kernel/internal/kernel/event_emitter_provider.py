"""Event emitter provider for Role Kernel execution flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from polaris.cells.roles.kernel.internal.kernel.error_handler import KernelEventEmitter
from polaris.cells.roles.kernel.services.contracts import IEventEmitter

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def get_kernel_event_emitter(kernel: RoleExecutionKernel) -> IEventEmitter:
    """Return the injected or lazily-created event emitter for a kernel turn."""
    injected = getattr(kernel, "_injected_event_emitter", None)
    if injected is not None:
        return cast(IEventEmitter, injected)

    emitter = getattr(kernel, "_event_emitter", None)
    if emitter is None:
        emitter = KernelEventEmitter()
        kernel._event_emitter = emitter
    return cast(IEventEmitter, emitter)


__all__ = ["get_kernel_event_emitter"]
