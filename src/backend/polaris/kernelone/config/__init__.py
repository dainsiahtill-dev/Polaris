"""KernelOne configuration governance package.

Home of the ``KERNELONE_*`` environment flag registry
(``flag_registry.py``), enforced by
``polaris/tests/architecture/test_kernelone_flag_registry_fence.py``.
"""

from __future__ import annotations

from polaris.kernelone.config.flag_registry import (
    DYNAMIC_ENV_READ_ALLOWLIST,
    KERNELONE_FLAG_REGISTRY,
    FlagSpec,
    is_registered,
    registered_flag_names,
)

__all__ = [
    "DYNAMIC_ENV_READ_ALLOWLIST",
    "KERNELONE_FLAG_REGISTRY",
    "FlagSpec",
    "is_registered",
    "registered_flag_names",
]
