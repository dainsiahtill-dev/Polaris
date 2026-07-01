"""Quality checker provider for Role Kernel execution flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker
from polaris.cells.roles.kernel.services.contracts import IQualityChecker

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel


def get_quality_checker(kernel: RoleExecutionKernel) -> IQualityChecker:
    """Return the injected or lazily-created quality checker for a kernel turn."""
    injected = getattr(kernel, "_injected_quality_checker", None)
    if injected is not None:
        return cast(IQualityChecker, injected)

    checker = getattr(kernel, "_quality_checker", None)
    if checker is None:
        checker = QualityChecker(kernel.workspace)
        kernel._quality_checker = checker
    return cast(IQualityChecker, checker)


__all__ = ["get_quality_checker"]
