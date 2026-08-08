"""Bootstrap-only binding surface for completion owner facts."""

from polaris.cells.factory.verification_guard.internal.project_completion_authority import (
    bind_project_completion_owner_observation_port,
    build_project_completion_contract_observation,
)
from polaris.cells.factory.verification_guard.internal.project_physical_evidence import (
    bind_project_completion_physical_evidence_port,
    build_project_completion_physical_evidence_intent,
)

__all__ = [
    "bind_project_completion_owner_observation_port",
    "bind_project_completion_physical_evidence_port",
    "build_project_completion_contract_observation",
    "build_project_completion_physical_evidence_intent",
]
