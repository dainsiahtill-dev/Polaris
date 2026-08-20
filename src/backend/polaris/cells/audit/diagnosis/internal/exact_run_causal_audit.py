"""Pure exact-run causal classification for the audit diagnosis Cell."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _strings(value: object) -> list[str]:
    return [str(item).strip() for item in _sequence(value) if str(item).strip()]


def _link(status: str, source: str, **details: object) -> dict[str, object]:
    return {"status": status, "source": source, "details": details}


def _factory_stage_root_cause(failed_stages: list[str]) -> tuple[str, str, str, str]:
    stage = failed_stages[0] if failed_stages else "factory"
    if stage == "pm_planning":
        return (
            "orchestration.pm_planning.stage_failed",
            "orchestration.pm_planning",
            "same_pm_stage",
            "CHAIN_INCOMPLETE",
        )
    if stage == "chief_engineer_review":
        return (
            "chief_engineer.blueprint.stage_failed",
            "chief_engineer.blueprint",
            "same_ce_stage",
            "CHAIN_INCOMPLETE",
        )
    if stage == "director_dispatch":
        return ("director.runtime.stage_failed", "director.runtime", "same_director_task_only", "CHAIN_INCOMPLETE")
    if stage == "quality_gate":
        return ("qa.audit_verdict.quality_gate_failed", "qa.audit_verdict", "same_run_quality_gate_only", "QA_FAILED")
    return ("factory.pipeline.stage_failed", "factory.pipeline", "same_factory_stage_only", "CHAIN_INCOMPLETE")


def build_exact_run_causal_report(
    *,
    workspace: str,
    factory_run_id: str,
    project_id: str,
    factory_projection: Mapping[str, Any],
    ledger_projection: Mapping[str, Any],
    terminal_task_runtime_projection: Mapping[str, Any] | None,
    provider_request_audits: Sequence[Mapping[str, Any]] = (),
    audit_trail_total: int = 0,
) -> dict[str, object]:
    """Return one current root cause plus an evidence-link matrix.

    Priority follows the authority chain, not log arrival order. Historical
    failures remain counted but cannot override a newer current projection.
    """

    run_projection = _mapping(ledger_projection.get("run_projection"))
    revisions = _mapping(run_projection.get("gate_revisions"))
    revision_issues = _strings(revisions.get("issues"))
    evidence_policy = _mapping(ledger_projection.get("evidence_policy"))
    task_boundary = _mapping(ledger_projection.get("task_boundary"))
    tool_lifecycle = _mapping(ledger_projection.get("tool_lifecycle"))
    evidence_modalities = _mapping(ledger_projection.get("evidence_modalities"))
    failed_stages = _strings(factory_projection.get("failed_stages"))
    historical_failed_gate_count = int(run_projection.get("historical_failed_gate_count") or 0)
    historical_task_boundary_count = int(task_boundary.get("historical_failed_count") or 0)
    historical_error_count = historical_failed_gate_count + historical_task_boundary_count

    failed_required = _strings(evidence_policy.get("failed_required_modalities"))
    missing_required = _strings(evidence_policy.get("missing_required_modalities"))
    failed_control_plane = _strings(ledger_projection.get("failed_control_plane_events"))
    terminal_projection = _mapping(terminal_task_runtime_projection)
    terminal_rows = _sequence(_mapping(terminal_projection.get("projection")).get("rows"))
    terminal_failed_rows = [
        str(_mapping(row).get("external_task_id") or _mapping(row).get("task_id") or "")
        for row in terminal_rows
        if str(_mapping(row).get("status") or "").strip().lower() in {"failed", "blocked", "cancelled"}
    ]

    provider_ok = sum(1 for item in provider_request_audits if item.get("ok") is True)
    evidence_chain = {
        "factory_terminal": _link(
            "proven" if factory_projection.get("available") is True else "missing",
            "factory.pipeline",
            factory_status=str(factory_projection.get("status") or ""),
            failed_stages=failed_stages,
            chain_completed=bool(factory_projection.get("chain_completed")),
        ),
        "run_ledger": _link(
            "proven" if ledger_projection.get("available") is True else "missing",
            "control_plane.run_ledger",
            ok=bool(ledger_projection.get("ok")),
            ledger_status=str(ledger_projection.get("status") or ""),
            integrity_ok=bool(run_projection.get("integrity_ok")),
            outcome_ok=bool(run_projection.get("outcome_ok")),
            revision_issues=revision_issues,
        ),
        "task_boundary": _link(
            "proven" if task_boundary else "missing",
            "control_plane.run_ledger",
            ok=bool(task_boundary.get("ok")),
            latest_by_task_count=len(_mapping(task_boundary.get("latest_by_task"))),
            historical_failed_count=historical_task_boundary_count,
        ),
        "task_runtime": _link(
            "proven" if terminal_projection else "missing",
            "runtime.task_runtime",
            frozen_failed_rows=terminal_failed_rows,
            frozen_row_count=len(terminal_rows),
        ),
        "tool_lifecycle": _link(
            "proven" if tool_lifecycle else "missing",
            "control_plane.run_ledger",
            ok=bool(tool_lifecycle.get("ok")),
            effect_receipt_count=int(tool_lifecycle.get("effect_receipt_count") or 0),
            failed_count=int(tool_lifecycle.get("failed_count") or 0),
        ),
        "verifier": _link(
            "proven" if evidence_modalities else "missing",
            "control_plane.run_ledger",
            failed_required_modalities=failed_required,
            missing_required_modalities=missing_required,
            command=_mapping(evidence_modalities.get("command")),
        ),
        "qa_verdict": _link(
            "proven" if evidence_modalities.get("qa") else "missing",
            "qa.audit_verdict",
            qa=_mapping(evidence_modalities.get("qa")),
        ),
        "final_provider_request": _link(
            "proven" if provider_ok else "missing",
            "context.engine",
            audited_count=len(provider_request_audits),
            available_count=provider_ok,
        ),
    }

    root_cause_code = ""
    responsible_cell = ""
    retry_boundary = "none"
    current_status = "DELIVERY_VERIFIED"
    target_project_defect = False
    contradiction = False

    if factory_projection.get("available") is not True:
        root_cause_code = "factory.pipeline.run_not_found"
        responsible_cell = "factory.pipeline"
        retry_boundary = "inspect_instance_workspace_binding"
        current_status = "CONTROL_PLANE_FAIL"
    elif any(issue.startswith("gate_revision_chain_fork_or_stale:") for issue in revision_issues):
        root_cause_code = "control_plane.run_ledger.gate_revision_fork_after_runtime_reentry"
        responsible_cell = "control_plane.run_ledger"
        retry_boundary = "same_run_quality_gate_only"
        current_status = "CONTROL_PLANE_FAIL"
        contradiction = bool(task_boundary.get("ok")) and bool(_mapping(evidence_modalities.get("qa")).get("ok"))
    elif failed_control_plane:
        root_cause_code = "control_plane.run_ledger.failed_control_plane_event"
        responsible_cell = "control_plane.run_ledger"
        retry_boundary = "same_run_failed_boundary_only"
        current_status = "CONTROL_PLANE_FAIL"
    elif task_boundary and task_boundary.get("ok") is not True:
        root_cause_code = "runtime.task_runtime.canonical_task_boundary_incomplete"
        responsible_cell = "runtime.task_runtime"
        retry_boundary = "same_director_task_only"
        current_status = "CONTROL_PLANE_FAIL"
    elif tool_lifecycle and tool_lifecycle.get("ok") is not True:
        root_cause_code = "roles.kernel.tool_lifecycle_incomplete"
        responsible_cell = "roles.kernel"
        retry_boundary = "same_director_task_only"
        current_status = "CHAIN_INCOMPLETE"
    elif missing_required:
        root_cause_code = "control_plane.run_ledger.required_evidence_missing"
        responsible_cell = "control_plane.run_ledger"
        retry_boundary = "same_failed_verifier_only"
        current_status = "CONTROL_PLANE_FAIL"
    elif failed_required:
        root_cause_code = "qa.audit_verdict.required_evidence_failed"
        responsible_cell = "qa.audit_verdict"
        retry_boundary = "same_failed_verifier_only"
        current_status = "QA_FAILED"
        target_project_defect = True
    elif factory_projection.get("chain_completed") is not True:
        root_cause_code, responsible_cell, retry_boundary, current_status = _factory_stage_root_cause(failed_stages)

    provider_request_required = responsible_cell in {
        "orchestration.pm_planning",
        "chief_engineer.blueprint",
        "director.runtime",
        "roles.kernel",
    }
    evidence_gaps = []
    if provider_request_required and provider_ok == 0:
        evidence_gaps.append("final_provider_request_unavailable_for_role_or_tool_failure")

    return {
        "schema_version": "audit.exact-run-causal-report.v1",
        "workspace": workspace,
        "factory_run_id": factory_run_id,
        "project_id": project_id,
        "current_status": current_status,
        "root_cause_code": root_cause_code,
        "responsible_cell": responsible_cell,
        "retry_boundary": retry_boundary,
        "pm_ce_restart_allowed": retry_boundary in {"same_pm_stage", "same_ce_stage"},
        "target_project_defect": target_project_defect,
        "authority_contradiction_detected": contradiction,
        "historical_error_count": historical_error_count,
        "historical_counts": {
            "failed_gate_count": historical_failed_gate_count,
            "failed_task_boundary_count": historical_task_boundary_count,
            "audit_trail_event_count": int(audit_trail_total),
        },
        "evidence_gaps": evidence_gaps,
        "evidence_chain": evidence_chain,
    }


__all__ = ["build_exact_run_causal_report"]
