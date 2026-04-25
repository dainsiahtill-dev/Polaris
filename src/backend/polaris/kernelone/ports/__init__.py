"""KernelOne abstraction ports.

This module defines the stable interfaces (Ports) that KernelOne uses to
communicate with Cells and Infrastructure. This maintains the ACGA 2.0
dependency direction: KernelOne defines abstract interfaces, upper layers
provide concrete implementations.

Architecture::

    +-------------------+       +-------------------+       +-------------------+
    |   KernelOne       |       |      Cells        |       | Infrastructure    |
    |   (Platform)      |       |  (Business Logic) |       |  (Adapters)       |
    +-------------------+       +-------------------+       +-------------------+
              |                         |                         |
              v                         v                         v
    +-------------------+       +-------------------+       +-------------------+
    | kernelone/ports/  |       | cells/adapters/   |       | infra/adapters/   |
    | - IRoleProvider   |       | - RoleProviderAdpt|       | - LocalFSAdapter  |
    | - IBusPort        |       | - BusPortAdapter  |       | - DIContainer     |
    | - IAlignmentSvc   |       | - AlignmentAdpt   |       | - ProviderManager |
    | - IContainerPort  |       | - LayoutResolver  |       |                   |
    | - IFSAdapterFact  |       |                   |       |                   |
    | - IProviderRegPt  |       |                   |       |                   |
    | - ILayoutResolver |       |                   |       |                   |
    +-------------------+       +-------------------+       +-------------------+

Usage::

    from polaris.kernelone.ports import IRoleProvider
    from polaris.cells.adapters.kernelone import RoleProviderAdapter

    port: IRoleProvider = RoleProviderAdapter()
    normalized = port.normalize_role_alias("auditor")  # Returns "qa"
"""

from polaris.kernelone.ports.alignment import IAlignmentService
from polaris.kernelone.ports.bus_port import IAgentBusPort, IBusPort
from polaris.kernelone.ports.container import IContainerPort
from polaris.kernelone.ports.layout import ILayoutResolverPort, IStorageRoots
from polaris.kernelone.ports.provider_registry import IProviderRegistryPort
from polaris.kernelone.ports.role_provider import IRoleProvider
from polaris.kernelone.ports.role_tool_integration import IRoleToolIntegration
from polaris.kernelone.ports.storage import IFileSystemAdapterFactory

__all__ = [
    "IAgentBusPort",
    "IAlignmentService",
    "IBusPort",
    "IContainerPort",
    "IFileSystemAdapterFactory",
    "ILayoutResolverPort",
    "IProviderRegistryPort",
    "IRoleProvider",
    "IRoleToolIntegration",
    "IStorageRoots",
]
