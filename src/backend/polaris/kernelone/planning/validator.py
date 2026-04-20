"""Formal plan validation layer for detecting structural issues like circular dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from polaris.kernelone.planning.models import Plan


class ViolationSeverity(Enum):
    """Severity level of a violation.

    Attributes:
        ERROR: Critical error that prevents plan execution
        WARNING: Non-critical issue that should be addressed
        INFO: Informational message
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Violation:
    """A violation of a validation rule.

    Attributes:
        severity: Severity level of the violation
        rule_id: Unique identifier for the rule that was violated
        message: Human-readable violation message
        location: Optional location identifier (e.g., step ID)
    """

    severity: ViolationSeverity
    rule_id: str
    message: str
    location: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Validation result containing violations and suggestions.

    Attributes:
        is_valid: True if no ERROR-level violations were found
        violations: Tuple of violations found during validation
        suggestions: Tuple of suggestions to fix the violations
    """

    is_valid: bool
    violations: tuple[Violation, ...]
    suggestions: tuple[str, ...]

    def format_errors(self) -> str:
        """Format validation errors into a user-friendly message.

        Returns:
            A formatted string describing all errors and suggestions
        """
        if self.is_valid:
            return "Plan validation passed."

        lines: list[str] = ["Plan validation failed with the following errors:"]

        for violation in self.violations:
            if violation.severity == ViolationSeverity.ERROR:
                location_msg = f" at {violation.location}" if violation.location else ""
                lines.append(f"  - [{violation.rule_id}]{location_msg}: {violation.message}")

        if self.suggestions:
            lines.append("")
            lines.append("Suggestions to fix:")
            for suggestion in self.suggestions:
                lines.append(f"  - {suggestion}")

        return "\n".join(lines)

    def format_warnings(self) -> str:
        """Format validation warnings into a user-friendly message.

        Returns:
            A formatted string describing all warnings
        """
        warnings = [v for v in self.violations if v.severity == ViolationSeverity.WARNING]

        if not warnings:
            return ""

        lines: list[str] = ["Plan validation warnings:"]
        for warning in warnings:
            location_msg = f" at {warning.location}" if warning.location else ""
            lines.append(f"  - [{warning.rule_id}]{location_msg}: {warning.message}")

        return "\n".join(lines)


class PlanValidator(Protocol):
    """Plan validator protocol."""

    def validate(self, plan: Plan) -> ValidationResult:
        """Validate a plan and return validation result."""
        ...


class StructuralPlanValidator:
    """Structural plan validator that checks plan integrity and dependencies."""

    def validate(self, plan: Plan) -> ValidationResult:
        """Validate plan structure.

        Checks:
        - Plan has at least one step
        - All dependencies reference existing steps
        - No circular dependencies exist
        - Resource constraints are respected
        """
        violations: list[Violation] = []

        # Check plan completeness
        if not plan.steps:
            violations.append(
                Violation(
                    severity=ViolationSeverity.ERROR,
                    rule_id="EMPTY_PLAN",
                    message="Plan has no steps",
                )
            )
            return ValidationResult(
                is_valid=False,
                violations=tuple(violations),
                suggestions=self._generate_suggestions(plan, tuple(violations)),
            )

        # Check step dependencies
        step_ids: set[str] = {s.id for s in plan.steps}
        for step in plan.steps:
            if step.depends_on:
                for dep_id in step.depends_on:
                    if dep_id not in step_ids:
                        violations.append(
                            Violation(
                                severity=ViolationSeverity.ERROR,
                                rule_id="INVALID_DEPENDENCY",
                                message=f"Step {step.id} depends on non-existent step {dep_id}",
                                location=step.id,
                            )
                        )

        # Check for circular dependencies
        if self._has_cycle(plan):
            violations.append(
                Violation(
                    severity=ViolationSeverity.ERROR,
                    rule_id="CYCLE_DETECTED",
                    message="Plan contains circular dependencies",
                )
            )

        # Check resource constraints
        for step in plan.steps:
            if (
                hasattr(step, "estimated_duration")
                and plan.max_duration is not None
                and step.estimated_duration is not None
                and step.estimated_duration > plan.max_duration
            ):
                violations.append(
                    Violation(
                        severity=ViolationSeverity.WARNING,
                        rule_id="EXCEEDS_DURATION",
                        message=f"Step {step.id} estimated duration exceeds plan max",
                        location=step.id,
                    )
                )

        return ValidationResult(
            is_valid=not any(v.severity == ViolationSeverity.ERROR for v in violations),
            violations=tuple(violations),
            suggestions=self._generate_suggestions(plan, tuple(violations)),
        )

    def _has_cycle(self, plan: Plan) -> bool:
        """Detect circular dependencies using DFS.

        Returns True if a cycle is detected in the dependency graph.
        """
        visited: set[str] = set()
        path: set[str] = set()

        def dfs(step_id: str) -> bool:
            if step_id in path:
                return True
            if step_id in visited:
                return False
            path.add(step_id)
            visited.add(step_id)

            step = next((s for s in plan.steps if s.id == step_id), None)
            if step and step.depends_on:
                for dep_id in step.depends_on:
                    if dfs(dep_id):
                        return True
            path.remove(step_id)
            return False

        return any(step.id not in visited and dfs(step.id) for step in plan.steps)

    def _generate_suggestions(self, plan: Plan, violations: tuple[Violation, ...]) -> tuple[str, ...]:
        """Generate suggestions to fix violations."""
        suggestions: list[str] = []

        if any(v.rule_id == "EMPTY_PLAN" for v in violations):
            suggestions.append("Add at least one step to the plan")

        if any(v.rule_id == "CYCLE_DETECTED" for v in violations):
            suggestions.append("Reorder dependencies to break cycles")

        if any(v.rule_id == "INVALID_DEPENDENCY" for v in violations):
            suggestions.append("Ensure all depends_on references point to existing step ids")

        return tuple(suggestions)
