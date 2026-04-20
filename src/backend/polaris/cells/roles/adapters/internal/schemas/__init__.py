"""Role Output Schemas - Structured output models for Instructor integration.

This module defines Pydantic models for type-safe LLM outputs.
"""

from .architect_schema import ArchitectOutput
from .base import BaseToolEnabledOutput, ToolCall
from .ce_schema import BlueprintOutput, ConstructionPlan
from .director_schema import DirectorOutput, PatchOperation
from .pm_schema import Task, TaskListOutput
from .qa_schema import QAFinding, QAReportOutput

__all__ = [
    # Architect
    "ArchitectOutput",
    "BaseToolEnabledOutput",
    "BlueprintOutput",
    # Chief Engineer
    "ConstructionPlan",
    # Director
    "DirectorOutput",
    "PatchOperation",
    # QA
    "QAFinding",
    "QAReportOutput",
    # PM
    "Task",
    "TaskListOutput",
    # Base
    "ToolCall",
]

# Role to schema mapping for kernel integration
ROLE_OUTPUT_SCHEMAS = {
    "pm": TaskListOutput,
    "chief_engineer": BlueprintOutput,
    "architect": ArchitectOutput,
    "qa": QAReportOutput,
    "director": DirectorOutput,
}


def get_schema_for_role(role: str) -> type | None:
    """Get the output schema for a given role.

    Args:
        role: Role identifier (pm, chief_engineer, architect, qa, director)

    Returns:
        Pydantic model class or None if role not supported
    """
    return ROLE_OUTPUT_SCHEMAS.get(role)
