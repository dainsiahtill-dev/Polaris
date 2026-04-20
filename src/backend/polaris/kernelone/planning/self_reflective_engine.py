"""Self-Reflective Planning Engine.

This module implements a planning engine with self-reflection capabilities,
allowing the planner to evaluate and improve its own plans before execution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from polaris.kernelone.llm.engine.client import LLMProvider
from polaris.kernelone.llm.shared_contracts import AIRequest, TaskType
from polaris.kernelone.planning.models import Constraints, Plan, PlanStep

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reflection:
    """Result of plan self-reflection."""

    is_reasonable: bool
    missing_info: tuple[str, ...] = field(default_factory=tuple)
    suggested_action: str | None = None
    needs_rethink: bool = False
    confidence: float = 1.0
    gaps: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EnhancedPlanStep:
    """Enhanced plan step with alternatives and confidence."""

    id: str
    description: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    estimated_duration: int | None = None
    confidence: float = 1.0
    alternatives: tuple[str, ...] = field(default_factory=tuple)


class BasePlannerPort(Protocol):
    """Protocol for base planners."""

    def plan(self, goal: str, constraints: Constraints) -> Plan:
        """Create a plan for the given goal and constraints."""
        ...


REFLECTION_USER_PROMPT = """## Goal
{goal}

## Current Plan
{plan_description}

## Constraints
{constraints_description}

Please analyze this plan and respond in JSON format:
{{
  "is_reasonable": true/false,
  "confidence": 0.0-1.0,
  "missing_info": ["list of missing information"],
  "gaps": ["list of identified gaps"],
  "suggested_action": "what should be done to improve",
  "needs_rethink": true/false
}}
"""


ALTERNATIVES_USER_PROMPT = """## Original Goal
{goal}

## Plan Steps
{steps_description}

For each step, provide 1-2 alternative approaches:
{{
  "alternatives": [
    {{
      "step_id": "step_1",
      "alternatives": ["alt1 description", "alt2 description"]
    }}
  ]
}}
"""


class SelfReflectivePlanner:
    """A planning engine with self-reflection capabilities."""

    __slots__ = (
        "_base",
        "_llm",
        "_max_reflection_iterations",
        "_reflection_threshold",
    )

    def __init__(
        self,
        llm: LLMProvider,
        base_planner: BasePlannerPort,
        *,
        max_reflection_iterations: int = 3,
        reflection_threshold: float = 0.7,
    ) -> None:
        self._llm = llm
        self._base = base_planner
        self._max_reflection_iterations = max_reflection_iterations
        self._reflection_threshold = reflection_threshold

    async def plan(self, goal: str, constraints: Constraints) -> Plan:
        """Create a plan with self-reflection."""
        initial_plan = self._base.plan(goal, constraints)
        reflection = await self._reflect(initial_plan, goal, constraints)

        if reflection.needs_rethink and reflection.confidence < self._reflection_threshold:
            refined_goal = self._incorporate_feedback(goal, reflection)
            logger.info("Refining goal based on reflection: %s", refined_goal)
            initial_plan = self._base.plan(refined_goal, constraints)
            reflection = await self._reflect(initial_plan, refined_goal, constraints)

        enhanced_plan = await self._add_alternatives(initial_plan, goal)
        return enhanced_plan

    async def _reflect(self, plan: Plan, goal: str, constraints: Constraints) -> Reflection:
        """Reflect on a plan to evaluate its quality."""
        plan_description = self._format_plan(plan)
        constraints_description = self._format_constraints(constraints)
        prompt = REFLECTION_USER_PROMPT.format(
            goal=goal,
            plan_description=plan_description,
            constraints_description=constraints_description,
        )
        request = AIRequest(task_type=TaskType.GENERATION, role="system", input=prompt)

        try:
            response = await self._llm.invoke(request)
            if not response.ok:
                logger.warning("LLM reflection failed: %s", response.error)
                return self._create_fallback_reflection()
            return self._parse_reflection_response(response.output)
        except (RuntimeError, ValueError):
            logger.exception("Reflection failed with exception")
            return self._create_fallback_reflection()

    def _format_plan(self, plan: Plan) -> str:
        """Format a plan as a readable string."""
        if not plan.steps:
            return "Empty plan (no steps)"
        lines = []
        for i, step in enumerate(plan.steps, 1):
            dep_str = f" (depends on: {', '.join(step.depends_on)})" if step.depends_on else ""
            duration_str = f" [~{step.estimated_duration}s]" if step.estimated_duration else ""
            lines.append(f"{i}. {step.id}: {step.description}{dep_str}{duration_str}")
        return "\n".join(lines)

    def _format_constraints(self, constraints: Constraints) -> str:
        """Format constraints as a readable string."""
        parts = []
        if constraints.max_steps is not None:
            parts.append(f"max_steps: {constraints.max_steps}")
        if constraints.max_duration is not None:
            parts.append(f"max_duration: {constraints.max_duration}s")
        if constraints.required_resources:
            parts.append(f"required_resources: {', '.join(constraints.required_resources)}")
        if constraints.forbidden_actions:
            parts.append(f"forbidden_actions: {', '.join(constraints.forbidden_actions)}")
        if constraints.deadline is not None:
            parts.append(f"deadline: {constraints.deadline}")
        if constraints.metadata:
            parts.append(f"metadata: {constraints.metadata}")
        return "\n".join(parts) if parts else "No explicit constraints"

    def _parse_reflection_response(self, output: str) -> Reflection:
        """Parse LLM reflection response into a Reflection object."""
        json_str = self._extract_json(output)
        if json_str is None:
            logger.warning("Could not extract JSON from reflection response")
            return self._create_fallback_reflection()
        try:
            data = json.loads(json_str)
            return Reflection(
                is_reasonable=bool(data.get("is_reasonable", True)),
                missing_info=tuple(data.get("missing_info", [])),
                suggested_action=data.get("suggested_action"),
                needs_rethink=bool(data.get("needs_rethink", False)),
                confidence=float(data.get("confidence", 0.5)),
                gaps=tuple(data.get("gaps", [])),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to parse reflection JSON: %s", e)
            return self._create_fallback_reflection()

    def _extract_json(self, text: str) -> str | None:
        """Extract JSON object from text."""
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _create_fallback_reflection(self) -> Reflection:
        """Create a fallback reflection when LLM call fails."""
        return Reflection(
            is_reasonable=True,
            missing_info=(),
            suggested_action=None,
            needs_rethink=False,
            confidence=0.5,
            gaps=(),
        )

    def _incorporate_feedback(self, goal: str, reflection: Reflection) -> str:
        """Incorporate reflection feedback into the goal."""
        refined_parts = [goal]
        if reflection.missing_info:
            refined_parts.append("\n\nImportant considerations:")
            for info in reflection.missing_info:
                refined_parts.append(f"- {info}")
        if reflection.gaps:
            refined_parts.append("\n\nAddress these gaps:")
            for gap in reflection.gaps:
                refined_parts.append(f"- {gap}")
        if reflection.suggested_action:
            refined_parts.append(f"\n\nSuggestion: {reflection.suggested_action}")
        return "".join(refined_parts)

    async def _add_alternatives(self, plan: Plan, goal: str) -> Plan:
        """Add alternative approaches to each plan step."""
        if not plan.steps:
            return plan
        steps_description = self._format_plan(plan)
        prompt = ALTERNATIVES_USER_PROMPT.format(goal=goal, steps_description=steps_description)
        request = AIRequest(task_type=TaskType.GENERATION, role="system", input=prompt)
        try:
            response = await self._llm.invoke(request)
            if not response.ok:
                logger.warning("LLM alternatives generation failed: %s", response.error)
                return self._return_enhanced_plan(plan)
            alternatives_map = self._parse_alternatives_response(response.output)
        except (RuntimeError, ValueError):
            logger.exception("Alternatives generation failed with exception")
            return self._return_enhanced_plan(plan)

        enhanced_steps = []
        for step in plan.steps:
            step_alts = alternatives_map.get(step.id, ())
            confidence = 1.0 if step_alts else 0.7
            enhanced_step = PlanStep(
                id=step.id,
                description=step.description,
                depends_on=step.depends_on,
                estimated_duration=step.estimated_duration,
                metadata={
                    **step.metadata,
                    "alternatives": step_alts,
                    "confidence": confidence,
                },
            )
            enhanced_steps.append(enhanced_step)

        return Plan(
            steps=tuple(enhanced_steps),
            max_duration=plan.max_duration,
            estimated_duration=plan.estimated_duration,
            metadata={**plan.metadata, "reflection_enhanced": True},
        )

    def _return_enhanced_plan(self, plan: Plan) -> Plan:
        """Return plan marked as reflection_enhanced without alternatives."""
        return Plan(
            steps=plan.steps,
            max_duration=plan.max_duration,
            estimated_duration=plan.estimated_duration,
            metadata={**plan.metadata, "reflection_enhanced": True},
        )

    def _parse_alternatives_response(self, output: str) -> dict[str, tuple[str, ...]]:
        """Parse LLM alternatives response."""
        json_str = self._extract_json(output)
        if json_str is None:
            return {}
        try:
            data = json.loads(json_str)
            alternatives_data = data.get("alternatives", [])
            result: dict[str, tuple[str, ...]] = {}
            for item in alternatives_data:
                step_id = item.get("step_id", "")
                alts = item.get("alternatives", [])
                if step_id and alts:
                    result[step_id] = tuple(alt for alt in alts if isinstance(alt, str))
            return result
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to parse alternatives JSON: %s", e)
            return {}


__all__ = ["EnhancedPlanStep", "Reflection", "SelfReflectivePlanner"]
