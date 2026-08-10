"""Resident AGI decision gates and capability selection helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from polaris.cells.audit.evidence.public.service import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
    append_evidence_event,
)
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    RunLedgerAppendResultV1,
    append_run_ledger_event,
)
from polaris.cells.director.runtime.public import RepairAdvisoryV1
from polaris.cells.resident.autonomy.public.contracts import (
    ResidentAgiDecisionOutputV1,
    RunResidentAgiDecisionTurnCommandV1,
)

from ._agi_participation import (
    _resident_agi_repair_advisory_decision_relevant,
)


def _resident_decision_verdict(agi_verdict: str, *, runtime_success: bool) -> str:
    normalized = str(agi_verdict or "").strip().lower()
    if not runtime_success and normalized in {"block", "escalate", "request_evidence"}:
        return "blocked"
    if not runtime_success:
        return "failure"
    if normalized == "continue":
        return "success"
    if normalized in {"block", "escalate", "request_evidence"}:
        return "blocked"
    return "unknown"


def _resident_agi_decision_summary(
    *,
    objective: str,
    agi_verdict: str,
    rationale: str,
    error: str,
) -> str:
    verdict = str(agi_verdict or "").strip() or "unknown"
    detail = str(rationale or error or objective or "").strip()
    if len(detail) > 180:
        detail = f"{detail[:177]}..."
    return f"Resident AGI decision [{verdict}]: {detail}" if detail else f"Resident AGI decision [{verdict}]"


def _resident_agi_policy_decision(
    *,
    agi_verdict: str,
    resident_verdict: str,
    runtime_success: bool,
    downstream_allowed: bool,
) -> str:
    """Map a Resident AGI judgement into a control-plane policy decision."""

    normalized_agi = str(agi_verdict or "").strip().lower()
    normalized_resident = str(resident_verdict or "").strip().lower()
    if not runtime_success:
        return "block"
    if normalized_agi == "continue" and downstream_allowed and normalized_resident == "success":
        return "allow"
    if normalized_agi in {"request_evidence", "escalate", "block"}:
        return normalized_agi
    if normalized_resident == "success" and downstream_allowed:
        return "allow"
    return "block"


def _resident_agi_control_run_id(
    command: RunResidentAgiDecisionTurnCommandV1,
    recorded: dict[str, Any],
) -> str:
    decision_id = str(recorded.get("decision_id") or "").strip()
    return (
        str(command.run_id or "").strip()
        or str(recorded.get("run_id") or "").strip()
        or (f"resident-agi-{decision_id}" if decision_id else "")
        or f"resident-agi-{uuid4().hex[:12]}"
    )


def _resident_agi_control_gate_summary(
    *,
    policy_decision: str,
    agi_verdict: str,
    error: str,
) -> str:
    if policy_decision == "allow":
        return "Resident AGI permitted downstream continuation."
    if policy_decision == "request_evidence":
        return "Resident AGI blocked downstream work until required evidence is available."
    if policy_decision == "escalate":
        return "Resident AGI escalated the decision before downstream work can continue."
    if error:
        return f"Resident AGI blocked downstream work: {error}"
    return f"Resident AGI blocked downstream work with verdict `{agi_verdict or 'unknown'}`."


def _append_resident_agi_control_plane_gate(
    *,
    command: RunResidentAgiDecisionTurnCommandV1,
    recorded: dict[str, Any],
    audit_pack: dict[str, Any] | None,
    selected_decision_capability: dict[str, Any],
    decision_preflight: dict[str, Any],
    output_contract_gate: dict[str, Any],
    runtime_contract_gate: dict[str, Any],
    agi_verdict: str,
    resident_verdict: str,
    downstream_allowed: bool,
    runtime_success: bool,
    next_action: str,
    rationale: str,
    risks: list[Any],
    error: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    """Persist Resident AGI judgement as a platform control-plane gate."""

    run_id = _resident_agi_control_run_id(command, recorded)
    decision_id = str(recorded.get("decision_id") or "").strip()
    policy_decision = _resident_agi_policy_decision(
        agi_verdict=agi_verdict,
        resident_verdict=resident_verdict,
        runtime_success=runtime_success,
        downstream_allowed=downstream_allowed,
    )
    control_downstream_allowed = policy_decision == "allow"
    gate_summary = _resident_agi_control_gate_summary(
        policy_decision=policy_decision,
        agi_verdict=agi_verdict,
        error=error,
    )
    event: dict[str, Any] = {
        "schema_version": "resident.agi_control_gate.v1",
        "event_type": "gate_evaluated",
        "decision_event_type": "resident_agi_decision_evaluated",
        "source": "resident.autonomy.public.run_resident_agi_decision_turn",
        "run_id": run_id,
        "task_id": command.task_id,
        "goal_id": command.goal_id,
        "stage": command.decision_type,
        "decision_id": decision_id,
        "decision_type": command.decision_type,
        "actor": "resident_agi",
        "gate": {
            "name": "resident_agi_decision",
            "ok": control_downstream_allowed,
            "summary": gate_summary,
            "policy_decision": policy_decision,
            "downstream_allowed": control_downstream_allowed,
            "runtime_success": runtime_success,
        },
        "resident_agi_decision": {
            "agi_verdict": agi_verdict,
            "resident_verdict": resident_verdict,
            "policy_decision": policy_decision,
            "downstream_allowed": control_downstream_allowed,
            "next_action": next_action,
            "rationale": rationale,
            "risks": list(risks),
            "error": error,
            "decision_capability_id": str(selected_decision_capability.get("decision_id") or ""),
        },
        "contract_gates": {
            "decision_preflight": decision_preflight,
            "output_contract_gate": output_contract_gate,
            "runtime_contract_gate": runtime_contract_gate,
        },
        "job_token": {
            "token_id": f"resident-agi-{decision_id or run_id}",
            "run_id": run_id,
            "task_id": command.task_id,
            "goal_id": command.goal_id,
            "capability_audit": {"ok": True, "issues": []},
            "gate_policy": {
                "enabled_evidence_modalities": [
                    "resident_decision_trace",
                    "resident_agi_audit_pack",
                ],
                "required_evidence_modalities": [],
            },
        },
        "physical_evidence": {
            "modalities": {
                "resident_decision_trace": {
                    "present": bool(decision_id),
                    "ok": bool(decision_id),
                    "detail": "Resident decision trace entry recorded before control-plane gate append.",
                    "metadata": {"decision_id": decision_id},
                },
                "resident_agi_audit_pack": {
                    "present": audit_pack is not None,
                    "ok": audit_pack is not None,
                    "detail": "Resident AGI audit pack was injected into the decision turn.",
                    "metadata": {"schema_version": (audit_pack or {}).get("schema_version", "")},
                },
            },
            "decision_trace": {
                "decision_id": decision_id,
                "evidence_refs": list(evidence_refs),
            },
        },
    }
    evidence_result: EvidenceAppendedEventV1 = append_evidence_event(
        AppendEvidenceEventCommandV1(
            kind="resident_agi.decision_gate",
            workspace=command.workspace,
            payload={
                "run_id": run_id,
                "decision_id": decision_id,
                "event": event,
            },
            metadata={
                "source": "resident.autonomy.public.run_resident_agi_decision_turn",
                "run_id": run_id,
                "task_id": command.task_id,
                "goal_id": command.goal_id,
                "decision_id": decision_id,
                "decision_type": command.decision_type,
                "policy_decision": policy_decision,
            },
        )
    )
    event["physical_evidence"]["decision_trace"]["evidence_event_ref"] = evidence_result.receipt_path
    ledger_result: RunLedgerAppendResultV1 = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=command.workspace,
            run_id=run_id,
            event=event,
        )
    )
    return {
        "schema_version": "resident.agi_control_gate_receipt.v1",
        "persistence_ok": True,
        "run_id": run_id,
        "decision_id": decision_id,
        "policy_decision": policy_decision,
        "downstream_allowed": control_downstream_allowed,
        "gate_ok": control_downstream_allowed,
        "ledger_receipt": dict(ledger_result.receipt),
        "evidence_receipt_path": evidence_result.receipt_path,
    }


def _resident_agi_runtime_contract_gate(
    *,
    role_result: dict[str, Any],
    role_metadata: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    decision_profile: dict[str, Any],
    decision_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify that a successful Resident AGI turn returned RoleRuntime receipt evidence."""

    if hard_rule_gate.get("status") == "block":
        return {
            "schema_version": "resident.agi_runtime_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before runtime execution.",
            "checks": [],
            "failed_check_ids": [],
        }
    preflight = decision_preflight if isinstance(decision_preflight, dict) else {}
    if preflight and not bool(preflight.get("passed")):
        return {
            "schema_version": "resident.agi_runtime_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked by decision evidence preflight.",
            "checks": [],
            "failed_check_ids": [],
        }

    role_turn_allowed = bool(decision_profile.get("role_turn_allowed", True))
    checks = [
        {
            "check_id": "role_result.success",
            "passed": bool(role_result.get("success")),
            "detail": "Role adapter must report runtime success before the decision can be accepted.",
        },
        {
            "check_id": "metadata.role_runtime_entrypoint",
            "passed": role_metadata.get("role_runtime_entrypoint") == "roles.runtime.execute_role_session",
            "detail": "Resident AGI must return the canonical RoleRuntime entrypoint receipt.",
        },
        {
            "check_id": "metadata.context_os_expected",
            "passed": role_metadata.get("context_os_expected") is True,
            "detail": "Resident AGI runtime receipt must preserve ContextOS expectation evidence.",
        },
        {
            "check_id": "metadata.runtime_fallback_used",
            "passed": role_metadata.get("runtime_fallback_used") is False,
            "detail": "Resident AGI runtime cannot fall back to a sidecar or direct LLM path.",
        },
        {
            "check_id": "metadata.fallback_policy",
            "passed": role_metadata.get("fallback_policy") == "fail_closed",
            "detail": "Resident AGI runtime fallback policy must be fail-closed.",
        },
        {
            "check_id": "decision_profile.role_turn_allowed",
            "passed": role_turn_allowed,
            "detail": "Decision profile must allow a Resident AGI role turn.",
        },
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "resident.agi_runtime_contract_gate.v1",
        "status": "pass" if not failed else "fail",
        "passed": not failed,
        "required": True,
        "reason": "RoleRuntime receipt evidence accepted."
        if not failed
        else "RoleRuntime receipt evidence is incomplete.",
        "checks": checks,
        "failed_check_ids": [str(item["check_id"]) for item in failed],
    }


def _resident_agi_decision_sequence(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(str(item or "").strip() for item in raw if str(item or "").strip())


def _resident_agi_output_contract_gate(
    *,
    decision: dict[str, Any],
    selected_decision_capability: dict[str, Any],
    hard_rule_gate: dict[str, Any],
    evidence_gate: dict[str, Any],
    decision_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the Resident AGI model output before accepting its decision."""

    if hard_rule_gate.get("status") == "block":
        return {
            "schema_version": "resident.agi_output_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before model output validation.",
            "checks": [],
            "failed_check_ids": [],
            "normalized_decision": {},
        }
    preflight = decision_preflight if isinstance(decision_preflight, dict) else {}
    if preflight and not bool(preflight.get("passed")):
        return {
            "schema_version": "resident.agi_output_contract_gate.v1",
            "status": "preflight_blocked",
            "passed": False,
            "required": False,
            "reason": "Resident AGI role turn was blocked before model output validation.",
            "checks": [],
            "failed_check_ids": [],
            "normalized_decision": {},
        }

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"check_id": check_id, "passed": passed, "detail": detail})

    selected_decision_id = str(selected_decision_capability.get("decision_id") or "").strip()
    add_check(
        "selected_decision_capability.present",
        bool(selected_decision_id),
        "A Resident AGI output must be validated against a selected decision capability.",
    )
    output: ResidentAgiDecisionOutputV1 | None = None
    normalized_decision: dict[str, Any] = {}
    if not decision:
        add_check("decision.output_schema", False, "Resident AGI must return a non-empty JSON decision object.")
    else:
        try:
            downstream_allowed = decision.get("downstream_allowed")
            if type(downstream_allowed) is not bool:
                raise ValueError("downstream_allowed must be an exact bool")
            output = ResidentAgiDecisionOutputV1(
                verdict=str(decision.get("verdict") or ""),
                rationale=str(decision.get("rationale") or ""),
                evidence_refs=_resident_agi_decision_sequence(decision, "evidence_refs"),
                risks=_resident_agi_decision_sequence(decision, "risks"),
                next_action=str(decision.get("next_action") or ""),
                downstream_allowed=downstream_allowed,
                decision_capability_id=str(decision.get("decision_capability_id") or ""),
            )
            normalized_decision = output.to_dict()
            add_check("decision.output_schema", True, "Resident AGI output matches ResidentAgiDecisionOutputV1.")
        except ValueError as exc:
            add_check("decision.output_schema", False, str(exc))

    if output is not None:
        add_check(
            "decision_capability_id.matches_selected",
            output.decision_capability_id == selected_decision_id,
            "Resident AGI must echo the selected decision capability id.",
        )
        evidence_gate_status = str(evidence_gate.get("status") or "").strip().lower()
        evidence_blocks_downstream = evidence_gate_status in {"hold", "fail", "block"}
        add_check(
            "evidence_gate.continue_guard",
            not (evidence_blocks_downstream and output.verdict == "continue"),
            "Resident AGI cannot continue when the evidence gate is hold/fail/block.",
        )
        add_check(
            "evidence_gate.downstream_guard",
            not (evidence_blocks_downstream and output.downstream_allowed),
            "Resident AGI cannot allow downstream execution while evidence is incomplete.",
        )
        add_check(
            "non_continue.downstream_guard",
            output.verdict == "continue" or not output.downstream_allowed,
            "Only a continue verdict may allow downstream execution.",
        )
        next_action = str(output.next_action or "").strip()
        suggested_rules_raw = decision.get("suggested_rules")
        suggested_rules = suggested_rules_raw if isinstance(suggested_rules_raw, list) else []
        repair_advisory_relevant = _resident_agi_repair_advisory_decision_relevant(
            decision=decision,
            decision_capability_id=selected_decision_id,
        )
        if repair_advisory_relevant:
            add_check(
                "repair_advisory.suggested_rules_present",
                next_action != "suggest_repair_rule" or bool(suggested_rules),
                "Repair-rule suggestions require a non-empty suggested_rules list.",
            )
        if suggested_rules:
            try:
                advisory = RepairAdvisoryV1(
                    advisor_source="resident_agi",
                    message=output.rationale,
                    confidence=0.0,
                    suggested_rules=tuple(suggested_rules),
                    metadata={"source_role": "resident_agi"},
                )
                normalized_decision["suggested_rules"] = advisory.to_dict()["suggested_rules"]
                add_check(
                    "repair_advisory.suggested_rules_policy",
                    True,
                    "Resident AGI suggested_rules pass Director Runtime advisory policy.",
                )
            except (TypeError, ValueError) as exc:
                add_check(
                    "repair_advisory.suggested_rules_policy",
                    False,
                    str(exc),
                )

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_version": "resident.agi_output_contract_gate.v1",
        "status": "pass" if not failed else "fail",
        "passed": not failed,
        "required": True,
        "reason": "Resident AGI output contract accepted."
        if not failed
        else "Resident AGI output contract is incomplete or unsafe.",
        "checks": checks,
        "failed_check_ids": [str(item["check_id"]) for item in failed],
        "normalized_decision": normalized_decision,
    }


def _resident_agi_decision_type_tokens(decision_type: str) -> set[str]:
    token = str(decision_type or "").strip().lower()
    compact = token.replace("-", "_").replace(".", "_").replace(" ", "_")
    dotted = compact.replace("_", ".")
    aliases = {
        "architecture": "architecture.option.selection",
        "architecture_option": "architecture.option.selection",
        "architecture_option_selection": "architecture.option.selection",
        "architecture_options": "architecture.option.selection",
        "dependency_choice": "architecture.option.selection",
        "evidence": "evidence.interface.selection",
        "evidence_interface": "evidence.interface.selection",
        "evidence_interface_selection": "evidence.interface.selection",
        "goal_execution": "goal.promotion.readiness",
        "goal_promotion": "goal.promotion.readiness",
        "goal_promotion_readiness": "goal.promotion.readiness",
        "hard_rule": "platform.invariant.blocker",
        "invariant": "platform.invariant.blocker",
        "platform_invariant": "platform.invariant.blocker",
        "platform_invariant_blocker": "platform.invariant.blocker",
        "platform_supervision": "evidence.interface.selection",
        "quality_gate": "quality.gate.response",
        "quality_gate_response": "quality.gate.response",
        "director_repair": "director.repair.advisory",
        "director_repair_advisory": "director.repair.advisory",
        "repair_advisory": "director.repair.advisory",
        "repair_rule_suggestion": "director.repair.advisory",
        "suggest_repair_rule": "director.repair.advisory",
        "verification": "quality.gate.response",
    }
    return {value for value in {token, compact, dotted, aliases.get(compact), aliases.get(dotted)} if value}


def _resident_agi_select_decision_capability(
    *,
    decision_type: str,
    audit_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select the AGI decision capability that governs a requested decision type."""

    capability_surface_raw = (audit_pack or {}).get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    decision_capabilities_raw = capability_surface.get("decision_capabilities")
    decision_capabilities = decision_capabilities_raw if isinstance(decision_capabilities_raw, list) else []
    valid_capabilities = [item for item in decision_capabilities if isinstance(item, dict)]
    if not valid_capabilities:
        return {}

    requested_tokens = _resident_agi_decision_type_tokens(decision_type)
    for capability in valid_capabilities:
        capability_id = str(capability.get("decision_id") or "").strip().lower()
        if capability_id in requested_tokens:
            return dict(capability)

    for fallback_id in ("evidence.interface.selection", "quality.gate.response"):
        for capability in valid_capabilities:
            if str(capability.get("decision_id") or "").strip().lower() == fallback_id:
                return dict(capability)
    return dict(valid_capabilities[0])


def _resident_agi_capability_by_id(audit_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    capability_surface_raw = audit_pack.get("capability_surface")
    capability_surface: dict[str, Any] = capability_surface_raw if isinstance(capability_surface_raw, dict) else {}
    items_raw = capability_surface.get("items")
    items = items_raw if isinstance(items_raw, list) else []
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        capability_id = str(item.get("capability_id") or "").strip()
        if capability_id:
            result[capability_id] = dict(item)
    return result
