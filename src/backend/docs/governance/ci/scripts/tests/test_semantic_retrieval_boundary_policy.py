"""Tests for graph-constrained semantic retrieval policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.check_semantic_boundary import SemanticBoundaryChecker
from docs.governance.ci.scripts.semantic_retrieval_boundary_policy import (
    RULE_ID,
    evaluate_semantic_retrieval_boundary,
)


def _write_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a UTF-8 Python source fixture."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_semantic_boundary_entrypoints_use_canonical_policy(tmp_path: Path) -> None:
    """The standalone and aggregate entrypoints must match the policy."""
    _write_source(
        tmp_path,
        "polaris/cells/example/semantic_search.py",
        """
class ExampleSemanticSearch:
    def search(self, query: str) -> list[str]:
        ContextCatalogService
        return [query]
""",
    )

    policy = evaluate_semantic_retrieval_boundary(tmp_path)
    standalone = SemanticBoundaryChecker(tmp_path).check()
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_semantic_retrieval_boundary()

    assert policy.passed is True
    assert standalone.passed is True
    assert aggregate.passed is True
    assert policy.rule_id == standalone.rule_id == aggregate.rule_id == RULE_ID
    assert aggregate.evidence == list(policy.evidence) == standalone.evidence


def test_semantic_boundary_policy_reports_cell_level_bypass(tmp_path: Path) -> None:
    """Cell-level semantic search without graph constraints is a hard violation."""
    _write_source(
        tmp_path,
        "polaris/cells/example/semantic_search.py",
        """
class ExampleSemanticSearch:
    def search(self, query: str) -> list[str]:
        vector_search
        return [query]
""",
    )

    result = evaluate_semantic_retrieval_boundary(tmp_path)

    assert result.passed is False
    assert result.rule_id == RULE_ID
    assert any("polaris/cells/example/semantic_search.py" in item for item in result.violations)
    assert result.details["cell_level_violations"]


def test_semantic_boundary_policy_accepts_workspace_level_memory(tmp_path: Path) -> None:
    """Workspace-level memory search remains acceptable outside Cell retrieval."""
    _write_source(
        tmp_path,
        "polaris/kernelone/akashic/semantic_memory.py",
        """
class AkashicSemanticMemory:
    def search(self, query: str) -> list[str]:
        vector_search
        return [query]
""",
    )

    result = evaluate_semantic_retrieval_boundary(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert result.details["compliant_sites"][0]["file"] == "polaris/kernelone/akashic/semantic_memory.py"
