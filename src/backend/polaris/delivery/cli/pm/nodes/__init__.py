"""Role nodes for Polaris orchestration.

This module defines the role node protocol and implementations for
PM, ChiefEngineer, Director, and QA roles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.delivery.cli.pm.nodes.protocols import RoleNode

from polaris.delivery.cli.pm.nodes.base import BaseRoleNode, SequentialRoleNode
from polaris.delivery.cli.pm.nodes.coordinator import OrchestrationCoordinator, create_coordinator
from polaris.delivery.cli.pm.nodes.protocols import (
    OrchestrationConfig,
    OrchestrationState,
    RoleContext,
    RoleNode,
    RoleResult,
)

# Role node implementations
PMNode: type[RoleNode] | None = None
ChiefEngineerNode: type[RoleNode] | None = None
DirectorNode: type[RoleNode] | None = None
QANode: type[RoleNode] | None = None

try:
    from polaris.delivery.cli.pm.nodes.pm_node import PMNode as _PMNode

    PMNode = _PMNode
except ImportError:
    pass

try:
    from polaris.delivery.cli.pm.nodes.chief_engineer_node import ChiefEngineerNode as _CE  # noqa: N814

    ChiefEngineerNode = _CE
except ImportError:
    pass

try:
    from polaris.delivery.cli.pm.nodes.director_node import DirectorNode as _DN  # noqa: N814

    DirectorNode = _DN
except ImportError:
    pass

try:
    from polaris.delivery.cli.pm.nodes.qa_node import QANode as _QA  # noqa: N814

    QANode = _QA
except ImportError:
    pass

__all__ = [
    # Base classes
    "BaseRoleNode",
    "ChiefEngineerNode",
    "DirectorNode",
    "OrchestrationConfig",
    # Coordinator
    "OrchestrationCoordinator",
    "OrchestrationState",
    # Role nodes
    "PMNode",
    "QANode",
    # Protocols
    "RoleContext",
    "RoleNode",
    "RoleResult",
    "SequentialRoleNode",
    "create_coordinator",
]
