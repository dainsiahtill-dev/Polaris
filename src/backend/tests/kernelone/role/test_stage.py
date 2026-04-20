"""Unit tests for Stage workflow system."""

from __future__ import annotations

import pytest

from polaris.kernelone.role.stage import (
    Stage,
    StageExecutionContext,
    StageTransition,
    StageType,
    WorkflowDefinition,
    create_workflow_from_config,
)


class TestStage:
    """Tests for Stage dataclass."""

    def test_stage_creation(self) -> None:
        """Test creating a Stage."""
        stage = Stage(
            id="analysis",
            name="Analysis Stage",
            description="Analyzes requirements",
        )

        assert stage.id == "analysis"
        assert stage.name == "Analysis Stage"
        assert stage.description == "Analyzes requirements"
        assert stage.required is True
        assert stage.trigger_on == []

    def test_stage_with_trigger(self) -> None:
        """Test Stage with trigger_on."""
        stage = Stage(
            id="blueprint",
            name="Blueprint Stage",
            description="Creates architecture blueprint",
            trigger_on=["new_feature", "refactor"],
        )

        assert "new_feature" in stage.trigger_on
        assert "refactor" in stage.trigger_on


class TestStageTransition:
    """Tests for StageTransition dataclass."""

    def test_can_transition_within_limit(self) -> None:
        """Test can_transition within loop limit."""
        transition = StageTransition(
            from_stage="a",
            to_stage="b",
            condition="has_context",
            max_loop=3,
        )

        assert transition.can_transition() is True
        transition.increment_loop()
        assert transition.can_transition() is True
        transition.increment_loop()
        assert transition.can_transition() is True
        transition.increment_loop()
        assert transition.can_transition() is False

    def test_can_transition_default_max_loop(self) -> None:
        """Test can_transition with default max_loop."""
        transition = StageTransition(
            from_stage="a",
            to_stage="b",
            condition="always",
        )

        assert transition.can_transition() is True
        transition.increment_loop()
        assert transition.can_transition() is False


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition."""

    def test_workflow_with_stages(self) -> None:
        """Test WorkflowDefinition with stages."""
        stages = [
            Stage(id="a", name="Stage A", description=""),
            Stage(id="b", name="Stage B", description=""),
        ]
        workflow = WorkflowDefinition(
            id="test_workflow",
            name="Test Workflow",
            workflow_type=StageType.SEQUENTIAL,
            stages=stages,
            transitions=[],
        )

        assert workflow.id == "test_workflow"
        assert len(workflow.stages) == 2
        assert workflow.get_stage("a") == stages[0]
        assert workflow.get_stage("nonexistent") is None

    def test_get_stages_for_task_with_mapping(self) -> None:
        """Test get_stages_for_task with explicit mapping."""
        stages = [
            Stage(id="analysis", name="Analysis", description=""),
            Stage(id="blueprint", name="Blueprint", description=""),
            Stage(id="execution", name="Execution", description=""),
        ]
        workflow = WorkflowDefinition(
            id="test_workflow",
            name="Test Workflow",
            workflow_type=StageType.BLUEPRINT_THEN_EXECUTE,
            stages=stages,
            transitions=[],
            task_type_mapping={
                "new_code": ["blueprint", "execution"],
                "bug_fix": ["execution"],
            },
        )

        # new_code should only return blueprint and execution
        applicable = workflow.get_stages_for_task("new_code")
        assert len(applicable) == 2
        assert all(s.id in ["blueprint", "execution"] for s in applicable)

        # bug_fix should only return execution
        applicable = workflow.get_stages_for_task("bug_fix")
        assert len(applicable) == 1
        assert applicable[0].id == "execution"

        # Unknown task type returns all stages
        applicable = workflow.get_stages_for_task("unknown")
        assert len(applicable) == 3

    def test_get_next_transition(self) -> None:
        """Test getting next transition."""
        stages = [
            Stage(id="a", name="Stage A", description=""),
            Stage(id="b", name="Stage B", description=""),
        ]
        transitions = [
            StageTransition(from_stage="a", to_stage="b", condition="has_context"),
        ]
        workflow = WorkflowDefinition(
            id="test_workflow",
            name="Test Workflow",
            workflow_type=StageType.SEQUENTIAL,
            stages=stages,
            transitions=transitions,
        )

        next_trans = workflow.get_next_transition("a", "has_context")
        assert next_trans is not None
        assert next_trans.to_stage == "b"

        # No transition found
        next_trans = workflow.get_next_transition("b", "has_context")
        assert next_trans is None

        # Condition mismatch
        next_trans = workflow.get_next_transition("a", "other_condition")
        assert next_trans is None


class TestStageExecutionContext:
    """Tests for StageExecutionContext."""

    def test_advance_to(self) -> None:
        """Test advancing to a new stage."""
        context = StageExecutionContext()
        stage_a = Stage(id="a", name="Stage A", description="")
        stage_b = Stage(id="b", name="Stage B", description="")

        context.advance_to(stage_a)
        assert context.current_stage == stage_a
        assert context.stage_history == []

        context.advance_to(stage_b)
        assert context.current_stage == stage_b
        assert context.stage_history == ["a"]

    def test_set_and_get_output(self) -> None:
        """Test setting and getting stage outputs."""
        context = StageExecutionContext()
        context.set_output("blueprint", {"file": "docs/architecture.md"})

        assert context.get_output("blueprint") == {"file": "docs/architecture.md"}
        assert context.get_output("nonexistent") is None

    def test_get_all_outputs(self) -> None:
        """Test getting all outputs."""
        context = StageExecutionContext()
        context.set_output("stage1", {"data": "1"})
        context.set_output("stage2", {"data": "2"})

        outputs = context.get_all_outputs()
        assert len(outputs) == 2
        assert outputs["stage1"] == {"data": "1"}


class TestCreateWorkflowFromConfig:
    """Tests for create_workflow_from_config."""

    def test_create_from_dict_stages(self) -> None:
        """Test creating workflow from dict with keyed stages."""
        config = {
            "type": "blueprint_then_execute",
            "stages": {
                "analysis": {
                    "id": "analysis",
                    "name": "Analysis",
                    "description": "Analyzes context",
                    "trigger_on": [],
                },
                "blueprint": {
                    "id": "blueprint",
                    "name": "Blueprint",
                    "description": "Creates blueprint",
                    "trigger_on": ["new_feature"],
                },
            },
            "transitions": [
                {"from": "analysis", "to": "blueprint", "condition": "has_context"},
            ],
            "task_type_mapping": {
                "new_feature": ["analysis", "blueprint"],
            },
        }

        workflow = create_workflow_from_config("test_workflow", config)

        assert workflow.id == "test_workflow"
        assert workflow.workflow_type == StageType.BLUEPRINT_THEN_EXECUTE
        assert len(workflow.stages) == 2

        analysis = workflow.get_stage("analysis")
        assert analysis is not None
        assert analysis.description == "Analyzes context"
        assert analysis.trigger_on == []

        blueprint = workflow.get_stage("blueprint")
        assert blueprint is not None
        assert "new_feature" in blueprint.trigger_on

    def test_create_from_list_stages(self) -> None:
        """Test creating workflow from dict with list stages."""
        config = {
            "type": "sequential",
            "stages": [
                {"id": "a", "name": "Stage A", "description": ""},
                {"id": "b", "name": "Stage B", "description": ""},
            ],
            "transitions": [],
        }

        workflow = create_workflow_from_config("list_workflow", config)

        assert workflow.id == "list_workflow"
        assert len(workflow.stages) == 2

    def test_create_with_invalid_type_defaults_to_sequential(self) -> None:
        """Test that invalid workflow type defaults to sequential."""
        config = {
            "type": "invalid_type",
            "stages": [{"id": "a", "name": "A", "description": ""}],
            "transitions": [],
        }

        workflow = create_workflow_from_config("test", config)
        assert workflow.workflow_type == StageType.SEQUENTIAL
