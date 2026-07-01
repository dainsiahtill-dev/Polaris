"""Domain models for Polaris backend.

This package contains domain entities and value objects that represent
the core business concepts of the Polaris system.
"""

from .config_snapshot import (
    ConfigSnapshot,
    ConfigSnapshotImmutableError,
    ConfigValidationResult,
    SourceType,
)

__all__ = [
    "ConfigSnapshot",
    "ConfigSnapshotImmutableError",
    "ConfigValidationResult",
    "SourceType",
]
