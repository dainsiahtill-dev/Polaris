"""Bootstrap-only composition surface for managed-process dependency ports."""

from __future__ import annotations

from polaris.cells.runtime.execution_broker.internal.managed_process_ports import (
    bind_managed_process_ports as _bind_managed_process_ports,
)
from polaris.cells.runtime.execution_broker.public.contracts import ManagedProcessPortsV1


def bind_managed_process_ports(ports: ManagedProcessPortsV1) -> None:
    """Bind managed-process ports exactly once during application bootstrap."""

    _bind_managed_process_ports(ports)


__all__ = ["bind_managed_process_ports"]
