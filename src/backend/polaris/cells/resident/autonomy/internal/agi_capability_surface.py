"""Resident/AGI capability surface for governed unattended decisions."""

from __future__ import annotations

from polaris.cells.resident.autonomy.public.contracts import (
    ResidentAgiCapabilityV1,
    ResidentAgiDecisionBoundaryV1,
)


def build_resident_agi_capability_surface() -> list[ResidentAgiCapabilityV1]:
    """Return platform capabilities the Resident/AGI may use for decisions.

    This is a governed capability catalog, not a bypass around existing cells.
    Read capabilities expose evidence. Write/execute capabilities must still
    pass through their canonical contract, role chain, and safety gates.
    """

    return [
        ResidentAgiCapabilityV1(
            capability_id="resident.decision_trace.read_write",
            name="Resident decision trace",
            category="decision_trace",
            access="write_through_resident_contract",
            purpose="Record AGI, PM, CE, Director, and QA decisions as durable evidence.",
            contract_ref="resident.decision_trace",
            endpoint="/v2/resident/decisions",
            risk_level="medium",
            guardrails=(
                "Decision records must include actor and stage.",
                "Execution-impacting decisions must include evidence or context refs.",
            ),
            evidence_refs=("workspace/meta/resident/decision_trace.jsonl",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="audit.evidence_bundle.read",
            name="Evidence bundle reader",
            category="audit_evidence",
            access="read_only",
            purpose="Inspect file changes, related symbols, and run evidence linked to decisions.",
            contract_ref="audit.evidence.bundle",
            endpoint="/v2/resident/decisions/{decision_id}/evidence",
            risk_level="low",
            guardrails=("Evidence is read-only for AGI decisions.",),
            evidence_refs=("workspace evidence bundles",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="contextos.final_request_audit.read",
            name="Final provider-request audit",
            category="llm_audit",
            access="read_only",
            purpose="Verify the actual LLM request, tools, sampling, token budget, and coverage flags.",
            contract_ref="roles.final_request_context_audit",
            risk_level="low",
            guardrails=("Provider request snapshots are the truth source for LLM call auditing.",),
            evidence_refs=("runtime/contexts/<shard>/<hash>",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="run_ledger.read",
            name="Run Ledger projection",
            category="run_ledger",
            access="read_only",
            purpose="Inspect role runs, receipts, effect status, and quality gates before deciding next actions.",
            contract_ref="control_plane.run_ledger",
            risk_level="low",
            guardrails=("Ledger facts are read-only; writes stay inside canonical runtime services.",),
            evidence_refs=("runtime run ledger projections",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="task.execution_profile.read",
            name="Task execution profile",
            category="task_profile",
            access="read_only",
            purpose="Read task type, language, best-practice guidance, temperature, output contract, and audit tags.",
            contract_ref="task.execution_profile.v1",
            risk_level="low",
            guardrails=("Profile is a decision contract; prompts, temperature, and audit consume the same payload.",),
            evidence_refs=("director_execution_profile",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="chief_engineer.blueprint.read",
            name="Chief Engineer blueprint and handoff",
            category="architecture_decision",
            access="read_only",
            purpose="Inspect architecture guidance, task slicing, risks, and Director handoff evidence.",
            contract_ref="chief_engineer.blueprint",
            risk_level="low",
            guardrails=("CE blueprint remains upstream of Director execution.",),
            evidence_refs=("runtime/contracts/chief_engineer.blueprint.json",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="runtime.events.read",
            name="Runtime event stream",
            category="runtime_observation",
            access="read_only",
            purpose="Observe PM → Chief Engineer → Director → QA progress through the canonical runtime stream.",
            contract_ref="runtime.v2.websocket",
            endpoint="/v2/ws/runtime",
            risk_level="low",
            guardrails=("Realtime product observation must use NATS JetStream + runtime.v2 WebSocket.",),
            evidence_refs=("runtime.v2 events",),
        ),
        ResidentAgiCapabilityV1(
            capability_id="resident.goal_bridge.execute",
            name="Resident governed goal bridge",
            category="controlled_execution",
            access="execute_through_pm_ce_director_chain",
            purpose="Promote approved AGI goals into PM contracts and optionally run them through the governed chain.",
            contract_ref="resident.goal_bridge",
            endpoint="/v2/resident/goals/{goal_id}/run",
            risk_level="high",
            guardrails=(
                "No shortcut from PM directly to Director.",
                "Execution must preserve PM → Chief Engineer → Director.",
                "Tool/path/security gates still apply.",
            ),
            evidence_refs=("resident goal artifacts", "PM runtime contract"),
        ),
    ]


def build_resident_agi_decision_boundaries() -> list[ResidentAgiDecisionBoundaryV1]:
    """Return the governed boundary between platform rules and AGI judgement."""

    return [
        ResidentAgiDecisionBoundaryV1(
            boundary_id="platform.invariants",
            name="Platform hard invariants",
            authority="platform_hard_rule",
            platform_hard_rule=(
                "Security, path authorization, realtime single-rail transport, final provider-request audit, "
                "and PM → Chief Engineer → Director topology are enforced by code."
            ),
            agi_decision_scope=(
                "AGI may detect missing evidence, explain the blocker, and propose remediation; it cannot override gates."
            ),
            evidence_required=(
                "final_request_context_audit",
                "runtime.v2 events",
                "permission/tool receipts",
            ),
            escalation="Block or request governed remediation when hard-rule evidence is missing.",
            contract_refs=(
                "roles.final_request_context_audit",
                "runtime.v2.websocket",
                "tool_permission_policy",
            ),
        ),
        ResidentAgiDecisionBoundaryV1(
            boundary_id="architecture.options",
            name="Architecture and dependency choice",
            authority="agi_recommendation",
            platform_hard_rule=(
                "AGI must preserve repository architecture standards, Cell/KernelOne reuse, and existing role handoff contracts."
            ),
            agi_decision_scope=(
                "AGI may compare architecture options, libraries, storage, messaging, and UI patterns using current task evidence."
            ),
            evidence_required=(
                "task.execution_profile.v1",
                "chief_engineer.blueprint",
                "workspace code evidence",
            ),
            escalation="Escalate to CE blueprint revision for high-risk or cross-cell architecture changes.",
            contract_refs=(
                "task.execution_profile.v1",
                "chief_engineer.blueprint",
                "resident.decision_trace",
            ),
        ),
        ResidentAgiDecisionBoundaryV1(
            boundary_id="goal.execution",
            name="Goal promotion and unattended execution",
            authority="agi_governed_execution",
            platform_hard_rule=(
                "Approved goals may only execute through the governed PM → Chief Engineer → Director chain."
            ),
            agi_decision_scope=(
                "AGI may prioritize goals, stage them, attach evidence, and decide whether a run is ready for promotion."
            ),
            evidence_required=(
                "resident goal artifact",
                "decision_trace.jsonl",
                "PM runtime contract",
            ),
            escalation="Hold execution when evidence is incomplete or chain handoff cannot be proven.",
            contract_refs=(
                "resident.goal_bridge",
                "resident.decision_trace",
                "PM runtime contract",
            ),
        ),
        ResidentAgiDecisionBoundaryV1(
            boundary_id="quality.response",
            name="Quality gate response",
            authority="agi_recommendation",
            platform_hard_rule="AGI cannot mark failed build, lint, test, audit, or QA gates as passed.",
            agi_decision_scope=(
                "AGI may choose retry strategy, evidence collection priority, and whether to ask CE/Director for a targeted fix."
            ),
            evidence_required=(
                "test/lint/build output",
                "Run Ledger projection",
                "ContextOS final request coverage",
            ),
            escalation="Block promotion and create a remediation decision when gates remain red.",
            contract_refs=(
                "control_plane.run_ledger",
                "contextos.final_request_audit",
                "resident.decision_trace",
            ),
        ),
    ]


def resident_agi_capability_surface_payload() -> dict[str, object]:
    """Return a serializable capability-surface payload."""

    items = [item.to_dict() for item in build_resident_agi_capability_surface()]
    decision_boundaries = [item.to_dict() for item in build_resident_agi_decision_boundaries()]
    categories = sorted({str(item["category"]) for item in items})
    return {
        "schema_version": "resident.agi_capability_surface.v1",
        "decision_boundary_schema": "resident.agi_decision_boundary.v1",
        "role_id": "resident_agi",
        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
        "implementation_cell": "resident.autonomy",
        "product_role": "embedded_agi_supervisor",
        "unattended_factory_role": "replace_human_supervision",
        "categories": categories,
        "items": items,
        "decision_boundaries": decision_boundaries,
        "count": len(items),
    }


__all__ = [
    "build_resident_agi_capability_surface",
    "build_resident_agi_decision_boundaries",
    "resident_agi_capability_surface_payload",
]
