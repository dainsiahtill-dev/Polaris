"""Compatibility shim for the Director repair strategy catalog."""

from __future__ import annotations

from polaris.cells.director.runtime.internal.repair_kernel.strategy_catalog import (
    KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS as KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS,
    DeterministicRepairStrategy as DeterministicRepairStrategy,
    describe_deterministic_repair_strategy as describe_deterministic_repair_strategy,
    deterministic_repair_source_tool_known as deterministic_repair_source_tool_known,
    deterministic_repair_strategy_catalog as deterministic_repair_strategy_catalog,
    summarize_deterministic_repair_source_tools as summarize_deterministic_repair_source_tools,
)

__all__ = [
    "KNOWN_DETERMINISTIC_REPAIR_SOURCE_TOOLS",
    "DeterministicRepairStrategy",
    "describe_deterministic_repair_strategy",
    "deterministic_repair_source_tool_known",
    "deterministic_repair_strategy_catalog",
    "summarize_deterministic_repair_source_tools",
]
