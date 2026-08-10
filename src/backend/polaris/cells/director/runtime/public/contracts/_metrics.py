"""Repair metrics projection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._coverage_catalog import DirectorRepairCoverageReportV1
from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import RepairReceiptV1
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
)


@dataclass(frozen=True)
class ProjectDirectorRepairMetricsV1:
    """Read-only command for projecting deterministic repair health metrics."""

    receipts: tuple[RepairReceiptV1, ...] = ()
    coverage_reports: tuple[DirectorRepairCoverageReportV1, ...] = ()
    schedule_run_summaries: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "coverage_reports", tuple(self.coverage_reports or ()))
        object.__setattr__(
            self,
            "schedule_run_summaries",
            tuple(dict(item or {}) for item in self.schedule_run_summaries),
        )


@dataclass(frozen=True)
class DirectorRepairMetricsResultV1:
    """Public read-only metrics projection for repair kernel health."""

    schema_version: str
    source: str
    access: str
    receipt_count: int
    applied_receipt_count: int
    failed_receipt_count: int
    ineffective_receipt_count: int
    success_rate: float
    average_convergence_rounds: float
    uncovered_diagnostic_count: int
    coverage_gap_count: int
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_metrics_projection_no_writes"
    advisory_only: bool = True
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "receipt_count", max(0, int(self.receipt_count)))
        object.__setattr__(self, "applied_receipt_count", max(0, int(self.applied_receipt_count)))
        object.__setattr__(self, "failed_receipt_count", max(0, int(self.failed_receipt_count)))
        object.__setattr__(self, "ineffective_receipt_count", max(0, int(self.ineffective_receipt_count)))
        object.__setattr__(self, "success_rate", max(0.0, min(1.0, float(self.success_rate))))
        object.__setattr__(self, "average_convergence_rounds", max(0.0, float(self.average_convergence_rounds)))
        object.__setattr__(self, "uncovered_diagnostic_count", max(0, int(self.uncovered_diagnostic_count)))
        object.__setattr__(self, "coverage_gap_count", max(0, int(self.coverage_gap_count)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "advisory_only", True)
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "advisory_only": True,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "receipt_count": self.receipt_count,
            "applied_receipt_count": self.applied_receipt_count,
            "failed_receipt_count": self.failed_receipt_count,
            "ineffective_receipt_count": self.ineffective_receipt_count,
            "success_rate": self.success_rate,
            "average_convergence_rounds": self.average_convergence_rounds,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "coverage_gap_count": self.coverage_gap_count,
            "metadata": dict(self.metadata),
        }
