"""Runtime health checks for Polaris backend.

This module provides runtime health status checks.
No HTTP semantics — callers map domain exceptions to HTTP at the delivery boundary.

.. deprecated::
    Functions have been moved to ``polaris.application.health``.
    Import from there for new code; this module re-exports for backward compatibility.
"""

from __future__ import annotations

# Re-export from new location for backward compatibility
# Use explicit import-as to avoid false-positive F401 (unused import)
from polaris.application.health import (
    _resolve_pm_runtime_binding as _resolve_pm_runtime_binding,
    _resolve_pm_runtime_provider_kind as _resolve_pm_runtime_provider_kind,
    build_runtime_issues as build_runtime_issues,
    check_backend_available as check_backend_available,
    get_lancedb_status as get_lancedb_status,
    log_backend_error as log_backend_error,
    require_lancedb as require_lancedb,
)
