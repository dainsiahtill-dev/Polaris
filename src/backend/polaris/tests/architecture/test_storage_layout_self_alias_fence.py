"""Architecture fence for retired storage-layout self aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.cells.storage.layout.internal.layout_business as layout_business

BACKEND_ROOT = Path(__file__).resolve().parents[3]
LAYOUT_BUSINESS = BACKEND_ROOT / "polaris" / "cells" / "storage" / "layout" / "internal" / "layout_business.py"


def test_resolve_polaris_roots_remains_canonical_export() -> None:
    """The storage layout module must expose the real resolver function."""
    assert callable(layout_business.resolve_polaris_roots)
    assert "resolve_polaris_roots" in layout_business.__all__


def test_storage_layout_source_has_no_self_compat_alias() -> None:
    """Source-level fence blocks no-op compatibility self-aliases."""
    source = LAYOUT_BUSINESS.read_text(encoding="utf-8")
    assert "resolve_polaris_roots = resolve_polaris_roots" not in source
