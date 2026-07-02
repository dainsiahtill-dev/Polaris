"""Tests for KernelOne LLM import policy wiring."""

from __future__ import annotations

from pathlib import Path

from docs.governance.ci.scripts import fitness_rule_checker
from docs.governance.ci.scripts.llm_import_policy import evaluate_llm_import


def _write_kernelone_llm(workspace: Path) -> None:
    """Write the minimal canonical KernelOne LLM directory fixture."""
    llm_dir = workspace / "polaris" / "kernelone" / "llm"
    llm_dir.mkdir(parents=True, exist_ok=True)
    (llm_dir / "__init__.py").write_text('"""KernelOne LLM fixture."""\n', encoding="utf-8")


def _write_cell_source(workspace: Path, relative_path: str, content: str) -> None:
    """Write a Cell source fixture into a temporary workspace."""
    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_llm_import_runner_uses_canonical_policy(tmp_path: Path) -> None:
    """The aggregate fitness runner must match the LLM import policy."""
    _write_kernelone_llm(tmp_path)
    _write_cell_source(
        tmp_path,
        "polaris/cells/roles/adapters/internal/adapter.py",
        "def _call_role_llm(prompt: str) -> str:\n    return prompt\n",
    )

    policy = evaluate_llm_import(tmp_path)
    aggregate = fitness_rule_checker.FitnessRuleChecker(tmp_path).check_llm_import()

    assert policy.passed is False
    assert aggregate.passed is False
    assert aggregate.rule_id == policy.rule_id
    assert aggregate.violations == list(policy.violations)
    assert any("Local LLM caller" in violation for violation in aggregate.violations)


def test_llm_import_policy_requires_kernelone_llm_directory(tmp_path: Path) -> None:
    """Missing KernelOne LLM directory is a hard failure."""
    result = evaluate_llm_import(tmp_path)

    assert result.passed is False
    assert result.violations == ("kernelone/llm/ directory not found",)


def test_llm_import_policy_accepts_centralized_llm_directory(tmp_path: Path) -> None:
    """A workspace with KernelOne LLM and no local role caller passes."""
    _write_kernelone_llm(tmp_path)

    result = evaluate_llm_import(tmp_path)

    assert result.passed is True
    assert result.violations == ()
    assert "No local _call_role_llm implementations found in cells/" in result.evidence
