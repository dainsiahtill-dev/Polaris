"""Shared Resident AGI autonomy boundary contracts."""

from __future__ import annotations

from typing import Any


def resident_tick_autonomy_boundary() -> dict[str, Any]:
    """Return the Resident tick boundary that keeps AGI judgement on RoleRuntime."""

    return {
        "schema_version": "resident.tick_autonomy_boundary.v1",
        "tick_role": "deterministic_evidence_producer",
        "tick_outputs": [
            "meta_insights",
            "skill_artifacts",
            "capability_graph",
            "counterfactual_experiments",
            "improvement_proposals",
            "pending_goal_proposals",
        ],
        "goal_proposal_semantics": "pending_proposals_only",
        "agi_judgement_entrypoint": "resident_agi_decision_turn",
        "agi_judgement_endpoint": "/v2/resident/agi/decide",
        "execution_impacting_decision_policy": "requires_resident_agi_runtime_contract_gate",
        "sidecar_llm_allowed": False,
    }


__all__ = ["resident_tick_autonomy_boundary"]
