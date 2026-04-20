"""Strategy benchmark adapter.

This adapter bridges the unified benchmark interface to the
strategy receipt replay system.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.unified_models import (
    ObservedBenchmarkRun,
    ToolCallObservation,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.storage import resolve_runtime_path


class StrategyBenchmarkAdapter:
    """Adapter for Strategy benchmark replay from receipts.

    This adapter loads pre-recorded strategy execution receipts
    and parses them into the unified observation format.

    Example:
        adapter = StrategyBenchmarkAdapter()
        observation = adapter.load_receipt(case, workspace)
    """

    def __init__(self) -> None:
        """Initialize the adapter."""
        self._receipt_cache: dict[str, dict[str, Any]] = {}

    def find_receipt(
        self,
        case_id: str,
        workspace: str,
    ) -> Path | None:
        """Find the most recent receipt file for a case.

        Args:
            case_id: The benchmark case ID.
            workspace: The workspace path.

        Returns:
            Path to the receipt file, or None if not found.
        """
        receipt_dir = Path(resolve_runtime_path(workspace, "runtime/strategy_runs"))
        if not receipt_dir.is_dir():
            return None

        pattern = f"*{case_id}*.json"
        matches = list(receipt_dir.glob(pattern))

        if not matches:
            return None

        # Return most recent
        return sorted(matches, key=lambda p: p.stat().st_mtime)[-1]

    def load_receipt(
        self,
        case_id: str,
        workspace: str,
    ) -> dict[str, Any] | None:
        """Load a receipt from cache or disk.

        Args:
            case_id: The benchmark case ID.
            workspace: The workspace path.

        Returns:
            Receipt dictionary, or None if not found.
        """
        cache_key = f"{workspace}:{case_id}"

        if cache_key in self._receipt_cache:
            return self._receipt_cache[cache_key]

        receipt_path = self.find_receipt(case_id, workspace)
        if receipt_path is None:
            return None

        try:
            with open(receipt_path, encoding="utf-8") as f:
                receipt = json.load(f)
            self._receipt_cache[cache_key] = receipt
            return receipt
        except (RuntimeError, ValueError):
            return None

    def parse_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        receipt: dict[str, Any],
    ) -> ObservedBenchmarkRun:
        """Parse a receipt into an ObservedBenchmarkRun.

        Args:
            case: The benchmark case.
            workspace: The workspace path.
            receipt: The strategy receipt data.

        Returns:
            ObservedBenchmarkRun with parsed execution trace.
        """
        tool_calls = [
            ToolCallObservation(
                tool=tc.get("tool", ""),
                args=tc.get("args", {}),
                event_index=idx,
            )
            for idx, tc in enumerate(receipt.get("tool_calls", []))
        ]

        return ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output=receipt.get("output", ""),
            thinking=receipt.get("thinking", ""),
            tool_calls=tuple(tool_calls),
            duration_ms=receipt.get("duration_ms", 0),
            event_count=receipt.get("event_count", len(tool_calls)),
            fingerprint=receipt.get("fingerprint", {}),
        )

    def load_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> ObservedBenchmarkRun | None:
        """Load an observation from a receipt.

        Args:
            case: The benchmark case.
            workspace: The workspace path.

        Returns:
            ObservedBenchmarkRun, or None if no receipt found.
        """
        receipt = self.load_receipt(case.case_id, workspace)
        if receipt is None:
            return None

        return self.parse_observation(case, workspace, receipt)

    def clear_cache(self) -> None:
        """Clear the receipt cache."""
        self._receipt_cache.clear()
