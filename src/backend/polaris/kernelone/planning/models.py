"""Planning domain models."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Constraints:
    """Planning constraints specification.

    Attributes:
        max_steps: Maximum number of steps allowed in the plan
        max_duration: Maximum allowed duration in seconds
        required_resources: Tuple of required resource identifiers
        forbidden_actions: Tuple of forbidden action identifiers
        deadline: Optional deadline timestamp
        metadata: Additional constraint metadata
    """

    max_steps: int | None = None
    max_duration: int | None = None
    required_resources: tuple[str, ...] = field(default_factory=tuple)
    forbidden_actions: tuple[str, ...] = field(default_factory=tuple)
    deadline: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanStep:
    """A single step in a plan.

    Attributes:
        id: Unique identifier for the step
        description: Human-readable description of the step
        depends_on: Tuple of step IDs that must complete before this step
        estimated_duration: Estimated duration in seconds (optional)
        metadata: Additional metadata for the step (optional)
    """

    id: str
    description: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    estimated_duration: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Plan:
    """A plan consisting of ordered steps with dependencies.

    Attributes:
        steps: Tuple of PlanStep objects representing the execution plan
        max_duration: Maximum allowed duration for the entire plan in seconds (optional)
        estimated_duration: Estimated total duration in seconds (optional, computed)
        metadata: Additional metadata for the plan (optional)
    """

    steps: tuple[PlanStep, ...]
    max_duration: int | None = None
    estimated_duration: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
