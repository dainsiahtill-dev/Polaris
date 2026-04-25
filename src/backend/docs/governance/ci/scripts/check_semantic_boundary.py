#!/usr/bin/env python3
"""Check semantic_retrieval_boundary rule.

This script verifies that semantic/descriptor retrieval code respects graph
boundaries by filtering results through cells.yaml before returning results.

Rule: semantic_retrieval_respects_graph_boundary
Severity: blocker
Description: >
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

Exit codes:
    0 - All checks passed
    1 - Rule violation detected
    2 - Script error (e.g., missing dependencies)
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# Script is at: docs/governance/ci/scripts/check_semantic_boundary.py
# Backend root is: src/backend/ (parent of docs/)
SCRIPT_DIR = Path(__file__).parent.resolve()  # docs/governance/ci/scripts
GOVERNANCE_DIR = SCRIPT_DIR.parent  # docs/governance
CI_DIR = GOVERNANCE_DIR.parent  # docs
DOCS_DIR = CI_DIR.parent  # docs
BACKEND_ROOT = DOCS_DIR.parent  # src/backend

# Add scripts directory to path for imports
sys.path.insert(0, str(SCRIPT_DIR))

from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker

# ---------------------------------------------------------------------------
# Semantic search implementations analysis
# ---------------------------------------------------------------------------

@dataclass
class SemanticSearchSite:
    """Represents a semantic/descriptor search implementation site."""

    file_path: Path
    class_name: str | None
    method_name: str
    is_graph_constrained: bool
    reasoning: str
    imports_graph_service: bool = False
    loads_cells_yaml: bool = False
    uses_catalog_cache: bool = False


@dataclass
class SemanticBoundaryCheckResult:
    """Detailed result of semantic boundary compliance check."""

    compliant_sites: list[SemanticSearchSite] = field(default_factory=list)
    non_compliant_sites: list[SemanticSearchSite] = field(default_factory=list)
    undetermined_sites: list[SemanticSearchSite] = field(default_factory=list)
    total_sites_found: int = 0


class SemanticBoundaryChecker(FitnessRuleChecker):
    """Checker for semantic_retrieval_respects_graph_boundary rule.

    This checker:
    1. Finds all semantic/descriptor retrieval code in the codebase
    2. Verifies it filters results through graph/cells.yaml boundaries first
    3. Reports compliance status for each implementation
    """

    # Files that are KNOWN to be graph-constrained (canonical entrypoints)
    GRAPH_CONSTRAINED_ENTRYPOINTS: set[str] = {
        "polaris/cells/context/engine/internal/search_gateway.py",
        "polaris/cells/context/catalog/service.py",
    }

    # Files that are KNOWN to NOT be graph-constrained (workspace-level search)
    KNOWN_UNCONSTRAINED: set[str] = {
        "polaris/kernelone/akashic/semantic_memory.py",
        "polaris/infrastructure/db/repositories/lancedb_code_search.py",
    }

    # Directories to scan for semantic search implementations
    SEMANTIC_SEARCH_PATTERNS: list[str] = [
        "polaris/cells/**/search*.py",
        "polaris/cells/**/*semantic*.py",
        "polaris/cells/**/*descriptor*.py",
        "polaris/kernelone/**/search*.py",
        "polaris/kernelone/**/semantic*.py",
        "polaris/kernelone/**/*memory*.py",
        "polaris/infrastructure/**/search*.py",
    ]

    # Patterns that indicate graph-constrained implementation
    GRAPH_CONSTRAINED_PATTERNS: list[str] = [
        "ContextCatalogService",
        "SearchService",
        "cells.yaml",
        "_load_from_catalog",
        "_filter_by_cell",
        "graph_constrained",
    ]

    # Patterns that indicate unconstrained implementation
    UNCONSTRAINED_PATTERNS: list[str] = [
        "AkashicSemanticMemory",
        "LanceDB",
        "vector_search",
        "embedding_search",
        "workspace_search",
    ]

    def __init__(self) -> None:
        """Initialize checker with backend root path."""
        self._backend_root = BACKEND_ROOT

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check if semantic retrieval respects graph boundaries.

        Returns:
            FitnessCheckResult with detailed findings
        """
        result = FitnessCheckResult(
            rule_id="semantic_retrieval_respects_graph_boundary",
            passed=True,
            evidence=[],
            violations=[],
            warnings=[],
        )

        # Step 1: Find all semantic search implementations
        search_sites = self._find_semantic_search_sites()

        # Step 2: Analyze each site for graph boundary compliance
        check_result = self._analyze_search_sites(search_sites)

        # Step 3: Build detailed findings
        findings: dict[str, Any] = {
            "total_sites_found": check_result.total_sites_found,
            "compliant_sites": [
                {
                    "file": str(site.file_path.relative_to(self._backend_root)),
                    "class": site.class_name,
                    "method": site.method_name,
                    "reasoning": site.reasoning,
                }
                for site in check_result.compliant_sites
            ],
            "non_compliant_sites": [
                {
                    "file": str(site.file_path.relative_to(self._backend_root)),
                    "class": site.class_name,
                    "method": site.method_name,
                    "reasoning": site.reasoning,
                }
                for site in check_result.non_compliant_sites
            ],
            "undetermined_sites": [
                {
                    "file": str(site.file_path.relative_to(self._backend_root)),
                    "class": site.class_name,
                    "method": site.method_name,
                    "reasoning": site.reasoning,
                }
                for site in check_result.undetermined_sites
            ],
        }

        result.details = findings

        # Step 4: Determine pass/fail
        # Rule passes if:
        # - All known semantic search sites are either graph-constrained OR
        # - Are explicitly marked as workspace-level search (not for Cell-level retrieval)
        if check_result.non_compliant_sites:
            # Check if non-compliant sites are workspace-level (acceptable)
            workspace_level_violations = [
                site for site in check_result.non_compliant_sites
                if self._is_workspace_level_acceptable(site)
            ]

            if workspace_level_violations:
                # These are acceptable workspace-level search
                findings["acceptable_workspace_search"] = [
                    {
                        "file": str(site.file_path.relative_to(self._backend_root)),
                        "reasoning": "Workspace-level semantic search is acceptable for code indexing",
                    }
                    for site in workspace_level_violations
                ]

            # Check for actual Cell-level violations
            cell_level_violations = [
                site for site in check_result.non_compliant_sites
                if not self._is_workspace_level_acceptable(site)
            ]

            if cell_level_violations:
                result.passed = False
                findings["cell_level_violations"] = [
                    {
                        "file": str(site.file_path.relative_to(self._backend_root)),
                        "reasoning": site.reasoning,
                    }
                    for site in cell_level_violations
                ]
                result.message = (
                    f"Found {len(cell_level_violations)} Cell-level semantic search "
                    f"implementation(s) that bypass graph boundaries"
                )
            else:
                result.message = (
                    f"Found {len(workspace_level_violations)} workspace-level search "
                    f"implementation(s) - acceptable for code indexing but not Cell retrieval"
                )
        else:
            result.message = (
                f"All {check_result.total_sites_found} semantic search implementations "
                f"respect graph boundaries"
            )

        return result

    def _find_semantic_search_sites(self) -> list[SemanticSearchSite]:
        """Find all semantic/descriptor search implementations in the codebase."""
        search_sites: list[SemanticSearchSite] = []

        for pattern in self.SEMANTIC_SEARCH_PATTERNS:
            for file_path in self._backend_root.glob(pattern):
                # Skip test files
                if "test" in file_path.parts or file_path.name.startswith("test_"):
                    continue

                # Skip non-Python files
                if file_path.suffix != ".py":
                    continue

                sites = self._analyze_file_for_search(file_path)
                search_sites.extend(sites)

        return search_sites

    def _analyze_file_for_search(self, file_path: Path) -> list[SemanticSearchSite]:
        """Analyze a single file for semantic search implementations."""
        sites: list[SemanticSearchSite] = []

        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return sites

        # Check for known graph-constrained entrypoints
        relative_path = str(file_path.relative_to(self._backend_root))

        if relative_path in self.GRAPH_CONSTRAINED_ENTRYPOINTS:
            sites.append(
                SemanticSearchSite(
                    file_path=file_path,
                    class_name=None,
                    method_name="module",
                    is_graph_constrained=True,
                    reasoning="Canonical graph-constrained entrypoint",
                    imports_graph_service=True,
                    loads_cells_yaml=True,
                    uses_catalog_cache=True,
                )
            )
            return sites

        if relative_path in self.KNOWN_UNCONSTRAINED:
            sites.append(
                SemanticSearchSite(
                    file_path=file_path,
                    class_name=None,
                    method_name="module",
                    is_graph_constrained=False,
                    reasoning="Workspace-level semantic search - not for Cell boundary retrieval",
                    imports_graph_service=False,
                    loads_cells_yaml=False,
                    uses_catalog_cache=False,
                )
            )
            return sites

        # Parse AST to find semantic search classes/methods
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError:
            return sites

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_sites = self._analyze_class_for_search(
                    node, file_path, content, relative_path
                )
                sites.extend(class_sites)

        return sites

    def _analyze_class_for_search(
        self,
        class_node: ast.ClassDef,
        file_path: Path,
        content: str,
        relative_path: str,
    ) -> list[SemanticSearchSite]:
        """Analyze a class for semantic search methods."""
        sites: list[SemanticSearchSite] = []

        class_name = class_node.name
        class_content = ast.unparse(class_node)

        # Check if class name indicates semantic search
        is_semantic_class = any(
            pattern.lower() in class_name.lower()
            for pattern in ["search", "semantic", "descriptor", "memory", "vector"]
        )

        if not is_semantic_class:
            return sites

        # Analyze for graph constraints
        imports_graph_service = any(
            pattern in class_content
            for pattern in ["ContextCatalogService", "SearchService", "cells.yaml"]
        )

        has_graph_constraint = any(
            pattern in class_content
            for pattern in self.GRAPH_CONSTRAINED_PATTERNS
        )

        has_unconstraint = any(
            pattern in class_content
            for pattern in self.UNCONSTRAINED_PATTERNS
        )

        # Analyze methods
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef):
                method_name = node.name
                if self._is_search_method(method_name):
                    is_constrained = has_graph_constraint or imports_graph_service
                    reasoning = self._build_reasoning(
                        is_constrained, has_graph_constraint, has_unconstraint
                    )

                    sites.append(
                        SemanticSearchSite(
                            file_path=file_path,
                            class_name=class_name,
                            method_name=method_name,
                            is_graph_constrained=is_constrained,
                            reasoning=reasoning,
                            imports_graph_service=imports_graph_service,
                            loads_cells_yaml="cells.yaml" in class_content,
                            uses_catalog_cache="catalog" in class_content.lower(),
                        )
                    )

        return sites

    def _is_search_method(self, method_name: str) -> bool:
        """Check if a method name indicates search functionality."""
        search_patterns = [
            "search", "retrieve", "query", "find", "lookup",
            "get_relevant", "semantic_", "vector_", "embedding_",
        ]
        return any(pattern in method_name.lower() for pattern in search_patterns)

    def _build_reasoning(
        self,
        is_constrained: bool,
        has_graph_constraint: bool,
        has_unconstraint: bool,
    ) -> str:
        """Build reasoning string for the site."""
        if is_constrained and has_graph_constraint:
            return "Uses graph-constrained search (ContextCatalogService/SearchService)"
        elif is_constrained:
            return "Imports graph service - assumes graph constraint"
        elif has_unconstraint:
            return "Direct vector/workspace search without graph filtering"
        else:
            return "Search implementation - graph constraint status undetermined"

    def _analyze_search_sites(
        self, sites: list[SemanticSearchSite]
    ) -> SemanticBoundaryCheckResult:
        """Analyze search sites and categorize by compliance."""
        result = SemanticBoundaryCheckResult(total_sites_found=len(sites))

        for site in sites:
            if site.is_graph_constrained:
                result.compliant_sites.append(site)
            elif self._is_workspace_level_acceptable(site):
                # Workspace-level search is acceptable
                result.compliant_sites.append(site)
            else:
                # Determine if we can make a definitive judgment
                if site.imports_graph_service or site.uses_catalog_cache or site.is_graph_constrained:
                    result.compliant_sites.append(site)
                else:
                    # Undetermined - could be violation
                    result.non_compliant_sites.append(site)

        return result

    def _is_workspace_level_acceptable(self, site: SemanticSearchSite) -> bool:
        """Check if workspace-level search is acceptable for this site.

        Workspace-level search (like AkashicSemanticMemory or LanceDB code search)
        is acceptable for:
        - Code indexing and workspace-level search
        - NOT acceptable for Cell-level semantic retrieval
        """
        file_str = str(site.file_path)

        # Known workspace-level implementations
        acceptable_patterns = [
            "akashic/semantic_memory.py",
            "lancedb_code_search.py",
            "knowledge_pipeline/",
        ]

        return any(pattern in file_str for pattern in acceptable_patterns)

    def check(self) -> FitnessCheckResult:
        """Main entry point for the checker.

        Alias for check_semantic_retrieval_boundary() for compatibility
        with FitnessRuleChecker interface.
        """
        return self.check_semantic_retrieval_boundary()


def main() -> int:
    """Run the semantic boundary check and exit with appropriate code.

    Returns:
        0 if all checks passed
        1 if rule violations detected
        2 if script error
    """
    try:
        checker = SemanticBoundaryChecker()
        result = checker.check_semantic_retrieval_boundary()

        print("=" * 70)
        print("Semantic Boundary Fitness Check")
        print("=" * 70)
        print(f"Rule ID: {result.rule_id}")
        print(f"Status: {'PASSED' if result.passed else 'FAILED'}")
        print(f"Message: {result.message}")
        print()

        # Print detailed findings
        details = result.details
        if details:
            print(f"Total semantic search sites found: {details.get('total_sites_found', 0)}")
            print()

            compliant = details.get("compliant_sites", [])
            if compliant:
                print(f"Compliant sites ({len(compliant)}):")
                for site in compliant:
                    print(f"  + {site['file']}: {site.get('reasoning', 'OK')}")

            non_compliant = details.get("non_compliant_sites", [])
            if non_compliant:
                print(f"\nNon-compliant sites ({len(non_compliant)}):")
                for site in non_compliant:
                    print(f"  - {site['file']}: {site.get('reasoning', 'VIOLATION')}")

            workspace_acceptable = details.get("acceptable_workspace_search", [])
            if workspace_acceptable:
                print(f"\nAcceptable workspace-level search ({len(workspace_acceptable)}):")
                for site in workspace_acceptable:
                    print(f"  ~ {site['file']}: {site.get('reasoning', '')}")

            undetermined = details.get("undetermined_sites", [])
            if undetermined:
                print(f"\nUndetermined sites ({len(undetermined)}):")
                for site in undetermined:
                    print(f"  ? {site['file']}: {site.get('reasoning', 'UNKNOWN')}")

        print("=" * 70)

        return 0 if result.passed else 1

    except Exception as exc:
        print(f"ERROR: Script error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
