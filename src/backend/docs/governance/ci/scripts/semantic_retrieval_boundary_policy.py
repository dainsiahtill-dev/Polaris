"""Pure policy for graph-constrained semantic retrieval governance checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULE_ID = "graph_constrained_semantic_retrieval"


@dataclass
class SemanticSearchSite:
    """A semantic, descriptor, vector, or memory search implementation site."""

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
    """Detailed site classification for semantic retrieval boundary checks."""

    compliant_sites: list[SemanticSearchSite] = field(default_factory=list)
    non_compliant_sites: list[SemanticSearchSite] = field(default_factory=list)
    undetermined_sites: list[SemanticSearchSite] = field(default_factory=list)
    total_sites_found: int = 0


@dataclass(frozen=True)
class SemanticRetrievalBoundaryPolicyResult:
    """Evaluation result for graph-constrained semantic retrieval governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class SemanticRetrievalBoundaryPolicy:
    """Evaluate whether semantic retrieval respects graph Cell boundaries.

    The policy distinguishes Cell-level semantic retrieval from workspace-level
    memory/index search. Cell-level retrieval must go through graph-constrained
    services or explicitly reference catalog/cells.yaml boundaries. Workspace
    stores remain acceptable when they are not retrieval entrypoints for Cell
    context.

    Complexity:
        O(f + n) time for matching files and AST nodes. O(s) space for detected
        search sites and emitted evidence, where ``s`` is the number of search
        implementation sites.
    """

    GRAPH_CONSTRAINED_ENTRYPOINTS: frozenset[str] = frozenset(
        {
            "polaris/cells/context/engine/internal/search_gateway.py",
            "polaris/cells/context/catalog/service.py",
        }
    )
    KNOWN_UNCONSTRAINED: frozenset[str] = frozenset(
        {
            "polaris/kernelone/akashic/hybrid_memory.py",
            "polaris/kernelone/akashic/memory_manager.py",
            "polaris/kernelone/akashic/semantic_cache.py",
            "polaris/kernelone/akashic/semantic_memory.py",
            "polaris/kernelone/akashic/working_memory.py",
            "polaris/kernelone/memory/memory_store.py",
            "polaris/infrastructure/db/repositories/lancedb_code_search.py",
        }
    )
    SEMANTIC_SEARCH_PATTERNS: tuple[str, ...] = (
        "polaris/cells/**/search*.py",
        "polaris/cells/**/*semantic*.py",
        "polaris/cells/**/*descriptor*.py",
        "polaris/kernelone/**/search*.py",
        "polaris/kernelone/**/semantic*.py",
        "polaris/kernelone/**/*memory*.py",
        "polaris/infrastructure/**/search*.py",
    )
    GRAPH_CONSTRAINED_PATTERNS: tuple[str, ...] = (
        "ContextCatalogService",
        "SearchService",
        "cells.yaml",
        "_load_from_catalog",
        "_filter_by_cell",
        "graph_constrained",
    )
    UNCONSTRAINED_PATTERNS: tuple[str, ...] = (
        "AkashicSemanticMemory",
        "LanceDB",
        "vector_search",
        "embedding_search",
        "workspace_search",
    )
    WORKSPACE_LEVEL_ACCEPTABLE_PATTERNS: tuple[str, ...] = (
        "akashic/semantic_cache.py",
        "akashic/semantic_memory.py",
        "akashic/working_memory.py",
        "akashic/memory_manager.py",
        "akashic/hybrid_memory.py",
        "kernelone/memory/memory_store.py",
        "lancedb_code_search.py",
        "knowledge_pipeline/",
    )
    SEARCH_METHOD_PATTERNS: tuple[str, ...] = (
        "search",
        "retrieve",
        "query",
        "find",
        "lookup",
        "get_relevant",
        "semantic_",
        "vector_",
        "embedding_",
    )

    def __init__(self, backend_root: Path) -> None:
        """Create the policy evaluator for a backend workspace root."""
        self.backend_root = backend_root

    def evaluate(self) -> SemanticRetrievalBoundaryPolicyResult:
        """Evaluate all semantic search sites in the backend workspace."""
        search_sites = self.find_semantic_search_sites()
        check_result = self.analyze_search_sites(search_sites)
        details = self.build_details(check_result)
        violations: list[str] = []

        cell_level_violations = [
            site for site in check_result.non_compliant_sites if not self.is_workspace_level_acceptable(site)
        ]
        if cell_level_violations:
            details["cell_level_violations"] = [
                {
                    "file": self.relative_path(site.file_path),
                    "reasoning": site.reasoning,
                }
                for site in cell_level_violations
            ]
            violations.extend(
                f"Semantic search bypasses graph boundaries: {self.relative_path(site.file_path)}"
                for site in cell_level_violations
            )
            message = (
                f"Found {len(cell_level_violations)} Cell-level semantic search "
                "implementation(s) that bypass graph boundaries"
            )
        else:
            acceptable_workspace_search = [
                site for site in check_result.non_compliant_sites if self.is_workspace_level_acceptable(site)
            ]
            if acceptable_workspace_search:
                details["acceptable_workspace_search"] = [
                    {
                        "file": self.relative_path(site.file_path),
                        "reasoning": "Workspace-level semantic search is acceptable for code indexing",
                    }
                    for site in acceptable_workspace_search
                ]
                message = (
                    f"Found {len(acceptable_workspace_search)} workspace-level search "
                    "implementation(s) - acceptable for code indexing but not Cell retrieval"
                )
            else:
                message = (
                    f"All {check_result.total_sites_found} semantic search implementations respect graph boundaries"
                )

        evidence = (
            f"Total semantic search sites found: {check_result.total_sites_found}",
            f"Compliant sites: {len(check_result.compliant_sites)}",
        )
        return SemanticRetrievalBoundaryPolicyResult(
            rule_id=RULE_ID,
            passed=not violations,
            evidence=evidence,
            violations=tuple(violations),
            message=message,
            details=details,
        )

    def find_semantic_search_sites(self) -> list[SemanticSearchSite]:
        """Find semantic, descriptor, vector, and memory search implementations."""
        search_sites: list[SemanticSearchSite] = []
        seen: set[tuple[Path, str | None, str]] = set()

        for pattern in self.SEMANTIC_SEARCH_PATTERNS:
            for file_path in self.backend_root.glob(pattern):
                if is_test_path(file_path) or file_path.suffix != ".py":
                    continue

                for site in self.analyze_file_for_search(file_path):
                    key = (site.file_path, site.class_name, site.method_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    search_sites.append(site)

        return search_sites

    def analyze_file_for_search(self, file_path: Path) -> list[SemanticSearchSite]:
        """Analyze one Python file for semantic search implementations."""
        content = read_text(file_path)
        if content is None:
            return []

        relative_path = self.relative_path(file_path)
        if relative_path in self.GRAPH_CONSTRAINED_ENTRYPOINTS:
            return [
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
            ]

        if relative_path in self.KNOWN_UNCONSTRAINED:
            return [
                SemanticSearchSite(
                    file_path=file_path,
                    class_name=None,
                    method_name="module",
                    is_graph_constrained=False,
                    reasoning="Workspace-level semantic search - not for Cell boundary retrieval",
                )
            ]

        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError:
            return []

        sites: list[SemanticSearchSite] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                sites.extend(self.analyze_class_for_search(node, file_path, content))
        return sites

    def analyze_class_for_search(
        self,
        class_node: ast.ClassDef,
        file_path: Path,
        _content: str,
    ) -> list[SemanticSearchSite]:
        """Analyze one class for semantic search methods."""
        class_name = class_node.name
        is_semantic_class = any(
            pattern in class_name.lower() for pattern in ("search", "semantic", "descriptor", "memory", "vector")
        )
        if not is_semantic_class:
            return []

        class_content = ast.unparse(class_node)
        imports_graph_service = any(
            pattern in class_content for pattern in ("ContextCatalogService", "SearchService", "cells.yaml")
        )
        has_graph_constraint = any(pattern in class_content for pattern in self.GRAPH_CONSTRAINED_PATTERNS)
        has_unconstraint = any(pattern in class_content for pattern in self.UNCONSTRAINED_PATTERNS)

        sites: list[SemanticSearchSite] = []
        for node in class_node.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            method_name = node.name
            if not self.is_search_method(method_name):
                continue

            is_constrained = has_graph_constraint or imports_graph_service
            sites.append(
                SemanticSearchSite(
                    file_path=file_path,
                    class_name=class_name,
                    method_name=method_name,
                    is_graph_constrained=is_constrained,
                    reasoning=self.build_reasoning(
                        is_constrained=is_constrained,
                        has_graph_constraint=has_graph_constraint,
                        has_unconstraint=has_unconstraint,
                    ),
                    imports_graph_service=imports_graph_service,
                    loads_cells_yaml="cells.yaml" in class_content,
                    uses_catalog_cache="catalog" in class_content.lower(),
                )
            )
        return sites

    def is_search_method(self, method_name: str) -> bool:
        """Return true when a method name indicates retrieval behavior."""
        lowered = method_name.lower()
        return any(pattern in lowered for pattern in self.SEARCH_METHOD_PATTERNS)

    def build_reasoning(
        self,
        *,
        is_constrained: bool,
        has_graph_constraint: bool,
        has_unconstraint: bool,
    ) -> str:
        """Build an audit-friendly reason for a classified search site."""
        if is_constrained and has_graph_constraint:
            return "Uses graph-constrained search (ContextCatalogService/SearchService)"
        if is_constrained:
            return "Imports graph service - assumes graph constraint"
        if has_unconstraint:
            return "Direct vector/workspace search without graph filtering"
        return "Search implementation - graph constraint status undetermined"

    def analyze_search_sites(self, sites: list[SemanticSearchSite]) -> SemanticBoundaryCheckResult:
        """Categorize detected search sites by graph-boundary compliance."""
        result = SemanticBoundaryCheckResult(total_sites_found=len(sites))
        for site in sites:
            if site.is_graph_constrained:
                result.compliant_sites.append(site)
            elif self.is_workspace_level_acceptable(site):
                site.reasoning = "Workspace-level memory search - not for Cell boundary retrieval"
                result.compliant_sites.append(site)
            elif site.imports_graph_service or site.uses_catalog_cache:
                result.compliant_sites.append(site)
            else:
                result.non_compliant_sites.append(site)
        return result

    def is_workspace_level_acceptable(self, site: SemanticSearchSite) -> bool:
        """Return true for workspace-level memory/indexing search implementations."""
        file_str = str(site.file_path).replace("\\", "/")
        return any(pattern in file_str for pattern in self.WORKSPACE_LEVEL_ACCEPTABLE_PATTERNS)

    def build_details(self, check_result: SemanticBoundaryCheckResult) -> dict[str, Any]:
        """Build structured details for CLI, UI, and test assertions."""
        return {
            "total_sites_found": check_result.total_sites_found,
            "compliant_sites": [self.site_detail(site) for site in check_result.compliant_sites],
            "non_compliant_sites": [self.site_detail(site) for site in check_result.non_compliant_sites],
            "undetermined_sites": [self.site_detail(site) for site in check_result.undetermined_sites],
        }

    def site_detail(self, site: SemanticSearchSite) -> dict[str, Any]:
        """Return a stable dictionary representation of one search site."""
        return {
            "file": self.relative_path(site.file_path),
            "class": site.class_name,
            "method": site.method_name,
            "reasoning": site.reasoning,
        }

    def relative_path(self, path: Path) -> str:
        """Return a stable path relative to the backend workspace."""
        try:
            return str(path.relative_to(self.backend_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def is_test_path(path: Path) -> bool:
    """Return true for test files and test directories."""
    return any(part in {"test", "tests"} for part in path.parts) or path.name.startswith("test_")


def read_text(path: Path) -> str | None:
    """Read a UTF-8 source file, returning None when it cannot be inspected."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def evaluate_semantic_retrieval_boundary(workspace: Path) -> SemanticRetrievalBoundaryPolicyResult:
    """Evaluate graph-constrained semantic retrieval policy for a workspace."""
    return SemanticRetrievalBoundaryPolicy(workspace).evaluate()
