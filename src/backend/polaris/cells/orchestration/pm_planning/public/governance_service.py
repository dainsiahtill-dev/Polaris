"""Runtime governance facade for orchestration.pm_planning.

This module exposes the cell's landed PM capability cores (risk register,
commitments, decisions, backlog ranking/critical-path, project report) as plain,
JSON-safe service functions. It keeps the live-calling surface in one place so
the PM agent and other consumers do not need to import the individual internal
stores directly.

Every function is fail-closed: storage or compute errors degrade to an
``{"ok": False, "error": ...}`` response rather than raising into the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..internal.decision_log import DecisionRegister, DecisionStatus
from ..internal.dependency_validator import DependencyCycleError, compute_schedule
from ..internal.milestones import MilestoneRegister, MilestoneStatus
from ..internal.project_report import _load_pm_tasks, build_pm_project_report
from ..internal.raid_register import (
    RaidCategory,
    RaidProbability,
    RaidRegister,
    RaidSeverity,
    RaidStatus,
)
from ..internal.wsjf import score_tasks_wsjf

__all__ = [
    "build_project_status_report",
    "compute_backlog_ranking",
    "compute_critical_path",
    "list_commitments",
    "list_decisions",
    "list_risks",
    "register_commitment",
    "register_decision",
    "register_risk",
    "summarize_commitments",
    "summarize_decisions",
    "summarize_risks",
    "update_commitment_status",
    "update_decision_status",
    "update_risk_status",
]


# --------------------------------------------------------------------------------------
# Coercion helpers (fail-closed string -> enum).
# --------------------------------------------------------------------------------------


def _coerce_risk_category(value: str | None) -> RaidCategory:
    try:
        return RaidCategory(str(value or "risk").strip().lower() or "risk")
    except ValueError:
        return RaidCategory.RISK


def _coerce_risk_severity(value: str | None) -> RaidSeverity:
    try:
        return RaidSeverity(str(value or "medium").strip().lower() or "medium")
    except ValueError:
        return RaidSeverity.MEDIUM


def _coerce_risk_probability(value: str | None) -> RaidProbability:
    try:
        return RaidProbability(str(value or "possible").strip().lower() or "possible")
    except ValueError:
        return RaidProbability.POSSIBLE


def _coerce_risk_status(value: str | None) -> RaidStatus:
    try:
        return RaidStatus(str(value or "open").strip().lower() or "open")
    except ValueError:
        return RaidStatus.OPEN


def _coerce_commitment_status(value: str | None) -> MilestoneStatus:
    try:
        return MilestoneStatus(str(value or "planned").strip().lower() or "planned")
    except ValueError:
        return MilestoneStatus.PLANNED


def _coerce_decision_status(value: str | None) -> DecisionStatus:
    try:
        return DecisionStatus(str(value or "proposed").strip().lower() or "proposed")
    except ValueError:
        return DecisionStatus.PROPOSED


# --------------------------------------------------------------------------------------
# Risk register.
# --------------------------------------------------------------------------------------


def summarize_risks(workspace: str) -> dict[str, Any]:
    """Return a fail-closed risk register summary."""
    try:
        register = RaidRegister(workspace)
        return {"ok": True, "summary": register.summarize()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Risk summary failed: {exc}"}


def list_risks(
    workspace: str,
    *,
    category: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """List risk register entries with optional filters."""
    try:
        register = RaidRegister(workspace)
        records = register.list(
            category=_coerce_risk_category(category) if category else None,
            status=_coerce_risk_status(status) if status else None,
            severity=_coerce_risk_severity(severity) if severity else None,
            task_id=str(task_id).strip() if task_id else None,
        )
        return {
            "ok": True,
            "entries": [record.to_dict() for record in records],
            "count": len(records),
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Risk list failed: {exc}"}


def register_risk(
    workspace: str,
    *,
    title: str,
    category: str,
    severity: str,
    probability: str,
    task_id: str,
    owner: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """Register a new risk register entry."""
    try:
        register = RaidRegister(workspace)
        record = register.register(
            task_id=str(task_id).strip(),
            category=_coerce_risk_category(category),
            title=str(title).strip(),
            severity=_coerce_risk_severity(severity),
            probability=_coerce_risk_probability(probability),
            owner=str(owner).strip(),
            detail=str(detail).strip(),
        )
        return {"ok": True, "entry_id": record.entry_id, "entry": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Risk register failed: {exc}"}


def update_risk_status(
    workspace: str,
    *,
    entry_id: str,
    status: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Update the status of a risk register entry."""
    try:
        register = RaidRegister(workspace)
        record = register.update_status(
            entry_id=str(entry_id).strip(),
            status=_coerce_risk_status(status),
            actor=str(actor).strip(),
            note=str(note).strip(),
        )
        return {"ok": True, "entry_id": record.entry_id, "entry": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Risk update failed: {exc}"}


# --------------------------------------------------------------------------------------
# Commitments (milestones).
# --------------------------------------------------------------------------------------


def summarize_commitments(workspace: str) -> dict[str, Any]:
    """Return a fail-closed commitment register summary."""
    try:
        register = MilestoneRegister(workspace)
        return {"ok": True, "summary": register.summarize()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Commitment summary failed: {exc}"}


def list_commitments(
    workspace: str,
    *,
    status: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """List commitments with optional filters."""
    try:
        register = MilestoneRegister(workspace)
        records = register.list(
            status=_coerce_commitment_status(status) if status else None,
            task_id=str(task_id).strip() if task_id else None,
        )
        return {
            "ok": True,
            "commitments": [record.to_dict() for record in records],
            "count": len(records),
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Commitment list failed: {exc}"}


def register_commitment(
    workspace: str,
    *,
    name: str,
    description: str = "",
    target_iteration: int | None = None,
    task_ids: list[str] | None = None,
    owner: str = "",
    acceptance: str = "",
    actor: str = "",
) -> dict[str, Any]:
    """Register a new commitment."""
    try:
        register = MilestoneRegister(workspace)
        record = register.register(
            name=str(name).strip(),
            description=str(description).strip(),
            target_iteration=int(target_iteration) if target_iteration is not None else None,
            task_ids=tuple(str(t).strip() for t in (task_ids or [])),
            owner=str(owner).strip(),
            acceptance=str(acceptance).strip(),
            actor=str(actor).strip(),
        )
        return {"ok": True, "commitment_id": record.milestone_id, "commitment": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Commitment register failed: {exc}"}


def update_commitment_status(
    workspace: str,
    *,
    commitment_id: str,
    status: str,
    actor: str,
    note: str = "",
    current_iteration: int | None = None,
) -> dict[str, Any]:
    """Update the status of a commitment."""
    try:
        register = MilestoneRegister(workspace)
        record = register.update_status(
            milestone_id=str(commitment_id).strip(),
            status=_coerce_commitment_status(status),
            actor=str(actor).strip(),
            note=str(note).strip(),
            current_iteration=int(current_iteration) if current_iteration is not None else None,
        )
        return {"ok": True, "commitment_id": record.milestone_id, "commitment": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Commitment update failed: {exc}"}


# --------------------------------------------------------------------------------------
# Decisions.
# --------------------------------------------------------------------------------------


def summarize_decisions(workspace: str) -> dict[str, Any]:
    """Return a fail-closed decision register summary."""
    try:
        register = DecisionRegister(workspace)
        return {"ok": True, "summary": register.summarize()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Decision summary failed: {exc}"}


def list_decisions(
    workspace: str,
    *,
    status: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """List governance decisions with optional filters."""
    try:
        register = DecisionRegister(workspace)
        records = register.list(
            status=_coerce_decision_status(status) if status else None,
            task_id=str(task_id).strip() if task_id else None,
        )
        return {
            "ok": True,
            "decisions": [record.to_dict() for record in records],
            "count": len(records),
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Decision list failed: {exc}"}


def register_decision(
    workspace: str,
    *,
    title: str,
    context: str = "",
    options: list[str] | None = None,
    decision: str = "",
    rationale: str = "",
    owner: str = "",
    task_ids: list[str] | None = None,
    risk_ids: list[str] | None = None,
    actor: str = "",
) -> dict[str, Any]:
    """Register a governance decision."""
    try:
        register = DecisionRegister(workspace)
        record = register.register(
            title=str(title).strip(),
            context=str(context).strip(),
            options=tuple(str(o).strip() for o in (options or [])),
            decision=str(decision).strip(),
            rationale=str(rationale).strip(),
            owner=str(owner).strip(),
            task_ids=tuple(str(t).strip() for t in (task_ids or [])),
            risk_ids=tuple(str(r).strip() for r in (risk_ids or [])),
            actor=str(actor).strip(),
        )
        return {"ok": True, "decision_id": record.decision_id, "decision": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Decision register failed: {exc}"}


def update_decision_status(
    workspace: str,
    *,
    decision_id: str,
    status: str,
    actor: str,
    note: str = "",
) -> dict[str, Any]:
    """Update the status of a governance decision."""
    try:
        register = DecisionRegister(workspace)
        record = register.update_status(
            decision_id=str(decision_id).strip(),
            status=_coerce_decision_status(status),
            actor=str(actor).strip(),
            note=str(note).strip(),
        )
        return {"ok": True, "decision_id": record.decision_id, "decision": record.to_dict()}
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Decision update failed: {exc}"}


# --------------------------------------------------------------------------------------
# Planning / reporting.
# --------------------------------------------------------------------------------------


def build_project_status_report(
    workspace: str,
    *,
    current_iteration: int = 0,
) -> dict[str, Any]:
    """Build and publish the composed PM project status report."""
    try:
        return build_pm_project_report(
            workspace,
            current_iteration=int(current_iteration),
            generated_at=datetime.now().isoformat(),
        )
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Project status report failed: {exc}"}


def compute_backlog_ranking(workspace: str) -> dict[str, Any]:
    """Rank tasks by priority against the current contract."""
    try:
        tasks = _load_pm_tasks(workspace)
        try:
            schedule = compute_schedule(tasks)
        except DependencyCycleError:
            schedule = compute_schedule([])
        task_ids = [str(t.get("id", "")).strip() for t in tasks if t.get("id")]
        raid = RaidRegister(workspace)
        pressure: dict[str, int] = {}
        for task_id in task_ids:
            try:
                summary = raid.summarize(task_id=task_id)
                value = summary.get("open_critical_or_blocker", 0)
                pressure[task_id] = value if isinstance(value, int) and not isinstance(value, bool) else 0
            except (OSError, ValueError, TypeError):
                pressure[task_id] = 0
        scores = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=pressure)
        return {
            "ok": True,
            "ranking": [score.to_dict() for score in scores],
            "count": len(scores),
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Backlog ranking compute failed: {exc}"}


def compute_critical_path(workspace: str) -> dict[str, Any]:
    """Compute the critical path from the current PM task contract."""
    try:
        tasks = _load_pm_tasks(workspace)
        try:
            schedule = compute_schedule(tasks)
        except DependencyCycleError:
            return {
                "ok": True,
                "critical_path": [],
                "makespan": 0.0,
                "degraded": True,
                "reason": "dependency cycle",
            }
        return {
            "ok": True,
            "critical_path": list(schedule.critical_path),
            "makespan": float(schedule.makespan),
            "degraded": False,
        }
    except (OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"Critical path compute failed: {exc}"}
