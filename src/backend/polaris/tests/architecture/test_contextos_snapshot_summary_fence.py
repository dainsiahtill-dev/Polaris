"""Architecture guard for ContextOS snapshot summary ownership."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.context_os.snapshot_summary import SnapshotSummaryView

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROJECTION_BUILDER = (
    BACKEND_ROOT
    / "polaris"
    / "cells"
    / "roles"
    / "kernel"
    / "internal"
    / "context_gateway"
    / "projection_dict_builder.py"
)


def test_snapshot_summary_has_canonical_module() -> None:
    """Snapshot summaries are owned outside deprecated ContextOS models."""
    assert SnapshotSummaryView.__module__ == "polaris.kernelone.context.context_os.snapshot_summary"


def test_role_projection_does_not_import_deprecated_snapshot_summary() -> None:
    """Role projection must not import SnapshotSummaryView from deprecated models."""
    source = PROJECTION_BUILDER.read_text(encoding="utf-8")
    assert "context_os.models import SnapshotSummaryView" not in source
    assert "context_os.snapshot_summary import SnapshotSummaryView" in source
