"""Tests for polaris.cells.context.catalog.public.contracts."""

from __future__ import annotations

from polaris.cells.context.catalog.public.contracts import (
    CellDescriptorV1,
    SearchCellsQueryV1,
    SearchCellsResultV1,
)


class TestSearchCellsQueryV1:
    def test_defaults(self) -> None:
        q = SearchCellsQueryV1(query="test")
        assert q.query == "test"
        assert q.limit == 10

    def test_custom_limit(self) -> None:
        q = SearchCellsQueryV1(query="test", limit=5)
        assert q.limit == 5


class TestCellDescriptorV1:
    def test_fields(self) -> None:
        d = CellDescriptorV1(
            cell_id="c1",
            title="Test",
            purpose="testing",
            domain="test",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="team",
            capability_summary="does testing",
        )
        assert d.cell_id == "c1"
        assert d.title == "Test"
        assert d.stateful is False


class TestSearchCellsResultV1:
    def test_fields(self) -> None:
        d = CellDescriptorV1(
            cell_id="c1",
            title="Test",
            purpose="testing",
            domain="test",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="team",
            capability_summary="does testing",
        )
        r = SearchCellsResultV1(descriptors=(d,), total=1)
        assert r.total == 1
        assert len(r.descriptors) == 1
