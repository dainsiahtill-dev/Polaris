"""Resource, database, and role data-store errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class ResourceError(KernelOneError):
    """Resource-related errors.

    Raised when a resource is unavailable, exhausted, or cannot be accessed.

    Attributes:
        resource_type: Type of the resource (file, database, network, etc.).
        resource_id: Identifier of the resource.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "RESOURCE_ERROR",
        resource_type: str = "",
        resource_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.resource_type = resource_type
        self.resource_id = resource_id
        if resource_type:
            self.details["resource_type"] = resource_type
        if resource_id:
            self.details["resource_id"] = resource_id


class FileNotFoundError(ResourceError):
    """File not found.

    Raised when a required file does not exist.
    Note: This shadows Python's built-in FileNotFoundError intentionally
    for unified error handling within KernelOne.
    """

    def __init__(
        self,
        message: str,
        *,
        file_path: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="FILE_NOT_FOUND_ERROR",
            resource_type="file",
            resource_id=file_path,
            **kwargs,
        )
        self.file_path = file_path


class StateNotFoundError(ResourceError):
    """State not found in persistence layer.

    Raised when attempting to load state that doesn't exist.
    """

    def __init__(
        self,
        message: str,
        *,
        state_key: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="STATE_NOT_FOUND_ERROR",
            resource_type="state",
            resource_id=state_key,
            **kwargs,
        )


class EvidenceNotFoundError(ResourceError):
    """Evidence not found in audit store.

    Raised when attempting to retrieve evidence that doesn't exist.
    """

    def __init__(
        self,
        message: str,
        *,
        evidence_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVIDENCE_NOT_FOUND_ERROR",
            resource_type="evidence",
            resource_id=evidence_id,
            **kwargs,
        )


class DatabaseError(ResourceError):
    """Database-related errors.

    Raised when database operations fail.

    Attributes:
        database_name: Name of the database.
        operation: The database operation that failed.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "DATABASE_ERROR",
        database_name: str = "",
        operation: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code=code,
            resource_type="database",
            resource_id=database_name,
            **kwargs,
        )
        self.database_name = database_name
        self.operation = operation
        if operation:
            self.details["operation"] = operation


class DatabasePathError(DatabaseError):
    """Database path resolution failed."""

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="DATABASE_PATH_ERROR",
            operation="path_resolution",
            **kwargs,
        )
        if path:
            self.details["path"] = path


class DatabasePolicyError(DatabaseError):
    """Database path violates storage policy."""

    def __init__(
        self,
        message: str,
        *,
        policy: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="DATABASE_POLICY_ERROR",
            operation="policy_check",
            **kwargs,
        )
        if policy:
            self.details["policy"] = policy


class DatabaseConnectionError(DatabaseError):
    """Database connection failed."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="DATABASE_CONNECTION_ERROR",
            operation="connect",
            **kwargs,
        )


class DatabaseDriverNotAvailableError(DatabaseError):
    """Database driver is missing."""

    def __init__(
        self,
        message: str,
        *,
        driver_name: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(
            message,
            code="DATABASE_DRIVER_NOT_AVAILABLE_ERROR",
            operation="driver_check",
            **kwargs,
        )
        if driver_name:
            self.details["driver_name"] = driver_name


class RoleDataStoreError(ResourceError):
    """Role data store error."""

    def __init__(
        self,
        message: str,
        *,
        role_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="ROLE_DATA_STORE_ERROR",
            resource_type="role_data",
            resource_id=role_id,
            **kwargs,
        )
