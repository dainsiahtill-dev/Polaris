"""Strategy receipt emitter and storage — zero behavior drift.

ReceiptEmitter writes to `<metadata_dir>/runtime/strategy_runs/`.
No existing logic is modified.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

if TYPE_CHECKING:
    from .strategy_contracts import StrategyReceipt

    # StrategyRunContext is imported for type hints but may not exist in all versions
    try:
        from .strategy_contracts import StrategyRunContext
    except ImportError:
        StrategyRunContext = Any

_logger = logging.getLogger(__name__)

# Runtime dir name under workspace
_RUNTIME_DIR_NAME = "runtime/strategy_runs"


def _ensure_runtime_dir(workspace: str | Path) -> Path:
    """Create the strategy_runs runtime directory if it does not exist."""
    root = Path(workspace)
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    target = Path(resolve_runtime_path(str(root), "runtime/strategy_runs"))
    target.mkdir(parents=True, exist_ok=True)
    return target


# ------------------------------------------------------------------
# StrategyReceiptEmitter
# ------------------------------------------------------------------


class StrategyReceiptEmitter:
    """Emit and persist StrategyReceipt records.

    Usage::

        emitter = StrategyReceiptEmitter(workspace="/path/to/repo")
        receipt = emitter.emit(run=run_ctx)
        path = emitter.write_receipt(receipt)
        loaded = emitter.load_receipt(path.stem)
    """

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace).resolve()
        self._run_dir: Path | None = None

    @property
    def run_dir(self) -> Path:
        """Lazily created runtime directory."""
        if self._run_dir is None:
            self._run_dir = _ensure_runtime_dir(self._workspace)
        return self._run_dir

    def emit(self, run: StrategyRunContext | None = None) -> StrategyReceipt:
        """Build a StrategyReceipt from a StrategyRunContext.

        If run is None, returns a minimal receipt with no decisions.
        """
        from .strategy_contracts import (
            BudgetDecision,
            ReadEscalation,
            StrategyReceipt as SR,  # noqa: N817
        )

        bundle_id = "kernelone.default.v1"
        bundle_version = "1.0.0"
        profile_id = "unknown"
        profile_hash = "none"
        turn_index = 0
        session_id = ""
        workspace = str(self._workspace)
        run_id = ""
        prompt_tokens_estimate = 0
        phase_reached = "map"
        compaction_triggered = False
        budget_decisions: tuple[BudgetDecision, ...] = ()
        read_escalations: tuple[ReadEscalation, ...] = ()
        tool_sequence: tuple[str, ...] = ()
        cache_hits: tuple[str, ...] = ()
        cache_misses: tuple[str, ...] = ()

        if run is not None:
            profile_id = run.profile_id
            profile_hash = run.profile_hash
            turn_index = run.turn_index
            session_id = run.session_id or ""
            workspace = run.workspace
            run_id = run.run_id
            if run.budget is not None and run.budget.current_tokens > 0:
                prompt_tokens_estimate = run.budget.current_tokens
            elif hasattr(run, "resolved_overrides"):
                # Fallback: extract from ContextOS BudgetPlan in resolved_overrides
                ctx_os = cast(Any, run).resolved_overrides.get("context_os_snapshot")
                if isinstance(ctx_os, dict):
                    budget_plan = ctx_os.get("budget_plan")
                    if isinstance(budget_plan, dict):
                        cit = budget_plan.get("current_input_tokens")
                        if isinstance(cit, (int, float)) and int(cit) > 0:
                            prompt_tokens_estimate = int(cit)
            if hasattr(run, "_compaction_triggered"):
                compaction_triggered = cast(Any, run)._compaction_triggered
            if hasattr(run, "_budget_decisions"):
                budget_decisions = tuple(cast(Any, run)._budget_decisions)
            if hasattr(run, "_read_escalations"):
                read_escalations = tuple(cast(Any, run)._read_escalations)
            if hasattr(run, "_tool_sequence"):
                tool_sequence = tuple(cast(Any, run)._tool_sequence)
            if hasattr(run, "_cache_hits"):
                cache_hits = tuple(cast(Any, run)._cache_hits)
            if hasattr(run, "_cache_misses"):
                cache_misses = tuple(cast(Any, run)._cache_misses)
            if hasattr(run, "exploration_phase_reached"):
                phase_reached = cast(Any, run).exploration_phase_reached

        return SR(
            bundle_id=bundle_id,
            bundle_version=bundle_version,
            profile_id=profile_id,
            profile_hash=profile_hash,
            turn_index=turn_index,
            timestamp=_utc_now_iso(),
            budget_decisions=budget_decisions,
            read_escalations=read_escalations,
            compaction_triggered=compaction_triggered,
            tool_sequence=tool_sequence,
            prompt_tokens_estimate=prompt_tokens_estimate,
            exploration_phase_reached=phase_reached,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            run_id=run_id,
            session_id=session_id,
            workspace=workspace,
        )

    def write_receipt(self, receipt: StrategyReceipt) -> Path:
        """Write a receipt to the runtime directory.

        Returns the path of the written file.
        File name: {run_id}_{turn_index:04d}_{profile_hash[:8]}.json
        """
        run_id = receipt.run_id or "unknown"
        turn = receipt.turn_index
        prefix = receipt.profile_hash[:8] if receipt.profile_hash != "none" else "none"
        filename = f"{run_id}_{turn:04d}_{prefix}.json"
        path = self.run_dir / filename
        try:
            text = json.dumps(receipt.to_dict(), indent=2, ensure_ascii=False)
            path.write_text(text, encoding="utf-8")
            _logger.debug("Receipt written: %s", path)
        except (RuntimeError, ValueError) as exc:  # pragma: no cover
            _logger.warning("Failed to write receipt to %s: %s", path, exc)
            raise
        return path

    def load_receipt(self, receipt_id: str) -> StrategyReceipt:
        """Load a receipt by its base filename (without .json)."""
        from .strategy_contracts import (
            BudgetDecision,
            BudgetDecisionKind,
            ReadEscalation,
            StrategyReceipt as SR,  # noqa: N817
        )

        path = self.run_dir / f"{receipt_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Receipt not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (RuntimeError, ValueError) as exc:
            _logger.error("Failed to load receipt from %s: %s", path, exc)
            raise

        bd_list = []
        for d in data.get("budget_decisions", []):
            bd_list.append(
                BudgetDecision(
                    kind=BudgetDecisionKind(d.get("kind", "check")),
                    estimated_tokens=d.get("estimated_tokens", 0),
                    headroom_before=d.get("headroom_before", 0),
                    headroom_after=d.get("headroom_after", 0),
                    decision=d.get("decision", ""),
                    reason=d.get("reason", ""),
                )
            )

        re_list = []
        for r in data.get("read_escalations", []):
            re_list.append(
                ReadEscalation(
                    asset_key=r.get("asset_key", ""),
                    decision=r.get("decision", ""),
                    estimated_tokens=r.get("estimated_tokens", 0),
                    reason=r.get("reason", ""),
                )
            )

        return SR(
            bundle_id=data.get("bundle_id", "unknown"),
            bundle_version=data.get("bundle_version", "1.0.0"),
            profile_id=data.get("profile_id", "unknown"),
            profile_hash=data.get("profile_hash", "none"),
            turn_index=data.get("turn_index", 0),
            timestamp=data.get("timestamp", _utc_now_iso()),
            budget_decisions=tuple(bd_list),
            read_escalations=tuple(re_list),
            compaction_triggered=data.get("compaction_triggered", False),
            tool_sequence=tuple(data.get("tool_sequence", [])),
            prompt_tokens_estimate=data.get("prompt_tokens_estimate", 0),
            exploration_phase_reached=data.get("exploration_phase_reached", "map"),
            cache_hits=tuple(data.get("cache_hits", [])),
            cache_misses=tuple(data.get("cache_misses", [])),
            run_id=data.get("run_id", ""),
            session_id=data.get("session_id", ""),
            workspace=data.get("workspace", ""),
        )

    def list_receipts(self) -> list[Path]:
        """Return all receipt JSON files in the run directory, sorted by mtime."""
        if not self.run_dir.is_dir():
            return []
        return sorted(self.run_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)


# StrategyRunContext is imported lazily in StrategyReceiptEmitter.emit()
# to avoid circular dependency (this file ← strategy_run_context.py).


__all__ = [
    "StrategyReceiptEmitter",
]
