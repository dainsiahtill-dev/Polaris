"""Public read services for the `director.runtime` cell."""

from __future__ import annotations

from typing import Any

from polaris.cells.director.runtime.internal.repair_kernel import deterministic_repair_strategy_catalog
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairStrategyCatalogResultV1,
    QueryDirectorRepairStrategyCatalogV1,
)


def _count_by_key(items: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def query_director_repair_strategy_catalog(
    query: QueryDirectorRepairStrategyCatalogV1 | None = None,
) -> DirectorRepairStrategyCatalogResultV1:
    """Return the read-only Director deterministic repair strategy catalog."""

    request = query or QueryDirectorRepairStrategyCatalogV1()
    all_items = deterministic_repair_strategy_catalog()
    visible_items = all_items[: request.max_items] if request.include_items else []
    summary: dict[str, Any] = {
        "total": len(all_items),
        "returned": len(visible_items),
        "by_language": _count_by_key(all_items, "language"),
        "by_phase": _count_by_key(all_items, "phase"),
        "by_concern": _count_by_key(all_items, "concern"),
        "by_risk": _count_by_key(all_items, "risk_level"),
    }
    return DirectorRepairStrategyCatalogResultV1(
        schema_version="director.deterministic_repair_strategy_catalog.v1",
        source="director.runtime.repair_kernel.strategy_catalog",
        access="read_only",
        agi_execution_authority=False,
        director_tool_execution_required=True,
        items=tuple(visible_items),
        summary=summary,
    )


__all__ = ["query_director_repair_strategy_catalog"]
