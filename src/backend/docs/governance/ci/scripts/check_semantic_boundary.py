"""Check graph-constrained semantic retrieval governance.

The canonical policy lives in ``semantic_retrieval_boundary_policy``. This
module preserves the standalone CLI and legacy checker import path without
duplicating search-boundary analysis logic.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
BACKEND_ROOT = SCRIPT_DIR.parent.parent.parent.parent

try:
    from docs.governance.ci.scripts.fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from docs.governance.ci.scripts.semantic_retrieval_boundary_policy import (
        SemanticBoundaryCheckResult,
        SemanticRetrievalBoundaryPolicy,
        SemanticSearchSite,
        evaluate_semantic_retrieval_boundary,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(SCRIPT_DIR))
    from fitness_rule_checker import FitnessCheckResult, FitnessRuleChecker
    from semantic_retrieval_boundary_policy import (
        SemanticBoundaryCheckResult,
        SemanticRetrievalBoundaryPolicy,
        SemanticSearchSite,
        evaluate_semantic_retrieval_boundary,
    )


class SemanticBoundaryChecker(FitnessRuleChecker):
    """Compatibility checker for graph-constrained semantic retrieval."""

    GRAPH_CONSTRAINED_ENTRYPOINTS = SemanticRetrievalBoundaryPolicy.GRAPH_CONSTRAINED_ENTRYPOINTS
    KNOWN_UNCONSTRAINED = SemanticRetrievalBoundaryPolicy.KNOWN_UNCONSTRAINED
    SEMANTIC_SEARCH_PATTERNS = SemanticRetrievalBoundaryPolicy.SEMANTIC_SEARCH_PATTERNS
    GRAPH_CONSTRAINED_PATTERNS = SemanticRetrievalBoundaryPolicy.GRAPH_CONSTRAINED_PATTERNS
    UNCONSTRAINED_PATTERNS = SemanticRetrievalBoundaryPolicy.UNCONSTRAINED_PATTERNS

    def __init__(self, workspace: Path | None = None) -> None:
        """Initialize the checker with a backend workspace root."""
        super().__init__(workspace or BACKEND_ROOT)
        self._backend_root = self.workspace
        self._policy = SemanticRetrievalBoundaryPolicy(self._backend_root)

    def check_semantic_retrieval_boundary(self) -> FitnessCheckResult:
        """Check whether semantic retrieval respects graph boundaries."""
        policy_result = evaluate_semantic_retrieval_boundary(self._backend_root)
        result = FitnessCheckResult(
            rule_id=policy_result.rule_id,
            passed=policy_result.passed,
            evidence=list(policy_result.evidence),
            violations=list(policy_result.violations),
            warnings=list(policy_result.warnings),
        )
        result.message = policy_result.message
        result.details = policy_result.details
        return result

    def _find_semantic_search_sites(self) -> list[SemanticSearchSite]:
        """Find semantic search sites through the canonical policy."""
        return self._policy.find_semantic_search_sites()

    def _analyze_file_for_search(self, file_path: Path) -> list[SemanticSearchSite]:
        """Analyze one source file through the canonical policy."""
        return self._policy.analyze_file_for_search(file_path)

    def _analyze_class_for_search(
        self,
        class_node: ast.ClassDef,
        file_path: Path,
        content: str,
        relative_path: str,
    ) -> list[SemanticSearchSite]:
        """Analyze one class through the canonical policy.

        ``content`` and ``relative_path`` are retained for compatibility with
        older tests and callers. The policy derives the required facts from the
        AST node and file path.
        """
        _ = (content, relative_path)
        return self._policy.analyze_class_for_search(class_node, file_path, content)

    def _is_search_method(self, method_name: str) -> bool:
        """Return true when a method name indicates retrieval behavior."""
        return self._policy.is_search_method(method_name)

    def _build_reasoning(
        self,
        is_constrained: bool,
        has_graph_constraint: bool,
        has_unconstraint: bool,
    ) -> str:
        """Build an audit reason through the canonical policy."""
        return self._policy.build_reasoning(
            is_constrained=is_constrained,
            has_graph_constraint=has_graph_constraint,
            has_unconstraint=has_unconstraint,
        )

    def _analyze_search_sites(self, sites: list[SemanticSearchSite]) -> SemanticBoundaryCheckResult:
        """Categorize search sites through the canonical policy."""
        return self._policy.analyze_search_sites(sites)

    def _is_workspace_level_acceptable(self, site: SemanticSearchSite) -> bool:
        """Return true for workspace-level memory/indexing implementations."""
        return self._policy.is_workspace_level_acceptable(site)

    def check(self) -> FitnessCheckResult:
        """Return the graph-constrained semantic retrieval fitness result."""
        return self.check_semantic_retrieval_boundary()


def main() -> int:
    """Run the semantic boundary check and return a process exit code."""
    try:
        result = SemanticBoundaryChecker().check_semantic_retrieval_boundary()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: Script error: {exc}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("Semantic Boundary Fitness Check")
    print("=" * 70)
    print(f"Rule ID: {result.rule_id}")
    print(f"Status: {'PASSED' if result.passed else 'FAILED'}")
    print(f"Message: {getattr(result, 'message', '')}")
    print()

    details = getattr(result, "details", {})
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


if __name__ == "__main__":
    sys.exit(main())
