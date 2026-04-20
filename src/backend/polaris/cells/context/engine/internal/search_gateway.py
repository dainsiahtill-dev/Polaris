"""Graph-constrained search facade for `context.engine`.

Retrieval is delegated to ``context.catalog`` (descriptor cache + graph truth),
not a standalone ad-hoc vector index.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.context.catalog.public.contracts import SearchCellsQueryV1
from polaris.cells.context.catalog.service import ContextCatalogService

logger = logging.getLogger(__name__)

_SEARCH_CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class _CacheEntry:
    value: list[dict[str, Any]]
    timestamp: float


class _TTLCache:
    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: dict[str, _CacheEntry] = {}
        self._access_order: list[str] = []

    def get_or_compute(self, key: str, compute: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None and (now - entry.timestamp) < self._ttl:
            return entry.value

        value = compute()

        if key in self._cache:
            self._cache[key] = _CacheEntry(value=value, timestamp=now)
        else:
            if len(self._cache) >= self._maxsize:
                oldest_key = self._access_order.pop(0)
                self._cache.pop(oldest_key, None)
            self._cache[key] = _CacheEntry(value=value, timestamp=now)
            self._access_order.append(key)

        return value

    def clear(self) -> None:
        self._cache.clear()
        self._access_order.clear()


def _default_backend_root() -> Path:
    """Resolve ``src/backend`` (contains ``docs/graph`` and ``polaris/cells``)."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / "docs" / "graph" / "catalog" / "cells.yaml"
        if candidate.is_file():
            return parent
    return here.parents[5]


def _resolve_workspace() -> Path:
    env = os.environ.get("POLARIS_WORKSPACE", "").strip()
    if env:
        return Path(env).resolve()
    return _default_backend_root()


class SearchService:
    """Catalog-backed cell search (graph-constrained)."""

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace or _resolve_workspace()
        self._catalog = ContextCatalogService(str(self._workspace))
        self._search_cache = _TTLCache(maxsize=128, ttl=_SEARCH_CACHE_TTL_SECONDS)

    @property
    def available(self) -> bool:
        """True when the derived descriptor cache exists (``context.catalog`` output)."""
        return self._catalog.cache_path.is_file()

    def add_documents(self, docs: list[dict[str, str]]) -> None:
        """Deprecated: graph-constrained search does not ingest arbitrary documents."""
        if docs:
            warnings.warn(
                "SearchService.add_documents is ignored: use context.catalog sync "
                "and graph-backed descriptors instead of ad-hoc document indexing.",
                DeprecationWarning,
                stacklevel=2,
            )
        logger.debug(
            "SearchService.add_documents ignored (%d docs); catalog search is graph-bound",
            len(docs),
        )

    def _generate_query_hash(self, query_str: str, limit: int) -> str:
        """Generate a unique hash for the query and limit."""
        key = f"{query_str.strip().lower()}:{limit}"
        return hashlib.sha256(key.encode()).hexdigest()

    def _cached_catalog_search(self, query_hash: str, query_str: str, limit: int) -> list[dict[str, Any]]:
        """Execute catalog search with TTL caching (5 min TTL)."""

        def _compute() -> list[dict[str, Any]]:
            try:
                result = self._catalog.search(SearchCellsQueryV1(query=query_str, limit=limit))
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning(
                    "context.engine search: catalog read failed (%s); returning no hits",
                    exc,
                )
                return []

            hits: list[dict[str, Any]] = []
            for idx, d in enumerate(result.descriptors):
                hits.append(
                    {
                        "score": 1.0 / float(idx + 1),
                        "path": f"polaris/cells/{d.cell_id.replace('.', '/')}/",
                        "cell_id": d.cell_id,
                        "title": d.title,
                        "purpose": d.purpose,
                    }
                )
            return hits

        return self._search_cache.get_or_compute(query_hash, _compute)

    def search(self, query_str: str, limit: int = 10) -> list[dict[str, Any]]:
        if not query_str.strip():
            return []

        query_hash = self._generate_query_hash(query_str, limit)
        return self._cached_catalog_search(query_hash, query_str.strip(), limit)


_service: SearchService | None = None


def get_search_service() -> SearchService:
    """Return a process-local catalog-backed search service."""
    global _service
    if _service is None:
        _service = SearchService()
    return _service


def reset_search_service_for_tests() -> None:
    """Test hook: clear singleton."""
    global _service
    _service = None
