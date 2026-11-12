"""Shared helpers for the kernel tool-batch inventory contract.

The kernelone tool-batch inventory contract has two supported forms.  An
inventory capture declares the invocations, and an executable batch declares
the same invocations again in their execution groups.  The executor requires
the executable form, so a hand-built batch has to bind both, and the group
payload has to stay identical to the declared invocation.

The partitioning rule is the one the kernel's own decoder uses: the declared
``execution_mode`` decides the group.  These helpers keep the test batches in
lockstep with that rule instead of restating it per module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from polaris.cells.roles.kernel.public.turn_contracts import ToolBatch, ToolInvocation

GROUP_BY_EXECUTION_MODE: dict[str, str] = {
    "readonly_parallel": "parallel_readonly",
    "readonly_serial": "readonly_serial",
    "write_serial": "serial_writes",
    "async_receipt": "async_receipts",
}


def _execution_mode(invocation: Any) -> str:
    """Read the declared execution mode from a model instance or a mapping."""
    mode = getattr(invocation, "execution_mode", None)
    if mode is None and isinstance(invocation, Mapping):
        mode = invocation.get("execution_mode")
    if mode is None:
        return ""
    return str(getattr(mode, "value", mode))


def execution_groups(invocations: Iterable[Any]) -> dict[str, list[Any]]:
    """Partition the declared invocations into their execution groups."""
    groups: dict[str, list[Any]] = {name: [] for name in GROUP_BY_EXECUTION_MODE.values()}
    for invocation in invocations:
        group = GROUP_BY_EXECUTION_MODE.get(_execution_mode(invocation))
        if group is None:
            continue
        groups[group].append(invocation)
    return groups


def tool_batch_with_groups(batch_id: Any, invocations: Iterable[Any]) -> dict[str, Any]:
    """Build the executable wire form of a tool batch.

    The batch declares its invocations once for the inventory and once for the
    execution groups, with identical member payloads.
    """
    declared = list(invocations)
    return {
        "batch_id": batch_id,
        "invocations": declared,
        **execution_groups(declared),
    }


def tool_batch_model_with_groups(batch_id: Any, invocations: Iterable[ToolInvocation]) -> ToolBatch:
    """Build the executable ``ToolBatch`` model with its groups bound.

    The executor reads the executable form of the batch, so the model has to
    carry the same invocations in their execution groups.
    """
    declared = list(invocations)
    groups = execution_groups(declared)
    return ToolBatch(
        batch_id=batch_id,
        invocations=declared,
        parallel_readonly=groups["parallel_readonly"],
        readonly_serial=groups["readonly_serial"],
        serial_writes=groups["serial_writes"],
        async_receipts=groups["async_receipts"],
    )
