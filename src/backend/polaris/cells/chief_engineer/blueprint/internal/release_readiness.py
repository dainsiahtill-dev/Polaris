"""Release Readiness / Change-Advisory synthesis (Tier-2 capstone).

A read-time executive GO / NO-GO that aggregates the whole ChiefEngineer
governance surface — it does NOT store a ledger. It reuses the existing
RiskRegister, PostMortemLog, TechRadarLedger, TechDebtLedger, and the
per-blueprint Handoff Decision, turning their signals into one decision.

§8: reads existing, caller-supplied governance data only — synthesizes no
business/project-specific strings.
"""

from __future__ import annotations

import builtins
from datetime import datetime, timezone
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.handoff import (
    build_handoff_decision,
)
from polaris.cells.chief_engineer.blueprint.internal.post_mortem import PostMortemLog
from polaris.cells.chief_engineer.blueprint.internal.risks import RiskRegister
from polaris.cells.chief_engineer.blueprint.internal.tech_debt import TechDebtLedger
from polaris.cells.chief_engineer.blueprint.internal.tech_radar import TechRadarLedger
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    IncidentSeverity,
    PostMortemStatus,
    ReleaseDecision,
    ReleaseReadinessV1,
    RiskSeverity,
    RiskStatus,
    TechDebtSeverity,
    TechDebtStatus,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_release_readiness(
    workspace: str,
    *,
    blueprint_ids: builtins.list[str] | None = None,
    libraries: builtins.list[str] | None = None,
    assessed_at: str | None = None,
) -> ReleaseReadinessV1:
    """Synthesize an executive release decision from the governance surface.

    Args:
        workspace: Root workspace path.
        blueprint_ids: Optional release-candidate blueprints; each is run
            through the Handoff Decision and a blocked one is a hard blocker.
        libraries: Optional libraries in the release; any on a hold/deprecated
            Tech-Radar ring is a hard blocker (stack policy).
        assessed_at: Override for the assessment timestamp.

    Returns:
        A :class:`ReleaseReadinessV1`. Hard blockers => ``no_go``; warnings
        only => ``conditional_go``; clean => ``go``.
    """
    blockers: list[str] = []
    warnings: list[str] = []
    signals: dict[str, Any] = {}

    # 1) Risk Register — open critical/blocker = hard; open high = warning.
    # One disk read: derive both counts from a single list() (summarize() would
    # glob the dir a second time).
    risk_register = RiskRegister(workspace, ensure_directory=False)
    all_risks = risk_register.list()
    open_blocker_risks = sum(
        1
        for r in all_risks
        if r.status == RiskStatus.OPEN and r.severity in (RiskSeverity.BLOCKER, RiskSeverity.CRITICAL)
    )
    open_high = sum(1 for r in all_risks if r.status == RiskStatus.OPEN and r.severity == RiskSeverity.HIGH)
    signals["risk"] = {
        "open_critical_or_blocker": open_blocker_risks,
        "open_high": open_high,
        "total": len(all_risks),
    }
    if open_blocker_risks:
        blockers.append(f"risk: {open_blocker_risks} open critical/blocker risk(s)")
    if open_high:
        warnings.append(f"risk: {open_high} open high risk(s)")

    # 2) Per-blueprint Quality Gate via the Handoff Decision.
    # Count only NON-risk-derived gate blockers here: evaluate_quality_gate folds
    # a blueprint-task's open blocker/critical risks into gate.blockers, and those
    # risks are already counted workspace-wide in step 1. Subtracting the
    # decision's open_blocker_risk_count (exposed for exactly this) avoids
    # double-counting the same risk in both the `risk` and `quality_gate` signals.
    gate_blocked: list[str] = []
    if blueprint_ids:
        persistence = BlueprintPersistence(workspace, ensure_directory=False)
        for blueprint_id in blueprint_ids:
            payload = persistence.load(blueprint_id)
            if not isinstance(payload, dict):
                blockers.append(f"quality_gate: blueprint {blueprint_id} not found")
                gate_blocked.append(blueprint_id)
                continue
            decision = build_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)
            contract_blockers = max(0, decision.blocker_count - decision.open_blocker_risk_count)
            if contract_blockers > 0:
                gate_blocked.append(blueprint_id)
                blockers.append(
                    f"quality_gate: blueprint {blueprint_id} has {contract_blockers} contract blocker(s)"
                )
    signals["quality_gate"] = {
        "assessed": len(blueprint_ids or []),
        "blocked": len(gate_blocked),
    }

    # 3) Post-Mortems — open sev1 = hard; open sev2 = warning.
    pm_log = PostMortemLog(workspace, ensure_directory=False)
    open_sev1 = 0
    open_sev2 = 0
    for record in pm_log.list():
        if record.status == PostMortemStatus.CLOSED:
            continue
        if record.severity == IncidentSeverity.SEV1:
            open_sev1 += 1
        elif record.severity == IncidentSeverity.SEV2:
            open_sev2 += 1
    signals["post_mortem"] = {"open_sev1": open_sev1, "open_sev2": open_sev2}
    if open_sev1:
        blockers.append(f"post_mortem: {open_sev1} open SEV1 incident(s)")
    if open_sev2:
        warnings.append(f"post_mortem: {open_sev2} open SEV2 incident(s)")

    # 4) Stack policy — any requested library on hold/deprecated = hard.
    radar = TechRadarLedger(workspace, ensure_directory=False)
    violations = radar.check_stack_policy(libraries or [])
    signals["stack_policy"] = {"violations": len(violations)}
    for violation in violations:
        blockers.append(f"stack_policy: {violation.library} on {violation.ring.value} ring")

    # 5) Tech Debt — unpaid fatal = hard; unpaid severe = warning.
    debt_ledger = TechDebtLedger(workspace, ensure_directory=False)
    unpaid_fatal = 0
    unpaid_severe = 0
    for debt in debt_ledger.list():
        if debt.status in (TechDebtStatus.PAID, TechDebtStatus.WONTFIX):
            continue
        if debt.severity == TechDebtSeverity.FATAL:
            unpaid_fatal += 1
        elif debt.severity == TechDebtSeverity.SEVERE:
            unpaid_severe += 1
    signals["tech_debt"] = {"unpaid_fatal": unpaid_fatal, "unpaid_severe": unpaid_severe}
    if unpaid_fatal:
        blockers.append(f"tech_debt: {unpaid_fatal} unpaid fatal item(s)")
    if unpaid_severe:
        warnings.append(f"tech_debt: {unpaid_severe} unpaid severe item(s)")

    if blockers:
        decision_value = ReleaseDecision.NO_GO
    elif warnings:
        decision_value = ReleaseDecision.CONDITIONAL_GO
    else:
        decision_value = ReleaseDecision.GO

    return ReleaseReadinessV1(
        decision=decision_value,
        workspace=workspace,
        blocker_count=len(blockers),
        warning_count=len(warnings),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        signals=signals,
        assessed_at=str(assessed_at or _utc_now()),
    )


__all__ = ["build_release_readiness"]
