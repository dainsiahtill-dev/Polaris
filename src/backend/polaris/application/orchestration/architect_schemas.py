"""Pydantic v2 DTOs for Architect orchestration layer.

Replaces raw ``dict[str, Any]`` returns with validated, typed models.
All external data crossing the Architect boundary MUST use these schemas.

Schema hierarchy:
    DesignResultSchema              — Single design outcome
    BlueprintResultSchema           — Compiled blueprint
    ArchitectDesignLifecycleResult  — Full lifecycle result
    ArchitectDesignConfig           — Configuration
    ArchitectContextSchema          — Gathered context
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Design DTOs
# ---------------------------------------------------------------------------


class DesignResultSchema(BaseModel):
    """Immutable snapshot of a single architecture design outcome."""

    model_config = {"frozen": True}

    design_id: str = Field(..., description="Design document identifier")
    doc_type: Literal["requirements", "adr", "interface_contract", "plan"] = Field(
        ..., description="Document type classification"
    )
    title: str = Field(..., description="Human-readable title")
    status: Literal["completed", "failed"] = Field(..., description="Processing status")
    content_length: int = Field(default=0, description="Byte length of content")
    output_path: str = Field(default="", description="File system path where document was written")
    error: str = Field(default="", description="Error message if failed")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Design metadata")


# ---------------------------------------------------------------------------
# Blueprint DTOs
# ---------------------------------------------------------------------------


class BlueprintResultSchema(BaseModel):
    """Immutable snapshot of a compiled architecture blueprint."""

    model_config = {"frozen": True}

    blueprint_id: str = Field(..., description="Blueprint identifier")
    design_ids: list[str] = Field(default_factory=list, description="Included design IDs")
    summary: str = Field(..., description="Blueprint summary")
    recommendation_paths: list[str] = Field(default_factory=list, description="Recommendation file paths")
    status: Literal["ready", "incomplete", "failed"] = Field(default="ready", description="Blueprint status")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Blueprint metadata")


# ---------------------------------------------------------------------------
# Lifecycle DTOs
# ---------------------------------------------------------------------------


class ArchitectDesignLifecycleResult(BaseModel):
    """Immutable snapshot of a full Architect design lifecycle outcome."""

    model_config = {"frozen": True}

    success: bool = Field(..., description="Whether lifecycle completed")
    workspace: str = Field(..., description="Workspace path")
    designs: list[DesignResultSchema] = Field(default_factory=list, description="Design results")
    blueprint: BlueprintResultSchema | None = Field(default=None, description="Compiled blueprint")
    handoff_package: dict[str, str | int | float | bool | list[str]] = Field(
        default_factory=dict, description="Handoff artifact"
    )
    notes: str = Field(default="", description="Lifecycle notes")


# ---------------------------------------------------------------------------
# Config DTOs
# ---------------------------------------------------------------------------


class ArchitectDesignConfig(BaseModel):
    """Configuration for Architect design execution."""

    model_config = {"populate_by_name": True}

    workspace: str = Field(..., description="Workspace path")
    docs_dir: str = Field(default="docs/product", description="Documentation directory")
    objective: str = Field(default="", description="Design objective")
    constraints: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Design constraints")
    context: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Additional context")


# ---------------------------------------------------------------------------
# Context DTOs
# ---------------------------------------------------------------------------


class ArchitectContextSchema(BaseModel):
    """Gathered and normalized design context."""

    model_config = {"frozen": True}

    workspace: str = Field(..., description="Workspace path")
    objective: str = Field(..., description="Design objective")
    constraints: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Merged constraints")
    context: dict[str, str | int | float | bool] = Field(default_factory=dict, description="Merged context")


# ---------------------------------------------------------------------------
# Requirements Input DTO
# ---------------------------------------------------------------------------


class RequirementsInput(BaseModel):
    """Input for requirements document creation."""

    model_config = {"frozen": True}

    goal: str = Field(..., description="Design goal / objective")
    in_scope: list[str] = Field(default_factory=list, description="Items in scope")
    out_of_scope: list[str] = Field(default_factory=list, description="Items out of scope")
    constraints: list[str] = Field(default_factory=list, description="Design constraints")
    definition_of_done: list[str] = Field(default_factory=list, description="Acceptance criteria")
    backlog: list[str] = Field(default_factory=list, description="Backlog items")


__all__ = [
    "ArchitectContextSchema",
    "ArchitectDesignConfig",
    "ArchitectDesignLifecycleResult",
    "BlueprintResultSchema",
    "DesignResultSchema",
    "RequirementsInput",
]
