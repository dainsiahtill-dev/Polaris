"""QA (QA) Output Schema - Quality assurance report.

Defines the structured output format for code review and quality assessment.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .base import BaseToolEnabledOutput


class QAFinding(BaseModel):
    """Single QA finding/issue."""

    severity: Literal["critical", "high", "medium", "low", "info"] = Field(..., description="Issue severity level")
    category: Literal[
        "security", "performance", "maintainability", "functionality", "style", "architecture", "other"
    ] = Field(...)
    location: str | None = Field(default=None, description="File location, e.g., 'src/fastapi_entrypoint.py:42'")
    description: str = Field(..., min_length=10, max_length=500, description="Issue description")
    recommendation: str = Field(..., min_length=10, description="Recommended fix or improvement")


class Metrics(BaseModel):
    """Quality metrics summary."""

    code_coverage: float | None = Field(default=None, ge=0, le=100, description="Test coverage percentage")
    complexity_score: float | None = Field(
        default=None, ge=0, le=100, description="Code complexity score (higher is better)"
    )
    maintainability_index: float | None = Field(default=None, ge=0, le=100)


class QAReportOutput(BaseToolEnabledOutput):
    """QA structured output - Quality assurance report with tool support.

    This model ensures LLM outputs conform to the expected QA review format.
    Supports tool calls for gathering code information before final review.
    """

    verdict: Literal["PASS", "CONDITIONAL", "FAIL", "BLOCKED", ""] = Field(
        default="", description="Overall review verdict (empty if need more tools)"
    )
    summary: str = Field(
        default="", max_length=500, description="Executive summary of findings (min 20 chars when complete)"
    )
    findings: list[QAFinding] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    blockers: list[str] = Field(default_factory=list, description="List of blocking issues (for CONDITIONAL/FAIL)")
    recommendations: list[str] = Field(default_factory=list, description="General improvement recommendations")

    def model_post_init(self, __context) -> None:
        """Validate verdict consistency with findings."""
        critical_count = sum(1 for f in self.findings if f.severity == "critical")

        if self.verdict == "PASS" and critical_count > 0:
            raise ValueError("Cannot have PASS verdict with critical findings")

        if self.verdict == "FAIL" and not self.findings:
            raise ValueError("FAIL verdict must have at least one finding")

        if self.verdict == "BLOCKED" and not self.blockers:
            raise ValueError("BLOCKED verdict must have blockers listed")
