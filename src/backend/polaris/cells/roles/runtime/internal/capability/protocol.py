"""The ``CapabilityHandler`` Strategy protocol for the dispatch seam.

A capability handler decomposes one capability-identity branch of
``execute_role_capability_invocation`` into three single-responsibility steps:

* :meth:`validate` — reject a malformed payload / failed precondition by raising
  :class:`CapabilityInvocationError` with a code that mirrors the legacy
  ``error_code`` literal byte-for-byte. Returns ``None`` on success.
* :meth:`invoke` — call the owner cell's public contract (via the matching
  :class:`CapabilityDeps` port, or the cell's module-level function when the port
  is ``None``) and return the raw, cell-native result object.
* :meth:`map_result` — translate that raw result into the canonical
  :class:`RoleCapabilityInvocationResultV1`.

The non-uniform branches (``budget_reservation``, ``workspace_guard``,
``boundary_validation``) chain multi-stage validation; their handlers absorb that
divergence inside :meth:`validate`/:meth:`invoke` while keeping this three-method
surface stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityInvocationResultV1,
    )


@runtime_checkable
class CapabilityHandler(Protocol):
    """Strategy for executing a single capability-identity branch."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        """Validate the command payload/preconditions.

        Raises:
            CapabilityInvocationError: when the payload is malformed or a
                precondition fails; the raised ``code`` mirrors the dispatcher's
                legacy ``error_code`` literal byte-for-byte.
        """
        ...

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        """Invoke the owner cell's public contract and return its raw result."""
        ...

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        """Map the raw owner-cell result onto the canonical invocation result."""
        ...
