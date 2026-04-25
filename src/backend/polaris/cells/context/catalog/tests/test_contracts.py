"""Tests for Context Catalog Public Contracts.

Tests the public contracts for context.catalog cell including
SearchCellsQueryV1, CellDescriptorV1, SearchCellsResultV1,
and related utility functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.context.catalog.public import (
    CellDescriptorV1,
    SearchCellsQueryV1,
    SearchCellsResultV1,
    resolve_context_catalog_cache_path,
    resolve_context_catalog_index_state_path,
    validate_descriptor_cache_payload,
)


class TestSearchCellsQueryV1:
    """Tests for SearchCellsQueryV1 contract."""

    def test_query_required_fields(self) -> None:
        """Test query with required fields only."""
        query = SearchCellsQueryV1(query="authentication")
        assert query.query == "authentication"
        assert query.limit == 10

    def test_query_with_limit(self) -> None:
        """Test query with custom limit."""
        query = SearchCellsQueryV1(query="kernel", limit=20)
        assert query.limit == 20

    def test_query_empty_query(self) -> None:
        """Test query with empty string (allowed)."""
        query = SearchCellsQueryV1(query="")
        assert query.query == ""


class TestCellDescriptorV1:
    """Tests for CellDescriptorV1 contract."""

    def test_descriptor_required_fields(self) -> None:
        """Test descriptor with required fields."""
        descriptor = CellDescriptorV1(
            cell_id="roles.kernel",
            title="Kernel Role",
            purpose="Execute turns",
            domain="runtime",
            kind="business",
            visibility="internal",
            stateful=True,
            owner="kernel_team",
            capability_summary="Turn execution kernel",
        )
        assert descriptor.cell_id == "roles.kernel"
        assert descriptor.title == "Kernel Role"
        assert descriptor.purpose == "Execute turns"
        assert descriptor.domain == "runtime"
        assert descriptor.kind == "business"
        assert descriptor.visibility == "internal"
        assert descriptor.stateful is True
        assert descriptor.owner == "kernel_team"
        assert descriptor.capability_summary == "Turn execution kernel"

    def test_descriptor_stateless(self) -> None:
        """Test stateless descriptor."""
        descriptor = CellDescriptorV1(
            cell_id="context.os",
            title="Context OS",
            purpose="Manage context",
            domain="runtime",
            kind="utility",
            visibility="public",
            stateful=False,
            owner="kernel_team",
            capability_summary="Context management",
        )
        assert descriptor.stateful is False


class TestSearchCellsResultV1:
    """Tests for SearchCellsResultV1 contract."""

    def test_result_empty(self) -> None:
        """Test empty result."""
        result = SearchCellsResultV1(
            descriptors=(),
            total=0,
        )
        assert result.descriptors == ()
        assert result.total == 0

    def test_result_with_descriptors(self) -> None:
        """Test result with descriptors."""
        descriptors = (
            CellDescriptorV1(
                cell_id="roles.kernel",
                title="Kernel",
                purpose="Execute",
                domain="runtime",
                kind="business",
                visibility="internal",
                stateful=True,
                owner="team",
                capability_summary="Kernel",
            ),
            CellDescriptorV1(
                cell_id="roles.runtime",
                title="Runtime",
                purpose="Orchestrate",
                domain="runtime",
                kind="business",
                visibility="internal",
                stateful=True,
                owner="team",
                capability_summary="Runtime",
            ),
        )
        result = SearchCellsResultV1(
            descriptors=descriptors,
            total=2,
        )
        assert len(result.descriptors) == 2
        assert result.total == 2
        assert result.descriptors[0].cell_id == "roles.kernel"
        assert result.descriptors[1].cell_id == "roles.runtime"


class TestResolveCachePath:
    """Tests for cache path resolution utilities."""

    def test_resolve_cache_path_returns_path(self) -> None:
        """Test cache path resolution returns Path object."""
        workspace = "/workspace"
        path = resolve_context_catalog_cache_path(workspace)
        assert isinstance(path, Path)
        assert "catalog" in str(path)
        assert "descriptors.json" in str(path)

    def test_resolve_index_state_path_returns_path(self) -> None:
        """Test index state path resolution returns Path object."""
        workspace = "/workspace"
        path = resolve_context_catalog_index_state_path(workspace)
        assert isinstance(path, Path)
        assert "index" in str(path)
        assert "state" in str(path)


class TestValidateDescriptorCachePayload:
    """Tests for descriptor cache validation.

    Note: validate_descriptor_cache_payload returns a list of errors,
    not a boolean.
    """

    def test_validate_valid_full_payload(self) -> None:
        """Test validation of a complete valid payload."""
        payload = {
            "version": 1,
            "generated_at": "2024-01-01T00:00:00Z",
            "workspace": "/workspace",
            "embedding_runtime_fingerprint": "graph-catalog-seed:none",
            "descriptors": [
                {
                    "cell_id": "test.cell",
                    "title": "Test Cell",
                    "primary_category": "test",
                    "domain": "test",
                    "kind": "utility",
                    "visibility": "public",
                    "stateful": False,
                    "owner": "test_team",
                    "capability_summary": "Test capability",
                    "purpose": "Testing",
                    "schema_version": 1,
                    "descriptor_version": 1,
                    "generated_at": "2024-01-01T00:00:00Z",
                    "graph_fingerprint": "sha256:abc123",
                    "embedding_runtime_fingerprint": "graph-catalog-seed:none",
                    "descriptor_hash": "sha256:def456",
                    "derived_from": {
                        "cell_manifest": "path/to/manifest",
                        "readme": "path/to/readme",
                        "context_pack": "path/to/context",
                        "code_fingerprint": "sha256:ghi789",
                    },
                    "classification": {
                        "plane": "capability",
                        "kind": "utility",
                        "domain": "test",
                        "role": "system",
                        "state_profile": "stateless",
                        "effect_profile": [],
                        "criticality": "normal",
                    },
                    "subgraphs": [],
                    "when_to_use": ["Use for testing"],
                    "when_not_to_use": ["Do not use in production"],
                    "responsibilities": ["Provide test capability"],
                    "non_goals": [],
                    "invariants": [],
                    "key_invariants": [],
                    "testability": {},
                    "public_contracts": {},
                    "dependencies": [],
                    "state_owners": [],
                    "effects_allowed": [],
                    "source_hash": "sha256:xyz",
                    "descriptor_text": "test cell",
                    "embedding_vector": [0.1, 0.2, 0.3],
                    "embedding_provider": "test",
                    "embedding_model_name": "test-model",
                    "embedding_device": "cpu",
                },
            ],
        }
        errors = validate_descriptor_cache_payload(payload)
        assert errors == []

    def test_validate_missing_root_fields(self) -> None:
        """Test validation fails for missing root fields."""
        payload = {
            "version": 1,
            # missing other required fields
        }
        errors = validate_descriptor_cache_payload(payload)
        assert len(errors) > 0
        assert any("missing root field" in e for e in errors)

    def test_validate_non_dict_payload(self) -> None:
        """Test validation fails for non-dict payload."""
        errors = validate_descriptor_cache_payload("not a dict")
        assert errors == ["descriptor cache root must be an object"]

    def test_validate_none_payload(self) -> None:
        """Test validation fails for None payload."""
        errors = validate_descriptor_cache_payload(None)
        assert errors == ["descriptor cache root must be an object"]

    def test_validate_empty_payload(self) -> None:
        """Test validation fails for empty payload."""
        errors = validate_descriptor_cache_payload({})
        assert len(errors) > 0
        assert any("missing root field" in e for e in errors)

    def test_validate_descriptors_not_list(self) -> None:
        """Test validation fails when descriptors is not a list."""
        payload = {
            "version": 1,
            "generated_at": "2024-01-01T00:00:00Z",
            "workspace": "/workspace",
            "embedding_runtime_fingerprint": "test",
            "descriptors": "not a list",
        }
        errors = validate_descriptor_cache_payload(payload)
        assert "root field 'descriptors' must be a list" in errors

    def test_validate_empty_descriptors_ok(self) -> None:
        """Test validation passes with empty descriptors list."""
        payload = {
            "version": 1,
            "generated_at": "2024-01-01T00:00:00Z",
            "workspace": "/workspace",
            "embedding_runtime_fingerprint": "test",
            "descriptors": [],
        }
        errors = validate_descriptor_cache_payload(payload)
        assert errors == []


class TestContextCatalogIntegration:
    """Integration tests for context catalog contracts."""

    def test_search_and_result_flow(self) -> None:
        """Test search query and result flow."""
        # Create search query
        query = SearchCellsQueryV1(
            query="kernel runtime",
            limit=5,
        )

        # Create matching descriptors
        descriptors = (
            CellDescriptorV1(
                cell_id="roles.kernel",
                title="Kernel Role",
                purpose="Turn execution",
                domain="runtime",
                kind="business",
                visibility="internal",
                stateful=True,
                owner="kernel_team",
                capability_summary="Turn execution",
            ),
        )

        # Create result
        result = SearchCellsResultV1(
            descriptors=descriptors,
            total=1,
        )

        # Verify flow
        assert query.query == "kernel runtime"
        assert query.limit == 5
        assert len(result.descriptors) == 1
        assert result.descriptors[0].cell_id == "roles.kernel"

    def test_descriptor_immutability(self) -> None:
        """Test that descriptors are immutable."""
        descriptor = CellDescriptorV1(
            cell_id="roles.kernel",
            title="Kernel",
            purpose="Execute",
            domain="runtime",
            kind="business",
            visibility="internal",
            stateful=True,
            owner="team",
            capability_summary="Kernel",
        )

        # Should not be able to modify frozen dataclass
        with pytest.raises(AttributeError):
            descriptor.cell_id = "modified"  # type: ignore[assignment]

    def test_result_immutability(self) -> None:
        """Test that results are immutable."""
        descriptor = CellDescriptorV1(
            cell_id="roles.kernel",
            title="Kernel",
            purpose="Execute",
            domain="runtime",
            kind="business",
            visibility="internal",
            stateful=True,
            owner="team",
            capability_summary="Kernel",
        )
        result = SearchCellsResultV1(
            descriptors=(descriptor,),
            total=1,
        )

        # Should not be able to modify frozen dataclass
        with pytest.raises(AttributeError):
            result.total = 999  # type: ignore[assignment]
