"""Permission and testing-infrastructure errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class TestingInfrastructureError(KernelOneError):
    """Testing infrastructure error."""

    def __init__(
        self,
        message: str,
        *,
        infrastructure_component: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="TESTING_INFRASTRUCTURE_ERROR",
            **kwargs,
        )
        if infrastructure_component:
            self.details["infrastructure_component"] = infrastructure_component


class PermissionError(KernelOneError):
    """Permission-related errors."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "PERMISSION_ERROR",
        permission_name: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        if permission_name:
            self.details["permission_name"] = permission_name


class PermissionServiceError(PermissionError):
    """Permission service error."""

    def __init__(
        self,
        message: str,
        *,
        service_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PERMISSION_SERVICE_ERROR",
            **kwargs,
        )
        if service_name:
            self.details["service_name"] = service_name
