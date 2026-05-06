"""Public service exports for `audit.verdict` cell.

This module provides public-facing APIs that delegate to internal implementations.
All internal imports are lazy-loaded to maintain proper architectural boundaries.

IMPORTANT: Do NOT pre-declare module-level variables for lazy-loaded names
(e.g., "X: type | None = None"). Python evaluates these at import time,
which triggers __getattr__ but then uses the pre-declared None value instead
of the dynamically loaded class. This is a known Python __getattr__ gotcha.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ARTIFACT_REGISTRY",
    "LEGACY_KEY_MAPPING",
    "LEGACY_PATH_ALIASES",
    "ArtifactService",
    "AuditContext",
    "CodeChange",
    "IndependentAuditService",
    "Review",
    "ReviewEventType",
    "ReviewGate",
    "create_artifact_service",
    "get_artifact_key",
    "get_artifact_path",
    "get_review_gate",
    "list_artifact_keys",
]


def __getattr__(name: str) -> Any:
    """Lazy import dispatcher for internal modules."""
    if name in {
        "ARTIFACT_REGISTRY",
        "LEGACY_KEY_MAPPING",
        "LEGACY_PATH_ALIASES",
        "ArtifactService",
        "create_artifact_service",
        "get_artifact_key",
        "get_artifact_path",
        "list_artifact_keys",
    }:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.audit.verdict.internal.artifact_service import (
            ARTIFACT_REGISTRY,
            LEGACY_KEY_MAPPING,
            LEGACY_PATH_ALIASES,
            ArtifactService,
            create_artifact_service,
            get_artifact_key,
            get_artifact_path,
            list_artifact_keys,
        )

        g = globals()
        g["ARTIFACT_REGISTRY"] = ARTIFACT_REGISTRY
        g["LEGACY_KEY_MAPPING"] = LEGACY_KEY_MAPPING
        g["LEGACY_PATH_ALIASES"] = LEGACY_PATH_ALIASES
        g["ArtifactService"] = ArtifactService
        g["create_artifact_service"] = create_artifact_service
        g["get_artifact_key"] = get_artifact_key
        g["get_artifact_path"] = get_artifact_path
        g["list_artifact_keys"] = list_artifact_keys
        return g[name]

    if name in {"AuditContext", "IndependentAuditService"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.audit.verdict.internal.independent_audit_service import (
            AuditContext,
            IndependentAuditService,
        )

        g = globals()
        g["AuditContext"] = AuditContext
        g["IndependentAuditService"] = IndependentAuditService
        return g[name]

    if name in {"CodeChange", "Review", "ReviewEventType", "ReviewGate", "get_review_gate"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.audit.verdict.internal.review_gate import (
            CodeChange,
            Review,
            ReviewEventType,
            ReviewGate,
            get_review_gate,
        )

        g = globals()
        g["CodeChange"] = CodeChange
        g["Review"] = Review
        g["ReviewEventType"] = ReviewEventType
        g["ReviewGate"] = ReviewGate
        g["get_review_gate"] = get_review_gate
        return g[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
