from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.context.catalog import ContextCatalogService
from polaris.cells.context.catalog.public.contracts import SearchCellsQueryV1


def _write_catalog(workspace: Path) -> None:
    catalog_dir = workspace / "docs" / "graph" / "catalog"
    subgraphs_dir = workspace / "docs" / "graph" / "subgraphs"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    subgraphs_dir.mkdir(parents=True, exist_ok=True)
    (subgraphs_dir / "context_plane.yaml").write_text(
        "version: 1\nid: context_plane\n",
        encoding="utf-8",
    )
    (catalog_dir / "cells.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "cells:",
                "  - id: context.catalog",
                "    title: Context Catalog",
                "    kind: capability",
                "    visibility: public",
                "    stateful: true",
                "    owner: context",
                "    purpose: Build descriptors from graph assets.",
                "    owned_paths:",
                "      - polaris/cells/context/catalog/**",
                "    public_contracts:",
                "      queries: [SearchCellsQueryV1]",
                "      results: [SearchCellsResultV1, CellDescriptorV1]",
                "    depends_on: [storage.layout]",
                "    subgraphs: [context_plane]",
                "    state_owners: [workspace/meta/context_catalog/*]",
                "    effects_allowed: [fs.write:workspace/meta/context_catalog/*]",
                "    verification:",
                "      tests: [polaris/cells/context/catalog/tests/test_service.py]",
            ]
        ),
        encoding="utf-8",
    )


def test_sync_builds_descriptor_cache(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    service = ContextCatalogService(str(tmp_path))

    result = service.sync()

    cache_path = Path(result["cache_path"])
    state_path = Path(result["index_state_path"])
    assert cache_path.is_file()
    assert state_path.is_file()

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["descriptors"][0]["cell_id"] == "context.catalog"
    assert payload["descriptors"][0]["derived_from"]["cell_manifest"] == "polaris/cells/context/catalog/cell.yaml"
    assert service.is_index_stale() is False


def test_search_uses_descriptor_cache(tmp_path: Path) -> None:
    _write_catalog(tmp_path)
    service = ContextCatalogService(str(tmp_path))
    service.sync()

    result = service.search(SearchCellsQueryV1(query="descriptor graph", limit=5))

    assert result.total == 1
    assert result.descriptors[0].cell_id == "context.catalog"
