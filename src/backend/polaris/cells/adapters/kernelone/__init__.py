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
    +-------------------+       +-------------------+

Usage:
    from polaris.kernelone.ports import IRoleProvider
    from polaris.cells.adapters.kernelone import RoleProviderAdapter

    port: IRoleProvider = RoleProviderAdapter()
    normalized = port.normalize_role_alias("auditor")  # Returns "qa"
"""

from polaris.cells.adapters.kernelone.role_provider_adapter import RoleProviderAdapter
from polaris.cells.adapters.kernelone.bus_adapter import KernelOneBusPortAdapter

__all__ = [
    "RoleProviderAdapter",
    "KernelOneBusPortAdapter",
]
