"""PlanBuilder DSL for constructing plans programmatically."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.planning.models import Plan, PlanStep


class PlanBuilder:
    """Fluent builder for creating Plan objects.

    Example:
        >>> plan = (
        ...     PlanBuilder()
        ...     .step("read", description="Read file", estimated_duration=5)
        ...     .step("edit", description="Edit file", depends_on=["read"])
        ...     .max_duration(300)
        ...     .metadata({"author": "test"})
        ...     .build()
        ... )
    """

    def __init__(self) -> None:
        """Initialize the PlanBuilder."""
        self._steps: list[PlanStep] = []
        self._max_duration: int | None = None
        self._metadata: dict[str, Any] = {}

    def step(
        self,
        step_id: str,
        description: str = "",
        depends_on: list[str] | None = None,
        estimated_duration: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PlanBuilder:
        """Add a step to the plan.

        Args:
            step_id: Unique identifier for the step
            description: Human-readable description of the step
            depends_on: List of step IDs that must complete before this step
            estimated_duration: Estimated duration in seconds
            metadata: Additional metadata for the step

        Returns:
            Self for method chaining
        """
        new_step = PlanStep(
            id=step_id,
            description=description,
            depends_on=tuple(depends_on) if depends_on else (),
            estimated_duration=estimated_duration,
            metadata=metadata or {},
        )
        self._steps.append(new_step)
        return self

    def max_duration(self, duration: int) -> PlanBuilder:
        """Set the maximum duration for the entire plan.

        Args:
            duration: Maximum duration in seconds

        Returns:
            Self for method chaining
        """
        self._max_duration = duration
        return self

    def metadata(self, **kwargs: Any) -> PlanBuilder:
        """Add metadata to the plan.

        Args:
            **kwargs: Key-value pairs to add to metadata

        Returns:
            Self for method chaining
        """
        self._metadata.update(kwargs)
        return self

    def build(self) -> Plan:
        """Build the Plan object.

        Returns:
            A frozen Plan object

        Raises:
            ValueError: If the plan has no steps
        """
        if not self._steps:
            raise ValueError("Plan must have at least one step")

        # Compute total estimated duration
        durations: list[int] = []
        for step in self._steps:
            if step.estimated_duration is not None:
                durations.append(step.estimated_duration)
        total_duration: int | None = sum(durations) if durations else None

        return Plan(
            steps=tuple(self._steps),
            max_duration=self._max_duration,
            estimated_duration=total_duration,
            metadata=self._metadata.copy(),
        )


class PlanStepBuilder:
    """Step-by-step builder for more complex step construction.

    Example:
        >>> step_builder = PlanStepBuilder("read")
        >>> step = (
        ...     step_builder
        ...     .description("Read the configuration file")
        ...     .depends_on("validate")
        ...     .estimated_duration(10)
        ...     .build()
        ... )
    """

    __slots__ = ("_depends_on", "_description", "_estimated_duration", "_id", "_metadata")

    def __init__(self, step_id: str) -> None:
        """Initialize with a step ID.

        Args:
            step_id: Unique identifier for the step
        """
        self._id: str = step_id
        self._description: str = ""
        self._depends_on: list[str] = []
        self._estimated_duration: int | None = None
        self._metadata: dict[str, Any] = {}

    def description(self, desc: str) -> PlanStepBuilder:
        """Set the step description.

        Args:
            desc: Human-readable description

        Returns:
            Self for method chaining
        """
        self._description = desc
        return self

    def depends_on(self, step_id: str | list[str]) -> PlanStepBuilder:
        """Add dependency on one or more steps.

        Args:
            step_id: Single step ID or list of step IDs

        Returns:
            Self for method chaining
        """
        if isinstance(step_id, str):
            self._depends_on.append(step_id)
        else:
            self._depends_on.extend(step_id)
        return self

    def estimated_duration(self, duration: int) -> PlanStepBuilder:
        """Set the estimated duration.

        Args:
            duration: Estimated duration in seconds

        Returns:
            Self for method chaining
        """
        self._estimated_duration = duration
        return self

    def metadata(self, **kwargs: Any) -> PlanStepBuilder:
        """Add metadata to the step.

        Args:
            **kwargs: Key-value pairs to add to metadata

        Returns:
            Self for method chaining
        """
        self._metadata.update(kwargs)
        return self

    def build(self) -> PlanStep:
        """Build the PlanStep object.

        Returns:
            A frozen PlanStep object

        Raises:
            ValueError: If the step ID is empty
        """
        if not self._id:
            raise ValueError("Step ID cannot be empty")

        return PlanStep(
            id=self._id,
            description=self._description,
            depends_on=tuple(self._depends_on),
            estimated_duration=self._estimated_duration,
            metadata=self._metadata.copy(),
        )
