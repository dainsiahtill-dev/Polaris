"""Resident/AGI capability surface for governed unattended decisions."""

from __future__ import annotations

from typing import Any

from polaris.cells.director.runtime.public import (
    QueryDirectorRepairStrategyCatalogV1,
    query_director_repair_strategy_catalog,
)
from polaris.cells.resident.autonomy.public.contracts import (
    ResidentAgiCapabilityV1,
    ResidentAgiDecisionBoundaryV1,
    ResidentAgiDecisionCapabilityV1,
)


def build_resident_agi_capability_surface() -> list[ResidentAgiCapabilityV1]:
    """Return platform capabilities the Resident/AGI may use for decisions.

    This is a governed capability catalog, not a bypass around existing cells.
    Read capabilities expose evidence. Write/execute capabilities must still
    pass through their canonical contract, role chain, and safety gates.
    """

    return [
        ResidentAgiCapabilityV1(
            capability_id="resident.agi_decision_turn.execute",
            name="Resident AGI role decision turn",
            category="role_runtime",
            access="execute_through_role_runtime",
            purpose=(
                "Run platform-level AGI judgements as resident_agi role turns through "
                "RoleRuntime, ContextOS, and TurnEngine."
            ),
            contract_ref="resident.agi_decision_turn",
            endpoint="/v2/resident/agi/decide",
            risk_level="medium",
            guardrails=(
                "AGI decisions must use the resident_agi role adapter, never a sidecar runtime.",
                "Decision results must be recorded back into the Resident decision trace.",
                "Execution-impacting outcomes still require governed downstream role handoff.",
            ),
            evidence_refs=(
                "resident_agi role_result",
                "workspace/meta/resident/decision_trace.jsonl",
            ),
        ),
        ResidentAgiCapabilityV1(
            capability_id="roles.registry.read",
            name="Canonical role registry",
            category="role_runtime",
            access="read_only",
            purpose=(
                "Discover PM, Chief Engineer, Director, QA, and Resident AGI from the same role registry "
                "instead of maintaining a separate AGI role universe."
            ),
            contract_ref="roles.registry",
            risk_level="low",
            guardrails=("Registry facts are read-only; role execution still enters through role adapters.",),
            evidence_refs=("role registry projection",),
        ),
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
            capability_id="audit.diagnosis.read",
            name="Audit diagnosis trail",
            category="audit_diagnosis",
            access="read_only",
            purpose="Inspect diagnosis trails, failure hops, and triage bundles before deciding remediation.",
            contract_ref="audit.diagnosis",
            risk_level="low",
            guardrails=("Diagnosis trails are evidence; they do not authorize code or state changes by themselves.",),
            evidence_refs=("QueryAuditDiagnosisTrailV1", "AuditDiagnosisResultV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="audit.diagnosis.execute",
            name="Audit diagnosis runner",
            category="audit_diagnosis",
            access="execute_through_audit_contract",
            purpose="Request a governed audit diagnosis command when existing evidence is insufficient.",
            contract_ref="audit.diagnosis",
            risk_level="medium",
            guardrails=(
                "Diagnosis execution must use RunAuditDiagnosisCommandV1.",
                "AGI may use diagnosis output as evidence, but fixes still go through governed roles.",
            ),
            evidence_refs=("RunAuditDiagnosisCommandV1", "AuditDiagnosisCompletedEventV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="audit.verdict.read",
            name="Audit verdict read model",
            category="audit_verdict",
            access="read_only",
            purpose="Read independent audit verdict state and artifacts for a run or task.",
            contract_ref="audit.verdict",
            risk_level="low",
            guardrails=("Audit verdicts are read-only evidence for AGI decisions.",),
            evidence_refs=("QueryAuditVerdictV1", "AuditVerdictResultV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="audit.verdict.execute",
            name="Audit verdict runner",
            category="audit_verdict",
            access="execute_through_audit_contract",
            purpose="Request a governed audit verdict when the current run needs independent review evidence.",
            contract_ref="audit.verdict",
            risk_level="medium",
            guardrails=(
                "AGI cannot mark failed gates as passed.",
                "Verdict execution must use RunAuditVerdictCommandV1 and record resulting evidence.",
            ),
            evidence_refs=("RunAuditVerdictCommandV1", "AuditVerdictIssuedEventV1"),
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
            capability_id="context.catalog.search",
            name="Context catalog search",
            category="context_discovery",
            access="read_only",
            purpose="Discover relevant public Cells and capability descriptors before choosing evidence or architecture inputs.",
            contract_ref="context.catalog",
            risk_level="low",
            guardrails=(
                "Catalog search is read-only; discovered Cells must still be called through their public contracts.",
            ),
            evidence_refs=("SearchCellsQueryV1", "CellDescriptorV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="context.engine.resolve",
            name="Role context resolver",
            category="context_discovery",
            access="read_only",
            purpose="Resolve graph-constrained context items for a role or objective before AGI judgement.",
            contract_ref="context.engine",
            risk_level="low",
            guardrails=("Resolved context is prompt/evidence material, not execution authority.",),
            evidence_refs=("ResolveRoleContextQueryV1", "RoleContextResultV1"),
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
            capability_id="verifier.policy.read",
            name="Verifier policy read model",
            category="verification_policy",
            access="read_only",
            purpose="Inspect which optional verifier modalities are enabled or required for the workspace.",
            contract_ref="control_plane.verifier_policy",
            risk_level="low",
            guardrails=(
                "AGI can read verifier policy; changing required modalities remains a governed platform policy action.",
            ),
            evidence_refs=("ReadVerifierPolicyQueryV1", "VerifierPolicyResultV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="verifier.execution.execute",
            name="Verifier execution request",
            category="verification_policy",
            access="execute_through_control_plane_contract",
            purpose="Request enabled verifier providers to produce physical evidence for a decision.",
            contract_ref="control_plane.verifier_execution",
            risk_level="high",
            guardrails=(
                "Verifier execution must use a policy snapshot and cannot invent required modalities.",
                "Bench-only verifier assumptions must not become production requirements.",
            ),
            evidence_refs=("RunVerifierPolicyCommandV1", "VerifierExecutionResultV1"),
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
            capability_id="director.deterministic_repair_strategy_catalog.read",
            name="Director hard-coded repair strategy catalog",
            category="director_repair_strategy",
            access="read_only",
            purpose=(
                "Inspect Polaris code-enforced Director repair strategies, language scope, repair phase, "
                "concern, and risk level before deciding whether AGI should request evidence, retry, or escalate."
            ),
            contract_ref="director.deterministic_repair_strategy_catalog.v1",
            risk_level="low",
            guardrails=(
                "Catalog access is read-only and does not authorize AGI to execute repairs.",
                "Actual code changes remain Director-authorized tool actions behind existing quality gates.",
                "Unregistered deterministic repair source_tool values are treated as high-risk evidence.",
            ),
            evidence_refs=(
                "director.deterministic_repair_profile_summary.v1",
                "director.deterministic_repair_strategy_catalog.v1",
            ),
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
            capability_id="runtime.status_resident.read",
            name="Resident AGI runtime projection",
            category="runtime_observation",
            access="read_only",
            purpose="Observe Resident AGI state through the dedicated runtime.v2 status.resident projection.",
            contract_ref="runtime.v2.status.resident",
            endpoint="/v2/ws/runtime",
            risk_level="low",
            guardrails=(
                "status.resident is an observation channel; state changes still enter through public commands.",
            ),
            evidence_refs=("runtime.v2.status.resident", "snapshot.resident"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="resident.lifecycle.manage",
            name="Resident lifecycle management",
            category="resident_control",
            access="write_through_resident_contract",
            purpose="Start, stop, and tick the embedded Resident AGI supervisor through public commands.",
            contract_ref="resident.lifecycle.commands",
            endpoint="/v2/resident/{start|stop|tick}",
            risk_level="medium",
            guardrails=(
                "Lifecycle actions must publish status.resident.",
                "Tick execution must preserve hard platform gates and evidence traces.",
            ),
            evidence_refs=("StartResidentCommandV1", "StopResidentCommandV1", "RunResidentTickCommandV1"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="resident.identity.write",
            name="Resident identity profile",
            category="resident_control",
            access="write_through_resident_contract",
            purpose="Update the Resident AGI identity and operating profile through the public identity command.",
            contract_ref="resident.identity.commands",
            endpoint="/v2/resident/identity",
            risk_level="medium",
            guardrails=(
                "Identity changes must remain scoped to the Resident profile.",
                "Role identity must remain resident_agi on the shared role runtime.",
            ),
            evidence_refs=("UpdateResidentIdentityCommandV1", "workspace/meta/resident/identity.json"),
        ),
        ResidentAgiCapabilityV1(
            capability_id="resident.goal_governance.write",
            name="Resident goal governance",
            category="controlled_execution",
            access="write_through_resident_contract",
            purpose="Create, approve, reject, materialize, and stage evidence-backed Resident goals.",
            contract_ref="resident.goal_governance.commands",
            endpoint="/v2/resident/goals",
            risk_level="high",
            guardrails=(
                "Goal execution remains separate from approval and staging.",
                "Approved goals must still execute through PM → Chief Engineer → Director.",
            ),
            evidence_refs=(
                "CreateResidentGoalCommandV1",
                "ApproveResidentGoalCommandV1",
                "RejectResidentGoalCommandV1",
                "resident goal artifacts",
            ),
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
        ResidentAgiCapabilityV1(
            capability_id="resident.autonomy_labs.execute",
            name="Resident autonomy labs",
            category="resident_learning",
            access="execute_through_resident_contract",
            purpose="Refresh skill extraction, counterfactual experiments, and self-improvement proposals.",
            contract_ref="resident.autonomy_lab.commands",
            endpoint="/v2/resident/{skills|experiments|improvements}",
            risk_level="medium",
            guardrails=(
                "Lab outputs are proposals/evidence, not direct code changes.",
                "Any implementation impact must be promoted through governed goals and role handoff.",
            ),
            evidence_refs=(
                "ExtractResidentSkillsCommandV1",
                "RunResidentExperimentsCommandV1",
                "RunResidentImprovementsCommandV1",
            ),
        ),
    ]


def build_resident_agi_decision_boundaries() -> list[ResidentAgiDecisionBoundaryV1]:
    """Return the governed boundary between platform rules and AGI judgement."""

    return [
        ResidentAgiDecisionBoundaryV1(
            boundary_id="role.runtime.foundation",
            name="Shared role runtime foundation",
            authority="platform_hard_rule",
            platform_hard_rule=(
                "Resident AGI is a first-class platform role on the same RoleRuntime, ContextOS, and TurnEngine "
                "foundation as PM, Chief Engineer, Director, and QA."
            ),
            agi_decision_scope=(
                "AGI may request broader platform evidence and make supervision decisions, but every turn must "
                "remain observable as a resident_agi role session."
            ),
            evidence_required=(
                "resident_agi role_result",
                "ContextOS context snapshot",
                "TurnEngine/runtime metadata",
            ),
            escalation="Block AGI automation when the decision cannot be tied to a role-runtime turn.",
            contract_refs=(
                "resident.agi_decision_turn",
                "roles.runtime",
                "contextos.final_request_audit",
            ),
        ),
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
                "director.deterministic_repair_strategy_catalog.v1",
            ),
            escalation="Block promotion and create a remediation decision when gates remain red.",
            contract_refs=(
                "control_plane.run_ledger",
                "contextos.final_request_audit",
                "director.deterministic_repair_strategy_catalog.v1",
                "resident.decision_trace",
            ),
        ),
        ResidentAgiDecisionBoundaryV1(
            boundary_id="audit.interface.selection",
            name="Audit and evidence interface selection",
            authority="agi_recommendation",
            platform_hard_rule=(
                "AGI may request audit, verifier, ContextOS, and catalog evidence only through public Cell contracts; "
                "it cannot treat missing or failed evidence as passed."
            ),
            agi_decision_scope=(
                "AGI may choose which existing audit interface to query or execute based on task risk, missing evidence, "
                "and current runtime state."
            ),
            evidence_required=(
                "AuditDiagnosisResultV1",
                "AuditVerdictResultV1",
                "VerifierPolicyResultV1",
                "RoleContextResultV1",
            ),
            escalation="Hold or request evidence when the required audit interface is unavailable or policy-disabled.",
            contract_refs=(
                "audit.diagnosis",
                "audit.verdict",
                "control_plane.verifier_policy",
                "control_plane.verifier_execution",
                "context.catalog",
                "context.engine",
            ),
        ),
    ]


def build_resident_agi_decision_capabilities() -> list[ResidentAgiDecisionCapabilityV1]:
    """Return decision types the Resident AGI may judge under evidence contracts."""

    shared_constraints = (
        "resident_agi_role_runtime_required",
        "contextos_expected",
        "turn_engine_expected",
        "preserve_pm_chief_engineer_director_qa_chain",
    )
    return [
        ResidentAgiDecisionCapabilityV1(
            decision_id="platform.invariant.blocker",
            name="Platform invariant enforcement",
            owner="platform_hard_rule",
            decision_scope=(
                "Detect whether non-negotiable platform invariants block AGI judgement before any LLM decision."
            ),
            risk_level="high",
            required_evidence_interfaces=(
                "roles.registry.read",
                "contextos.final_request_audit.read",
                "runtime.status_resident.read",
            ),
            candidate_actions=("block", "request_evidence"),
            hard_constraints=(
                "hard_platform_invariants_non_overridable",
                "sidecar_llm_for_resident_agi_forbidden",
            ),
            escalation="Fail closed and repair platform evidence before running Resident AGI.",
            output_contract="resident.agi_hard_rule_gate.v1",
            contract_refs=(
                "roles.registry",
                "roles.final_request_context_audit",
                "runtime.v2.status.resident",
            ),
            llm_decision_required=False,
            platform_enforced=True,
        ),
        ResidentAgiDecisionCapabilityV1(
            decision_id="evidence.interface.selection",
            name="Evidence interface selection",
            owner="resident_agi",
            decision_scope=(
                "Choose which audit, verifier, ContextOS, catalog, and Run Ledger interfaces should be queried "
                "before continuing a platform decision."
            ),
            risk_level="medium",
            required_evidence_interfaces=(
                "contextos.final_request_audit.read",
                "run_ledger.read",
                "audit.diagnosis.read",
                "audit.verdict.read",
                "context.catalog.search",
                "context.engine.resolve",
                "verifier.policy.read",
            ),
            optional_evidence_interfaces=(
                "audit.diagnosis.execute",
                "audit.verdict.execute",
                "verifier.execution.execute",
            ),
            candidate_actions=("request_evidence", "block", "escalate", "continue"),
            hard_constraints=(
                *shared_constraints,
                "missing_or_failed_evidence_cannot_be_treated_as_passed",
                "evidence_execution_must_use_public_cell_contracts",
            ),
            escalation="Hold or request governed evidence when required interfaces are unavailable.",
            contract_refs=(
                "roles.final_request_context_audit",
                "control_plane.run_ledger",
                "audit.diagnosis",
                "audit.verdict",
                "context.catalog",
                "context.engine",
                "control_plane.verifier_policy",
                "control_plane.verifier_execution",
            ),
        ),
        ResidentAgiDecisionCapabilityV1(
            decision_id="architecture.option.selection",
            name="Architecture option selection",
            owner="resident_agi",
            decision_scope=(
                "Compare architecture, dependency, storage, messaging, and UI options from current project evidence "
                "without hard-coding future technology choices."
            ),
            risk_level="medium",
            required_evidence_interfaces=(
                "task.execution_profile.read",
                "chief_engineer.blueprint.read",
                "context.catalog.search",
                "context.engine.resolve",
            ),
            optional_evidence_interfaces=(
                "audit.diagnosis.read",
                "run_ledger.read",
            ),
            candidate_actions=("continue", "request_evidence", "escalate", "block"),
            hard_constraints=(
                *shared_constraints,
                "cell_reuse_first",
                "kernelone_foundation_first",
                "architecture_choice_must_match_actual_project_use",
            ),
            escalation="Escalate to Chief Engineer blueprint revision for high-risk cross-cell changes.",
            contract_refs=(
                "task.execution_profile.v1",
                "chief_engineer.blueprint",
                "context.catalog",
                "context.engine",
            ),
        ),
        ResidentAgiDecisionCapabilityV1(
            decision_id="goal.promotion.readiness",
            name="Goal promotion readiness",
            owner="resident_agi_governed_execution",
            decision_scope=(
                "Decide whether a Resident goal is ready to be staged or promoted into the governed PM chain."
            ),
            risk_level="high",
            required_evidence_interfaces=(
                "resident.decision_trace.read_write",
                "run_ledger.read",
                "runtime.events.read",
            ),
            optional_evidence_interfaces=(
                "audit.verdict.read",
                "contextos.final_request_audit.read",
            ),
            candidate_actions=("continue", "request_evidence", "block", "escalate"),
            hard_constraints=(
                *shared_constraints,
                "goal_execution_must_enter_pm_chief_engineer_director_chain",
                "resident_tick_outputs_are_pending_proposals_only",
            ),
            escalation="Hold promotion until PM/CE/Director handoff evidence is present.",
            contract_refs=(
                "resident.goal_bridge",
                "resident.decision_trace",
                "control_plane.run_ledger",
                "runtime.v2.websocket",
            ),
        ),
        ResidentAgiDecisionCapabilityV1(
            decision_id="quality.gate.response",
            name="Quality gate response",
            owner="resident_agi",
            decision_scope=(
                "Choose whether to block, ask for evidence, escalate, or continue after build/lint/test/audit results."
            ),
            risk_level="high",
            required_evidence_interfaces=(
                "run_ledger.read",
                "contextos.final_request_audit.read",
                "audit.verdict.read",
                "director.deterministic_repair_strategy_catalog.read",
            ),
            optional_evidence_interfaces=(
                "audit.diagnosis.execute",
                "verifier.execution.execute",
            ),
            candidate_actions=("block", "request_evidence", "escalate", "continue"),
            hard_constraints=(
                *shared_constraints,
                "failed_quality_gate_cannot_be_marked_passed_by_agi",
                "fixes_must_go_through_governed_roles",
            ),
            escalation="Block downstream promotion when gates remain red or evidence is incomplete.",
            contract_refs=(
                "control_plane.run_ledger",
                "roles.final_request_context_audit",
                "audit.verdict",
                "audit.diagnosis",
                "director.deterministic_repair_strategy_catalog.v1",
                "control_plane.verifier_execution",
            ),
        ),
    ]


def build_resident_agi_decision_capability_registry(
    decision_capabilities: list[ResidentAgiDecisionCapabilityV1],
) -> dict[str, Any]:
    """Return the machine-readable split between platform and AGI-owned decisions."""

    platform_owned = [item.decision_id for item in decision_capabilities if item.platform_enforced]
    agi_owned = [
        item.decision_id
        for item in decision_capabilities
        if item.owner == "resident_agi" and not item.platform_enforced
    ]
    governed_execution = [
        item.decision_id for item in decision_capabilities if item.owner == "resident_agi_governed_execution"
    ]
    evidence_interface_ids = sorted(
        {
            interface_id
            for item in decision_capabilities
            for interface_id in (*item.required_evidence_interfaces, *item.optional_evidence_interfaces)
        }
    )
    candidate_actions = sorted({action for item in decision_capabilities for action in item.candidate_actions})
    return {
        "schema_version": "resident.agi_decision_capability_registry.v1",
        "role_id": "resident_agi",
        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
        "platform_owned_decisions": platform_owned,
        "agi_owned_decisions": agi_owned,
        "governed_execution_decisions": governed_execution,
        "evidence_interface_ids": evidence_interface_ids,
        "candidate_actions": candidate_actions,
        "counts": {
            "decisions": len(decision_capabilities),
            "platform_owned": len(platform_owned),
            "agi_owned": len(agi_owned),
            "governed_execution": len(governed_execution),
            "evidence_interfaces": len(evidence_interface_ids),
        },
        "decision_policy": {
            "platform_hard_rules": "code_enforced_before_llm",
            "agi_judgement": "resident_agi_role_turn_with_audit_pack",
            "governed_execution": "pm_chief_engineer_director_chain_only",
            "evidence_execution": "public_cell_contracts_only",
        },
    }


def build_resident_agi_evidence_interface_contract(
    *,
    capabilities: list[ResidentAgiCapabilityV1],
    decision_capabilities: list[ResidentAgiDecisionCapabilityV1],
) -> dict[str, Any]:
    """Validate decision evidence interfaces against the capability surface."""

    capability_by_id = {item.capability_id: item for item in capabilities}
    required_by_decision: dict[str, list[str]] = {}
    optional_by_decision: dict[str, list[str]] = {}
    for decision in decision_capabilities:
        for interface_id in decision.required_evidence_interfaces:
            required_by_decision.setdefault(interface_id, []).append(decision.decision_id)
        for interface_id in decision.optional_evidence_interfaces:
            optional_by_decision.setdefault(interface_id, []).append(decision.decision_id)

    required_ids = set(required_by_decision)
    optional_ids = set(optional_by_decision)
    declared_ids = required_ids | optional_ids
    missing_required_ids = sorted(required_ids - capability_by_id.keys())
    missing_optional_ids = sorted(optional_ids - capability_by_id.keys())
    missing_ids = sorted(declared_ids - capability_by_id.keys())
    interface_items: list[dict[str, Any]] = []
    for interface_id in sorted(declared_ids):
        capability = capability_by_id.get(interface_id)
        interface_items.append(
            {
                "interface_id": interface_id,
                "status": "available" if capability else "missing",
                "required_by_decisions": sorted(required_by_decision.get(interface_id, [])),
                "optional_by_decisions": sorted(optional_by_decision.get(interface_id, [])),
                "access": capability.access if capability else "",
                "category": capability.category if capability else "",
                "contract_ref": capability.contract_ref if capability else "",
                "risk_level": capability.risk_level if capability else "unknown",
            }
        )

    return {
        "schema_version": "resident.agi_evidence_interface_contract.v1",
        "role_id": "resident_agi",
        "source": "resident.autonomy.capability_surface",
        "coverage_complete": not missing_ids,
        "supported_interface_ids": sorted(declared_ids & capability_by_id.keys()),
        "declared_interface_ids": sorted(declared_ids),
        "required_interface_ids": sorted(required_ids),
        "optional_interface_ids": sorted(optional_ids),
        "missing_interface_ids": missing_ids,
        "missing_required_interface_ids": missing_required_ids,
        "missing_optional_interface_ids": missing_optional_ids,
        "interfaces": interface_items,
        "decision_policy": {
            "declared_interfaces_must_exist": "fail_closed_before_agi_decision",
            "missing_required_interface": "block_or_request_platform_facade",
            "missing_optional_interface": "degrade_with_audit_note",
        },
    }


def resident_agi_participation_policy_payload() -> dict[str, Any]:
    """Return discoverable Resident AGI participation switches.

    The policy is deliberately extensible: current platform scopes are exposed
    for UI/defaults, while custom scope ids remain allowed so future AGI
    capabilities do not require hard-coded platform migrations.
    """

    decision_capabilities = build_resident_agi_decision_capabilities()
    decision_boundaries = build_resident_agi_decision_boundaries()
    base_scopes: list[dict[str, Any]] = [
        {
            "scope_id": "final_request_audit",
            "name": "Final request audit coverage",
            "category": "llm_audit",
            "risk_level": "medium",
            "default_enabled": False,
        },
        {
            "scope_id": "decision_trace",
            "name": "Resident decision trace handoff",
            "category": "decision_trace",
            "risk_level": "medium",
            "default_enabled": False,
        },
        {
            "scope_id": "capability_surface",
            "name": "Capability surface visibility",
            "category": "capability_surface",
            "risk_level": "low",
            "default_enabled": False,
        },
        {
            "scope_id": "decision_boundary",
            "name": "Decision boundary enforcement visibility",
            "category": "decision_boundary",
            "risk_level": "medium",
            "default_enabled": False,
        },
        {
            "scope_id": "director_repair_strategy_catalog",
            "name": "Director repair strategy catalog visibility",
            "category": "director_repair_strategy",
            "risk_level": "low",
            "default_enabled": False,
        },
    ]
    decision_scopes = [
        {
            "scope_id": item.decision_id,
            "name": item.name,
            "category": "decision_capability",
            "risk_level": item.risk_level,
            "owner": item.owner,
            "default_enabled": False,
        }
        for item in decision_capabilities
        if item.decision_id
    ]
    boundary_scopes = [
        {
            "scope_id": item.boundary_id,
            "name": item.name,
            "category": "decision_boundary",
            "authority": item.authority,
            "risk_level": "high" if item.authority == "platform_hard_rule" else "medium",
            "default_enabled": False,
        }
        for item in decision_boundaries
        if item.boundary_id
    ]
    return {
        "schema_version": "resident.agi_participation_policy.v1",
        "role_id": "resident_agi",
        "source": "resident.autonomy.capability_surface",
        "enabled_default": False,
        "custom_scopes_allowed": True,
        "scope_semantics": "extensible_scope_ids; current ids are recommendations, not a closed enum",
        "participation_flags": [
            "final_request_audit",
            "quality_gate_response",
            "architecture_option_selection",
            "evidence_interface_selection",
            "goal_promotion",
            "decision_trace",
            "capability_surface",
            "decision_boundary",
            "director_repair_strategy_catalog",
        ],
        "available_scopes": [*base_scopes, *decision_scopes, *boundary_scopes],
    }


def build_resident_agi_authority_matrix(
    *,
    capabilities: list[ResidentAgiCapabilityV1],
    decision_boundaries: list[ResidentAgiDecisionBoundaryV1],
) -> dict[str, Any]:
    """Return the machine-readable split between platform rules and AGI judgement."""

    platform_hard_rules: list[str] = []
    agi_recommendation_boundaries: list[str] = []
    governed_execution_boundaries: list[str] = []
    boundary_contracts: set[str] = set()
    for boundary in decision_boundaries:
        if boundary.authority == "platform_hard_rule":
            platform_hard_rules.append(boundary.boundary_id)
        elif boundary.authority == "agi_recommendation":
            agi_recommendation_boundaries.append(boundary.boundary_id)
        elif boundary.authority == "agi_governed_execution":
            governed_execution_boundaries.append(boundary.boundary_id)
        boundary_contracts.update(boundary.contract_refs)

    read_only_capabilities: list[str] = []
    governed_operation_capabilities: list[str] = []
    high_risk_capabilities: list[str] = []
    capability_contracts: set[str] = set()
    chain_required = False
    for capability in capabilities:
        access = capability.access.lower()
        capability_contracts.add(capability.contract_ref)
        if access == "read_only":
            read_only_capabilities.append(capability.capability_id)
        if "write" in access or "execute" in access:
            governed_operation_capabilities.append(capability.capability_id)
        if capability.risk_level.lower() == "high":
            high_risk_capabilities.append(capability.capability_id)
        if "pm_ce_director" in access or capability.contract_ref == "resident.goal_bridge":
            chain_required = True

    canonical_contracts = sorted(capability_contracts | boundary_contracts)
    chain_required = chain_required or bool(governed_execution_boundaries)
    return {
        "schema_version": "resident.agi_authority_matrix.v1",
        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
        "role_id": "resident_agi",
        "chain": "PM → Chief Engineer → Director",
        "chain_required": chain_required,
        "platform_enforced": True,
        "llm_decision_required": True,
        "platform_hard_rules": platform_hard_rules,
        "agi_recommendation_boundaries": agi_recommendation_boundaries,
        "governed_execution_boundaries": governed_execution_boundaries,
        "read_only_capabilities": read_only_capabilities,
        "governed_operation_capabilities": governed_operation_capabilities,
        "high_risk_capabilities": high_risk_capabilities,
        "canonical_contracts": canonical_contracts,
        "counts": {
            "platform_hard_rules": len(platform_hard_rules),
            "agi_recommendations": len(agi_recommendation_boundaries),
            "governed_execution_boundaries": len(governed_execution_boundaries),
            "read_only_capabilities": len(read_only_capabilities),
            "governed_operation_capabilities": len(governed_operation_capabilities),
            "high_risk_capabilities": len(high_risk_capabilities),
            "canonical_contracts": len(canonical_contracts),
        },
        "decision_policy": {
            "hard_rules": "platform_enforced_non_overridable",
            "evidence_gates": "agi_judgement_with_fail_closed_recommendation",
            "governed_execution": "canonical_role_chain_only",
            "code_changes": "director_authorized_tools_only",
        },
    }


def resident_agi_capability_surface_payload() -> dict[str, object]:
    """Return a serializable capability-surface payload."""

    capability_items = build_resident_agi_capability_surface()
    decision_boundary_items = build_resident_agi_decision_boundaries()
    decision_capability_items = build_resident_agi_decision_capabilities()
    items = [item.to_dict() for item in capability_items]
    decision_boundaries = [item.to_dict() for item in decision_boundary_items]
    decision_capabilities = [item.to_dict() for item in decision_capability_items]
    categories = sorted({str(item["category"]) for item in items})
    return {
        "schema_version": "resident.agi_capability_surface.v1",
        "decision_boundary_schema": "resident.agi_decision_boundary.v1",
        "decision_capability_schema": "resident.agi_decision_capability.v1",
        "authority_matrix_schema": "resident.agi_authority_matrix.v1",
        "evidence_interface_contract_schema": "resident.agi_evidence_interface_contract.v1",
        "role_id": "resident_agi",
        "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
        "implementation_cell": "resident.autonomy",
        "product_role": "embedded_agi_supervisor",
        "unattended_factory_role": "replace_human_supervision",
        "categories": categories,
        "items": items,
        "decision_boundaries": decision_boundaries,
        "decision_capabilities": decision_capabilities,
        "decision_capability_registry": build_resident_agi_decision_capability_registry(
            decision_capability_items,
        ),
        "evidence_interface_contract": build_resident_agi_evidence_interface_contract(
            capabilities=capability_items,
            decision_capabilities=decision_capability_items,
        ),
        "participation_policy": resident_agi_participation_policy_payload(),
        "hardcoded_repair_strategy_catalog": resident_agi_director_repair_strategy_catalog_payload(),
        "authority_matrix": build_resident_agi_authority_matrix(
            capabilities=capability_items,
            decision_boundaries=decision_boundary_items,
        ),
        "count": len(items),
    }


def resident_agi_director_repair_strategy_catalog_payload() -> dict[str, Any]:
    """Return Director hard-coded repair strategy evidence for AGI judgement."""

    return query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1()).to_dict()


__all__ = [
    "build_resident_agi_authority_matrix",
    "build_resident_agi_capability_surface",
    "build_resident_agi_decision_boundaries",
    "build_resident_agi_decision_capabilities",
    "build_resident_agi_decision_capability_registry",
    "build_resident_agi_evidence_interface_contract",
    "resident_agi_capability_surface_payload",
    "resident_agi_director_repair_strategy_catalog_payload",
    "resident_agi_participation_policy_payload",
]
