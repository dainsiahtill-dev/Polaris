"""Architect (Architect) Output Schema - Architecture design.

Defines the structured output format for system architecture and technology decisions.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .base import BaseToolEnabledOutput


class TechnologyChoice(BaseModel):
    """Technology stack choice for a layer."""

    layer: str = Field(..., description="Architecture layer (e.g., 'Data', 'Service')")
    technology: str = Field(..., description="Selected technology")
    rationale: str = Field(..., min_length=20, description="Why this technology")
    alternatives: list[str] = Field(default_factory=list)


class ModuleDesign(BaseModel):
    """Module design specification."""

    name: str = Field(...)
    responsibility: str = Field(..., min_length=20)
    interfaces: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    """Architecture risk assessment."""

    risk: str = Field(...)
    probability: Literal["low", "medium", "high"] = Field(...)
    impact: Literal["low", "medium", "high"] = Field(...)
    mitigation: str = Field(..., min_length=10)


class NFRDesign(BaseModel):
    """Non-functional requirements design."""

    performance: str | None = Field(default=None, description="Performance targets (QPS, latency)")
    availability: str | None = Field(default=None, description="Availability design (fault tolerance,降级)")
    security: str | None = Field(default=None, description="Security design (auth, encryption)")
    scalability: str | None = Field(default=None, description="Scalability design (horizontal scaling)")


class ArchitectOutput(BaseToolEnabledOutput):
    """Architect structured output - Architecture design document with tool support.

    This model ensures LLM outputs conform to the expected architecture format.
    Supports tool calls for gathering project information before final design.
    """

    system_overview: str = Field(default="", min_length=50, max_length=1000, description="System positioning and goals")
    architecture_diagram: str = Field(default="", description="Textual description of architecture diagram")
    key_decisions: list[str] = Field(default_factory=list, description="Key architectural decisions")

    technology_stack: list[TechnologyChoice] = Field(default_factory=list, description="Technology choices by layer")

    modules: list[ModuleDesign] = Field(default_factory=list, description="Module breakdown")

    data_flow: str = Field(default="", min_length=30, description="Data flow description")

    non_functional: NFRDesign = Field(default_factory=NFRDesign, description="Non-functional requirements")

    risks: list[RiskAssessment] = Field(default_factory=list)

    anti_patterns: list[str] = Field(default_factory=list, description="Identified anti-patterns to avoid")

    milestones: list[str] = Field(default_factory=list, description="Implementation milestones")
