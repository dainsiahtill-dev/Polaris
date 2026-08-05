"""Private single-assignment registry for managed-process dependency ports."""

from __future__ import annotations

from threading import Lock

from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionBrokerError,
    ManagedProcessPortsV1,
)

_managed_process_ports: ManagedProcessPortsV1 | None = None
_managed_process_ports_lock = Lock()


def bind_managed_process_ports(ports: ManagedProcessPortsV1) -> None:
    """Bind one exact dependency bundle; only the public bootstrap may call this."""

    if type(ports) is not ManagedProcessPortsV1:
        raise ExecutionBrokerError(
            "managed process ports must be an exact ManagedProcessPortsV1 instance",
            code="execution_broker.invalid_managed_process_ports",
        )
    global _managed_process_ports
    with _managed_process_ports_lock:
        current = _managed_process_ports
        if current is None:
            _managed_process_ports = ports
            return
        if current is ports:
            return
    raise ExecutionBrokerError(
        "managed process ports are already bound to a different object",
        code="execution_broker.managed_process_ports_conflicting_rebind",
    )


def get_managed_process_ports() -> ManagedProcessPortsV1:
    """Return the bound bundle or fail closed before managed execution."""

    with _managed_process_ports_lock:
        ports = _managed_process_ports
    if ports is None:
        raise ExecutionBrokerError(
            "managed process ports are not bound",
            code="execution_broker.managed_process_ports_unbound",
        )
    return ports


__all__ = ["bind_managed_process_ports", "get_managed_process_ports"]
