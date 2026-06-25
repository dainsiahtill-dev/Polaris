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

from polaris.cells.audit.verdict.public.contracts import AuditVerdictResultV1, QueryAuditVerdictV1

_PUBLIC_NAMES = (
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
    "query_audit_verdict",
    "list_artifact_keys",
)

__all__ = list(_PUBLIC_NAMES)


def _count_by_status(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown").strip() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _verdict_from_reviews(reviews: list[dict[str, Any]]) -> str | None:
    verdicts = [str(item.get("verdict") or "").strip() for item in reviews if str(item.get("verdict") or "").strip()]
    if not verdicts:
        return None
    if any(item == "rejected" for item in verdicts):
        return "rejected"
    if all(item == "approved" for item in verdicts):
        return "approved"
    return verdicts[-1]


def query_audit_verdict(query: QueryAuditVerdictV1) -> AuditVerdictResultV1:
    """Read audit verdict/review-gate evidence through the public Cell boundary."""

    try:
        gate = __getattr__("get_review_gate")()
        changes = [item.to_dict() for item in gate.get_all_changes()]
        reviews = [item.to_dict() for item in gate.get_all_reviews()]
        if query.task_id:
            changes = [item for item in changes if str(item.get("task_id") or "") == query.task_id]
            reviews = [item for item in reviews if str(item.get("task_id") or "") == query.task_id]
        verdict = _verdict_from_reviews(reviews)
        task_status = gate.get_task_review_status(query.task_id) if query.task_id else None
        details: dict[str, Any] = {
            "source": "audit.verdict.public.query_audit_verdict",
            "task_id": query.task_id or "",
            "include_artifacts": query.include_artifacts,
            "change_count": len(changes),
            "review_count": len(reviews),
            "change_status_counts": _count_by_status(changes),
            "review_status_counts": _count_by_status(reviews),
            "task_review_status": task_status,
            "changes": changes[:20],
            "reviews": reviews[:20],
        }
        status = "available" if changes or reviews else "empty"
        return AuditVerdictResultV1(
            ok=True,
            status=status,
            workspace=query.workspace,
            run_id=query.run_id or "workspace",
            verdict=verdict,
            details=details,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        return AuditVerdictResultV1(
            ok=False,
            status="unavailable",
            workspace=query.workspace,
            run_id=query.run_id or "workspace",
            details={"source": "audit.verdict.public.query_audit_verdict"},
            error_code="audit_verdict_query_failed",
            error_message=str(exc),
        )


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
