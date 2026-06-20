"""Typed ``CapabilityHandler`` dispatch seam for ``roles.runtime``.

This internal package hosts the additive, type-safe scaffolding that
``execute_role_capability_invocation`` will incrementally delegate to:

* :class:`CapabilityHandler` — three-method Strategy protocol per capability.
* :class:`CapabilityDeps` and its twelve Protocol-typed service ports —
  the zero-``Any`` replacement for the dispatcher's ``*_service`` kwargs.
* :class:`CapabilityHandlerRegistry` — frozen ``(capability_id, owner_cell,
  command_contract)`` -> handler table.
* :class:`CapabilityInvocationError` — coded rejection whose ``code`` mirrors the
  dispatcher's existing ``error_code`` literals byte-for-byte.

The Phase-0 characterization snapshot lives in the private
:mod:`._oracle` module (identity tuples + error-code literals) and is imported
on demand by the verification/fitness tests rather than re-exported here.
"""

from __future__ import annotations

from polaris.cells.roles.runtime.internal.capability.deps import (
    ArchitectDesignPort,
    BlueprintPort,
    BudgetGuardPort,
    CapabilityDeps,
    CodeIntelligencePort,
    DirectorExecutionPort,
    LlmControlPlanePort,
    PermissionPort,
    QaAuditPort,
    RuntimeProjectionPort,
    TaskMarketPort,
    VerificationGuardPort,
    WorkspaceGuardPort,
)
from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.internal.capability.protocol import CapabilityHandler
from polaris.cells.roles.runtime.internal.capability.registry import (
    CapabilityHandlerRegistry,
    CapabilityIdentity,
)
from polaris.cells.roles.runtime.internal.capability.registry_default import (
    default_capability_registry,
)

__all__ = [
    "ArchitectDesignPort",
    "BlueprintPort",
    "BudgetGuardPort",
    "CapabilityDeps",
    "CapabilityHandler",
    "CapabilityHandlerRegistry",
    "CapabilityIdentity",
    "CapabilityInvocationError",
    "CodeIntelligencePort",
    "DirectorExecutionPort",
    "LlmControlPlanePort",
    "PermissionPort",
    "QaAuditPort",
    "RuntimeProjectionPort",
    "TaskMarketPort",
    "VerificationGuardPort",
    "WorkspaceGuardPort",
    "default_capability_registry",
]
