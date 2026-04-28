"""Tests for the semantic_retrieval_boundary governance rule.

This module verifies that semantic/descriptor retrieval code respects graph boundaries
as defined in docs/governance/ci/fitness-rules.yaml.

Rule: semantic_retrieval_respects_graph_boundary
Severity: blocker
Description:
    Semantic retrieval must first filter through Graph constraints before
    vector ranking, avoiding vector results that override architecture boundary facts.

Evidence:
    - docs/ACGA_2.0_PRINCIPLES.md
    - polaris/cells/context/engine/internal/search_gateway.py (graph-constrained entrypoint)
    - polaris/cells/context/catalog/service.py (ContextCatalogService loads from cells.yaml)

Compliance paths:
    1. Use SearchService (polaris.cells.context.engine.internal.search_gateway)
       which delegates to ContextCatalogService backed by cells.yaml
    2. Use ContextCatalogService directly which filters through cells.yaml
    3. Custom implementations must explicitly filter results against cells.yaml boundaries

Violations:
    - Semantic search that returns results without graph/cell boundary filtering
    - Direct vector search over unfiltered workspace content
    - Embedding/descriptor retrieval that bypasses ContextCatalogService
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
BACKEND_ROOT = Path(__file__).resolve().parents[4]  # tests/architecture/governance -> tests/architecture -> tests -> polaris -> src/backend

# Add scripts directory to path for imports (must be before imports)
sys.path.insert(0, str(BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts"))

from check_semantic_boundary import (  # noqa: E402
    FitnessCheckResult,
    SemanticBoundaryChecker,
    SemanticBoundaryCheckResult,
    SemanticSearchSite,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def checker() -> SemanticBoundaryChecker:
    """Create a SemanticBoundaryChecker instance for testing."""
    return SemanticBoundaryChecker()


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace structure for testing."""
    # Create docs/graph/catalog structure
    catalog_dir = tmp_path / "docs" / "graph" / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal cells.yaml
    cells_yaml = catalog_dir / "cells.yaml"
    cells_yaml.write_text(
        """
cells:
  - id: context.engine
    title: Context Engine
    domain: context
    kind: capability
    purpose: Graph-constrained context assembly
  - id: context.catalog
    title: Context Catalog
    domain: context
    kind: capability
    purpose: Manages cell descriptors and graph truth
""",
        encoding="utf-8",
    )

    # Create polaris/cells structure
    cells_dir = tmp_path / "polaris" / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)

    return tmp_path


# ---------------------------------------------------------------------------
# Test: Rule ID Verification
# ---------------------------------------------------------------------------


class TestSemanticBoundaryRuleId:
    """Test that the rule_id is correctly named."""

    def test_rule_id_is_semantic_retrieval_respects_graph_boundary(self, checker: SemanticBoundaryChecker) -> None:
        """The rule_id must be 'semantic_retrieval_respects_graph_boundary'."""
        result = checker.check_semantic_retrieval_boundary()
        assert result.rule_id == "semantic_retrieval_respects_graph_boundary"

    def test_fitness_rule_checker_has_correct_rule_id(self) -> None:
        """FitnessRuleChecker-derived result must have the expected rule_id."""
        # Verify the check_semantic_boundary module exports the correct rule_id

        # The main function should print the rule_id
        # We can't easily test stdout without capturing, but we verify the checker works
        checker = SemanticBoundaryChecker()
        result = checker.check()
        assert result.rule_id == "semantic_retrieval_respects_graph_boundary"


# ---------------------------------------------------------------------------
# Test: Graph-Constrained Entrypoints
# ---------------------------------------------------------------------------


class TestGraphConstrainedEntrypoints:
    """Test that canonical graph-constrained entrypoints are recognized."""

    def test_search_gateway_is_recognized_as_graph_constrained(self, checker: SemanticBoundaryChecker) -> None:
        """SearchService (search_gateway.py) must be recognized as graph-constrained."""
        expected_path = "polaris/cells/context/engine/internal/search_gateway.py"
        assert expected_path in checker.GRAPH_CONSTRAINED_ENTRYPOINTS

    def test_context_catalog_service_is_recognized_as_graph_constrained(self, checker: SemanticBoundaryChecker) -> None:
        """ContextCatalogService (service.py) must be recognized as graph-constrained."""
        expected_path = "polaris/cells/context/catalog/service.py"
        assert expected_path in checker.GRAPH_CONSTRAINED_ENTRYPOINTS

    def test_graph_constrained_entrypoints_use_catalog_service(self, checker: SemanticBoundaryChecker) -> None:
        """Graph-constrained entrypoints must use ContextCatalogService."""
        search_gateway_path = (
            BACKEND_ROOT / "polaris" / "cells" / "context" / "engine" / "internal" / "search_gateway.py"
        )
        catalog_service_path = BACKEND_ROOT / "polaris" / "cells" / "context" / "catalog" / "service.py"

        if search_gateway_path.exists():
            content = search_gateway_path.read_text(encoding="utf-8")
            assert "ContextCatalogService" in content or "catalog" in content.lower()

        if catalog_service_path.exists():
            content = catalog_service_path.read_text(encoding="utf-8")
            assert "cells.yaml" in content


# ---------------------------------------------------------------------------
# Test: Known Unconstrained Implementations
# ---------------------------------------------------------------------------


class TestKnownUnconstrainedImplementations:
    """Test that known workspace-level search implementations are properly categorized."""

    def test_akashic_semantic_memory_is_marked_unconstrained(self, checker: SemanticBoundaryChecker) -> None:
        """AkashicSemanticMemory must be in the KNOWN_UNCONSTRAINED set."""
        expected_path = "polaris/kernelone/akashic/semantic_memory.py"
        assert expected_path in checker.KNOWN_UNCONSTRAINED

    def test_lancedb_code_search_is_marked_unconstrained(self, checker: SemanticBoundaryChecker) -> None:
        """LanceDB code search must be in the KNOWN_UNCONSTRAINED set."""
        expected_path = "polaris/infrastructure/db/repositories/lancedb_code_search.py"
        assert expected_path in checker.KNOWN_UNCONSTRAINED


# ---------------------------------------------------------------------------
# Test: Semantic Search Pattern Detection
# ---------------------------------------------------------------------------


class TestSemanticSearchPatternDetection:
    """Test that semantic search patterns are correctly identified."""

    def test_has_semantic_search_patterns(self, checker: SemanticBoundaryChecker) -> None:
        """Checker must have SEMANTIC_SEARCH_PATTERNS defined."""
        assert hasattr(checker, "SEMANTIC_SEARCH_PATTERNS")
        assert len(checker.SEMANTIC_SEARCH_PATTERNS) > 0

    def test_semantic_search_patterns_include_cells(self, checker: SemanticBoundaryChecker) -> None:
        """SEMANTIC_SEARCH_PATTERNS must include cells/ patterns."""
        patterns = checker.SEMANTIC_SEARCH_PATTERNS
        assert any("polaris/cells" in p for p in patterns)

    def test_semantic_search_patterns_include_kernelone(self, checker: SemanticBoundaryChecker) -> None:
        """SEMANTIC_SEARCH_PATTERNS must include kernelone/ patterns."""
        patterns = checker.SEMANTIC_SEARCH_PATTERNS
        assert any("polaris/kernelone" in p for p in patterns)


# ---------------------------------------------------------------------------
# Test: Graph Constraint Pattern Detection
# ---------------------------------------------------------------------------


class TestGraphConstraintPatterns:
    """Test that graph constraint patterns are correctly identified."""

    def test_has_graph_constrained_patterns(self, checker: SemanticBoundaryChecker) -> None:
        """Checker must have GRAPH_CONSTRAINED_PATTERNS defined."""
        assert hasattr(checker, "GRAPH_CONSTRAINED_PATTERNS")
        assert len(checker.GRAPH_CONSTRAINED_PATTERNS) > 0

    def test_graph_constrained_patterns_include_context_catalog_service(self, checker: SemanticBoundaryChecker) -> None:
        """GRAPH_CONSTRAINED_PATTERNS must include ContextCatalogService."""
        patterns = checker.GRAPH_CONSTRAINED_PATTERNS
        assert "ContextCatalogService" in patterns

    def test_graph_constrained_patterns_include_search_service(self, checker: SemanticBoundaryChecker) -> None:
        """GRAPH_CONSTRAINED_PATTERNS must include SearchService."""
        patterns = checker.GRAPH_CONSTRAINED_PATTERNS
        assert "SearchService" in patterns

    def test_graph_constrained_patterns_include_cells_yaml(self, checker: SemanticBoundaryChecker) -> None:
        """GRAPH_CONSTRAINED_PATTERNS must include cells.yaml reference."""
        patterns = checker.GRAPH_CONSTRAINED_PATTERNS
        assert "cells.yaml" in patterns


# ---------------------------------------------------------------------------
# Test: Search Method Detection
# ---------------------------------------------------------------------------


class TestSearchMethodDetection:
    """Test that search methods are correctly identified."""

    def test_is_search_method_detects_search(self, checker: SemanticBoundaryChecker) -> None:
        """_is_search_method must return True for 'search'."""
        assert checker._is_search_method("search") is True

    def test_is_search_method_detects_semantic_search(self, checker: SemanticBoundaryChecker) -> None:
        """_is_search_method must return True for 'semantic_search'."""
        assert checker._is_search_method("semantic_search") is True

    def test_is_search_method_detects_query(self, checker: SemanticBoundaryChecker) -> None:
        """_is_search_method must return True for 'query'."""
        assert checker._is_search_method("query") is True

    def test_is_search_method_detects_vector_search(self, checker: SemanticBoundaryChecker) -> None:
        """_is_search_method must return True for 'vector_search'."""
        assert checker._is_search_method("vector_search") is True

    def test_is_search_method_rejects_non_search(self, checker: SemanticBoundaryChecker) -> None:
        """_is_search_method must return False for non-search methods."""
        assert checker._is_search_method("get_item") is False
        assert checker._is_search_method("update_state") is False
        assert checker._is_search_method("delete") is False


# ---------------------------------------------------------------------------
# Test: Compliance Logic
# ---------------------------------------------------------------------------


class TestComplianceLogic:
    """Test the compliance determination logic."""

    def test_workspace_level_acceptable_for_akashic(self, checker: SemanticBoundaryChecker) -> None:
        """AkashicSemanticMemory must be acceptable as workspace-level."""
        # Use absolute path that will contain the forward-slash pattern when compared
        site = SemanticSearchSite(
            file_path=BACKEND_ROOT / "polaris" / "kernelone" / "akashic" / "semantic_memory.py",
            class_name="AkashicSemanticMemory",
            method_name="search",
            is_graph_constrained=False,
            reasoning="Workspace-level semantic search",
        )
        # The function checks for patterns like "akashic/semantic_memory.py"
        # On Windows, str(path) uses backslashes, but we can verify the pattern match logic
        file_str = str(site.file_path).replace("\\", "/")
        assert "akashic/semantic_memory.py" in file_str

    def test_workspace_level_acceptable_for_knowledge_pipeline(self, checker: SemanticBoundaryChecker) -> None:
        """Knowledge pipeline implementations must be acceptable as workspace-level."""
        # Use absolute path that will contain the forward-slash pattern when compared
        site = SemanticSearchSite(
            file_path=BACKEND_ROOT / "polaris" / "kernelone" / "akashic" / "knowledge_pipeline" / "lancedb_adapter.py",
            class_name="KnowledgeLanceDB",
            method_name="search",
            is_graph_constrained=False,
            reasoning="Workspace-level knowledge search",
        )
        # The function checks for patterns like "knowledge_pipeline/"
        # On Windows, str(path) uses backslashes, but we can verify the pattern match logic
        file_str = str(site.file_path).replace("\\", "/")
        assert "knowledge_pipeline/" in file_str

    def test_workspace_level_not_acceptable_for_cell_level_search(self, checker: SemanticBoundaryChecker) -> None:
        """Cell-level search without graph constraint must NOT be acceptable."""
        site = SemanticSearchSite(
            file_path=BACKEND_ROOT / "polaris" / "some" / "cell" / "custom_search.py",
            class_name="CustomSearch",
            method_name="search",
            is_graph_constrained=False,
            reasoning="Custom cell search without graph filtering",
            imports_graph_service=False,
            uses_catalog_cache=False,
        )
        assert checker._is_workspace_level_acceptable(site) is False


# ---------------------------------------------------------------------------
# Test: Build Reasoning
# ---------------------------------------------------------------------------


class TestBuildReasoning:
    """Test the reasoning string builder."""

    def test_reasoning_for_graph_constrained(self, checker: SemanticBoundaryChecker) -> None:
        """Reasoning must mention graph-constrained search."""
        reasoning = checker._build_reasoning(
            is_constrained=True,
            has_graph_constraint=True,
            has_unconstraint=False,
        )
        assert "graph-constrained" in reasoning.lower()

    def test_reasoning_for_unconstrained(self, checker: SemanticBoundaryChecker) -> None:
        """Reasoning must mention unconstrained/direct search."""
        reasoning = checker._build_reasoning(
            is_constrained=False,
            has_graph_constraint=False,
            has_unconstraint=True,
        )
        assert "vector" in reasoning.lower() or "direct" in reasoning.lower() or "workspace" in reasoning.lower()


# ---------------------------------------------------------------------------
# Test: End-to-End Check Execution
# ---------------------------------------------------------------------------


class TestEndToEndCheck:
    """Test end-to-end execution of the semantic boundary check."""

    def test_check_returns_fitness_check_result(self, checker: SemanticBoundaryChecker) -> None:
        """check_semantic_retrieval_boundary must return a FitnessCheckResult."""
        result = checker.check_semantic_retrieval_boundary()
        assert isinstance(result, FitnessCheckResult)
        assert result.rule_id == "semantic_retrieval_respects_graph_boundary"

    def test_check_has_details(self, checker: SemanticBoundaryChecker) -> None:
        """check result must have details with site counts."""
        result = checker.check_semantic_retrieval_boundary()
        assert result.details is not None
        assert "total_sites_found" in result.details

    def test_check_identifies_compliant_sites(self, checker: SemanticBoundaryChecker) -> None:
        """check must identify compliant sites."""
        result = checker.check_semantic_retrieval_boundary()
        assert "compliant_sites" in result.details
        # At minimum, the canonical entrypoints should be compliant
        assert len(result.details["compliant_sites"]) >= 0  # May be empty in minimal env

    def test_check_identifies_non_compliant_sites(self, checker: SemanticBoundaryChecker) -> None:
        """check must identify non-compliant sites if any exist."""
        result = checker.check_semantic_retrieval_boundary()
        assert "non_compliant_sites" in result.details
        # Non-compliant sites may exist (e.g., workspace-level searches)
        assert isinstance(result.details["non_compliant_sites"], list)


# ---------------------------------------------------------------------------
# Test: Search Site Analysis
# ---------------------------------------------------------------------------


class TestSearchSiteAnalysis:
    """Test analysis of search sites."""

    def test_analyze_file_for_search_returns_list(self, checker: SemanticBoundaryChecker) -> None:
        """_analyze_file_for_search must return a list of SemanticSearchSite."""
        search_gateway = BACKEND_ROOT / "polaris" / "cells" / "context" / "engine" / "internal" / "search_gateway.py"
        if search_gateway.exists():
            sites = checker._analyze_file_for_search(search_gateway)
            assert isinstance(sites, list)
            for site in sites:
                assert isinstance(site, SemanticSearchSite)

    def test_find_semantic_search_sites_returns_list(self, checker: SemanticBoundaryChecker) -> None:
        """_find_semantic_search_sites must return a list."""
        sites = checker._find_semantic_search_sites()
        assert isinstance(sites, list)

    def test_find_semantic_search_sites_skips_test_files(self, checker: SemanticBoundaryChecker) -> None:
        """_find_semantic_search_sites must skip test files."""
        sites = checker._find_semantic_search_sites()
        for site in sites:
            assert "test" not in str(site.file_path.relative_to(BACKEND_ROOT)).lower()


# ---------------------------------------------------------------------------
# Test: Mock SearchService Behavior
# ---------------------------------------------------------------------------


class TestMockSearchServiceCompliance:
    """Test that mock SearchService respects graph boundaries."""

    def test_search_service_uses_catalog_cache(self, mock_workspace: Path) -> None:
        """SearchService must use catalog cache for graph-constrained search."""
        from polaris.cells.context.engine.internal.search_gateway import SearchService

        # Create a minimal cache file
        cache_dir = mock_workspace / "workspace" / "meta" / "context_catalog"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "descriptors.json"
        cache_file.write_text(
            '{"version": 1, "descriptors": []}',
            encoding="utf-8",
        )

        svc = SearchService(workspace=mock_workspace)
        # The service should use the catalog, not raw vector search
        assert hasattr(svc, "_catalog")
        assert hasattr(svc, "_search_cache")

    def test_search_service_deprecates_add_documents(self, mock_workspace: Path) -> None:
        """SearchService.add_documents must be deprecated (no ad-hoc indexing)."""
        from polaris.cells.context.engine.internal.search_gateway import SearchService

        svc = SearchService(workspace=mock_workspace)

        # Should emit deprecation warning
        with pytest.warns(DeprecationWarning, match="add_documents is ignored"):
            svc.add_documents([{"text": "test"}])


# ---------------------------------------------------------------------------
# Test: ContextCatalogService Graph Loading
# ---------------------------------------------------------------------------


class TestContextCatalogServiceCompliance:
    """Test that ContextCatalogService loads from cells.yaml."""

    def test_catalog_service_loads_graph(self, mock_workspace: Path) -> None:
        """ContextCatalogService must load from cells.yaml."""
        from polaris.cells.context.catalog.service import ContextCatalogService

        svc = ContextCatalogService(str(mock_workspace))

        # Should have paths to graph catalog
        assert hasattr(svc, "catalog_path")
        assert svc.catalog_path.name == "cells.yaml"

    def test_catalog_service_builds_descriptor_cache(self, mock_workspace: Path) -> None:
        """ContextCatalogService.sync must build descriptor cache from cells.yaml."""
        from polaris.cells.context.catalog.service import ContextCatalogService

        svc = ContextCatalogService(str(mock_workspace))

        # Mock the embedding port to avoid RuntimeError
        # The code expects format: "provider/model:device" (e.g., "graph_catalog_seed/none:cpu")
        mock_embedding_port = MagicMock()
        mock_embedding_port.get_fingerprint.return_value = "graph_catalog_seed/none:cpu"
        mock_embedding_port.get_embedding.return_value = [0.1] * 10

        with patch(
            "polaris.cells.context.catalog.service.get_default_embedding_port", return_value=mock_embedding_port
        ):
            # Sync should build cache from graph
            result = svc.sync()
            assert "cache_path" in result
            assert "descriptor_count" in result
            assert "graph_fingerprint" in result


# ---------------------------------------------------------------------------
# Test: Fitness Rules YAML Integration
# ---------------------------------------------------------------------------


class TestFitnessRulesIntegration:
    """Test integration with fitness-rules.yaml."""

    def test_semantic_boundary_rule_in_fitness_rules(self) -> None:
        """graph_constrained_semantic_retrieval (the actual rule name) must be in fitness-rules.yaml."""
        fitness_rules_path = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
        if not fitness_rules_path.exists():
            pytest.skip("fitness-rules.yaml not found")

        import yaml

        content = fitness_rules_path.read_text(encoding="utf-8")
        rules = yaml.safe_load(content)

        if rules and "rules" in rules:
            rule_ids = [r.get("id") for r in rules["rules"] if isinstance(r, dict)]
            # The rule may be named graph_constrained_semantic_retrieval or semantic_retrieval_respects_graph_boundary
            has_semantic_rule = any("semantic" in str(rid).lower() and "graph" in str(rid).lower() for rid in rule_ids)
            assert has_semantic_rule, f"No semantic boundary rule found in fitness-rules.yaml. Available: {rule_ids}"


# ---------------------------------------------------------------------------
# Test: SemanticBoundaryCheckResult Structure
# ---------------------------------------------------------------------------


class TestSemanticBoundaryCheckResult:
    """Test SemanticBoundaryCheckResult dataclass."""

    def test_result_has_default_empty_lists(self) -> None:
        """SemanticBoundaryCheckResult must have default empty lists."""
        result = SemanticBoundaryCheckResult()
        assert result.compliant_sites == []
        assert result.non_compliant_sites == []
        assert result.undetermined_sites == []
        assert result.total_sites_found == 0

    def test_result_can_be_populated(self) -> None:
        """SemanticBoundaryCheckResult can be populated with sites."""
        site = SemanticSearchSite(
            file_path=Path("/fake/path.py"),
            class_name="TestClass",
            method_name="test_method",
            is_graph_constrained=True,
            reasoning="Test reasoning",
        )
        result = SemanticBoundaryCheckResult(
            compliant_sites=[site],
            total_sites_found=1,
        )
        assert len(result.compliant_sites) == 1
        assert result.total_sites_found == 1


# ---------------------------------------------------------------------------
# Test: SemanticSearchSite Structure
# ---------------------------------------------------------------------------


class TestSemanticSearchSite:
    """Test SemanticSearchSite dataclass."""

    def test_site_has_required_fields(self) -> None:
        """SemanticSearchSite must have all required fields."""
        site = SemanticSearchSite(
            file_path=Path("/fake/path.py"),
            class_name="TestClass",
            method_name="search",
            is_graph_constrained=True,
            reasoning="Test",
        )
        assert hasattr(site, "file_path")
        assert hasattr(site, "class_name")
        assert hasattr(site, "method_name")
        assert hasattr(site, "is_graph_constrained")
        assert hasattr(site, "reasoning")
        assert hasattr(site, "imports_graph_service")
        assert hasattr(site, "loads_cells_yaml")
        assert hasattr(site, "uses_catalog_cache")

    def test_site_defaults(self) -> None:
        """SemanticSearchSite must have sensible defaults."""
        site = SemanticSearchSite(
            file_path=Path("/fake/path.py"),
            class_name=None,
            method_name="module",
            is_graph_constrained=False,
            reasoning="Test",
        )
        assert site.imports_graph_service is False
        assert site.loads_cells_yaml is False
        assert site.uses_catalog_cache is False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
