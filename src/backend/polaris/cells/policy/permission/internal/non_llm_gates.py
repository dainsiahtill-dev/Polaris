from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.runtime.shared_types import (
    normalize_policy_decision,
    normalize_str_list as _normalize_list,
)

logger = logging.getLogger(__name__)


def evaluate_policy_gate(task: dict[str, Any], required: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
    decision = normalize_policy_decision(required.get("policy_decision") or task.get("policy_decision"))
    if not decision:
        return (
            "blocked",
            {"decision": "block"},
            "POLICY_GATE_DECISION_MISSING",
            "PolicyGate decision missing; fail-closed BLOCK.",
        )
    if decision == "allow":
        return ("success", {"decision": "allow"}, "", "PolicyGate decision=ALLOW")
    # V3.2 strict mode: ESCALATE is treated as BLOCK.
    if decision == "escalate":
        return (
            "blocked",
            {"decision": "escalate", "treated_as": "block"},
            "POLICY_GATE_ESCALATED",
            "PolicyGate decision=ESCALATE (treated as BLOCK in strict mode)",
        )
    return (
        "blocked",
        {"decision": "block"},
        "POLICY_GATE_BLOCKED",
        "PolicyGate decision=BLOCK",
    )


def evaluate_finops_gate(task: dict[str, Any], required: dict[str, Any]) -> tuple[str, dict[str, Any], str, str]:
    # Budget input.
    budget_limit_raw = required.get("budget_limit")
    try:
        budget_limit = int(budget_limit_raw) if budget_limit_raw is not None else 0
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "evaluate_finops_gate: budget_limit=%r is not parseable: %s. "
            "Setting budget_limit=0 (fail-closed: no budget enforcement).",
            budget_limit_raw,
            exc,
        )
        budget_limit = 0  # Fail-closed: 0 means no budget limit configured → block by default

    # Runtime consumption evidence (explicit evidence first, fallback heuristic).
    evidence_units_raw = required.get("runtime_cost_units")
    if evidence_units_raw is None:
        evidence_units_raw = required.get("estimated_units")
    try:
        evidence_units = int(evidence_units_raw) if evidence_units_raw is not None else -1
    except (RuntimeError, ValueError) as exc:
        logger.warning("Non-LLM gate evaluation failed (evidence_units %r): %s", evidence_units_raw, exc)
        evidence_units = -1

    if evidence_units < 0:
        evidence_units = (
            len(_normalize_list(task.get("target_files")))
            + len(_normalize_list(task.get("scope_paths")))
            + len(_normalize_list(task.get("context_files")))
            + len(_normalize_list(task.get("acceptance")))
            + len(_normalize_list(task.get("acceptance_criteria")))
        )

    output = {
        "budget_limit": budget_limit,
        "runtime_cost_units": evidence_units,
    }
    if budget_limit > 0 and evidence_units > budget_limit:
        return (
            "blocked",
            output,
            "FINOPS_BUDGET_BLOCKED",
            f"FinOps blocked task: runtime_cost_units={evidence_units} > budget_limit={budget_limit}",
        )
    return ("success", output, "", "FinOps check passed")
