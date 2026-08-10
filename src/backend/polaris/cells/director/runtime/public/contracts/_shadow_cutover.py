"""Shadow comparison and cutover readiness contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import RepairReceiptV1
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)


@dataclass(frozen=True)
class CompareDirectorRepairShadowRunV1:
    """Read-only command for deterministic repair receipt projection comparison."""

    baseline_tool_results: tuple[Mapping[str, Any], ...] = ()
    kernel_receipts: tuple[RepairReceiptV1, ...] = ()
    comparison_mode: str = "independent_shadow_run"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "baseline_tool_results", tuple(dict(item or {}) for item in self.baseline_tool_results)
        )
        object.__setattr__(self, "kernel_receipts", tuple(self.kernel_receipts or ()))
        comparison_mode = str(self.comparison_mode or "").strip() or "independent_shadow_run"
        if comparison_mode not in {"independent_shadow_run", "receipt_projection_self_check"}:
            raise ValueError("comparison_mode must be independent_shadow_run or receipt_projection_self_check")
        object.__setattr__(self, "comparison_mode", comparison_mode)


@dataclass(frozen=True)
class DirectorRepairShadowComparisonResultV1:
    """Public read-only result for deterministic repair dark-launch comparison."""

    schema_version: str
    source: str
    access: str
    matched: bool
    baseline_source_tools: tuple[str, ...] = ()
    kernel_source_tools: tuple[str, ...] = ()
    baseline_paths: tuple[str, ...] = ()
    kernel_paths: tuple[str, ...] = ()
    missing_paths_in_kernel: tuple[str, ...] = ()
    extra_paths_in_kernel: tuple[str, ...] = ()
    missing_source_tools_in_kernel: tuple[str, ...] = ()
    extra_source_tools_in_kernel: tuple[str, ...] = ()
    comparison_mode: str = "independent_shadow_run"
    independent_shadow_required: bool = True
    independent_shadow_satisfied: bool = True
    cutover_ready: bool = False
    cutover_blockers: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_shadow_comparison_no_writes"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "matched", bool(self.matched))
        object.__setattr__(self, "baseline_source_tools", _to_tuple_str(list(self.baseline_source_tools)))
        object.__setattr__(self, "kernel_source_tools", _to_tuple_str(list(self.kernel_source_tools)))
        object.__setattr__(self, "baseline_paths", _to_tuple_str(list(self.baseline_paths)))
        object.__setattr__(self, "kernel_paths", _to_tuple_str(list(self.kernel_paths)))
        object.__setattr__(self, "missing_paths_in_kernel", _to_tuple_str(list(self.missing_paths_in_kernel)))
        object.__setattr__(self, "extra_paths_in_kernel", _to_tuple_str(list(self.extra_paths_in_kernel)))
        object.__setattr__(
            self,
            "missing_source_tools_in_kernel",
            _to_tuple_str(list(self.missing_source_tools_in_kernel)),
        )
        object.__setattr__(self, "extra_source_tools_in_kernel", _to_tuple_str(list(self.extra_source_tools_in_kernel)))
        comparison_mode = str(self.comparison_mode or "").strip() or "independent_shadow_run"
        object.__setattr__(self, "comparison_mode", comparison_mode)
        object.__setattr__(self, "independent_shadow_required", True)
        object.__setattr__(self, "independent_shadow_satisfied", bool(self.independent_shadow_satisfied))
        object.__setattr__(self, "cutover_ready", bool(self.cutover_ready))
        object.__setattr__(self, "cutover_blockers", _to_tuple_str(list(self.cutover_blockers)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "matched": self.matched,
            "baseline_source_tools": list(self.baseline_source_tools),
            "kernel_source_tools": list(self.kernel_source_tools),
            "baseline_paths": list(self.baseline_paths),
            "kernel_paths": list(self.kernel_paths),
            "missing_paths_in_kernel": list(self.missing_paths_in_kernel),
            "extra_paths_in_kernel": list(self.extra_paths_in_kernel),
            "missing_source_tools_in_kernel": list(self.missing_source_tools_in_kernel),
            "extra_source_tools_in_kernel": list(self.extra_source_tools_in_kernel),
            "comparison_mode": self.comparison_mode,
            "independent_shadow_required": self.independent_shadow_required,
            "independent_shadow_satisfied": self.independent_shadow_satisfied,
            "cutover_ready": self.cutover_ready,
            "cutover_blockers": list(self.cutover_blockers),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvaluateDirectorRepairCutoverReadinessV1:
    """Read-only command for requiring repeated independent shadow success before cutover."""

    comparisons: tuple[DirectorRepairShadowComparisonResultV1, ...] = ()
    required_successful_runs: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparisons", tuple(self.comparisons or ()))
        required = int(self.required_successful_runs or 0)
        object.__setattr__(self, "required_successful_runs", max(1, required))


@dataclass(frozen=True)
class DirectorRepairCutoverReadinessResultV1:
    """Public read-only gate result for deterministic repair cutover readiness."""

    schema_version: str
    source: str
    access: str
    cutover_ready: bool
    required_successful_runs: int
    comparison_count: int
    successful_comparison_count: int
    cutover_blockers: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_cutover_gate_no_writes"
    writes_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "cutover_ready", bool(self.cutover_ready))
        object.__setattr__(self, "required_successful_runs", max(1, int(self.required_successful_runs or 0)))
        object.__setattr__(self, "comparison_count", max(0, int(self.comparison_count or 0)))
        object.__setattr__(
            self,
            "successful_comparison_count",
            max(0, int(self.successful_comparison_count or 0)),
        )
        object.__setattr__(self, "cutover_blockers", _to_tuple_str(list(self.cutover_blockers)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "cutover_ready": self.cutover_ready,
            "required_successful_runs": self.required_successful_runs,
            "comparison_count": self.comparison_count,
            "successful_comparison_count": self.successful_comparison_count,
            "cutover_blockers": list(self.cutover_blockers),
            "metadata": dict(self.metadata),
        }
