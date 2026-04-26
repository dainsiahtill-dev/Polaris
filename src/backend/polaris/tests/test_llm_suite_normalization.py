from __future__ import annotations

from polaris.cells.llm.evaluation.public.service import EvaluationRunner


def test_normalize_suites_connectivity_role_forces_connectivity_only():
    runner = EvaluationRunner(workspace=".")
    suites = runner.normalize_suites(["connectivity", "response", "qualification"], "connectivity")
    assert suites == ["connectivity"]


def test_normalize_suites_empty_for_connectivity_role_returns_connectivity_only():
    runner = EvaluationRunner(workspace=".")
    suites = runner.normalize_suites([], "connectivity")
    assert suites == ["connectivity"]


def test_normalize_suites_non_connectivity_role_keeps_requested_unique_order():
    runner = EvaluationRunner(workspace=".")
    suites = runner.normalize_suites(["response", "response", "qualification"], "qa")
    assert suites == ["response", "qualification"]
