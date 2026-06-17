"""Stable public service exports for `chief_engineer.blueprint`."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..internal.blueprint_persistence import BlueprintPersistence
from ..internal.ce_consumer import CEConsumer
from ..internal.chief_engineer_agent import ChiefEngineerAgent
from ..internal.chief_engineer_preflight import run_pre_dispatch_chief_engineer
from ..internal.quality_gate import evaluate_quality_gate
from ..internal.risks import RiskRegister, build_risk_event
from ..internal.rollback_guard import create_rollback_guard
from ..internal.rollback_link import build_rollback_link
from ..internal.tech_debt import TechDebtLedger, build_tech_debt_event
from .contracts import (
    ChiefEngineerBlueprintError,
    ChiefEngineerBlueprintErrorV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    GovernanceSummaryV1,
    ListRisksQueryV1,
    ListTechDebtQueryV1,
    QualityGateResultV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RiskEventV1,
    RiskRecordV1,
    RollbackLinkV1,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintResultV1,
    TechDebtEventV1,
    TechDebtRecordV1,
    UpdateRiskStatusCommandV1,
    UpdateTechDebtStatusCommandV1,
)

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "task"


def _blueprint_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


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

    return TaskBlueprintResultV1(
        ok=True,
        task_id=command.task_id,
        workspace=command.workspace,
        status="generated",
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
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
    return TaskBlueprintResultV1(
        ok=True,
        task_id=query.task_id,
        workspace=query.workspace,
        status=status,
        blueprint_id=blueprint_id,
        blueprint_path=_blueprint_path(blueprint_id),
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


def summarize_risks(workspace: str, *, task_id: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Risk Register."""

    return RiskRegister(workspace, ensure_directory=False).summarize(task_id=task_id)


def summarize_tech_debt(workspace: str, *, surface: str | None = None) -> dict[str, Any]:
    """Return aggregate counts for the workspace Tech-Debt Ledger."""

    return TechDebtLedger(workspace, ensure_directory=False).summarize(surface=surface)


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


__all__ = [
    "CEConsumer",
    "ChiefEngineerAgent",
    "ChiefEngineerBlueprintError",
    "ChiefEngineerBlueprintErrorV1",
    "GenerateTaskBlueprintCommandV1",
    "GetBlueprintStatusQueryV1",
    "GovernanceSummaryV1",
    "ListRisksQueryV1",
    "ListTechDebtQueryV1",
    "QualityGateResultV1",
    "RegisterRiskCommandV1",
    "RegisterTechDebtCommandV1",
    "RiskEventV1",
    "RiskRecordV1",
    "RollbackLinkV1",
    "TaskBlueprintGeneratedEventV1",
    "TaskBlueprintResultV1",
    "TechDebtEventV1",
    "TechDebtRecordV1",
    "UpdateRiskStatusCommandV1",
    "UpdateTechDebtStatusCommandV1",
    "attach_governance_to_blueprint",
    "build_blueprint_governance",
    "create_rollback_guard",
    "generate_task_blueprint",
    "get_blueprint_status",
    "list_risks",
    "list_tech_debt",
    "register_risk",
    "register_tech_debt",
    "run_pre_dispatch_chief_engineer",
    "summarize_risks",
    "summarize_tech_debt",
    "update_risk_status",
    "update_tech_debt_status",
]
