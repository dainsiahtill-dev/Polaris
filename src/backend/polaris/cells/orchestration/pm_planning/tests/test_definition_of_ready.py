"""Unit tests for orchestration.pm_planning internal definition_of_ready."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal.definition_of_ready import (
    DorCheckResult,
    evaluate_definition_of_ready,
)


class TestEvaluateDefinitionOfReady:
    def test_empty_task_list_not_ready(self) -> None:
        result = evaluate_definition_of_ready([])
        assert result["ok"] is False
        assert result["total_tasks"] == 0

    def test_minimally_ready_task(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 2,
            }
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is True
        assert result["ready_count"] == 1
        assert result["tasks"][0]["ready"] is True
        assert result["tasks"][0]["missing"] == []

    def test_missing_multiple_checks(self) -> None:
        tasks = [{"id": "t1"}]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is False
        task_result = result["tasks"][0]
        assert task_result["ready"] is False
        assert set(task_result["missing"]) == {
            "title",
            "goal",
            "measurable_acceptance",
            "concrete_scope",
            "estimate",
        }

    def test_unknown_dependency_not_ready(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 2,
                "dependencies": ["missing-task"],
            }
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is False
        assert "valid_dependencies" in result["tasks"][0]["missing"]

    def test_dangling_depends_on_not_ready(self) -> None:
        # Dependencies emitted under ``depends_on`` (as the PM/dispatch pipeline
        # does) must be validated; a dangling ref must mark the task not-ready.
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 2,
                "depends_on": ["does-not-exist"],
            }
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is False
        assert result["tasks"][0]["has_valid_dependencies"] is False
        assert "valid_dependencies" in result["tasks"][0]["missing"]

    def test_known_depends_on_is_ready(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build auth core",
                "goal": "Core auth",
                "acceptance_criteria": ["verify src/auth/core.py exists"],
                "scope_paths": ["src/auth/core.py"],
                "estimated_effort": 2,
            },
            {
                "id": "t2",
                "title": "Build login",
                "goal": "Login page",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 1,
                "depends_on": ["t1"],
            },
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is True
        assert result["ready_count"] == 2

    def test_known_dependency_is_ready(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build auth core",
                "goal": "Core auth",
                "acceptance_criteria": ["verify src/auth/core.py exists"],
                "scope_paths": ["src/auth/core.py"],
                "estimated_effort": 2,
            },
            {
                "id": "t2",
                "title": "Build login",
                "goal": "Login page",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 1,
                "dependencies": ["t1"],
            },
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is True
        assert result["ready_count"] == 2

    def test_require_risk_assessment(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 2,
            }
        ]
        result_without = evaluate_definition_of_ready(tasks)
        assert result_without["ok"] is True

        result_with = evaluate_definition_of_ready(tasks, require_risk_assessment=True)
        assert result_with["ok"] is False
        assert "risk_assessment" in result_with["tasks"][0]["missing"]

    def test_risk_assessment_via_risk_ids(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "estimated_effort": 2,
                "risk_ids": ["risk-1"],
            }
        ]
        result = evaluate_definition_of_ready(tasks, require_risk_assessment=True)
        assert result["ok"] is True

    def test_size_class_estimate(self) -> None:
        tasks = [
            {
                "id": "t1",
                "title": "Build login",
                "goal": "Create a login form",
                "acceptance_criteria": ["verify login returns 200"],
                "scope_paths": ["src/auth/login.py"],
                "size": "m",
            }
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["ok"] is True
        assert result["tasks"][0]["has_estimate"] is True

    def test_aggregate_missing_counts(self) -> None:
        tasks = [
            {"id": "t1", "title": "A"},
            {"id": "t2", "title": "B"},
        ]
        result = evaluate_definition_of_ready(tasks)
        assert result["aggregate_missing"]["goal"] == 2
        assert result["aggregate_missing"]["measurable_acceptance"] == 2


class TestDorCheckResult:
    def test_to_dict(self) -> None:
        result = DorCheckResult(
            task_id="t1",
            ready=True,
            has_title=True,
            missing=("estimate",),
        )
        d = result.to_dict()
        assert d["task_id"] == "t1"
        assert d["ready"] is True
        assert d["missing"] == ["estimate"]

    def test_defaults_not_ready(self) -> None:
        result = DorCheckResult()
        assert result.ready is False
        assert result.task_id == ""


@pytest.mark.parametrize(
    "estimate_value,expected",
    [
        (1, True),
        (1.5, True),
        (0, False),
        (-1, False),
        ("l", True),
        ("xl", True),
        ("", False),
        (None, False),
    ],
)
def test_has_estimate_variants(estimate_value: Any, expected: bool) -> None:
    from polaris.cells.orchestration.pm_planning.internal.definition_of_ready import _has_estimate

    task = {"estimated_effort": estimate_value}
    assert _has_estimate(task) is expected
