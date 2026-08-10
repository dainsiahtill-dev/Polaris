"""Dataclass models for artifact quality issues and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from polaris.kernelone.quality.cross_artifact_interfaces import (
    ContractAmendmentRequest,
    CrossArtifactConsistencyIssue,
    CrossArtifactRepairPlan,
)


@dataclass(frozen=True, slots=True)
class ArtifactQualityIssue:
    """Typed projection for one artifact-quality finding.

    This is evidence, not a repair authorization. String-compatible callers
    still consume ``ArtifactQualityEvidence.errors`` while typed gates can rely
    on ``issues`` instead of reparsing human-readable strings.
    """

    code: str
    message: str
    path: str | None = None
    severity: str = "error"
    source: str = "artifact_quality"
    line: int | None = None
    column: int | None = None
    metadata: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }
        if self.line is not None:
            payload["line"] = self.line
        if self.column is not None:
            payload["column"] = self.column
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactQualityEvidence:
    """Structured quality evidence used by audit, AGI, and repair planning."""

    errors: tuple[str, ...] = ()
    issues: tuple[ArtifactQualityIssue, ...] = ()
    scanned_relative_paths: tuple[str, ...] = ()
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    cross_artifact_repair_plans: tuple[CrossArtifactRepairPlan, ...] = ()
    contract_amendment_request: ContractAmendmentRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": list(self.errors),
            "issues": [issue.to_dict() for issue in self.issues],
            "scanned_relative_paths": list(self.scanned_relative_paths),
            "cross_artifact_issues": [issue.to_dict() for issue in self.cross_artifact_issues],
            "cross_artifact_repair_plans": [plan.to_dict() for plan in self.cross_artifact_repair_plans],
            "contract_amendment_request": self.contract_amendment_request.to_dict()
            if self.contract_amendment_request is not None
            else None,
        }


@dataclass(frozen=True, slots=True)
class _FileArtifactQualityEvidence:
    """Internal per-file scanner output with legacy strings and typed issues."""

    errors: tuple[str, ...] = ()
    issues: tuple[ArtifactQualityIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _NodeEvalSyntaxIssue:
    """Structured npm script `node --eval` syntax finding."""

    display_error: str
    diagnostic_detail: str
    script_name: str
    relative_path: str
