"""PM (尚书令) Output Schema - Task list generation.

Defines the structured output format for project management task planning.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .base import BaseToolEnabledOutput


class Task(BaseModel):
    """Single task definition."""

    id: str = Field(..., description="Task ID, format: TASK-XXX (e.g., TASK-001)", pattern=r"^TASK-\d{3,}$")
    title: str = Field(..., min_length=5, max_length=100, description="Task title, concise and clear")
    description: str = Field(
        ..., min_length=20, max_length=800, description="Detailed task description (50-200 words recommended)"
    )
    target_files: list[str] = Field(default_factory=list, description="Target files to modify, relative paths only")
    acceptance_criteria: list[str] = Field(
        ..., min_length=1, max_length=10, description="List of verifiable acceptance criteria"
    )
    priority: Literal["high", "medium", "low"] = Field(..., description="Task priority level")
    phase: Literal["bootstrap", "core", "polish"] = Field(..., description="Implementation phase")
    estimated_effort: int = Field(..., ge=1, le=13, description="Story points (1-13, Fibonacci scale)")
    dependencies: list[str] = Field(default_factory=list, description="List of dependent task IDs")

    @field_validator("target_files")
    @classmethod
    def validate_paths(cls, v: list[str]) -> list[str]:
        """Ensure all paths are relative and safe."""
        for path in v:
            if path.startswith("/") or ".." in path:
                raise ValueError(f"Path must be relative, got: {path}")
        return v

    @field_validator("acceptance_criteria")
    @classmethod
    def validate_criteria(cls, v: list[str]) -> list[str]:
        """Ensure criteria are specific (avoid vague words)."""
        vague_words = ["适当的", "合适的", "根据需要", "等等", "适当"]
        for criterion in v:
            for word in vague_words:
                if word in criterion:
                    raise ValueError(f"Criterion too vague, avoid '{word}': {criterion}")
        return v


class TaskAnalysis(BaseModel):
    """Task list analysis summary."""

    total_tasks: int = Field(..., ge=0)
    risk_level: Literal["low", "medium", "high"] = Field(...)
    key_risks: list[str] = Field(default_factory=list)
    recommended_sequence: list[str] = Field(..., description="Recommended task execution order (task IDs)")


class TaskListOutput(BaseToolEnabledOutput):
    """PM structured output - Task list with analysis and optional tool calls.

    This model ensures LLM outputs conform to the expected task planning format.
    Supports tool calls for gathering project information before final output.
    """

    tasks: list[Task] = Field(
        default_factory=list,
        min_length=1,
        max_length=20,
        description="List of tasks to implement (empty if need more tools)",
    )
    analysis: TaskAnalysis = Field(
        default_factory=lambda: TaskAnalysis(total_tasks=0, risk_level="low", recommended_sequence=[]),
        description="Overall analysis of the task plan",
    )

    @field_validator("tasks")
    @classmethod
    def check_unique_ids(cls, v: list[Task]) -> list[Task]:
        """Ensure all task IDs are unique."""
        if not v:
            return v
        ids = [t.id for t in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task IDs found")
        return v

    @field_validator("tasks")
    @classmethod
    def check_dependency_exists(cls, v: list[Task]) -> list[Task]:
        """Ensure all dependencies reference existing tasks."""
        if not v:
            return v
        valid_ids = {t.id for t in v}
        for task in v:
            for dep in task.dependencies:
                if dep not in valid_ids:
                    raise ValueError(f"Task {task.id} depends on unknown task: {dep}")
        return v

    @field_validator("analysis", mode="after")
    @classmethod
    def check_analysis_consistency(cls, v: TaskAnalysis, info) -> TaskAnalysis:
        """Ensure analysis is consistent with tasks when complete."""
        data = info.data
        tasks = data.get("tasks", [])
        if tasks and v.total_tasks != len(tasks):
            raise ValueError(f"Analysis total_tasks ({v.total_tasks}) != actual tasks ({len(tasks)})")
        return v
