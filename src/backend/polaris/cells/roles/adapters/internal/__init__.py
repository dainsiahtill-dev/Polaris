"""Role orchestration adapters.

Implements the ``RoleOrchestrationAdapter`` port from ``workflow_runtime``,
exposing PM / Architect / Chief Engineer / QA / Director as workflow-ready
adapters.  All adapter classes are re-exported here for internal consumers.

NOTE: the canonical role-adapter factory is registered once at import time by
``polaris.cells.roles.adapters.public.service`` (the only module that calls
``configure_orchestration_role_adapter_factory``).  Do NOT add a second
registration here — it would overwrite the public one with the weaker
``internal``-only factory that lacks role-token normalisation and the
DirectorAdapter graceful-fallback logic.
"""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.public.service import (
    RoleOrchestrationAdapter,
)

# ---------------------------------------------------------------------------
# Re-export adapter classes (no factory registration here — see note above)
# ---------------------------------------------------------------------------
from polaris.cells.roles.adapters.internal.architect_adapter import ArchitectAdapter
from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter
from polaris.cells.roles.adapters.internal.chief_engineer_adapter import (
    ChiefEngineerAdapter,
)
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter

# Make classes available as module attributes (not just via `from module import *`)
RoleOrchestrationAdapter = RoleOrchestrationAdapter
ArchitectAdapter = ArchitectAdapter
BaseRoleAdapter = BaseRoleAdapter
ChiefEngineerAdapter = ChiefEngineerAdapter
DirectorAdapter = DirectorAdapter
PMAdapter = PMAdapter
QAAdapter = QAAdapter

__all__ = [
    "ArchitectAdapter",
    "BaseRoleAdapter",
    "ChiefEngineerAdapter",
    "DirectorAdapter",
    "PMAdapter",
    "QAAdapter",
    "RoleOrchestrationAdapter",
]
