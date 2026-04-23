from typing import Any

from pydantic import BaseModel, Field, field_validator


class DirectorConfig(BaseModel):
    """Director configuration."""

    model: str | None = Field(default=None, description="Model override for Director")
    iterations: int = Field(default=1, description="Default director iterations")
    execution_mode: str = Field(
        default="parallel",
        description="Director workflow execution mode: serial or parallel",
    )
    max_parallel_tasks: int = Field(
        default=3,
        description="Maximum number of Director tasks to run concurrently",
    )
    ready_timeout_seconds: int = Field(
        default=30,
        description="Timeout for resolving Director-ready tasks",
    )
    claim_timeout_seconds: int = Field(
        default=30,
        description="Timeout for claiming one Director task",
    )
    phase_timeout_seconds: int = Field(
        default=900,
        description="Timeout for one Director phase execution",
    )
    complete_timeout_seconds: int = Field(
        default=30,
        description="Timeout for recording Director task completion",
    )
    task_timeout_seconds: int = Field(
        default=3600,
        description="Timeout for one Director task workflow",
    )
    forever: bool = Field(default=False, description="Run director in infinite loop")
    show_output: bool = Field(default=True, description="Show director output")

    @field_validator("execution_mode", mode="before")
    @classmethod
    def validate_execution_mode(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in {"serial", "parallel"}:
            return "parallel"
        return token

    @field_validator(
        "iterations",
        "max_parallel_tasks",
        "ready_timeout_seconds",
        "claim_timeout_seconds",
        "phase_timeout_seconds",
        "complete_timeout_seconds",
        "task_timeout_seconds",
        mode="before",
    )
    @classmethod
    def validate_positive_int(cls, value: Any) -> int:
        try:
            return max(1, int(value))
        except (ValueError, TypeError):
            return 1
