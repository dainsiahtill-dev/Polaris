"""Cells adapters for KernelOne ports.

This module provides concrete implementations of KernelOne port interfaces.
Each adapter bridges between KernelOne's abstract interfaces and Cells' actual
implementations.

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
    | - IRoleToolInt   |       | - RoleToolIntAdapter|
    +-------------------+       +-------------------+

Usage:
    from polaris.kernelone.ports import IRoleProvider, IAlignmentService, IRoleToolIntegration
    from polaris.cells.adapters.kernelone import (
        RoleProviderAdapter,
        AlignmentServiceAdapter,
        RoleToolIntegrationAdapter,
    )

    role_port: IRoleProvider = RoleProviderAdapter()
    alignment_port: IAlignmentService = AlignmentServiceAdapter()
    tool_port: IRoleToolIntegration = RoleToolIntegrationAdapter()
    normalized = role_port.normalize_role_alias("auditor")  # Returns "qa"
    integration = tool_port.get_role_integration("pm", "/path/to/workspace")
"""

from polaris.cells.adapters.kernelone.alignment_adapter import AlignmentServiceAdapter
from polaris.cells.adapters.kernelone.bus_adapter import KernelOneBusPortAdapter
from polaris.cells.adapters.kernelone.role_provider_adapter import RoleProviderAdapter
from polaris.cells.adapters.kernelone.role_tool_integration_adapter import (
    RoleToolIntegrationAdapter,
)

__all__ = [
    "AlignmentServiceAdapter",
    "KernelOneBusPortAdapter",
    "RoleProviderAdapter",
    "RoleToolIntegrationAdapter",
]
