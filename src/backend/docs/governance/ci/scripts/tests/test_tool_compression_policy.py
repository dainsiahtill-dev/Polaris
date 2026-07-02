"""Tests for KernelOne tool-compression policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.tool_compression_policy import evaluate_tool_compression


def _write_canonical_tool_module(workspace: Path) -> None:
    """Write the minimal canonical KernelOne tool module fixture."""
    tool_dir = workspace / "polaris" / "kernelone" / "tool"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "compaction.py").write_text(
        "def compact_tool_result(payload: object) -> object:\n    return payload\n",
        encoding="utf-8",
    )


def _write_cell_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a Cell source fixture into a temporary workspace."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_tool_compression_runner_uses_canonical_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the tool-compression policy."""
    _write_canonical_tool_module(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/runtime/example/internal/tooling.py",
        "def compress_tool_result(payload: object) -> object:\n    return payload\n",
    )

    policy = evaluate_tool_compression(tmp_path)
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_tool_compression()

    assert policy.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == policy.rule_id
    assert aggregate.violations == list(policy.violations)
    assert any("Local tool compression" in violation for violation in aggregate.violations)


def test_tool_compression_policy_warns_when_canonical_modules_missing(tmp_path: Path) -> None:
    """Missing canonical modules remain warnings when no local duplicates exist."""
    result = evaluate_tool_compression(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert result.warnings == ("Canonical kernelone/tool/ modules not found",)


def test_tool_compression_policy_accepts_centralized_kernelone_module(tmp_path: Path) -> None:
    """A workspace with canonical modules and no Cell-local duplicates passes."""
    _write_canonical_tool_module(tmp_path)

    result = evaluate_tool_compression(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert "Canonical kernelone/tool/ modules exist" in result.evidence
