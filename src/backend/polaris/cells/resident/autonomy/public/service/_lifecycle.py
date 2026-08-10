"""Resident lifecycle, goals, cycle, and status queries."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from polaris.cells.resident.autonomy.internal.agi_capability_surface import (
    resident_agi_capability_surface_payload,
)
from polaris.cells.resident.autonomy.internal.goal_attempt_ledger import (
    observe_goal_attempt,
    query_goal_execution,
    settle_goal_attempt,
    start_goal_attempt,
)
from polaris.cells.resident.autonomy.internal.resident_runtime_service import (
    get_resident_service,
    record_resident_decision,
)
from polaris.cells.resident.autonomy.public.contracts import (
    ApproveResidentGoalCommandV1,
    ArchiveResidentGoalCommandV1,
    CreateResidentGoalCommandV1,
    ExtractResidentSkillsCommandV1,
    MaterializeResidentGoalCommandV1,
    ObserveResidentGoalAttemptCommandV1,
    QueryResidentCapabilitiesV1,
    QueryResidentGoalExecutionV1,
    QueryResidentStatusV1,
    RecordResidentDecisionCommandV1,
    RecordResidentEvidenceCommandV1,
    RejectResidentGoalCommandV1,
    ResidentAutonomyError,
    ResidentAutonomyResultV1,
    ResidentCycleCompletedEventV1,
    ResidentGoalAttemptReceiptV1,
    ResidentGoalExecutionV1,
    ResidentGoalLifecycleErrorV1,
    RunResidentCycleCommandV1,
    RunResidentExperimentsCommandV1,
    RunResidentGoalCommandV1,
    RunResidentImprovementsCommandV1,
    RunResidentTickCommandV1,
    SettleResidentGoalAttemptCommandV1,
    StageResidentGoalCommandV1,
    StartResidentCommandV1,
    StartResidentGoalAttemptCommandV1,
    StopResidentCommandV1,
    UpdateResidentAgiParticipationCommandV1,
    UpdateResidentIdentityCommandV1,
)
from polaris.domain.models.resident import utc_now_iso

from ._helpers import logger, publish_resident_status_update

_CYCLE_ACTIONS: tuple[str, ...] = (
    "meta_cognition",
    "skill_foundry",
    "capability_graph",
    "counterfactual_lab",
    "self_improvement_lab",
    "goal_generation",
)


def query_resident_status(query: QueryResidentStatusV1, *, include_details: bool = False) -> dict[str, Any]:
    """Handle :class:`QueryResidentStatusV1` → resident status snapshot."""
    return get_resident_service(query.workspace).get_status(include_details=include_details)


def start_resident_goal_attempt(
    command: StartResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not StartResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid start command type")
    return start_goal_attempt(command)


def observe_resident_goal_attempt(
    command: ObserveResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not ObserveResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid observe command type")
    return observe_goal_attempt(command)


def settle_resident_goal_attempt(
    command: SettleResidentGoalAttemptCommandV1,
) -> ResidentGoalAttemptReceiptV1:
    if type(command) is not SettleResidentGoalAttemptCommandV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_attempt_command", "invalid settle command type")
    return settle_goal_attempt(command)


def query_resident_goal_execution(query: QueryResidentGoalExecutionV1) -> ResidentGoalExecutionV1:
    """Zero-write durable execution query; does not construct ResidentService."""
    if type(query) is not QueryResidentGoalExecutionV1:
        raise ResidentGoalLifecycleErrorV1("invalid_goal_execution_query", "invalid execution query type")
    return query_goal_execution(query)


def query_resident_capabilities(query: QueryResidentCapabilitiesV1) -> dict[str, Any]:
    """Handle :class:`QueryResidentCapabilitiesV1` → AGI capability surface."""
    _ = get_resident_service(query.workspace)
    return resident_agi_capability_surface_payload()


def start_resident(command: StartResidentCommandV1) -> dict[str, Any]:
    """Handle :class:`StartResidentCommandV1` → activate Resident AGI."""

    result = get_resident_service(command.workspace).start(command.mode)
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_started",
        status_payload=result,
    )
    return result


def stop_resident(command: StopResidentCommandV1) -> dict[str, Any]:
    """Handle :class:`StopResidentCommandV1` → deactivate Resident AGI."""

    result = get_resident_service(command.workspace).stop()
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_stopped",
        status_payload=result,
    )
    return result


def run_resident_tick(command: RunResidentTickCommandV1) -> dict[str, Any]:
    """Handle :class:`RunResidentTickCommandV1` → advance one Resident loop."""

    result = get_resident_service(command.workspace).tick(force=command.force)
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_tick",
        status_payload=result,
        detail={"force": command.force},
    )
    return result


def update_resident_identity(command: UpdateResidentIdentityCommandV1) -> dict[str, Any]:
    """Handle :class:`UpdateResidentIdentityCommandV1` → update Resident identity."""

    result = get_resident_service(command.workspace).update_identity(command.payload)
    publish_resident_status_update(
        workspace=command.workspace,
        action="identity_updated",
    )
    return result


def update_resident_agi_participation(command: UpdateResidentAgiParticipationCommandV1) -> dict[str, Any]:
    """Handle AGI participation policy updates through a scoped public contract."""

    payload = {
        "enabled": command.enabled,
        "scopes": list(command.scopes),
        "participation": dict(command.participation),
        "custom_scopes_allowed": command.custom_scopes_allowed,
    }
    updated_identity = get_resident_service(command.workspace).update_identity({"resident_agi_participation": payload})
    participation_raw = updated_identity.get("resident_agi_participation")
    participation = participation_raw if isinstance(participation_raw, dict) else payload
    scopes_raw = participation.get("scopes")
    scopes = scopes_raw if isinstance(scopes_raw, list) else []
    publish_resident_status_update(
        workspace=command.workspace,
        action="resident_agi_participation_updated",
        detail={
            "enabled": bool(participation.get("enabled")),
            "scope_count": len(scopes),
            "configured_scope_ids": scopes,
        },
    )
    return participation


def create_resident_goal(command: CreateResidentGoalCommandV1) -> dict[str, Any]:
    """Handle :class:`CreateResidentGoalCommandV1` → create a governed goal."""

    result = get_resident_service(command.workspace).create_goal_proposal(command.payload).to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_created",
        detail={"goal_id": str(result.get("goal_id") or "")},
    )
    return result


def approve_resident_goal(command: ApproveResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`ApproveResidentGoalCommandV1` → approve a governed goal."""

    goal = get_resident_service(command.workspace).approve_goal(
        command.goal_id,
        note=command.note,
        expected_revision=command.expected_revision,
    )
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_approved",
        detail={"goal_id": command.goal_id},
    )
    return result


def reject_resident_goal(command: RejectResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`RejectResidentGoalCommandV1` → reject a governed goal."""

    goal = get_resident_service(command.workspace).reject_goal(
        command.goal_id,
        note=command.note,
        expected_revision=command.expected_revision,
    )
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_rejected",
        detail={"goal_id": command.goal_id},
    )
    return result


def materialize_resident_goal(command: MaterializeResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`MaterializeResidentGoalCommandV1` through the Resident goal bridge."""

    service = get_resident_service(command.workspace)
    contract = service.materialize_goal(
        command.goal_id,
        expected_revision=command.expected_revision,
    )
    if contract is not None:
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_materialized",
            status_payload=service.get_status(include_details=True),
            detail={"goal_id": command.goal_id},
        )
    return contract


def archive_resident_goal(command: ArchiveResidentGoalCommandV1) -> dict[str, Any] | None:
    """Archive a rejected or materialized Goal through strict CAS."""
    service = get_resident_service(command.workspace)
    goal = service.archive_goal(command.goal_id, expected_revision=command.expected_revision)
    if goal is None:
        return None
    result = goal.to_dict()
    publish_resident_status_update(
        workspace=command.workspace,
        action="goal_archived",
        detail={"goal_id": command.goal_id},
    )
    return result


def stage_resident_goal(command: StageResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`StageResidentGoalCommandV1` through the Resident goal bridge."""

    service = get_resident_service(command.workspace)
    staged = service.stage_goal(
        command.goal_id,
        promote_to_pm_runtime=command.promote_to_pm_runtime,
        ramdisk_root=command.ramdisk_root,
    )
    if staged is not None:
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_staged",
            status_payload=service.get_status(include_details=True),
            detail={
                "goal_id": command.goal_id,
                "promote_to_pm_runtime": command.promote_to_pm_runtime,
            },
        )
    return staged


async def run_resident_goal(command: RunResidentGoalCommandV1) -> dict[str, Any] | None:
    """Handle :class:`RunResidentGoalCommandV1` through the governed PM bridge."""

    service = get_resident_service(command.workspace)
    result = await service.run_goal(
        command.goal_id,
        settings=command.settings,
        run_type=command.run_type,
        run_director=command.run_director,
        director_iterations=command.director_iterations,
    )
    if result is not None:
        pm_run = result.get("pm_run") if isinstance(result, dict) else {}
        publish_resident_status_update(
            workspace=command.workspace,
            action="goal_run_submitted",
            status_payload=service.get_status(include_details=True),
            detail={
                "goal_id": command.goal_id,
                "run_id": str(pm_run.get("run_id") or "") if isinstance(pm_run, dict) else "",
                "run_director": command.run_director,
                "director_iterations": command.director_iterations,
            },
        )
    return result


def _emit_cycle_completed_event(
    command: RunResidentCycleCommandV1, result: ResidentAutonomyResultV1
) -> ResidentCycleCompletedEventV1:
    """Construct and surface a :class:`ResidentCycleCompletedEventV1`."""
    event = ResidentCycleCompletedEventV1(
        event_id=f"resident-cycle-{uuid4().hex[:12]}",
        workspace=result.workspace,
        cycle_id=command.cycle_id,
        status=result.status,
        completed_at=utc_now_iso(),
    )
    logger.info(
        "[resident] cycle completed: workspace=%s cycle=%s status=%s actions=%s",
        event.workspace,
        event.cycle_id,
        event.status,
        result.actions,
    )
    return event


def run_resident_cycle(command: RunResidentCycleCommandV1) -> ResidentAutonomyResultV1:
    """Handle :class:`RunResidentCycleCommandV1` → advance the resident loop.

    ``force`` is read from ``command.context['force']`` (default ``False``), so
    unattended callers preserve the "only ticks when active" gate.  Emits a
    :class:`ResidentCycleCompletedEventV1`; normalizes any failure to
    :class:`ResidentAutonomyError`.
    """
    force = bool(command.context.get("force", False))
    try:
        status = get_resident_service(command.workspace).tick(force=force)
    except ResidentAutonomyError:
        raise
    except Exception as exc:
        raise ResidentAutonomyError(
            f"resident cycle execution failed: {exc}",
            code="cycle_execution_failed",
            details={"cycle_id": command.cycle_id, "workspace": command.workspace},
        ) from exc

    runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
    summary = runtime.get("last_summary", {}) if isinstance(runtime, dict) else {}
    active = bool(runtime.get("active"))
    metrics = (
        {k: v for k, v in summary.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if isinstance(summary, dict)
        else {}
    )
    result = ResidentAutonomyResultV1(
        ok=True,
        workspace=command.workspace,
        cycle_id=command.cycle_id,
        status="completed" if active else "skipped_inactive",
        actions=_CYCLE_ACTIONS if active else (),
        evidence_refs=(),
        metrics=metrics,
    )
    _emit_cycle_completed_event(command, result)
    publish_resident_status_update(
        workspace=command.workspace,
        action="cycle_completed" if active else "cycle_skipped_inactive",
        status_payload=status,
        detail={"cycle_id": command.cycle_id, "actions": list(result.actions)},
    )
    return result


def record_resident_evidence(command: RecordResidentEvidenceCommandV1) -> dict[str, Any]:
    """Handle :class:`RecordResidentEvidenceCommandV1` → append to the decision trace.

    Evidence is mapped onto the evidence-first decision trace as an
    ``evidence:{kind}`` stage entry, so it feeds the same meta-cognition loop.
    """
    recorded = record_resident_decision(
        command.workspace,
        {
            "actor": "resident",
            "stage": f"evidence:{command.evidence_kind}",
            "summary": f"Recorded {command.evidence_kind} evidence for cycle {command.cycle_id}",
            "verdict": "success",
            "context_refs": [command.cycle_id],
            "actual_outcome": dict(command.payload),
        },
    )
    publish_resident_status_update(
        workspace=command.workspace,
        action="evidence_recorded",
        detail={
            "cycle_id": command.cycle_id,
            "evidence_kind": command.evidence_kind,
            "decision_id": str(recorded.get("decision_id") or ""),
        },
    )
    return recorded


def record_resident_decision_entry(command: RecordResidentDecisionCommandV1) -> dict[str, Any]:
    """Handle :class:`RecordResidentDecisionCommandV1` → append a decision trace entry."""

    recorded = record_resident_decision(command.workspace, command.payload)
    detail = {
        "decision_id": str(recorded.get("decision_id") or ""),
        "run_id": str(command.payload.get("run_id") or ""),
        "task_id": str(command.payload.get("task_id") or ""),
        "goal_id": str(command.payload.get("goal_id") or ""),
        **dict(command.detail),
    }
    publish_resident_status_update(
        workspace=command.workspace,
        action=command.action,
        detail=detail,
    )
    return recorded


def extract_resident_skills(command: ExtractResidentSkillsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`ExtractResidentSkillsCommandV1` → refresh skill artifacts."""

    skills = [item.to_dict() for item in get_resident_service(command.workspace).run_skill_foundry()]
    publish_resident_status_update(
        workspace=command.workspace,
        action="skills_extracted",
        detail={"count": len(skills)},
    )
    return skills


def run_resident_experiments(command: RunResidentExperimentsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`RunResidentExperimentsCommandV1` → replay counterfactual experiments."""

    experiments = get_resident_service(command.workspace).run_counterfactual_lab()
    publish_resident_status_update(
        workspace=command.workspace,
        action="experiments_run",
        detail={"count": len(experiments)},
    )
    return experiments


def run_resident_improvements(command: RunResidentImprovementsCommandV1) -> list[dict[str, Any]]:
    """Handle :class:`RunResidentImprovementsCommandV1` → propose self-improvements."""

    improvements = get_resident_service(command.workspace).run_self_improvement_lab()
    publish_resident_status_update(
        workspace=command.workspace,
        action="improvements_run",
        detail={"count": len(improvements)},
    )
    return improvements
