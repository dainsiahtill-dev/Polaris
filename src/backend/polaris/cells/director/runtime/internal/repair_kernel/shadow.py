"""Read-only shadow comparison helpers for deterministic repair cutover."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .contracts import RepairReceipt


@dataclass(frozen=True)
class RepairShadowComparison:
    """Comparison between baseline repair effects and kernel receipts."""

    baseline_source_tools: tuple[str, ...]
    kernel_source_tools: tuple[str, ...]
    baseline_paths: tuple[str, ...]
    kernel_paths: tuple[str, ...]
    missing_paths_in_kernel: tuple[str, ...] = ()
    extra_paths_in_kernel: tuple[str, ...] = ()
    missing_source_tools_in_kernel: tuple[str, ...] = ()
    extra_source_tools_in_kernel: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return not (
            self.missing_paths_in_kernel
            or self.extra_paths_in_kernel
            or self.missing_source_tools_in_kernel
            or self.extra_source_tools_in_kernel
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "baseline_source_tools": list(self.baseline_source_tools),
            "kernel_source_tools": list(self.kernel_source_tools),
            "baseline_paths": list(self.baseline_paths),
            "kernel_paths": list(self.kernel_paths),
            "missing_paths_in_kernel": list(self.missing_paths_in_kernel),
            "extra_paths_in_kernel": list(self.extra_paths_in_kernel),
            "missing_source_tools_in_kernel": list(self.missing_source_tools_in_kernel),
            "extra_source_tools_in_kernel": list(self.extra_source_tools_in_kernel),
            "metadata": dict(self.metadata),
        }


def compare_baseline_and_kernel_repairs(
    *,
    baseline_tool_results: Sequence[Mapping[str, Any]],
    kernel_receipts: Sequence[RepairReceipt],
) -> RepairShadowComparison:
    """Compare baseline repair write scope against kernel shadow/commit receipts."""

    baseline_paths, baseline_source_tools = _extract_baseline_scope(baseline_tool_results)
    kernel_paths = tuple(sorted({path for receipt in kernel_receipts for path in receipt.files_changed if path}))
    kernel_source_tools = tuple(sorted({receipt.source_tool for receipt in kernel_receipts if receipt.source_tool}))
    baseline_path_set = set(baseline_paths)
    kernel_path_set = set(kernel_paths)
    baseline_tool_set = set(baseline_source_tools)
    kernel_tool_set = set(kernel_source_tools)
    return RepairShadowComparison(
        baseline_source_tools=baseline_source_tools,
        kernel_source_tools=kernel_source_tools,
        baseline_paths=baseline_paths,
        kernel_paths=kernel_paths,
        missing_paths_in_kernel=tuple(sorted(baseline_path_set - kernel_path_set)),
        extra_paths_in_kernel=tuple(sorted(kernel_path_set - baseline_path_set)),
        missing_source_tools_in_kernel=tuple(sorted(baseline_tool_set - kernel_tool_set)),
        extra_source_tools_in_kernel=tuple(sorted(kernel_tool_set - baseline_tool_set)),
        metadata={
            "baseline_tool_result_count": len(tuple(baseline_tool_results or ())),
            "kernel_receipt_count": len(tuple(kernel_receipts or ())),
            "read_only": True,
            "writes_performed": False,
        },
    )


def _extract_baseline_scope(tool_results: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    paths: set[str] = set()
    source_tools: set[str] = set()
    for item in tool_results or ():
        result = item.get("result")
        payload = result if isinstance(result, Mapping) else {}
        source_tool = str(payload.get("source_tool") or item.get("tool_name") or item.get("tool") or "").strip()
        if source_tool:
            source_tools.add(source_tool)
        file_path = str(payload.get("file") or payload.get("path") or "").strip().replace("\\", "/")
        if file_path:
            paths.add(file_path)
    return tuple(sorted(paths)), tuple(sorted(source_tools))


__all__ = [
    "RepairShadowComparison",
    "compare_baseline_and_kernel_repairs",
]
