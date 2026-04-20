from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilityAssessment:
    """Result of self-capability evaluation."""

    task_description: str
    confidence: float  # 0.0-1.0 how confident the agent can complete this
    major_challenges: tuple[str, ...] = field(default_factory=tuple)
    needed_help: tuple[str, ...] = field(default_factory=tuple)
    success_probability: float = 0.5
    potential_failure_modes: tuple[str, ...] = field(default_factory=tuple)
    estimated_difficulty: str = "medium"  # easy/medium/hard/unknown


@dataclass
class SelfEvaluator:
    """Self-evaluator for agent capability boundary assessment."""

    async def evaluate_capability(
        self,
        task_description: str,
        task_requirements: dict[str, Any] | None = None,
    ) -> CapabilityAssessment:
        """Evaluate whether the agent can accomplish the given task.

        Returns a CapabilityAssessment with:
        - confidence: How confident (0-1)
        - major_challenges: What makes this hard
        - needed_help: What the agent needs
        - success_probability: Estimated success rate
        - potential_failure_modes: How it might fail
        """
        challenges: list[str] = []
        help_needed: list[str] = []
        failure_modes: list[str] = []
        difficulty = "medium"
        confidence = 0.5
        success_prob = 0.5

        task_lower = task_description.lower()

        # Check for code modification tasks
        if any(keyword in task_lower for keyword in ["write", "create", "implement", "modify", "edit"]):
            confidence += 0.1
            success_prob += 0.1

        # Check for analysis/review tasks
        if any(keyword in task_lower for keyword in ["analyze", "review", "audit", "inspect", "check"]):
            confidence += 0.15
            success_prob += 0.15

        # Check for read-only tasks
        if any(keyword in task_lower for keyword in ["read", "list", "get", "show", "find", "search"]):
            confidence += 0.25
            success_prob += 0.25

        # Check for complex tasks that may need help
        if any(
            keyword in task_lower
            for keyword in [
                "design",
                "architecture",
                "security",
                "performance",
                "deploy",
            ]
        ):
            challenges.append("Task requires specialized domain knowledge")
            help_needed.append("Expert review or guidelines")
            difficulty = "hard"
            confidence -= 0.1
            success_prob -= 0.15

        # Check for ambiguous tasks
        if any(keyword in task_lower for keyword in ["maybe", "perhaps", "possible", "try", "might"]):
            challenges.append("Task description is ambiguous")
            help_needed.append("Clearer requirements or scope definition")
            difficulty = "unknown"
            confidence -= 0.15
            success_prob -= 0.1

        # Check for external dependencies
        if any(
            keyword in task_lower
            for keyword in [
                "external",
                "third-party",
                "api",
                "service",
                "database",
                "network",
            ]
        ):
            challenges.append("Task depends on external systems")
            failure_modes.append("External service unavailable or changed")
            difficulty = "hard"
            confidence -= 0.1
            success_prob -= 0.1

        # Check for file operations
        if any(keyword in task_lower for keyword in ["file", "directory", "folder", "path"]):
            confidence += 0.05
            success_prob += 0.05

        # Check for test-related tasks
        if "test" in task_lower:
            confidence += 0.05
            success_prob += 0.05

        # Clamp values
        confidence = max(0.0, min(1.0, confidence))
        success_prob = max(0.0, min(1.0, success_prob))

        # Set difficulty based on confidence
        if confidence >= 0.8:
            difficulty = "easy"
        elif confidence >= 0.6:
            difficulty = "medium"
        elif confidence >= 0.4:
            difficulty = "hard"
        else:
            difficulty = "unknown"

        return CapabilityAssessment(
            task_description=task_description,
            confidence=confidence,
            major_challenges=tuple(challenges),
            needed_help=tuple(help_needed),
            success_probability=success_prob,
            potential_failure_modes=tuple(failure_modes),
            estimated_difficulty=difficulty,
        )

    async def evaluate_batch(
        self,
        tasks: list[str],
    ) -> list[CapabilityAssessment]:
        """Evaluate multiple tasks at once."""
        results: list[CapabilityAssessment] = []
        for task in tasks:
            result = await self.evaluate_capability(task)
            results.append(result)
        return results


@dataclass
class SelfEvaluationEngine:
    """Main engine for agent self-evaluation with async support."""

    _evaluator: SelfEvaluator = field(default_factory=SelfEvaluator)

    async def assess_task(
        self,
        task_description: str,
        task_requirements: dict[str, Any] | None = None,
    ) -> CapabilityAssessment:
        """Assess a single task and return capability assessment."""
        return await self._evaluator.evaluate_capability(task_description, task_requirements)

    async def assess_batch(
        self,
        tasks: list[str],
    ) -> list[CapabilityAssessment]:
        """Assess multiple tasks concurrently."""
        return await asyncio.gather(*[self._evaluator.evaluate_capability(t) for t in tasks])

    async def can_complete(
        self,
        task_description: str,
        threshold: float = 0.6,
    ) -> tuple[bool, CapabilityAssessment]:
        """Determine if the agent can likely complete the task.

        Returns:
            Tuple of (can_complete, assessment)
        """
        assessment = await self._evaluator.evaluate_capability(task_description)
        return assessment.confidence >= threshold, assessment
