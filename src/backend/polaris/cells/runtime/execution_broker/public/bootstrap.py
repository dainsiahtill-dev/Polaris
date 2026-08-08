"""Bootstrap-only composition surface for managed-process dependency ports."""

from __future__ import annotations

from polaris.cells.runtime.execution_broker.internal.managed_process_ports import (
    bind_managed_process_ports as _bind_managed_process_ports,
)
from polaris.cells.runtime.execution_broker.internal.project_verification_authority import (
    bind_project_verification_execution_authority_port as _bind_project_verification_execution_authority_port,
)
from polaris.cells.runtime.execution_broker.public.contracts import ManagedProcessPortsV1
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ProjectVerificationExecutionAuthorityPortV1,
)


def bind_managed_process_ports(ports: ManagedProcessPortsV1) -> None:
    """Bind managed-process ports exactly once during application bootstrap."""

    _bind_managed_process_ports(ports)


def bind_project_verification_execution_authority_port(
    port: ProjectVerificationExecutionAuthorityPortV1,
) -> None:
    """Bind exact CE/JobToken execution authority during bootstrap."""

    _bind_project_verification_execution_authority_port(port)


__all__ = ["bind_managed_process_ports", "bind_project_verification_execution_authority_port"]
