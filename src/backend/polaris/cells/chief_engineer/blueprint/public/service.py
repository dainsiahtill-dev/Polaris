"""Stable public service exports for `chief_engineer.blueprint`."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from polaris.cells.control_plane.run_ledger.public import stable_hash

from ..internal.adr_log import ADRDecisionLog, build_adr_event
from ..internal.blueprint_persistence import BlueprintPersistence
from ..internal.ce_consumer import CEConsumer
from ..internal.chief_engineer_agent import ChiefEngineerAgent
from ..internal.chief_engineer_preflight import run_pre_dispatch_chief_engineer
from ..internal.handoff import build_handoff_decision
from ..internal.post_mortem import PostMortemLog, build_post_mortem_event
from ..internal.quality_gate import evaluate_quality_gate
from ..internal.release_readiness import build_release_readiness
from ..internal.risks import RiskRegister, build_risk_event
from ..internal.rollback_guard import create_rollback_guard
from ..internal.rollback_link import build_rollback_link
from ..internal.tech_debt import TechDebtLedger, build_tech_debt_event
from ..internal.tech_radar import TechRadarLedger, build_tech_radar_event
from .contracts import (
    ADRRecordV1,
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    GovernanceSummaryV1,
    HandoffDecisionV1,
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
    TaskBlueprintResultV1,
    TechDebtRecordV1,
    TechRadarEntryV1,
    UpdateADRStatusCommandV1,
    UpdatePostMortemStatusCommandV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
    UpdateTechRadarRingCommandV1,
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "task"


def _blueprint_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


_BLUEPRINT_HASH_IGNORED_KEYS = frozenset({"blueprint_hash", "job_token", "capability_token"})


def _hashable_blueprint_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _hashable_blueprint_payload(item)
            for key, item in value.items()
            if str(key) not in _BLUEPRINT_HASH_IGNORED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_hashable_blueprint_payload(item) for item in value]
    return value


def _blueprint_hash(payload: dict[str, Any]) -> str:
    return stable_hash(_hashable_blueprint_payload(payload))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        token = ""
        if isinstance(item, str):
            token = item.strip()
        elif isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("description")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token:
            rows.append(token)
    return rows


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        rows = _string_list(value)
        if rows:
            return rows
    return []


def _task_payload_from_context(context: dict[str, Any]) -> dict[str, Any]:
    for key in ("task", "pm_task", "source_task", "contract_task"):
        nested = _mapping(context.get(key))
        if nested:
            return nested
    return {}


def _target_files_from_context(context: dict[str, Any]) -> list[str]:
    task_payload = _task_payload_from_context(context)
    for key in ("target_files", "scope_paths", "files", "affected_files"):
        rows = _first_string_list(context.get(key), task_payload.get(key))
        if rows:
            return rows
    return []


def _qa_acceptance_from_task(task_payload: dict[str, Any]) -> list[str]:
    qa_contract = _mapping(task_payload.get("qa_contract"))
    return _first_string_list(qa_contract.get("acceptance_criteria"), qa_contract.get("acceptance"))


def _blueprint_contract_fields(context: dict[str, Any]) -> dict[str, Any]:
    task_payload = _task_payload_from_context(context)
    acceptance_criteria = _first_string_list(
        context.get("acceptance_criteria"),
        context.get("acceptance"),
        task_payload.get("acceptance_criteria"),
        task_payload.get("acceptance"),
        _qa_acceptance_from_task(task_payload),
    )
    execution_checklist = _first_string_list(
        context.get("execution_checklist"),
        context.get("steps"),
        task_payload.get("execution_checklist"),
        task_payload.get("steps"),
    )
    scope_paths = _first_string_list(
        context.get("scope_paths"),
        context.get("scope"),
        task_payload.get("scope_paths"),
        task_payload.get("scope"),
    )
    dependencies = _first_string_list(
        context.get("dependencies"),
        context.get("depends_on"),
        context.get("blocked_by"),
        task_payload.get("dependencies"),
        task_payload.get("depends_on"),
        task_payload.get("blocked_by"),
    )
    risks = _first_string_list(context.get("risks"), task_payload.get("risks"))
    return {
        "task": task_payload,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "scope_paths": scope_paths,
        "dependencies": dependencies,
        "risks": risks,
    }


def _contract_completeness(
    *,
    target_files: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
) -> dict[str, Any]:
    missing_fields: list[str] = []
    if not target_files:
        missing_fields.append("target_files")
    if not acceptance_criteria:
        missing_fields.append("acceptance_criteria")
    if not execution_checklist:
        missing_fields.append("execution_checklist")
    return {
        "handoff_ready": not missing_fields,
        "missing_fields": missing_fields,
        "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
    }


def _tuple_from_payload(value: Any) -> tuple[str, ...]:
    return tuple(_string_list(value))


def _latest_blueprint_for_task(
    persistence: BlueprintPersistence,
    *,
    task_id: str,
    run_id: str | None,
) -> tuple[str, dict[str, Any]] | None:
    matches: list[tuple[str, str, dict[str, Any]]] = []
    for blueprint_id in persistence.list_all():
        payload = persistence.load(blueprint_id)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_id") or "").strip() != task_id:
            continue
        payload_run_id = str(payload.get("run_id") or "").strip()
        if run_id and payload_run_id != run_id:
            continue
        updated_at = str(payload.get("updated_at") or payload.get("created_at") or "").strip()
        matches.append((updated_at, blueprint_id, payload))
    if not matches:
        return None
    _updated_at, blueprint_id, payload = max(matches, key=lambda item: (item[0], item[1]))
    return blueprint_id, payload


def generate_task_blueprint(command: GenerateTaskBlueprintCommandV1) -> TaskBlueprintResultV1:
    """Generate and persist a task-level Chief Engineer blueprint."""

    now = _utc_now()
    blueprint_id = f"ce_{_safe_token(command.task_id)}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    context = dict(command.context)
    constraints = dict(command.constraints)
    contract_fields = _blueprint_contract_fields(context)
    target_files = _target_files_from_context(context)
    title = str(context.get("task_title") or context.get("title") or command.objective).strip()
    summary = f"Chief Engineer blueprint for {command.task_id}: {command.objective}"
    acceptance_criteria = list(contract_fields["acceptance_criteria"])
    execution_checklist = list(contract_fields["execution_checklist"])
    scope_paths = list(contract_fields["scope_paths"])
    dependencies = list(contract_fields["dependencies"])
    contract_completeness = _contract_completeness(
        target_files=target_files,
        acceptance_criteria=acceptance_criteria,
        execution_checklist=execution_checklist,
    )
    context.setdefault("acceptance_criteria", acceptance_criteria)
    context.setdefault("execution_checklist", execution_checklist)
    context.setdefault("target_files", target_files)
    context.setdefault("scope_paths", scope_paths)
    context.setdefault("dependencies", dependencies)
    recommendations = (
        "Validate PM acceptance criteria before Director execution.",
        "Keep implementation scope within the recorded target files.",
    )
    risks = tuple(contract_fields["risks"])
    payload: dict[str, Any] = {
        "schema_version": "chief_engineer.blueprint.v1",
        "role": "ChiefEngineer",
        "blueprint_id": blueprint_id,
        "task_id": command.task_id,
        "run_id": command.run_id,
        "title": title,
        "objective": command.objective,
        "summary": summary,
        "status": "generated",
        "source": "chief_engineer.generate_task_blueprint",
        "target_files": target_files,
        "scope_paths": scope_paths,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "constraints": constraints,
        "context": context,
        "pm_task": contract_fields["task"],
        "contract_completeness": contract_completeness,
        "handoff_ready": bool(contract_completeness["handoff_ready"]),
        "recommendations": list(recommendations),
        "risks": list(risks),
        "created_at": now,
        "updated_at": now,
    }

    BlueprintPersistence(command.workspace).save(blueprint_id, payload)

    # Tier-1: attach governance summary (risk + tech-debt summary +
    # quality gate + rollback link) to the persisted blueprint. The
    # governance field is additive; old consumers that ignore unknown
    # keys are unaffected. ``attach_governance_to_blueprint`` mutates
    # ``payload`` in place (adds ``governance`` + recomputes
    # ``handoff_ready``) and rewrites the on-disk JSON.
    attach_governance_to_blueprint(command.workspace, blueprint_id, payload)
    blueprint_hash = _blueprint_hash(payload)
    payload["blueprint_hash"] = blueprint_hash
    BlueprintPersistence(command.workspace).save(blueprint_id, payload)

    return TaskBlueprintResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status="generated",
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=summary,
        recommendations=recommendations,
        risks=risks,
    )


def get_blueprint_status(query: GetBlueprintStatusQueryV1) -> TaskBlueprintResultV1:
    """Return the latest persisted Chief Engineer blueprint status for a task."""

    persistence = BlueprintPersistence(query.workspace, ensure_directory=False)
    match = _latest_blueprint_for_task(
        persistence,
        task_id=query.task_id,
        run_id=query.run_id,
    )
    if match is None:
        return TaskBlueprintResultV1(
            ok=False,
            task_id=query.task_id,
            workspace=query.workspace,
            status="missing",
            summary="No Chief Engineer blueprint has been generated for this task.",
        )

    blueprint_id, payload = match
    status = str(payload.get("status") or "generated").strip() or "generated"
    blueprint_hash = str(payload.get("blueprint_hash") or "").strip() or _blueprint_hash(payload)
    return TaskBlueprintResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status=status,
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
        blueprint_hash=blueprint_hash,
        summary=str(payload.get("summary") or "").strip(),
        recommendations=_tuple_from_payload(payload.get("recommendations")),
        risks=_tuple_from_payload(payload.get("risks")),
    )


# ═══════════════════════════════════════════════════════════════════════
# Tier-1 governance surface (Risk Register, Tech-Debt Ledger, Quality
# Gate, Rollback Link, Governance Summary). All functions are additive;
# they do not change existing service signatures.
# ═══════════════════════════════════════════════════════════════════════


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


def evaluate_handoff_decision(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Decide whether a blueprint may be handed to the Director.

    The enforcement primitive that closes the quality-gate loop. A handoff
    is blocked when the deterministic quality gate has blockers OR when the
    workspace Risk Register has open critical/blocker risks for the task.

    Args:
        workspace: Root workspace path.
        blueprint: Blueprint payload (must carry the construction contract
            fields target_files / acceptance_criteria / ...).
        blueprint_id: Owning blueprint id (falls back to ``blueprint``).
        task_id: Owning PM task id (falls back to ``blueprint``).

    Returns:
        A :class:`HandoffDecisionV1`. Fail-closed: a malformed blueprint
        evaluates to ``allowed=False``.
    """
    return build_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )


def evaluate_handoff_decision_for_blueprint(workspace: str, blueprint_id: str) -> HandoffDecisionV1 | None:
    """Load a persisted blueprint and decide whether it may be handed off.

    Returns ``None`` (fail-closed: caller treats as "not ready") when the
    blueprint is missing or unreadable.
    """
    payload = BlueprintPersistence(workspace, ensure_directory=False).load(blueprint_id)
    if not isinstance(payload, dict):
        return None
    return evaluate_handoff_decision(workspace, blueprint=payload, blueprint_id=blueprint_id)


def assert_handoff_ready(
    workspace: str,
    *,
    blueprint: dict[str, Any],
    blueprint_id: str = "",
    task_id: str = "",
) -> HandoffDecisionV1:
    """Raise when a blueprint must not be handed to the Director.

    Fail-closed enforcement helper for callers that want a hard gate: on a
    blocked decision it raises :class:`ChiefEngineerBlueprintErrorV1` with
    code ``handoff_blocked`` and the decision in ``details``.
    """
    decision = evaluate_handoff_decision(
        workspace,
        blueprint=blueprint,
        blueprint_id=blueprint_id,
        task_id=task_id,
    )
    if not decision.allowed:
        raise ChiefEngineerBlueprintErrorV1(
            f"handoff blocked: {decision.reason}",
            code="handoff_blocked",
            details=decision.to_dict(),
        )
    return decision


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
) -> GovernanceSummaryV1:
    """Compute and *persist* governance for a blueprint.

    The governance summary is computed from the current payload and the
    workspace's Risk Register / Tech-Debt Ledger, then written back to
    the blueprint JSON under the ``governance`` key. ``blueprint`` is
    mutated in place: the ``governance`` field is added and
    ``handoff_ready`` is recomputed from the quality gate. This call is
    idempotent and safe to invoke from blueprint regeneration paths.
    """
    summary = build_blueprint_governance(workspace, blueprint_id, blueprint)
    blueprint["governance"] = summary.to_dict()
    blueprint["handoff_ready"] = bool(summary.quality_gate.passed)
    BlueprintPersistence(workspace).save(blueprint_id, blueprint)
    return summary


# Required for governance logger; defined at module bottom to avoid a
# top-level `logging.getLogger` if any future refactor reorders imports.
import logging  # noqa: E402

logger = logging.getLogger(__name__)


# Contract types (dataclasses, enums, errors) are owned by contracts.py and
# re-exported through public/__init__.py from there — they are intentionally
# NOT listed here. service.__all__ exposes only service functions and the
# re-exported agent/consumer classes, uniformly across all ledgers.
__all__ = [
    "CEConsumer",
    "ChiefEngineerAgent",
    "assert_handoff_ready",
    "assess_release_readiness",
    "attach_governance_to_blueprint",
    "build_blueprint_governance",
    "check_stack_policy",
    "create_rollback_guard",
    "evaluate_handoff_decision",
    "evaluate_handoff_decision_for_blueprint",
    "generate_task_blueprint",
    "get_blueprint_governance",
    "get_blueprint_status",
    "list_adrs",
    "list_post_mortems",
    "list_risks",
    "list_tech_debt",
    "list_tech_radar",
    "register_adr",
    "register_post_mortem",
    "register_risk",
    "register_tech_debt",
    "register_tech_radar",
    "run_pre_dispatch_chief_engineer",
    "summarize_adrs",
    "summarize_post_mortems",
    "summarize_risks",
    "summarize_tech_debt",
    "summarize_tech_radar",
    "update_adr_status",
    "update_post_mortem_status",
    "update_risk_status",
    "update_tech_debt_status",
    "update_tech_radar_ring",
]
