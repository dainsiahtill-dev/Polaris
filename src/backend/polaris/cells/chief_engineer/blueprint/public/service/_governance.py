"""Governance ledgers (risk/ADR/tech-debt/radar/post-mortem) and governance attach."""

from __future__ import annotations

from typing import Any

from ...internal.adr_log import ADRDecisionLog, build_adr_event
from ...internal.blueprint_persistence import BlueprintPersistence
from ...internal.post_mortem import PostMortemLog, build_post_mortem_event
from ...internal.quality_gate import evaluate_quality_gate
from ...internal.release_readiness import build_release_readiness
from ...internal.risks import RiskRegister, build_risk_event
from ...internal.rollback_link import build_rollback_link
from ...internal.tech_debt import TechDebtLedger, build_tech_debt_event
from ...internal.tech_radar import TechRadarLedger, build_tech_radar_event
from ..contracts import (
    ADRRecordV1,
    GovernanceSummaryV1,
    ListADRsQueryV1,
    ListPostMortemsQueryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    ListTechRadarQueryV1,
    PostMortemRecordV1,
    RegisterADRCommandV1,
    RegisterPostMortemCommandV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RegisterTechRadarCommandV1,
    ReleaseReadinessV1,
    RiskRecordV1,
    StackPolicyViolationV1,
    TechDebtRecordV1,
    TechRadarEntryV1,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
)
from ._helpers import logger


def register_risk(command: RegisterRiskCommandV1) -> RiskRecordV1:
    """Register a new entry in the workspace Risk Register."""

    register = RiskRegister(command.workspace)
    record = register.register(command)
    event = build_risk_event(
        risk_id=record.risk_id,
        workspace=command.workspace,
        action="registered",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.risk_registered risk_id=%s task_id=%s severity=%s event_id=%s",
        record.risk_id,
        record.task_id,
        record.severity.value,
        event.event_id,
    )
    return record


def list_risks(query: ListRisksQueryV1) -> list[RiskRecordV1]:
    """List Risk Register entries for the workspace with optional filters."""

    return RiskRegister(query.workspace).list(
        task_id=query.task_id,
        severity=query.severity,
        status=query.status,
    )


def update_risk_status(
    command: UpdateRiskStatusCommandV1,
    *,
    actor: str = "system",
) -> RiskRecordV1:
    """Transition a risk to a new status; append a history entry."""

    record = RiskRegister(command.workspace).update_status(command, actor)
    event = build_risk_event(
        risk_id=record.risk_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.risk_status_changed risk_id=%s status=%s event_id=%s",
        record.risk_id,
        record.status.value,
        event.event_id,
    )
    return record


def register_tech_debt(command: RegisterTechDebtCommandV1) -> TechDebtRecordV1:
    """Register a new entry in the workspace Tech-Debt Ledger."""

    ledger = TechDebtLedger(command.workspace)
    record = ledger.register(command)
    event = build_tech_debt_event(
        debt_id=record.debt_id,
        workspace=command.workspace,
        action="registered",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.tech_debt_registered debt_id=%s surface=%s severity=%s event_id=%s",
        record.debt_id,
        record.surface,
        record.severity.value,
        event.event_id,
    )
    return record


def list_tech_debt(query: ListTechDebtQueryV1) -> list[TechDebtRecordV1]:
    """List Tech-Debt Ledger entries for the workspace with optional filters."""

    return TechDebtLedger(query.workspace).list_for_query(query)


def update_tech_debt_status(
    command: UpdateTechDebtStatusCommandV1,
    *,
    actor: str = "system",
) -> TechDebtRecordV1:
    """Transition a tech-debt entry to a new status; append a history entry."""

    record = TechDebtLedger(command.workspace).update_status(command, actor)
    event = build_tech_debt_event(
        debt_id=record.debt_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.tech_debt_status_changed debt_id=%s status=%s event_id=%s",
        record.debt_id,
        record.status.value,
        event.event_id,
    )
    return record


def register_adr(command: RegisterADRCommandV1) -> ADRRecordV1:
    """Record a new Architecture Decision Record in the workspace."""

    record = ADRDecisionLog(command.workspace).register(command)
    event = build_adr_event(
        adr_id=record.adr_id,
        workspace=command.workspace,
        action="proposed",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.adr_registered adr_id=%s title=%s event_id=%s",
        record.adr_id,
        record.title,
        event.event_id,
    )
    return record


def list_adrs(query: ListADRsQueryV1) -> list[ADRRecordV1]:
    """List Architecture Decision Records for the workspace with optional filters."""

    return ADRDecisionLog(query.workspace, ensure_directory=False).list(
        status=query.status,
        task_id=query.task_id,
    )


def update_adr_status(
    command: UpdateADRStatusCommandV1,
    *,
    actor: str = "chief_engineer",
) -> ADRRecordV1:
    """Transition an ADR to a new status; append a history entry."""

    record = ADRDecisionLog(command.workspace).update_status(command, actor)
    event = build_adr_event(
        adr_id=record.adr_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.adr_status_changed adr_id=%s status=%s event_id=%s",
        record.adr_id,
        record.status.value,
        event.event_id,
    )
    return record


def summarize_adrs(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace Architecture Decision Log."""

    return ADRDecisionLog(workspace, ensure_directory=False).summarize()


def summarize_risks(workspace: str, *, task_id: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Risk Register."""

    return RiskRegister(workspace, ensure_directory=False).summarize(task_id=task_id)


def summarize_tech_debt(workspace: str, *, surface: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Tech-Debt Ledger."""

    return TechDebtLedger(workspace, ensure_directory=False).summarize(surface=surface)


def register_tech_radar(command: RegisterTechRadarCommandV1) -> TechRadarEntryV1:
    """Place a library on a Tech-Radar ring for the workspace."""

    record = TechRadarLedger(command.workspace).register(command)
    event = build_tech_radar_event(
        entry_id=record.entry_id,
        workspace=command.workspace,
        action=f"ring:{record.ring.value}",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.tech_radar_registered entry_id=%s library=%s ring=%s event_id=%s",
        record.entry_id,
        record.library,
        record.ring.value,
        event.event_id,
    )
    return record


def list_tech_radar(query: ListTechRadarQueryV1) -> list[TechRadarEntryV1]:
    """List Tech-Radar entries for the workspace with an optional ring filter."""

    return TechRadarLedger(query.workspace, ensure_directory=False).list(ring=query.ring)


def update_tech_radar_ring(
    command: UpdateTechRadarRingCommandV1,
    *,
    actor: str = "chief_engineer",
) -> TechRadarEntryV1:
    """Move a Tech-Radar entry to a new ring; append a history entry."""

    record = TechRadarLedger(command.workspace).update_ring(command, actor)
    event = build_tech_radar_event(
        entry_id=record.entry_id,
        workspace=command.workspace,
        action=f"ring:{record.ring.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.tech_radar_ring_changed entry_id=%s ring=%s event_id=%s",
        record.entry_id,
        record.ring.value,
        event.event_id,
    )
    return record


def summarize_tech_radar(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace Tech Radar."""

    return TechRadarLedger(workspace, ensure_directory=False).summarize()


def check_stack_policy(workspace: str, libraries: list[str]) -> list[StackPolicyViolationV1]:
    """Return a stack-policy violation for each library on a hold/deprecated ring."""

    return TechRadarLedger(workspace, ensure_directory=False).check_stack_policy(libraries)


def register_post_mortem(command: RegisterPostMortemCommandV1) -> PostMortemRecordV1:
    """Record a new post-mortem / incident review for the workspace."""

    record = PostMortemLog(command.workspace).register(command)
    event = build_post_mortem_event(
        incident_id=record.incident_id,
        workspace=command.workspace,
        action="recorded",
        actor=command.owner,
    )
    logger.info(
        "chief_engineer.post_mortem_recorded incident_id=%s severity=%s event_id=%s",
        record.incident_id,
        record.severity.value,
        event.event_id,
    )
    return record


def list_post_mortems(query: ListPostMortemsQueryV1) -> list[PostMortemRecordV1]:
    """List post-mortems for the workspace with optional filters."""

    return PostMortemLog(query.workspace, ensure_directory=False).list_for_query(query)


def update_post_mortem_status(
    command: UpdatePostMortemStatusCommandV1,
    *,
    actor: str = "chief_engineer",
) -> PostMortemRecordV1:
    """Transition a post-mortem to a new status; append a history entry."""

    record = PostMortemLog(command.workspace).update_status(command, actor)
    event = build_post_mortem_event(
        incident_id=record.incident_id,
        workspace=command.workspace,
        action=f"status:{record.status.value}",
        actor=actor,
        note=command.note,
    )
    logger.info(
        "chief_engineer.post_mortem_status_changed incident_id=%s status=%s event_id=%s",
        record.incident_id,
        record.status.value,
        event.event_id,
    )
    return record


def summarize_post_mortems(workspace: str) -> dict[str, Any]:
    """Return aggregate counts for the workspace post-mortem log."""

    return PostMortemLog(workspace, ensure_directory=False).summarize()


def assess_release_readiness(
    workspace: str,
    *,
    blueprint_ids: list[str] | None = None,
    libraries: list[str] | None = None,
) -> ReleaseReadinessV1:
    """Synthesize an executive release GO / NO-GO from the governance surface.

    The Tier-2 capstone: aggregates open blocker/critical risks, per-blueprint
    quality-gate blockers, open sev1/sev2 incidents, stack-policy violations,
    and unpaid fatal/severe tech debt into one decision. Read-time and
    fail-closed (a blocking signal => ``no_go``).
    """
    decision = build_release_readiness(
        workspace,
        blueprint_ids=blueprint_ids,
        libraries=libraries,
    )
    logger.info(
        "chief_engineer.release_readiness_assessed workspace=%s decision=%s blockers=%d warnings=%d",
        workspace,
        decision.decision.value,
        decision.blocker_count,
        decision.warning_count,
    )
    return decision


def get_blueprint_governance(workspace: str, blueprint_id: str) -> GovernanceSummaryV1 | None:
    """Read the governance summary for a persisted blueprint.

    This is the Tier-1 consumption API for the PM / Director / QA loop:
    given a blueprint id, return its freshly-evaluated governance summary
    (risk + tech-debt summary, quality gate, rollback link). The summary
    is recomputed deterministically from the on-disk payload and the
    current Risk Register / Tech-Debt Ledger, so a caller always sees the
    latest gate verdict (e.g. after a blocker risk was resolved) without
    re-running blueprint generation.

    Args:
        workspace: Root workspace path.
        blueprint_id: Persisted blueprint id.

    Returns:
        A :class:`GovernanceSummaryV1`, or ``None`` when the blueprint is
        not found / unreadable (fail-closed: callers must treat ``None``
        as "not handoff-ready").
    """
    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return build_blueprint_governance(workspace, blueprint_id, payload)


def build_blueprint_governance(
    workspace: str,
    blueprint_id: str,
    blueprint: dict[str, Any],
) -> GovernanceSummaryV1:
    """Compute the governance summary for a blueprint payload.

    Pulls risks from the workspace register, evaluates the quality gate,
    and assembles a :class:`GovernanceSummaryV1`. The function is pure
    except for the Risk Register read; pass an explicit ``risks`` list
    on the blueprint to make it fully deterministic.
    """
    task_id = str(blueprint.get("task_id") or "").strip()
    risk_register = RiskRegister(workspace, ensure_directory=False)
    risks = risk_register.list(task_id=task_id) if task_id else risk_register.list()
    gate = evaluate_quality_gate(blueprint, risks=risks)
    rollback = build_rollback_link(
        workspace=workspace,
        blueprint_id=blueprint_id,
        blueprint=blueprint,
        risks=risks,
    )
    return GovernanceSummaryV1(
        blueprint_id=blueprint_id,
        risk_summary=risk_register.summarize(task_id=task_id),
        tech_debt_summary=TechDebtLedger(workspace, ensure_directory=False).summarize(),
        quality_gate=gate,
        rollback=rollback,
    )


def attach_governance_to_blueprint(
    workspace: str,
    blueprint_id: str,
    blueprint: dict[str, Any],
    *,
    persist: bool = True,
) -> GovernanceSummaryV1:
    """Compute governance and optionally persist it for a blueprint.

    The governance summary is computed from the current payload and the
    workspace's Risk Register / Tech-Debt Ledger, then optionally written back
    to the blueprint JSON under the ``governance`` key. ``blueprint`` is mutated
    in place: the ``governance`` field is added and ``handoff_ready`` is
    recomputed from the quality gate. ``persist=False`` supports transaction-like
    callers that must establish another durable prerequisite before a handoff-ready
    blueprint is visible. This call is idempotent and safe to invoke from blueprint
    regeneration paths.
    """
    summary = build_blueprint_governance(workspace, blueprint_id, blueprint)
    blueprint["governance"] = summary.to_dict()
    blueprint["handoff_ready"] = bool(summary.quality_gate.passed)
    if persist:
        BlueprintPersistence(workspace).save(blueprint_id, blueprint)
    return summary
