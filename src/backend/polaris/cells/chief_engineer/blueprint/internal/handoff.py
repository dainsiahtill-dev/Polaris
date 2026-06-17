"""Director-handoff gate decision (enforcement primitive).

Single source of truth for "may this blueprint be handed to the Director?".
Lives in ``internal`` so both the public service layer and the CE consumer
can consume it without a public↔internal import cycle.
"""

from __future__ import annotations

import os
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.quality_gate import (
    evaluate_quality_gate,
)
from polaris.cells.chief_engineer.blueprint.internal.risks import RiskRegister
from polaris.cells.chief_engineer.blueprint.public.contracts import HandoffDecisionV1


def handoff_enforcement_enabled() -> bool:
    """Whether the CE consumer hard-blocks a failing handoff.

    Default OFF: a pipeline-behavior change must be opted into. When OFF,
    the decision is still computed and surfaced (advisory); when ON, a
    blocked decision requeues the task instead of acking it to the Director.
    """
    raw = os.getenv("KERNELONE_CE_HANDOFF_ENFORCEMENT", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def build_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Decide whether ``blueprint`` may be handed to the Director.

    Blocked when the deterministic quality gate has blockers OR the
    workspace Risk Register has open critical/blocker risks for the task.
    Fail-closed: a malformed blueprint evaluates to ``allowed=False``.
    """
    resolved_blueprint_id = (
        str(blueprint_id).strip() or str(blueprint.get("blueprint_id") or "").strip() or "unknown"
    )
    resolved_task_id = str(task_id).strip() or str(blueprint.get("task_id") or "").strip()
    risk_register = RiskRegister(workspace, ensure_directory=False)
    risks = risk_register.list(task_id=resolved_task_id) if resolved_task_id else risk_register.list()
    gate = evaluate_quality_gate(blueprint, risks=risks)
    risk_summary = risk_register.summarize(task_id=resolved_task_id or None)
    # open_blocker_risk_count is informational telemetry (how many blockers are
    # risk-derived). It is NOT re-applied to ``allowed``: evaluate_quality_gate
    # already folds open blocker/critical risks into gate.blockers, so
    # gate.passed is the single allow/block authority and a separate risk
    # subtraction here would be redundant.
    open_blocker_risks = int(risk_summary.get("open_critical_or_blocker", 0) or 0)
    allowed = gate.passed
    reason = "handoff_ready" if allowed else f"{gate.blocker_count} quality-gate blocker(s)"
    return HandoffDecisionV1(
        allowed=allowed,
        blueprint_id=resolved_blueprint_id,
        task_id=resolved_task_id,
        blocker_count=gate.blocker_count,
        warning_count=gate.warning_count,
        open_blocker_risk_count=open_blocker_risks,
        blockers=gate.blockers,
        reason=reason,
        evaluated_at=gate.evaluated_at,
    )


__all__ = ["build_handoff_decision", "handoff_enforcement_enabled"]
