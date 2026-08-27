"""Pure exact-run causal classification for the audit diagnosis Cell."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    project_tool_lifecycle_failure_status,
    suspected_files_from_failure_evidence_payload,
)
from polaris.kernelone.platform_modules import attribute_residual


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _strings(value: object) -> list[str]:
    return [str(item).strip() for item in _sequence(value) if item is not None and str(item).strip()]


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _link(status: str, source: str, **details: object) -> dict[str, object]:
    return {"status": status, "source": source, "details": details}


def _candidate(
    *,
    code: str,
    owner: str,
    retry_boundary: str,
    current_status: str,
    reason: str,
    detected_by: str,
    evidence_refs: Sequence[str] = (),
    target_project_defect: bool = False,
    contradiction: bool = False,
) -> dict[str, object]:
    """Build one deterministic root-cause candidate.

    Candidates are ordered by authority precedence at the call site. Keeping
    every losing candidate makes the classifier explainable without allowing
    historical/downstream symptoms to replace the selected owner.
    """

    return {
        "root_cause_code": code,
        "responsible_cell": owner,
        "detected_by": detected_by,
        "retry_boundary": retry_boundary,
        "current_status": current_status,
        "reason": reason,
        "evidence_refs": list(dict.fromkeys(str(item) for item in evidence_refs if str(item).strip())),
        "target_project_defect": target_project_defect,
        "authority_contradiction_detected": contradiction,
    }


def _diagnosis_id(
    *,
    workspace: str,
    factory_run_id: str,
    selected: Mapping[str, object] | None,
    evidence_refs: Sequence[str],
    failed_task_ids: Sequence[str],
) -> str:
    """Return a stable 24-hex identity for one exact causal verdict."""

    material = {
        "workspace": workspace,
        "factory_run_id": factory_run_id,
        "selected": dict(selected or {}),
        "evidence_refs": sorted(set(evidence_refs)),
        "failed_task_ids": sorted(set(failed_task_ids)),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _next_action(
    *,
    selected: Mapping[str, object] | None,
    failed_task_ids: Sequence[str],
    failed_modalities: Sequence[str],
    suspected_files: Sequence[str],
    evidence_gaps: Sequence[str],
    repair_diagnosis: Mapping[str, object] | None = None,
) -> dict[str, object]:
    retry_boundary = str((selected or {}).get("retry_boundary") or "none")
    owner = str((selected or {}).get("responsible_cell") or "")
    preserve_pm = retry_boundary != "same_pm_stage"
    preserve_ce = retry_boundary not in {"same_pm_stage", "same_ce_stage"}
    preserve_director = retry_boundary in {
        "same_director_task_only",
        "same_director_task_repair_only",
        "same_contract_projection_only",
        "same_failed_verifier_only",
        "same_run_quality_gate_only",
        "same_run_control_plane_reconcile_only",
        "same_run_task_boundary_reproject_only",
        "collect_same_run_final_request_evidence_only",
    }
    verifier_local = retry_boundary in {
        "same_director_task_only",
        "same_director_task_repair_only",
        "same_contract_projection_only",
        "same_failed_verifier_only",
        "same_run_quality_gate_only",
    }
    repair_files = [
        *_strings((repair_diagnosis or {}).get("diagnostic_paths")),
        *_strings((repair_diagnosis or {}).get("changed_paths")),
    ]
    prohibited = ["modify_generated_project_outside_authorized_director_tools"]
    if preserve_pm:
        prohibited.append("restart_pm")
    if preserve_ce:
        prohibited.append("restart_chief_engineer")
    if preserve_director:
        prohibited.append("restart_completed_director_tasks")
    return {
        "action": retry_boundary,
        "owner_cell": owner,
        "failed_task_ids": list(dict.fromkeys(item for item in failed_task_ids if item)),
        "failed_verifier_modalities": (list(dict.fromkeys(failed_modalities)) if verifier_local else []),
        "suspected_files": (
            list(dict.fromkeys(item for item in [*suspected_files, *repair_files] if item)) if verifier_local else []
        ),
        "required_evidence": list(dict.fromkeys(evidence_gaps)),
        "repair": dict(repair_diagnosis or {}),
        "preserve": {
            "pm": preserve_pm,
            "chief_engineer": preserve_ce,
            "completed_director_artifacts": preserve_director,
        },
        "prohibited_actions": prohibited,
    }


def _required_evidence_links(*, root_cause_code: str, responsible_cell: str) -> tuple[str, ...]:
    """Return only evidence needed to prove the selected root cause.

    Downstream evidence is not required when an upstream boundary failed before
    those layers could run.
    """

    links = ["factory_terminal", "run_ledger"]
    if root_cause_code == "factory.pipeline.run_not_found":
        return ("factory_terminal",)
    if root_cause_code.startswith("context.engine.final_provider_request"):
        links.extend(("final_provider_request", "role_context_coverage"))
    elif root_cause_code == "chief_engineer.blueprint.delivery_depth_completion_contract_infeasible":
        links.append("chief_engineer_authority_feasibility")
    elif root_cause_code == "director.tasking.delivery_contract_scope_contradiction":
        links.extend(("final_provider_request", "contract_feasibility"))
    elif root_cause_code == "runtime.task_runtime.canonical_task_boundary_incomplete":
        links.extend(("task_boundary", "task_runtime"))
    elif responsible_cell == "roles.kernel":
        links.extend(("tool_lifecycle", "failure_evidence", "final_provider_request"))
    elif responsible_cell == "llm.provider_runtime" or root_cause_code.endswith(".stage_failed"):
        links.extend(("structured_failure_signals", "final_provider_request"))
    elif root_cause_code == "control_plane.run_ledger.required_evidence_missing":
        links.append("verifier")
    elif root_cause_code == "director.runtime.generated_project_verifier_failed":
        links.extend(("verifier", "qa_verdict"))
    elif root_cause_code in {
        "director.runtime.deterministic_repair_available",
        "director.runtime.repair_coverage_matched_but_unplannable",
        "director.runtime.repair_coverage_gap",
    }:
        links.extend(("verifier", "qa_verdict", "repair_coverage_plan"))
    elif responsible_cell == "control_plane.run_ledger":
        links.append("task_boundary")
    return tuple(dict.fromkeys(links))


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


def _normalized_role(value: object) -> str:
    role = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if "chief" in role or role == "ce":
        return "chief_engineer"
    if "director" in role:
        return "director"
    if role in {"pm", "project_manager"}:
        return "pm"
    if "qa" in role or "quality" in role:
        return "qa"
    return role


def _stage_role(stage: str) -> str:
    return {
        "pm_planning": "pm",
        "chief_engineer_review": "chief_engineer",
        "director_dispatch": "director",
        "quality_gate": "qa",
    }.get(stage, "")


def _expected_llm_roles(factory_projection: Mapping[str, Any]) -> tuple[str, ...]:
    stages = [
        *_strings(factory_projection.get("completed_stages")),
        *_strings(factory_projection.get("failed_stages")),
    ]
    roles: list[str] = []
    for stage in stages:
        role = _stage_role(stage)
        # QA can be fully deterministic. Audit a QA request when present, but
        # absence alone is not a context defect.
        if role and role != "qa" and role not in roles:
            roles.append(role)
    return tuple(roles)


def _role_context_coverage(
    *,
    expected_roles: Sequence[str],
    provider_request_audits: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    by_role: dict[str, dict[str, object]] = {}
    for role in expected_roles:
        rows = [item for item in provider_request_audits if _normalized_role(item.get("role")) == role]
        available = [item for item in rows if item.get("ok") is True]
        latest = available[-1] if available else {}
        latest_failed = bool(
            latest
            and (latest.get("evidence_coverage_pass") is False or latest.get("role_identity_ok") is False)
        )
        status = "missing" if not available else "failed" if latest_failed else "proven"
        by_role[role] = {
            "status": status,
            "snapshot_count": len(available),
            "context_snapshot_ref": str(latest.get("context_snapshot_ref") or ""),
            "coverage_pass": latest.get("evidence_coverage_pass"),
            "role_identity_ok": latest.get("role_identity_ok"),
            "missing_required_refs": _strings(latest.get("missing_required_refs")),
            "missing_required_tools": _strings(latest.get("missing_required_tools")),
            "final_request_token_estimate": int(latest.get("final_request_token_estimate") or 0),
            "context_window_tokens": int(latest.get("context_window_tokens") or 0),
            "context_window_utilization": latest.get("context_window_utilization"),
        }
    statuses = [str(item.get("status") or "") for item in by_role.values()]
    overall = "failed" if "failed" in statuses else "missing" if "missing" in statuses else "proven"
    return {
        "status": overall if expected_roles else "not_applicable",
        "expected_roles": list(expected_roles),
        "roles": by_role,
    }


def _stage_failure_signal(
    *,
    failed_stages: Sequence[str],
    signals: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    stage = str(failed_stages[0] if failed_stages else "")
    expected_role = _stage_role(stage)
    for signal in reversed(signals):
        signal_stage = str(signal.get("stage") or "").strip()
        signal_role = _normalized_role(signal.get("role"))
        if (signal_stage and signal_stage == stage) or (expected_role and signal_role == expected_role):
            return signal
    return None


def _owner_for_structured_signal(*, default_owner: str, signal: Mapping[str, Any] | None) -> str:
    code = str((signal or {}).get("error_code") or "").casefold()
    if any(token in code for token in ("provider_", "rate_limit", "rate-limited", "429", "model_provider")):
        return "llm.provider_runtime"
    if any(token in code for token in ("structured_output", "tool_schema", "tool_call", "tool_dispatch")):
        return "roles.kernel"
    return default_owner


_NON_TERMINAL_FACTORY_STATUSES = frozenset(
    {"accepted", "created", "in_progress", "pending", "processing", "queued", "running", "started"}
)
_CREATE_CAPABLE_TOOLS = frozenset({"apply_patch", "create_file", "write_file"})


def _contract_scope_contradiction(
    provider_request_audits: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Return proof that a failed file-count gate cannot be satisfied.

    A contradiction requires one final Director request to contain a concrete
    file-count deficit and an offered tool surface with no create-capable tool.
    Merely missing provider evidence never creates this verdict.
    """

    candidates: list[dict[str, object]] = []
    for audit in provider_request_audits:
        if audit.get("ok") is not True or str(audit.get("role") or "").strip().lower() != "director":
            continue
        deficits = [
            dict(item)
            for item in _sequence(audit.get("file_deficits"))
            if isinstance(item, Mapping) and int(item.get("actual") or 0) < int(item.get("required") or 0)
        ]
        offered_tools = set(_strings(audit.get("tool_names")))
        if not deficits or not offered_tools or offered_tools.intersection(_CREATE_CAPABLE_TOOLS):
            continue
        target_scope = _mapping(audit.get("target_scope_summary"))
        candidates.append(
            {
                "detected": True,
                "context_snapshot_ref": str(audit.get("context_snapshot_ref") or ""),
                "file_deficits": deficits,
                "offered_tools": sorted(offered_tools),
                "allowed_write_path_count": int(target_scope.get("allowed_write_path_count") or 0),
                "reason": "file_count_deficit_without_create_capable_tool",
            }
        )
    if candidates:
        return max(
            candidates,
            key=lambda item: (
                _integer(item.get("allowed_write_path_count")) > 0,
                len(_sequence(item.get("file_deficits"))),
            ),
        )
    return {"detected": False}


def _repair_diagnosis(repair_evidence: Mapping[str, Any] | None) -> dict[str, object]:
    """Project verifier residuals into an immediately actionable repair route.

    Coverage is discovery only.  A rule is executable for this exact workspace
    only when the read-only plan probe produced a changed patch.  This prevents
    the historical failure mode where ``known_rule_matched`` caused repeated
    no-op repair rounds.
    """

    evidence = _mapping(repair_evidence)
    residual_errors = _strings(evidence.get("residual_errors"))
    if not residual_errors:
        residual_errors = _strings(evidence.get("artifact_quality_errors"))
    coverage = _mapping(evidence.get("director_runtime_repair_coverage"))
    probe = _mapping(evidence.get("plan_probe_preaudit"))
    coverage_report = _mapping(probe.get("coverage_report")) or coverage
    probe_status = str(probe.get("status") or "").strip()
    plannable_tools = _strings(probe.get("plannable_source_tools"))
    unplannable_tools = _strings(probe.get("covered_unplannable_source_tools"))
    gap_count = _integer(probe.get("coverage_gap_count"))
    if not gap_count:
        gap_count = _integer(coverage_report.get("uncovered_diagnostic_count"))
    covered_count = _integer(coverage_report.get("covered_diagnostic_count"))
    total_count = _integer(coverage_report.get("total_diagnostics")) or len(residual_errors)
    changed_paths: list[str] = []
    diagnostic_paths: list[str] = []
    for raw_item in _sequence(coverage_report.get("items")):
        item = _mapping(raw_item)
        diagnostic = _mapping(item.get("diagnostic"))
        path = str(diagnostic.get("path") or "").strip()
        if path:
            diagnostic_paths.append(path)
    for raw_item in _sequence(probe.get("items")):
        item = _mapping(raw_item)
        if str(item.get("status") or "") != "covered_plannable":
            continue
        changed_paths.extend(_strings(item.get("changed_paths")))

    if plannable_tools or probe_status == "covered_plannable":
        status = "deterministic_repair_available"
        retry_boundary = "same_director_task_repair_only"
    elif probe_status == "coverage_matched_but_unplannable" or (covered_count and not gap_count):
        status = "coverage_matched_but_unplannable"
        retry_boundary = "same_director_task_repair_only"
    elif gap_count or probe_status == "coverage_gap":
        status = "coverage_gap"
        retry_boundary = "same_director_task_repair_only"
    elif residual_errors:
        status = "verifier_failed_repair_route_unknown"
        retry_boundary = "same_failed_verifier_only"
    else:
        status = "not_available"
        retry_boundary = "none"
    return {
        "status": status,
        "retry_boundary": retry_boundary,
        "evidence_source": str(evidence.get("evidence_source") or ""),
        "full_evidence_ref": str(evidence.get("full_evidence_ref") or ""),
        "residual_error_count": len(residual_errors),
        "residual_errors": residual_errors[:20],
        "coverage": {
            "total_diagnostics": total_count,
            "covered_diagnostic_count": covered_count,
            "uncovered_diagnostic_count": gap_count,
        },
        "plan_probe_status": probe_status,
        "plannable_source_tools": plannable_tools,
        "covered_unplannable_source_tools": unplannable_tools,
        "diagnostic_paths": list(dict.fromkeys(diagnostic_paths)),
        "changed_paths": list(dict.fromkeys(changed_paths)),
        "coverage_is_not_planning": True,
        "pm_ce_restart_allowed": False,
    }


def build_exact_run_causal_report(
    *,
    workspace: str,
    factory_run_id: str,
    project_id: str,
    factory_projection: Mapping[str, Any],
    ledger_projection: Mapping[str, Any],
    terminal_task_runtime_projection: Mapping[str, Any] | None,
    provider_request_audits: Sequence[Mapping[str, Any]] = (),
    chief_engineer_authority_feasibility: Mapping[str, Any] | None = None,
    structured_failure_signals: Sequence[Mapping[str, Any]] = (),
    audit_trail_total: int = 0,
    repair_evidence: Mapping[str, Any] | None = None,
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
    latest_task_boundary = _mapping(task_boundary.get("latest"))
    task_boundary_failure_class = str(latest_task_boundary.get("failure_class") or "").strip().upper()
    missing_entrypoint_targets = _strings(latest_task_boundary.get("missing_entrypoint_targets"))
    task_boundary_failed_task_ids = list(
        dict.fromkeys(
            task_id
            for row in _sequence(task_boundary.get("failed"))
            if isinstance(row, Mapping)
            for task_id in [str(row.get("task_id") or row.get("task_key") or "").strip()]
            if task_id
        )
    )
    latest_boundary_task_id = str(
        latest_task_boundary.get("task_id") or latest_task_boundary.get("task_key") or ""
    ).strip()
    if latest_boundary_task_id and latest_boundary_task_id not in task_boundary_failed_task_ids:
        task_boundary_failed_task_ids.append(latest_boundary_task_id)
    tool_lifecycle = _mapping(ledger_projection.get("tool_lifecycle"))
    tool_lifecycle_failure = project_tool_lifecycle_failure_status(tool_lifecycle)
    tool_lifecycle_failed = tool_lifecycle_failure.get("failed") is True or (
        not str(tool_lifecycle_failure.get("status") or "").strip()
        and bool(tool_lifecycle)
        and tool_lifecycle.get("ok") is not True
    )
    evidence_modalities = _mapping(ledger_projection.get("evidence_modalities"))
    tool_failure_rows = [
        dict(item) for item in _sequence(tool_lifecycle.get("failure_evidence")) if isinstance(item, Mapping)
    ]
    failure_classes = _strings([item.get("failure_class") for item in tool_failure_rows])
    suspected_files = suspected_files_from_failure_evidence_payload(
        {"items": tool_failure_rows},
        limit=20,
    )
    failed_stages = _strings(factory_projection.get("failed_stages"))
    structured_signals = [dict(item) for item in structured_failure_signals if isinstance(item, Mapping)]
    stage_signal = _stage_failure_signal(failed_stages=failed_stages, signals=structured_signals)
    historical_failed_gate_count = int(run_projection.get("historical_failed_gate_count") or 0)
    historical_task_boundary_count = int(task_boundary.get("historical_failed_count") or 0)
    historical_error_count = historical_failed_gate_count + historical_task_boundary_count

    failed_required = _strings(evidence_policy.get("failed_required_modalities"))
    missing_required = _strings(evidence_policy.get("missing_required_modalities"))
    repair_diagnosis = _repair_diagnosis(repair_evidence)
    failed_control_plane = _strings(ledger_projection.get("failed_control_plane_events"))
    terminal_projection = _mapping(terminal_task_runtime_projection)
    terminal_rows = _sequence(_mapping(terminal_projection.get("projection")).get("rows"))
    terminal_failed_rows = [
        str(_mapping(row).get("external_task_id") or _mapping(row).get("task_id") or "")
        for row in terminal_rows
        if str(_mapping(row).get("status") or "").strip().lower() in {"failed", "blocked", "cancelled"}
    ]

    provider_ok = sum(1 for item in provider_request_audits if item.get("ok") is True)
    provider_refs = _strings(
        [item.get("context_snapshot_ref") for item in provider_request_audits if item.get("ok") is True]
    )
    provider_roles = sorted(
        set(_strings([item.get("role") for item in provider_request_audits if item.get("ok") is True]))
    )
    provider_tools = sorted(
        {
            tool
            for item in provider_request_audits
            if item.get("ok") is True
            for tool in _strings(item.get("tool_names"))
        }
    )
    expected_llm_roles = _expected_llm_roles(factory_projection)
    role_context_coverage = _role_context_coverage(
        expected_roles=expected_llm_roles,
        provider_request_audits=provider_request_audits,
    )
    contract_contradiction = _contract_scope_contradiction(provider_request_audits)
    ce_authority_feasibility = _mapping(chief_engineer_authority_feasibility)
    ce_authority_infeasible = (
        ce_authority_feasibility.get("available") is True and ce_authority_feasibility.get("ok") is False
    )
    factory_status = str(factory_projection.get("status") or "").strip().lower()
    factory_terminal = factory_status not in _NON_TERMINAL_FACTORY_STATUSES
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
            failed_task_ids=task_boundary_failed_task_ids,
            failure_class=task_boundary_failure_class,
            missing_entrypoint_targets=missing_entrypoint_targets,
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
            authoritative_failed=tool_lifecycle_failed,
            authoritative_status=str(tool_lifecycle_failure.get("status") or ""),
            effect_receipt_count=int(tool_lifecycle.get("effect_receipt_count") or 0),
            failed_count=int(tool_lifecycle.get("failed_count") or 0),
        ),
        "failure_evidence": _link(
            (
                "proven"
                if tool_failure_rows
                else "missing"
                if tool_lifecycle_failed
                else "not_applicable"
            ),
            "control_plane.run_ledger",
            row_count=len(tool_failure_rows),
            failure_classes=failure_classes,
            suspected_files=suspected_files,
        ),
        "structured_failure_signals": _link(
            (
                "proven"
                if structured_signals
                else "missing"
                if factory_terminal and factory_projection.get("chain_completed") is not True
                else "not_applicable"
            ),
            "audit.diagnosis",
            signal_count=len(structured_signals),
            selected_stage_signal=dict(stage_signal or {}),
            signals=structured_signals,
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
        "repair_coverage_plan": _link(
            (
                "proven"
                if repair_diagnosis.get("status") != "not_available"
                else "missing"
                if failed_required
                else "not_applicable"
            ),
            "director.runtime",
            diagnosis=repair_diagnosis,
        ),
        "final_provider_request": _link(
            "proven" if provider_ok else "missing",
            "context.engine",
            audited_count=len(provider_request_audits),
            available_count=provider_ok,
            context_snapshot_refs=provider_refs,
            roles=provider_roles,
            offered_tools=provider_tools,
        ),
        "role_context_coverage": _link(
            str(role_context_coverage.get("status") or "not_applicable"),
            "context.engine",
            expected_roles=role_context_coverage.get("expected_roles") or [],
            roles=role_context_coverage.get("roles") or {},
        ),
        "chief_engineer_authority_feasibility": _link(
            (
                "contradictory"
                if ce_authority_infeasible
                else "proven"
                if ce_authority_feasibility.get("available") is True
                else "missing"
            ),
            "chief_engineer.blueprint",
            **ce_authority_feasibility,
        ),
        "contract_feasibility": _link(
            "contradictory" if contract_contradiction.get("detected") is True else "not_applicable",
            "audit.diagnosis",
            **contract_contradiction,
        ),
    }

    candidates: list[dict[str, object]] = []
    if factory_projection.get("available") is not True:
        candidates.append(
            _candidate(
                code="factory.pipeline.run_not_found",
                owner="factory.pipeline",
                retry_boundary="inspect_instance_workspace_binding",
                current_status="CONTROL_PLANE_FAIL",
                reason="Exact Factory run is absent from the workspace-bound projection.",
                detected_by="factory.pipeline",
            )
        )
    elif factory_terminal:
        if any(issue.startswith("gate_revision_chain_fork_or_stale:") for issue in revision_issues):
            candidates.append(
                _candidate(
                    code="control_plane.run_ledger.gate_revision_fork_after_runtime_reentry",
                    owner="control_plane.run_ledger",
                    retry_boundary="same_run_quality_gate_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="Canonical gate revision chain contains a fork or stale branch after runtime re-entry.",
                    detected_by="control_plane.run_ledger",
                    contradiction=bool(task_boundary.get("ok"))
                    and bool(_mapping(evidence_modalities.get("qa")).get("ok")),
                )
            )
        if task_boundary_failure_class == "MISSING_ENTRYPOINT_TARGET":
            candidates.append(
                _candidate(
                    code="control_plane.run_ledger.task_boundary_missing_entrypoint_target",
                    owner="control_plane.run_ledger",
                    retry_boundary="same_run_task_boundary_reproject_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="TaskBoundary classified one or more manifest entrypoint targets as missing.",
                    detected_by="control_plane.run_ledger.task_boundary",
                    evidence_refs=missing_entrypoint_targets,
                )
            )
        if failed_control_plane:
            candidates.append(
                _candidate(
                    code="control_plane.run_ledger.failed_control_plane_event",
                    owner="control_plane.run_ledger",
                    retry_boundary="same_run_control_plane_reconcile_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="A canonical control-plane event failed for this exact run.",
                    detected_by="control_plane.run_ledger",
                    evidence_refs=failed_control_plane,
                )
            )
        if ce_authority_infeasible:
            candidates.append(
                _candidate(
                    code="chief_engineer.blueprint.delivery_depth_completion_contract_infeasible",
                    owner="chief_engineer.blueprint",
                    retry_boundary="same_ce_stage",
                    current_status="CHAIN_INCOMPLETE",
                    reason="Immutable CE artifact obligations cannot satisfy validated PM delivery minimums.",
                    detected_by="audit.diagnosis",
                    contradiction=True,
                )
            )
        if task_boundary and task_boundary.get("ok") is not True:
            candidates.append(
                _candidate(
                    code="runtime.task_runtime.canonical_task_boundary_incomplete",
                    owner="runtime.task_runtime",
                    retry_boundary="same_director_task_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="Canonical task boundary did not converge for one or more Director tasks.",
                    detected_by="control_plane.run_ledger",
                    evidence_refs=terminal_failed_rows,
                )
            )
        if tool_lifecycle_failed:
            lifecycle_failure_class = str(tool_lifecycle_failure.get("failure_class") or "").strip()
            candidates.append(
                _candidate(
                    code="roles.kernel.tool_lifecycle_incomplete",
                    owner="roles.kernel",
                    retry_boundary="same_director_task_only",
                    current_status="CHAIN_INCOMPLETE",
                    reason=(
                        "Tool lifecycle did not close with authoritative result/effect receipts"
                        + (f" ({lifecycle_failure_class})." if lifecycle_failure_class else ".")
                    ),
                    detected_by="control_plane.run_ledger",
                    evidence_refs=[lifecycle_failure_class] if lifecycle_failure_class else failure_classes,
                )
            )
        if missing_required:
            candidates.append(
                _candidate(
                    code="control_plane.run_ledger.required_evidence_missing",
                    owner="control_plane.run_ledger",
                    retry_boundary="same_failed_verifier_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="Required verifier modality has no authoritative receipt.",
                    detected_by="control_plane.run_ledger",
                    evidence_refs=missing_required,
                )
            )
        if contract_contradiction.get("detected") is True:
            candidates.append(
                _candidate(
                    code="director.tasking.delivery_contract_scope_contradiction",
                    owner="director.tasking",
                    retry_boundary="same_contract_projection_only",
                    current_status="CONTROL_PLANE_FAIL",
                    reason="Failed delivery minimum cannot be satisfied by the authorized scope and offered tools.",
                    detected_by="audit.diagnosis",
                    evidence_refs=_strings([contract_contradiction.get("context_snapshot_ref")]),
                    contradiction=True,
                )
            )
        if failed_required:
            repair_status = str(repair_diagnosis.get("status") or "")
            repair_code = "director.runtime.generated_project_verifier_failed"
            repair_reason = "Authoritative verifier receipt exists and reports a generated-project failure."
            repair_retry = "same_failed_verifier_only"
            detected_by = "qa.audit_verdict"
            if repair_status == "deterministic_repair_available":
                repair_code = "director.runtime.deterministic_repair_available"
                repair_reason = "Verifier failed and the public repair plan probe produced a concrete changed patch."
                repair_retry = "same_director_task_repair_only"
                detected_by = "director.runtime.repair_kernel"
            elif repair_status == "coverage_matched_but_unplannable":
                repair_code = "director.runtime.repair_coverage_matched_but_unplannable"
                repair_reason = (
                    "Repair coverage matched, but no source tool produced a concrete changed patch; "
                    "repeating deterministic repair would be a no-op."
                )
                repair_retry = "same_director_task_repair_only"
                detected_by = "director.runtime.repair_kernel"
            elif repair_status == "coverage_gap":
                repair_code = "director.runtime.repair_coverage_gap"
                repair_reason = (
                    "Verifier diagnostic has no executable runtime repair coverage and requires a "
                    "same-task repair path or a governed generic rule."
                )
                repair_retry = "same_director_task_repair_only"
                detected_by = "director.runtime.repair_kernel"
            candidates.append(
                _candidate(
                    code=repair_code,
                    owner="director.runtime",
                    retry_boundary=repair_retry,
                    current_status="QA_FAILED",
                    reason=repair_reason,
                    detected_by=detected_by,
                    evidence_refs=[
                        *failed_required,
                        *list(_strings(repair_diagnosis.get("plannable_source_tools"))),
                        *list(_strings(repair_diagnosis.get("covered_unplannable_source_tools"))),
                    ],
                    target_project_defect=True,
                )
            )
        if factory_projection.get("chain_completed") is not True:
            stage_code, stage_owner, stage_retry, stage_status = _factory_stage_root_cause(failed_stages)
            if stage_signal is not None:
                signal_code = str(stage_signal.get("error_code") or "").strip()
                if signal_code:
                    stage_code = signal_code
                stage_owner = _owner_for_structured_signal(default_owner=stage_owner, signal=stage_signal)
            candidates.append(
                _candidate(
                    code=stage_code,
                    owner=stage_owner,
                    retry_boundary=stage_retry,
                    current_status=stage_status,
                    reason=(
                        "Structured exact-run failure signal identifies the incomplete stage."
                        if stage_signal is not None
                        else "Factory terminal projection identifies the first incomplete stage."
                    ),
                    detected_by="factory.pipeline",
                    evidence_refs=_strings(
                        [
                            (stage_signal or {}).get("context_snapshot_ref"),
                            (stage_signal or {}).get("task_id"),
                        ]
                    ),
                )
            )

    selected = candidates[0] if candidates else None
    selected_code = str((selected or {}).get("root_cause_code") or "")
    selected_owner = str((selected or {}).get("responsible_cell") or "")
    provider_request_required = selected_code.endswith(".stage_failed") or selected_owner in {
        "roles.kernel",
        "llm.provider_runtime",
    }
    stage_signal_role = _normalized_role((stage_signal or {}).get("role"))
    required_provider_role = {
        "orchestration.pm_planning": "pm",
        "chief_engineer.blueprint": "chief_engineer",
        "director.runtime": "director",
    }.get(
        selected_owner,
        stage_signal_role or (_stage_role(failed_stages[0]) if failed_stages else ""),
    )
    required_provider_request_available = bool(required_provider_role) and required_provider_role in provider_roles
    final_provider_details = _mapping(evidence_chain["final_provider_request"].get("details"))
    evidence_chain["final_provider_request"]["details"] = {
        **final_provider_details,
        "required_role": required_provider_role,
        "required_role_available": required_provider_request_available,
    }
    evidence_gaps: list[str] = []
    if provider_request_required and not required_provider_request_available:
        evidence_gaps.append("final_provider_request_unavailable_for_role_or_tool_failure")

    required_role_row = _mapping(_mapping(role_context_coverage.get("roles")).get(required_provider_role))
    if provider_request_required and required_role_row.get("status") == "failed" and selected is not None:
        evidence_gaps.append(f"final_provider_request_context_invalid:{required_provider_role}")
        selected = _candidate(
            code="context.engine.final_provider_request_context_invalid",
            owner="context.engine",
            retry_boundary=str(selected.get("retry_boundary") or "same_factory_stage_only"),
            current_status="CONTROL_PLANE_FAIL",
            reason="Final provider request has role identity, required evidence, or required tool coverage defects.",
            detected_by="context.engine",
            evidence_refs=_strings([required_role_row.get("context_snapshot_ref")]),
        )
        candidates.insert(0, selected)

    # Role/tool attribution without the final physical provider request is a
    # guess. Promote the evidence break itself and retain the stage symptom as
    # a secondary candidate for later reclassification once evidence exists.
    if "final_provider_request_unavailable_for_role_or_tool_failure" in evidence_gaps and selected is not None:
        selected = _candidate(
            code="context.engine.final_provider_request_evidence_missing",
            owner="context.engine",
            retry_boundary="collect_same_run_final_request_evidence_only",
            current_status="CONTROL_PLANE_FAIL",
            reason="Role/tool failure cannot be attributed without its readable final provider request.",
            detected_by="audit.diagnosis",
            evidence_refs=evidence_gaps,
        )
        candidates.insert(0, selected)

    current_status = (
        str(selected.get("current_status") or "CONTROL_PLANE_FAIL")
        if selected is not None
        else "DELIVERY_VERIFIED"
        if factory_terminal
        else "RUNNING"
    )
    root_cause_code = str((selected or {}).get("root_cause_code") or "")
    responsible_cell = str((selected or {}).get("responsible_cell") or "")
    retry_boundary = str((selected or {}).get("retry_boundary") or "none")
    target_project_defect = bool((selected or {}).get("target_project_defect"))
    contradiction = bool((selected or {}).get("authority_contradiction_detected"))

    required_links = _required_evidence_links(
        root_cause_code=root_cause_code,
        responsible_cell=responsible_cell,
    )
    missing_links = [
        name for name in required_links if str(_mapping(evidence_chain.get(name)).get("status") or "") == "missing"
    ]
    available_links = [name for name in required_links if name not in missing_links]
    next_action_failed_task_ids = terminal_failed_rows
    if retry_boundary == "same_run_task_boundary_reproject_only":
        next_action_failed_task_ids = task_boundary_failed_task_ids
    diagnosis_id = _diagnosis_id(
        workspace=workspace,
        factory_run_id=factory_run_id,
        selected=selected,
        evidence_refs=provider_refs,
        failed_task_ids=next_action_failed_task_ids,
    )
    next_action = _next_action(
        selected=selected,
        failed_task_ids=next_action_failed_task_ids,
        failed_modalities=failed_required,
        suspected_files=suspected_files,
        evidence_gaps=evidence_gaps,
        repair_diagnosis=repair_diagnosis,
    )
    module_attribution = None
    if root_cause_code:
        module_attribution = attribute_residual(
            root_cause_signature=root_cause_code,
            failure_category=current_status,
            failure_reasons=[str((selected or {}).get("reason") or "")],
            error_code=root_cause_code,
            director_detail=" ".join(terminal_failed_rows),
            real_run_gate_ok=False if failed_required else None,
            chain_ok=bool(factory_projection.get("chain_completed")),
            evidence_notes=evidence_gaps,
        ).to_dict()

    return {
        "schema_version": "audit.exact-run-causal-report.v1",
        "diagnosis_id": diagnosis_id,
        "workspace": workspace,
        "factory_run_id": factory_run_id,
        "project_id": project_id,
        "current_status": current_status,
        "terminal": factory_terminal,
        "root_cause_code": root_cause_code,
        "responsible_cell": responsible_cell,
        "retry_boundary": retry_boundary,
        "pm_ce_restart_allowed": retry_boundary in {"same_pm_stage", "same_ce_stage"},
        "pm_restart_allowed": retry_boundary == "same_pm_stage",
        "ce_restart_allowed": retry_boundary == "same_ce_stage",
        "target_project_defect": target_project_defect,
        "authority_contradiction_detected": contradiction,
        "historical_error_count": historical_error_count,
        "historical_counts": {
            "failed_gate_count": historical_failed_gate_count,
            "failed_task_boundary_count": historical_task_boundary_count,
            "audit_trail_event_count": int(audit_trail_total),
        },
        "evidence_gaps": evidence_gaps,
        "evidence_completeness": {
            "complete": not missing_links and not evidence_gaps,
            "required_links": list(required_links),
            "available_links": available_links,
            "missing_links": missing_links,
        },
        "root_cause_candidates": [{"priority": index + 1, **candidate} for index, candidate in enumerate(candidates)],
        "next_action": next_action,
        "platform_residual_attribution": module_attribution,
        "repair_diagnosis": repair_diagnosis,
        "evidence_chain": evidence_chain,
    }


__all__ = ["build_exact_run_causal_report"]
