"""Stage definitions for multi-stage workflow execution.

This module defines the Stage abstraction for the Tri-Axis Role Engine,
enabling declarative workflow definitions that can be dynamically composed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StageType(Enum):
    """Stage type enumeration."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"
    BLUEPRINT_THEN_EXECUTE = "blueprint_then_execute"
    THREAT_MODELING_FIRST = "threat_modeling_first"
    REVIEW_THEN_VERIFY = "review_then_verify"


class TransitionCondition(Enum):
    """Stage transition conditions."""

    HAS_CONTEXT = "has_context"
    BLUEPRINT_APPROVED = "blueprint_approved"
    EXECUTION_COMPLETED = "execution_completed"
    VERIFICATION_FAILED = "verification_failed"
    INSPECTION_COMPLETE = "inspection_complete"
    TEST_COMPLETE = "test_complete"
    ISSUES_FOUND = "issues_found"
    ALWAYS = "always"


@dataclass
class StageTransition:
    """Represents a transition between stages."""

    from_stage: str
    to_stage: str
    condition: str
    max_loop: int = 1
    current_loop: int = 0

    def can_transition(self) -> bool:
        """Check if transition is allowed based on loop count."""
        return self.current_loop < self.max_loop

    def increment_loop(self) -> None:
        """Increment loop counter."""
        self.current_loop += 1


@dataclass
class Stage:
    """Represents a single stage in a workflow."""

    id: str
    name: str
    description: str
    stage_type: StageType = StageType.SEQUENTIAL
    trigger_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash for caching purposes."""
        return hash((self.id, self.name)).__hash__()


@dataclass
class WorkflowDefinition:
    """Complete workflow definition with stages and transitions."""

    id: str
    name: str
    workflow_type: StageType
    stages: list[Stage]
    transitions: list[StageTransition]
    task_type_mapping: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_stage(self, stage_id: str) -> Stage | None:
        """Get a stage by its ID."""
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        return None

    def get_stages_for_task(self, task_type: str) -> list[Stage]:
        """Get applicable stages for a given task type."""
        if task_type not in self.task_type_mapping:
            # Return all stages if no specific mapping
            return self.stages

        applicable_ids = self.task_type_mapping[task_type]
        return [s for s in self.stages if s.id in applicable_ids]

    def get_next_transition(self, current_stage_id: str, condition: str) -> StageTransition | None:
        """Get the next transition matching the current stage and condition."""
        for transition in self.transitions:
            if (
                transition.from_stage == current_stage_id
                and transition.condition == condition
                and transition.can_transition()
            ):
                return transition
        return None


@dataclass
class StageExecutionContext:
    """Context maintained during stage execution."""

    current_stage: Stage | None = None
    stage_history: list[str] = field(default_factory=list)
    stage_outputs: dict[str, Any] = field(default_factory=dict)
    transitions: list[StageTransition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def advance_to(self, stage: Stage) -> None:
        """Advance to a new stage."""
        if self.current_stage:
            self.stage_history.append(self.current_stage.id)
        self.current_stage = stage

    def set_output(self, stage_id: str, output: Any) -> None:
        """Set output for a stage."""
        self.stage_outputs[stage_id] = output

    def get_output(self, stage_id: str) -> Any:
        """Get output from a stage."""
        return self.stage_outputs.get(stage_id)

    def get_all_outputs(self) -> dict[str, Any]:
        """Get all stage outputs."""
        return self.stage_outputs.copy()


def create_workflow_from_config(
    workflow_id: str,
    workflow_config: dict[str, Any],
) -> WorkflowDefinition:
    """Create a WorkflowDefinition from configuration dictionary.

    Args:
        workflow_id: Unique identifier for the workflow
        workflow_config: Configuration dict with type, stages, transitions, etc.

    Returns:
        WorkflowDefinition instance
    """
    workflow_type_str = workflow_config.get("type", "sequential")
    try:
        workflow_type = StageType(workflow_type_str)
    except ValueError:
        workflow_type = StageType.SEQUENTIAL

    # Parse stages
    stages_config = workflow_config.get("stages", {})
    stages: list[Stage] = []

    if isinstance(stages_config, dict):
        # Stages keyed by ID
        for stage_id, stage_data in stages_config.items():
            stages.append(
                Stage(
                    id=stage_data.get("id", stage_id),
                    name=stage_data.get("name", stage_id),
                    description=stage_data.get("description", ""),
                    trigger_on=stage_data.get("trigger_on", []),
                    outputs=stage_data.get("outputs", []),
                    required=stage_data.get("required", True),
                )
            )
    elif isinstance(stages_config, list):
        # Stages as list
        for stage_data in stages_config:
            stages.append(
                Stage(
                    id=stage_data.get("id", ""),
                    name=stage_data.get("name", ""),
                    description=stage_data.get("description", ""),
                    trigger_on=stage_data.get("trigger_on", []),
                    outputs=stage_data.get("outputs", []),
                    required=stage_data.get("required", True),
                )
            )

    # Parse transitions
    transitions: list[StageTransition] = []
    for trans_config in workflow_config.get("transitions", []):
        transitions.append(
            StageTransition(
                from_stage=trans_config["from"],
                to_stage=trans_config["to"],
                condition=trans_config["condition"],
                max_loop=trans_config.get("max_loop", 1),
            )
        )

    return WorkflowDefinition(
        id=workflow_id,
        name=workflow_id,
        workflow_type=workflow_type,
        stages=stages,
        transitions=transitions,
        task_type_mapping=workflow_config.get("task_type_mapping", {}),
    )
