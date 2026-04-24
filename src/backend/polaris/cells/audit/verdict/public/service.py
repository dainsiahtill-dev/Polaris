"""Public service exports for `audit.verdict` cell.

This module provides public-facing APIs that delegate to internal implementations.
All internal imports are lazy-loaded to maintain proper architectural boundaries.
"""

from __future__ import annotations

__all__ = [
    "ARTIFACT_REGISTRY",  # noqa: F822
    "LEGACY_KEY_MAPPING",  # noqa: F822
    "LEGACY_PATH_ALIASES",  # noqa: F822
    "ArtifactService",  # noqa: F822
    "AuditContext",  # noqa: F822
    "CodeChange",  # noqa: F822
    "IndependentAuditService",  # noqa: F822
    "Review",  # noqa: F822
    "ReviewEventType",  # noqa: F822
    "ReviewGate",  # noqa: F822
    "create_artifact_service",  # noqa: F822
    "get_artifact_key",  # noqa: F822
    "get_artifact_path",  # noqa: F822
    "get_review_gate",  # noqa: F822
    "list_artifact_keys",  # noqa: F822
]


def __getattr__(name: str):
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
            ARTIFACT_REGISTRY as _ar,
            LEGACY_KEY_MAPPING as _lkm,
            LEGACY_PATH_ALIASES as _lpa,
            ArtifactService as _as,
            create_artifact_service as _cas,
            get_artifact_key as _gak,
            get_artifact_path as _gap,
            list_artifact_keys as _lak,
        )

        g = globals()
        g["ARTIFACT_REGISTRY"] = _ar
        g["LEGACY_KEY_MAPPING"] = _lkm
        g["LEGACY_PATH_ALIASES"] = _lpa
        g["ArtifactService"] = _as
        g["create_artifact_service"] = _cas
        g["get_artifact_key"] = _gak
        g["get_artifact_path"] = _gap
        g["list_artifact_keys"] = _lak
        return g[name]

    if name in {"AuditContext", "IndependentAuditService"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.audit.verdict.internal.independent_audit_service import (
            AuditContext as _ac,
            IndependentAuditService as _ias,
        )

        g = globals()
        g["AuditContext"] = _ac
        g["IndependentAuditService"] = _ias
        return g[name]

    if name in {"CodeChange", "Review", "ReviewEventType", "ReviewGate", "get_review_gate"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.audit.verdict.internal.review_gate import (
            CodeChange as _cc,
            Review as _r,
            ReviewEventType as _ret,
            ReviewGate as _rg,
            get_review_gate as _grg,
        )

        g = globals()
        g["CodeChange"] = _cc
        g["Review"] = _r
        g["ReviewEventType"] = _ret
        g["ReviewGate"] = _rg
        g["get_review_gate"] = _grg
        return g[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
