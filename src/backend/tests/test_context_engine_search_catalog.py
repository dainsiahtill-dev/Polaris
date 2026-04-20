"""context.engine search must delegate to context.catalog (graph-constrained)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from polaris.cells.context.catalog.public.contracts import (  # noqa: E402
    CellDescriptorV1,
    SearchCellsResultV1,
)
from polaris.cells.context.engine.internal.search_gateway import (  # noqa: E402
    SearchService,
)


def test_search_service_delegates_to_context_catalog():
    fake = MagicMock()
    fake.search.return_value = SearchCellsResultV1(
        descriptors=(
            CellDescriptorV1(
                cell_id="context.catalog",
                title="Context Catalog",
                purpose="graph lookup",
                domain="context",
                kind="capability",
                visibility="public",
                stateful=True,
                owner="context",
                capability_summary="lookup",
            ),
        ),
        total=1,
    )
    with patch(
        "polaris.cells.context.engine.internal.search_gateway.ContextCatalogService",
        return_value=fake,
    ):
        svc = SearchService(workspace=BACKEND_ROOT)
        out = svc.search("descriptor", limit=5)
        assert len(out) == 1
        assert out[0]["cell_id"] == "context.catalog"
        assert "polaris/cells/context/catalog/" in out[0]["path"]
        fake.search.assert_called_once()
