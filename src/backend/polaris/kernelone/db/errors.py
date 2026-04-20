from __future__ import annotations


class KernelDatabaseError(RuntimeError):
    """Base error for KernelDatabase."""


class DatabasePathError(KernelDatabaseError):
    """Raised when database path resolution fails."""


class DatabasePolicyError(KernelDatabaseError):
    """Raised when database path violates storage policy."""


class DatabaseDriverNotAvailableError(KernelDatabaseError):
    """Raised when required database driver is missing."""


class DatabaseConnectionError(KernelDatabaseError):
    """Raised when database connection or engine creation fails."""
