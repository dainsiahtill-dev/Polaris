"""Bootstrap binding surface for managed-process receipt ownership."""

from __future__ import annotations

from .managed_process_lifecycle import (
    ManagedProcessReceiptOwnerPortV1,
    _bind_managed_process_receipt_owner_port,
    _clear_managed_process_receipt_owner_port,
    _swap_managed_process_receipt_owner_port,
)


def bind_managed_process_receipt_owner_port(port: ManagedProcessReceiptOwnerPortV1) -> None:
    _bind_managed_process_receipt_owner_port(port)


def clear_managed_process_receipt_owner_port(port: ManagedProcessReceiptOwnerPortV1) -> None:
    _clear_managed_process_receipt_owner_port(port)


def swap_managed_process_receipt_owner_port(
    port: ManagedProcessReceiptOwnerPortV1,
) -> ManagedProcessReceiptOwnerPortV1 | None:
    """Bind *port* for one scope and return the owner it replaced.

    The production owner is bound for the process lifetime and a conflicting
    rebind fails closed.  A scoped owner replaces it for its own scope and
    restores it afterwards.
    """
    return _swap_managed_process_receipt_owner_port(port)


__all__ = [
    "bind_managed_process_receipt_owner_port",
    "clear_managed_process_receipt_owner_port",
    "swap_managed_process_receipt_owner_port",
]
