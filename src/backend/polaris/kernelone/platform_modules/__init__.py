"""Platform module solidification registry and gate contracts.

Sealed modules have fixed invariants and targeted pytest suites. Changes to a
sealed module require an explicit unfreeze and must re-pass the module gate
before cascade/bench gates may claim progress. This exists to stop the
R116–R153 pattern of infinite linear defect chasing without durable module
boundaries.
"""

from __future__ import annotations

from polaris.kernelone.platform_modules.registry import (
    MODULE_CASCADE_ORDER,
    PLATFORM_MODULES,
    PlatformModuleRecord,
    PlatformModuleStatus,
    get_module,
    list_modules,
    modules_by_status,
)

__all__ = [
    "MODULE_CASCADE_ORDER",
    "PLATFORM_MODULES",
    "PlatformModuleRecord",
    "PlatformModuleStatus",
    "get_module",
    "list_modules",
    "modules_by_status",
]
