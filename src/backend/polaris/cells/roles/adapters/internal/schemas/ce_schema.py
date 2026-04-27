"""Chief Engineer (Chief Engineer) Output Schema - Construction blueprint.

Defines the structured output format for technical analysis and implementation planning.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .base import BaseToolEnabledOutput


class ComplexityAssessment(BaseModel):
    """Technical complexity assessment."""

    level: Literal["low", "medium", "high"] = Field(...)
    estimated_files: int = Field(..., ge=1, le=100)
    estimated_lines: int = Field(..., ge=1, le=10000)
    technical_approach: str = Field(
        ..., min_length=50, max_length=1000, description="High-level implementation approach"
    )


class ConstructionPlan(BaseModel):
    """Step-by-step construction plan."""

    preparation: list[str] = Field(default_factory=list, description="Pre-implementation steps (dependencies, setup)")
    implementation: list[str] = Field(..., min_length=1, description="Implementation steps in order")
    verification: list[str] = Field(..., min_length=1, description="Verification and testing steps")


class DependencyInfo(BaseModel):
    """Dependency information."""

    required: list[str] = Field(default_factory=list)
    concurrent_safe: bool = Field(default=True)
    external_libs: list[str] = Field(default_factory=list)


class RiskFlag(BaseModel):
    """Risk assessment flag."""

    level: Literal["info", "warning", "error"] = Field(...)
    description: str = Field(..., min_length=10)
    mitigation: str | None = Field(default=None, description="Suggested mitigation strategy")


class BlueprintOutput(BaseToolEnabledOutput):
    """Chief Engineer structured output - Construction blueprint with tool support.

    This model ensures LLM outputs conform to the expected blueprint format.
    Supports tool calls for gathering codebase information before final blueprint.
    """

    blueprint_version: str = Field(default="1.0")
    blueprint_id: str | None = Field(default=None, description="Blueprint identifier")
    task_id: str | None = Field(default=None, description="Associated task ID if any")
    doc_id: str | None = Field(default=None, description="Document / run identifier")
    analysis: ComplexityAssessment | None = Field(
        default=None, description="Technical complexity assessment (None if need more tools)"
    )
    construction_plan: ConstructionPlan | None = Field(
        default=None, description="Implementation plan (None if need more tools)"
    )
    scope_for_apply: list[str] = Field(default_factory=list, description="Files that will be modified")
    dependencies: DependencyInfo = Field(default_factory=DependencyInfo)
    constraints: list[str] = Field(default_factory=list, description="Technical constraints to respect")
    missing_targets: list[str] = Field(default_factory=list, description="Files or info that couldn't be determined")
    risk_flags: list[RiskFlag] = Field(default_factory=list)

    @field_validator("scope_for_apply")
    @classmethod
    def validate_paths(cls, v: list[str]) -> list[str]:
        """Ensure all paths are relative and safe."""
        for path in v:
            if path.startswith("/") or ".." in path:
                raise ValueError(f"Path must be relative, got: {path}")
        return v

    @field_validator("risk_flags")
    @classmethod
    def check_critical_risks(cls, v: list[RiskFlag]) -> list[RiskFlag]:
        """Ensure critical risks have mitigations."""
        for risk in v:
            if risk.level == "error" and not risk.mitigation:
                raise ValueError(f"Critical risk must have mitigation: {risk.description}")
        return v
