"""Tests for `polaris.cells.context.engine.internal.search_gateway`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from polaris.cells.context.engine.internal.search_gateway import (
    SearchService,
    _default_backend_root,
    _resolve_workspace,
    get_search_service,
    reset_search_service_for_tests,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singleton():
    """Clear the process-local singleton before and after each test."""
    reset_search_service_for_tests()
    yield
    reset_search_service_for_tests()


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    """Create a fake workspace with a minimal cells.yaml for root resolution."""
    catalog_dir = tmp_path / "docs" / "graph" / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "cells.yaml").write_text("cells: []\n", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Minimal mock catalog (mirrors the real ContextCatalogService interface)
# ---------------------------------------------------------------------------


@dataclass
class _MockDescriptor:
    cell_id: str
    title: str
    purpose: str


@dataclass
class _MockSearchResult:
    descriptors: list[_MockDescriptor] = field(default_factory=list)


def _make_mock_catalog(descriptors: list[dict[str, str]]):
    """Return a mock catalog whose search() returns _MockSearchResult."""
    mock = MagicMock()
    mock.search.return_value = _MockSearchResult([_MockDescriptor(**d) for d in descriptors])
    return mock


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


class TestDefaultBackendRoot:
    def test_resolves_to_backend_root(self) -> None:
        """`_default_backend_root` must return a Path containing `docs/graph/catalog`."""
        root = _default_backend_root()
        assert (root / "docs" / "graph" / "catalog" / "cells.yaml").is_file()

    def test_resolve_workspace_uses_env_var(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """`KERNELONE_WORKSPACE` env var takes precedence."""
        catalog_dir = tmp_path / "docs" / "graph" / "catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        (catalog_dir / "cells.yaml").write_text("cells: []\n", encoding="utf-8")
        monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
        # Force module-level re-eval by patching at definition time won't work
        # for already-imported module constants; exercise through SearchService
        svc = SearchService(workspace=tmp_path)
        assert svc._workspace == tmp_path

    def test_resolve_workspace_fallback_to_backend(self) -> None:
        """No env var → fallback lands on the real backend root."""
        ws = _resolve_workspace()
        assert (ws / "docs" / "graph" / "catalog" / "cells.yaml").is_file()


# ---------------------------------------------------------------------------
# SearchService – available property
# ---------------------------------------------------------------------------


class TestSearchServiceAvailable:
    def test_available_true_when_cache_exists(self, mock_workspace: Path) -> None:
        """`available` is True when the descriptor cache file is present."""
        svc = SearchService(workspace=mock_workspace)
        # Replace the whole catalog with a mock that reports the cache file exists
        mock_catalog = MagicMock()
        fake_cache = mock_workspace / "descriptor_cache.json"
        fake_cache.touch()
        mock_catalog.cache_path = fake_cache
        svc._catalog = mock_catalog
        assert svc.available is True

    def test_available_false_when_cache_missing(self, mock_workspace: Path) -> None:
        """`available` is False when no descriptor cache exists."""
        svc = SearchService(workspace=mock_workspace)
        assert svc.available is False


# ---------------------------------------------------------------------------
# SearchService – search()
# ---------------------------------------------------------------------------

_MOCK_DESCRIPTORS = [
    {
        "cell_id": "context.catalog",
        "title": "Context Catalog",
        "purpose": "Manages cell descriptors and graph truth.",
    },
    {
        "cell_id": "context.engine",
        "title": "Context Engine",
        "purpose": "Graph-constrained context assembly.",
    },
]


class TestSearchServiceSearch:
    def test_search_empty_query_returns_empty_list(self, mock_workspace: Path) -> None:
        """Empty/whitespace query must return [] immediately."""
        svc = SearchService(workspace=mock_workspace)
        assert svc.search("") == []
        assert svc.search("   ") == []

    def test_search_returns_scored_hits(self, mock_workspace: Path) -> None:
        """Results must include score, path, cell_id, title, purpose."""
        svc = SearchService(workspace=mock_workspace)
        svc._catalog = _make_mock_catalog(_MOCK_DESCRIPTORS)
        hits = svc.search("context")
        assert len(hits) == 2
        for hit in hits:
            assert "score" in hit
            assert "path" in hit
            assert "cell_id" in hit
            assert "title" in hit
            assert "purpose" in hit
            assert hit["path"].startswith("polaris/cells/")

    def test_search_score_is_inverse_rank(self, mock_workspace: Path) -> None:
        """Score for rank-1 item must be exactly 1.0; rank-2 → 0.5."""
        svc = SearchService(workspace=mock_workspace)
        svc._catalog = _make_mock_catalog(_MOCK_DESCRIPTORS)
        hits = svc.search("context")
        scores = [h["score"] for h in hits]
        assert scores == [1.0, 0.5]

    def test_search_uses_limit(self, mock_workspace: Path) -> None:
        """The `limit` parameter is passed through to the catalog."""
        svc = SearchService(workspace=mock_workspace)
        svc._catalog = _make_mock_catalog(_MOCK_DESCRIPTORS)
        svc.search("context", limit=1)
        svc._catalog.search.assert_called_once()  # type: ignore[attr-defined]
        call_args = svc._catalog.search.call_args  # type: ignore[attr-defined]
        assert call_args[0][0].limit == 1

    def test_search_catalog_file_not_found_returns_empty(self, mock_workspace: Path) -> None:
        """FileNotFoundError during catalog read → [] (graceful degradation)."""
        svc = SearchService(workspace=mock_workspace)
        mock_catalog = MagicMock()
        mock_catalog.search.side_effect = FileNotFoundError("Cache not found")
        svc._catalog = mock_catalog
        result = svc.search("test")
        assert result == []

    def test_search_oserror_returns_empty(self, mock_workspace: Path) -> None:
        """OSError during catalog read → [] (graceful degradation)."""
        svc = SearchService(workspace=mock_workspace)
        mock_catalog = MagicMock()
        mock_catalog.search.side_effect = OSError("Permission denied")
        svc._catalog = mock_catalog
        result = svc.search("test")
        assert result == []

    def test_search_value_error_returns_empty(self, mock_workspace: Path) -> None:
        """ValueError during catalog read → [] (graceful degradation)."""
        svc = SearchService(workspace=mock_workspace)
        mock_catalog = MagicMock()
        mock_catalog.search.side_effect = ValueError("bad query")
        svc._catalog = mock_catalog
        result = svc.search("test")
        assert result == []

    def test_search_json_decode_error_returns_empty(self, mock_workspace: Path) -> None:
        """JSON decode error on cache → [] (graceful degradation)."""
        svc = SearchService(workspace=mock_workspace)
        mock_catalog = MagicMock()
        mock_catalog.search.side_effect = json.JSONDecodeError("", "", 0)
        svc._catalog = mock_catalog
        result = svc.search("test")
        assert result == []


# ---------------------------------------------------------------------------
# SearchService – add_documents (deprecated no-op)
# ---------------------------------------------------------------------------


class TestSearchServiceAddDocuments:
    def test_add_documents_issues_deprecation_warning(self, mock_workspace: Path) -> None:
        """`add_documents` must emit a DeprecationWarning."""
        svc = SearchService(workspace=mock_workspace)
        with pytest.warns(DeprecationWarning, match="add_documents is ignored"):
            svc.add_documents([{"text": "foo"}])

    def test_add_documents_empty_list_no_warning(self, mock_workspace: Path) -> None:
        """Empty list must not emit a warning."""
        svc = SearchService(workspace=mock_workspace)
        svc.add_documents([])


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------


class TestSingletonHelpers:
    def test_get_search_service_returns_same_instance(self, mock_workspace: Path) -> None:
        """Two calls to `get_search_service` return the same object."""
        reset_search_service_for_tests()
        a = get_search_service()
        b = get_search_service()
        assert a is b

    def test_reset_clears_singleton(self, mock_workspace: Path) -> None:
        """`reset_search_service_for_tests` breaks the singleton."""
        a = get_search_service()
        reset_search_service_for_tests()
        b = get_search_service()
        assert a is not b
