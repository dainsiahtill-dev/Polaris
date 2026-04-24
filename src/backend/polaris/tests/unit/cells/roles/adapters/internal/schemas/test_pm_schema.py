"""Tests for polaris.cells.roles.adapters.internal.schemas.pm_schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polaris.cells.roles.adapters.internal.schemas.pm_schema import (
    Task,
    TaskAnalysis,
    TaskListOutput,
)


class TestTask:
    def test_valid_task(self) -> None:
        task = Task(
            id="TASK-001",
            title="Implement login",
            description="Add user authentication functionality to the application",
            acceptance_criteria=["Users can log in", "Invalid credentials are rejected"],
            priority="high",
            phase="core",
            estimated_effort=5,
        )
        assert task.id == "TASK-001"
        assert task.priority == "high"
        assert task.phase == "core"

    def test_invalid_id_format(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Task(
                id="INVALID",
                title="Test",
                description="x" * 50,
                acceptance_criteria=["Done"],
                priority="high",
                phase="core",
                estimated_effort=1,
            )
        assert "TASK-\\d{3,}" in str(exc_info.value)

    def test_absolute_path_rejected_in_target_files(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Task(
                id="TASK-002",
                title="Test",
                description="x" * 50,
                target_files=["/absolute/path"],
                acceptance_criteria=["Done"],
                priority="high",
                phase="core",
                estimated_effort=1,
            )
        assert "Path must be relative" in str(exc_info.value)

    def test_parent_dir_rejected_in_target_files(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Task(
                id="TASK-003",
                title="Test",
                description="x" * 50,
                target_files=["../secret.txt"],
                acceptance_criteria=["Done"],
                priority="high",
                phase="core",
                estimated_effort=1,
            )
        assert "Path must be relative" in str(exc_info.value)

    def test_vague_criterion_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            Task(
                id="TASK-004",
                title="Testing vague criterion",
                description="x" * 50,
                acceptance_criteria=["适当的格式"],
                priority="high",
                phase="core",
                estimated_effort=1,
            )
        assert "Criterion too vague" in str(exc_info.value)

    def test_task_with_dependencies(self) -> None:
        task = Task(
            id="TASK-002",
            title="Second task",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=3,
            dependencies=["TASK-001"],
        )
        assert "TASK-001" in task.dependencies


class TestTaskAnalysis:
    def test_valid_analysis(self) -> None:
        analysis = TaskAnalysis(
            total_tasks=5,
            risk_level="medium",
            recommended_sequence=["TASK-001", "TASK-002"],
        )
        assert analysis.total_tasks == 5
        assert analysis.risk_level == "medium"

    def test_empty_recommendations(self) -> None:
        analysis = TaskAnalysis(total_tasks=0, risk_level="low", recommended_sequence=[])
        assert analysis.recommended_sequence == []


class TestTaskListOutput:
    def test_valid_task_list(self) -> None:
        task = Task(
            id="TASK-001",
            title="Implement feature",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=3,
        )
        output = TaskListOutput(
            tasks=[task],
            analysis=TaskAnalysis(
                total_tasks=1,
                risk_level="low",
                recommended_sequence=["TASK-001"],
            ),
        )
        assert len(output.tasks) == 1
        assert output.tasks[0].id == "TASK-001"

    def test_empty_tasks_incomplete(self) -> None:
        output = TaskListOutput()
        assert output.tasks == []

    def test_duplicate_task_ids_rejected(self) -> None:
        task1 = Task(
            id="TASK-001",
            title="Task 1",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=1,
        )
        task2 = Task(
            id="TASK-001",
            title="Task 2",
            description="y" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=1,
        )
        with pytest.raises(ValidationError) as exc_info:
            TaskListOutput(tasks=[task1, task2])
        assert "Duplicate task IDs" in str(exc_info.value)

    def test_unknown_dependency_rejected(self) -> None:
        task = Task(
            id="TASK-001",
            title="First task",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=1,
            dependencies=["TASK-999"],
        )
        with pytest.raises(ValidationError) as exc_info:
            TaskListOutput(tasks=[task])
        assert "depends on unknown task" in str(exc_info.value)

    def test_analysis_task_count_mismatch_rejected(self) -> None:
        task = Task(
            id="TASK-001",
            title="Task one",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=1,
        )
        analysis = TaskAnalysis(total_tasks=5, risk_level="low", recommended_sequence=[])
        with pytest.raises(ValidationError) as exc_info:
            TaskListOutput(tasks=[task], analysis=analysis)
        assert "total_tasks" in str(exc_info.value)

    def test_with_tool_calls(self) -> None:
        task = Task(
            id="TASK-001",
            title="A task",
            description="x" * 50,
            acceptance_criteria=["Done"],
            priority="high",
            phase="core",
            estimated_effort=1,
        )
        output = TaskListOutput(
            tasks=[task],
        )
        assert output.tool_calls == []
        assert output.is_complete is True
        assert output.next_action == "respond"
