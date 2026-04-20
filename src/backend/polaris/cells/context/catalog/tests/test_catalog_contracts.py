"""Contract tests for context.catalog cell.

Tests the public contracts and service boundaries of the context.catalog cell.
"""

from __future__ import annotations

import pytest
from polaris.cells.context.catalog.public.contracts import (
    CellDescriptorV1,
    SearchCellsQueryV1,
    SearchCellsResultV1,
)


class TestSearchCellsQueryV1:
    """Tests for SearchCellsQueryV1 contract."""

    def test_search_cells_query_basic(self) -> None:
        """Test basic query construction."""
        query = SearchCellsQueryV1(query="test cell")
        assert query.query == "test cell"
        assert query.limit == 10  # default

    def test_search_cells_query_with_limit(self) -> None:
        """Test query with custom limit."""
        query = SearchCellsQueryV1(query="test cell", limit=5)
        assert query.query == "test cell"
        assert query.limit == 5

    def test_search_cells_query_immutable(self) -> None:
        """Test that query is immutable (frozen dataclass)."""
        query = SearchCellsQueryV1(query="test")
        with pytest.raises(AttributeError):
            query.query = "modified"  # type: ignore[misc]


class TestCellDescriptorV1:
    """Tests for CellDescriptorV1 contract."""

    def test_cell_descriptor_construction(self) -> None:
        """Test descriptor construction with all fields."""
        descriptor = CellDescriptorV1(
            cell_id="test.cell",
            title="Test Cell",
            purpose="Testing purposes",
            domain="test",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="test-team",
            capability_summary="Provides testing capabilities",
        )
        assert descriptor.cell_id == "test.cell"
        assert descriptor.title == "Test Cell"
        assert descriptor.stateful is False

    def test_cell_descriptor_immutable(self) -> None:
        """Test that descriptor is immutable."""
        descriptor = CellDescriptorV1(
            cell_id="test.cell",
            title="Test",
            purpose="Test",
            domain="test",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="test",
            capability_summary="Test",
        )
        with pytest.raises(AttributeError):
            descriptor.cell_id = "modified"  # type: ignore[misc]


class TestSearchCellsResultV1:
    """Tests for SearchCellsResultV1 contract."""

    def test_search_result_empty(self) -> None:
        """Test empty search result."""
        result = SearchCellsResultV1(descriptors=(), total=0)
        assert result.total == 0
        assert len(result.descriptors) == 0

    def test_search_result_with_descriptors(self) -> None:
        """Test search result with descriptors."""
        descriptor = CellDescriptorV1(
            cell_id="test.cell",
            title="Test",
            purpose="Test",
            domain="test",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="test",
            capability_summary="Test",
        )
        result = SearchCellsResultV1(descriptors=(descriptor,), total=1)
        assert result.total == 1
        assert len(result.descriptors) == 1
        assert result.descriptors[0].cell_id == "test.cell"

    def test_search_result_immutable(self) -> None:
        """Test that result is immutable."""
        result = SearchCellsResultV1(descriptors=(), total=0)
        with pytest.raises(AttributeError):
            result.total = 5  # type: ignore[misc]


class TestDescriptorValidation:
    """Tests for descriptor validation."""

    def test_descriptor_validation_required_fields(self) -> None:
        """Test that all required fields must be provided."""
        with pytest.raises(TypeError):
            CellDescriptorV1()  # type: ignore[call-arg]

    def test_descriptor_validation_types(self) -> None:
        """Test type validation for descriptor fields.

        Note: dataclasses don't validate types at runtime by default.
        This test documents that type validation would require additional
        validation logic (e.g., pydantic or __post_init__).
        """
        # Dataclasses accept any type - no runtime validation by default
        descriptor = CellDescriptorV1(
            cell_id="test.cell",
            title="Test",
            purpose="Test",
            domain="test",
            kind="utility",
            visibility="public",
            stateful="not_a_bool",  # type: ignore[arg-type]
            owner="test",
            capability_summary="Test",
        )
        # The value is stored as-is without validation
        assert descriptor.stateful == "not_a_bool"
