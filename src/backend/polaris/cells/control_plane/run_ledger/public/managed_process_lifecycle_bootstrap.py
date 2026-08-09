"""Bootstrap binding surface for managed-process receipt ownership."""

from __future__ import annotations

from .managed_process_lifecycle import (
    ManagedProcessReceiptOwnerPortV1,
    _bind_managed_process_receipt_owner_port,
    _clear_managed_process_receipt_owner_port,
)


def bind_managed_process_receipt_owner_port(port: ManagedProcessReceiptOwnerPortV1) -> None:
    _bind_managed_process_receipt_owner_port(port)


def clear_managed_process_receipt_owner_port(port: ManagedProcessReceiptOwnerPortV1) -> None:
    _clear_managed_process_receipt_owner_port(port)


__all__ = [
    "bind_managed_process_receipt_owner_port",
    "clear_managed_process_receipt_owner_port",
]
