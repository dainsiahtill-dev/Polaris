"""KernelOne abstraction ports.

This module defines the stable interfaces (Ports) that KernelOne uses to
communicate with Cells. This maintains the ACGA 2.0 dependency direction:
KernelOne defines abstract interfaces, Cells provide concrete implementations.

Architecture:
    +-------------------+       +-------------------+
    |   KernelOne       |       |      Cells        |
    |   (Platform)     |       |  (Business Logic) |
    +-------------------+       +-------------------+
              |                         |
              v                         v
    +-------------------+       +-------------------+
    | kernelone/ports/ |       | cells/adapters/  |
    | - IRoleProvider  |       | - RoleProviderAdapter
    | - IBusPort       |       | - BusPortAdapter  |
    | - IAlignmentSvc  |       | - AlignmentAdapter|
    +-------------------+       +-------------------+

Usage:
    from polaris.kernelone.ports import IRoleProvider
    from polaris.cells.adapters.kernelone import RoleProviderAdapter

    port: IRoleProvider = RoleProviderAdapter()
    normalized = port.normalize_role_alias("auditor")  # Returns "qa"
"""

from polaris.kernelone.ports.alignment import IAlignmentService
from polaris.kernelone.ports.bus_port import IAgentBusPort, IBusPort
from polaris.kernelone.ports.role_provider import IRoleProvider
from polaris.kernelone.ports.role_tool_integration import IRoleToolIntegration

__all__ = [
    "IAgentBusPort",
    "IAlignmentService",
    "IBusPort",
    "IRoleProvider",
    "IRoleToolIntegration",
]
