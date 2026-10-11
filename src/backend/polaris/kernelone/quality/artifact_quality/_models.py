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
class ArtifactQualityDeferredCheck:
    """Required validation intent; grants no execution or success authority."""

    check_id: str
    relative_paths: tuple[str, ...]
    reason: str = "pure_static forbids process execution"
    script_name: str = ""
    project_relative_path: str | None = None

    @property
    def required(self) -> bool:
        return True

    @property
    def status(self) -> str:
        return "deferred"

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "relative_paths": list(self.relative_paths),
            "reason": self.reason,
            "script_name": self.script_name,
            "project_relative_path": self.project_relative_path,
            "required": self.required,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ArtifactQualityEvidence:
    """Structured quality evidence used by audit, AGI, and repair planning."""

    errors: tuple[str, ...] = ()
    issues: tuple[ArtifactQualityIssue, ...] = ()
    scanned_relative_paths: tuple[str, ...] = ()
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    cross_artifact_repair_plans: tuple[CrossArtifactRepairPlan, ...] = ()
    contract_amendment_request: ContractAmendmentRequest | None = None
    scan_mode: str = "legacy"
    deferred_checks: tuple[ArtifactQualityDeferredCheck, ...] = ()
    scan_complete: bool = True
    incomplete_reasons: tuple[str, ...] = ()

    @property
    def checks_complete(self) -> bool:
        """Coverage only, not a pass verdict (errors must still be consumed)."""
        # Legacy scanners never audited unavailable/skipped process coverage.
        return self.scan_mode == "pure_static" and self.scan_complete and not self.deferred_checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_mode": self.scan_mode,
            "deferred_checks": [check.to_dict() for check in self.deferred_checks],
            "scan_complete": self.scan_complete,
            "incomplete_reasons": list(self.incomplete_reasons),
            "checks_complete": self.checks_complete,
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
    deferred_checks: tuple[ArtifactQualityDeferredCheck, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _NodeEvalSyntaxIssue:
    """Structured npm script `node --eval` syntax finding."""

    display_error: str
    diagnostic_detail: str
    script_name: str
    relative_path: str
