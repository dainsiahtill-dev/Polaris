"""Repair profile projection using the Director Runtime public catalog."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from polaris.cells.director.runtime.public import (
    ProjectDirectorRepairKernelSummaryV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    QueryDirectorRepairStrategyCatalogV1,
    project_director_repair_kernel_summary,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
)


def summarize_deterministic_repair_source_tools(source_tools: Sequence[str]) -> list[dict[str, Any]]:
    """Return compact, deduplicated repair profiles from the runtime public catalog."""

    catalog = {
        str(item.get("source_tool") or ""): dict(item)
        for item in query_director_repair_strategy_catalog(
            QueryDirectorRepairStrategyCatalogV1(include_items=True, max_items=1000)
        ).items
    }
    schedule_profiles = {
        str(item.source_tool or ""): {
            "source_tool": item.source_tool,
            "language": item.language,
            "phase": item.phase,
            "concern": "post_execution_schedule",
            "risk_level": "medium",
        }
        for item in query_director_repair_post_execution_schedule(
            QueryDirectorRepairPostExecutionScheduleV1(include_items=True)
        ).items
    }
    seen: set[str] = set()
    profiles: list[dict[str, Any]] = []
    for raw_tool in source_tools:
        source_tool = str(raw_tool or "").strip()
        if not source_tool or source_tool in seen:
            continue
        seen.add(source_tool)
        profile = dict(
            catalog.get(
                source_tool,
                schedule_profiles.get(
                    source_tool,
                    {
                        "source_tool": source_tool,
                        "language": "unknown",
                        "phase": "unknown",
                        "concern": "unregistered",
                        "risk_level": "high",
                    },
                ),
            )
        )
        profile["registered"] = source_tool in catalog
        profiles.append(profile)
    return profiles


def project_repair_kernel_summary(
    *,
    stage: str,
    tool_results: Sequence[dict[str, Any]],
    artifact_quality_errors: Sequence[str] = (),
    mode: str = "commit",
) -> dict[str, Any]:
    """Project adapter tool results through the Director Runtime public boundary."""

    return dict(
        project_director_repair_kernel_summary(
            ProjectDirectorRepairKernelSummaryV1(
                stage=stage,
                tool_results=tuple(dict(item or {}) for item in tool_results),
                artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                mode=mode,
            )
        ).summary
    )


__all__ = ["project_repair_kernel_summary", "summarize_deterministic_repair_source_tools"]
