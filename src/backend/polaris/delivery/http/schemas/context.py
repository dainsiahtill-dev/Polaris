"""HTTP request/response schemas for the /v2/context/admin/* endpoints.

These schemas back the opt-in admin surface for
``ContextStoreRetention`` — they are intentionally JSON-friendly
(``epoch`` ints for timestamps) so the admin UI can display them
without any extra parsing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SweepRequest(BaseModel):
    """Request body for ``POST /v2/context/admin/sweep``.

    Attributes:
        triggers: Optional list of trigger tags to attach to the report.
            Empty list (the default) is treated as an unconditional
            manual sweep.
    """

    triggers: list[str] = Field(default_factory=list)


class SweepReportResponse(BaseModel):
    """Response body for ``POST /v2/context/admin/sweep``.

    Mirrors :class:`polaris.kernelone.llm.engine.context_store_retention.SweepReport`
    with JSON-friendly types (epoch ints for timestamps).
    """

    scanned_files: int
    removed_files: int
    removed_bytes: int
    kept_files: int
    total_bytes_after: int
    elapsed_ms: int
    triggers: list[str] = Field(default_factory=list)


class ContextStoreStatsResponse(BaseModel):
    """Response body for ``GET /v2/context/admin/stats``."""

    workspace: str
    contexts_root: str
    file_count: int
    total_bytes: int
    oldest_mtime: float | None
    newest_mtime: float | None
    config: dict[str, Any] = Field(default_factory=dict)
    last_sweep_at: float
    last_sweep_report: dict[str, Any] | None = None


__all__ = [
    "ContextStoreStatsResponse",
    "SweepReportResponse",
    "SweepRequest",
]
