"""Architecture fence for the context.engine search gateway."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
SEARCH_GATEWAY = BACKEND_ROOT / "polaris" / "cells" / "context" / "engine" / "internal" / "search_gateway.py"


def test_search_gateway_has_no_ad_hoc_document_ingest_api() -> None:
    """Context search is graph/catalog backed; arbitrary document ingest is retired."""
    source = SEARCH_GATEWAY.read_text(encoding="utf-8")

    assert "def add_documents" not in source
    assert "add_documents" not in source
    assert "DeprecationWarning" not in source
    assert "warnings.warn" not in source
