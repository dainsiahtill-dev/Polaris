"""Module-level constants for task-market lifecycle operations."""

from __future__ import annotations

_IN_PROGRESS_STATUSES = {"in_design", "in_execution", "in_qa"}
_NON_CONSUMING_REQUEUE_ERROR_CODES = frozenset({"SCOPE_CONFLICT"})
_LEGACY_RESOLVED_REOPEN_SOURCES: frozenset[str] = frozenset()
# Statuses a depends_on dependency can never recover from (subset of
# models.TERMINAL_STATUSES minus "resolved"): dependents must cascade,
# not strand.
_DEPENDENCY_TERMINAL_FAILURE_STATUSES = frozenset({"rejected", "dead_letter"})
_REQUEUE_CONTEXT_PAYLOAD_KEYS = frozenset(
    {
        "amendment_request",
        "director_interface_discrepancy_retry",
        "interface_discrepancy_context",
        "qa_local_repair_context",
        "task_boundary_discrepancy_evidence",
        "task_boundary_interface_discrepancy_retry",
    }
)
_LOCAL_RETRY_BACKOFF_BASE_SECONDS = 1.0
_LOCAL_RETRY_BACKOFF_MAX_SECONDS = 60.0
_LOCAL_RETRY_MAX_ROUNDS = 6
_LOCAL_RETRY_PARK_METADATA_KEY = "task_local_retry_control_plane_park"

__all__ = [
    "_DEPENDENCY_TERMINAL_FAILURE_STATUSES",
    "_IN_PROGRESS_STATUSES",
    "_LEGACY_RESOLVED_REOPEN_SOURCES",
    "_LOCAL_RETRY_BACKOFF_BASE_SECONDS",
    "_LOCAL_RETRY_BACKOFF_MAX_SECONDS",
    "_LOCAL_RETRY_MAX_ROUNDS",
    "_LOCAL_RETRY_PARK_METADATA_KEY",
    "_NON_CONSUMING_REQUEUE_ERROR_CODES",
    "_REQUEUE_CONTEXT_PAYLOAD_KEYS",
]
