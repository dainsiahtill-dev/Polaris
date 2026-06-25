import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ResidentWorkspace } from "./ResidentWorkspace";

const mockResidentState = {
  workspace: "X:/Git/polaris",
  status: null,
  goals: [
    {
      goal_id: "goal-approved",
      title: "Stabilize PM contract quality",
      goal_type: "reliability",
      source: "manual",
      status: "approved",
      motivation: "Reduce drift in PM output",
      updated_at: "2026-03-07T00:00:00Z",
      evidence_refs: ["docs/resident/resident-engineering-rfc.md"],
      scope: ["src/backend/app/orchestration"],
    },
    {
      goal_id: "goal-pending",
      title: "Investigate flaky retries",
      goal_type: "reliability",
      source: "auto",
      status: "pending",
      motivation: "Retry storms are noisy",
      updated_at: "2026-03-07T00:00:00Z",
      evidence_refs: [],
      scope: [],
    },
  ],
  decisions: [
    {
      decision_id: "decision-1",
      actor: "pm",
      stage: "goal_staging",
      summary: "Selected bounded decomposition strategy",
      timestamp: "2026-03-07T00:00:00Z",
      run_id: "resident-run-001",
      task_id: "TASK-1",
      goal_id: "goal-approved",
      verdict: "success",
      strategy_tags: ["task_split", "pm_bridge"],
      confidence: 0.92,
      context_refs: ["runtime/contexts/abc123"],
      options: [
        {
          option_id: "opt-a",
          label: "bounded decomposition",
          rationale: "Lower regression risk",
          estimated_score: 0.91,
        },
      ],
      selected_option_id: "opt-a",
      evidence_refs: ["runtime/contracts/plan.md"],
      evidence_bundle_id: "bundle-1",
      affected_files: [
        "src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py",
      ],
      affected_symbols: ["record_decision"],
      actual_outcome: {
        decision_source: "resident_agi_supervisor",
        evidence_schema: "resident.decision_event.v1",
        execution_profile_schema: "task.execution_profile.v1",
        validator_result: "validation_passed",
        promoted_to_pm_runtime: true,
        task_count: 2,
        role_runtime_entrypoint: "roles.runtime.execute_role_session",
        resident_agi_decision_profile: {
          schema_version: "resident.agi_decision_profile.v1",
          role_turn_allowed: true,
          downstream_precheck: "ready_for_agi_judgement",
        },
        resident_agi_decision_capability: {
          decision_id: "goal.promotion.readiness",
          name: "Goal promotion readiness",
        },
        resident_agi_required_evidence_interfaces: [
          "resident.decision_trace.read_write",
          "run_ledger.read",
        ],
        resident_agi_runtime_contract_gate: {
          schema_version: "resident.agi_runtime_contract_gate.v1",
          status: "pass",
          passed: true,
          required: true,
          failed_check_ids: [],
        },
      },
    },
  ],
  loading: false,
  actionKey: "",
  error: null,
  residentRuntime: {
    active: true,
    mode: "propose",
    tick_count: 3,
    last_tick_at: "2026-03-07T00:00:00Z",
    last_summary: {
      autonomy_boundary: {
        schema_version: "resident.tick_autonomy_boundary.v1",
        tick_role: "deterministic_evidence_producer",
        goal_proposal_semantics: "pending_proposals_only",
        agi_judgement_entrypoint: "resident_agi_decision_turn",
        agi_judgement_endpoint: "/v2/resident/agi/decide",
        execution_impacting_decision_policy:
          "requires_resident_agi_runtime_contract_gate",
        sidecar_llm_allowed: false,
      },
    },
  },
  residentRuntimeEvidence: {
    schema_version: "resident.runtime_projection_evidence.v1",
    realtime_channel: "runtime.v2.status.resident",
    snapshot_channel: "runtime.v2.status.snapshot",
    projection_field: "snapshot.resident",
    live_snapshot_available: true,
    http_details_loaded: true,
    source: "runtime.v2_snapshot+http_details",
  },
  residentIdentity: {
    name: "Resident AGI Supervisor",
    mission:
      "Supervise unattended Polaris development runs with governed evidence.",
    owner: "human",
    operating_mode: "propose",
  },
  residentAgenda: {
    current_focus: ["stabilize orchestration"],
    risk_register: ["goal backlog rising"],
    next_actions: ["approve reliability goal"],
    pending_goal_ids: [],
    approved_goal_ids: ["goal-approved"],
    materialized_goal_ids: [],
    active_experiment_ids: [],
    active_improvement_ids: [],
  },
  residentCounts: {
    goals: 1,
    decisions: 1,
    experiments: 1,
    improvements: 1,
  },
  residentInsights: [
    {
      insight_id: "insight-1",
      summary: "Prefer bounded decomposition",
      insight_type: "meta_cognition",
      strategy_tag: "task_split",
      confidence: 0.88,
      recommendation: "Use narrower task scopes for risky runs.",
    },
  ],
  residentSkills: [],
  residentExperiments: [],
  residentImprovements: [],
  residentCapabilityGraph: {
    generated_at: "2026-03-07T00:00:00Z",
    capabilities: [
      {
        capability_id: "cap-1",
        name: "Task decomposition",
        kind: "reasoning",
        score: 0.86,
        success_rate: 0.83,
        attempts: 6,
        evidence_count: 4,
      },
    ],
    gaps: ["shadow-runtime-promotion"],
  },
  residentAgiCapabilitySurface: {
    schema_version: "resident.agi_capability_surface.v1",
    decision_boundary_schema: "resident.agi_decision_boundary.v1",
    authority_matrix_schema: "resident.agi_authority_matrix.v1",
    evidence_interface_contract_schema:
      "resident.agi_evidence_interface_contract.v1",
    role_id: "resident_agi",
    runtime_foundation: "RoleRuntime / ContextOS / TurnEngine",
    implementation_cell: "resident.autonomy",
    count: 13,
    items: [
      {
        capability_id: "resident.agi_decision_turn.execute",
        name: "Resident AGI role decision turn",
        category: "role_runtime",
        access: "execute_through_role_runtime",
        contract_ref: "resident.agi_decision_turn",
        endpoint: "/v2/resident/agi/decide",
        risk_level: "medium",
        guardrails: [
          "AGI decisions must use the resident_agi role adapter, never a sidecar runtime.",
        ],
        evidence_refs: ["resident_agi role_result"],
      },
      {
        capability_id: "roles.registry.read",
        name: "Canonical role registry",
        category: "role_runtime",
        access: "read_only",
        contract_ref: "roles.registry",
      },
      {
        capability_id: "contextos.final_request_audit.read",
        name: "Final provider-request audit",
        category: "llm_audit",
        access: "read_only",
        contract_ref: "roles.final_request_context_audit",
      },
      {
        capability_id: "run_ledger.read",
        name: "Run Ledger projection",
        category: "run_ledger",
        access: "read_only",
        contract_ref: "control_plane.run_ledger",
      },
      {
        capability_id: "audit.diagnosis.read",
        name: "Audit diagnosis trail",
        category: "audit_diagnosis",
        access: "read_only",
        contract_ref: "audit.diagnosis",
        evidence_refs: ["QueryAuditDiagnosisTrailV1", "AuditDiagnosisResultV1"],
      },
      {
        capability_id: "audit.diagnosis.execute",
        name: "Audit diagnosis runner",
        category: "audit_diagnosis",
        access: "execute_through_audit_contract",
        contract_ref: "audit.diagnosis",
        risk_level: "medium",
        evidence_refs: [
          "RunAuditDiagnosisCommandV1",
          "AuditDiagnosisCompletedEventV1",
        ],
      },
      {
        capability_id: "audit.verdict.read",
        name: "Audit verdict read model",
        category: "audit_verdict",
        access: "read_only",
        contract_ref: "audit.verdict",
        evidence_refs: ["QueryAuditVerdictV1", "AuditVerdictResultV1"],
      },
      {
        capability_id: "audit.verdict.execute",
        name: "Audit verdict runner",
        category: "audit_verdict",
        access: "execute_through_audit_contract",
        contract_ref: "audit.verdict",
        risk_level: "medium",
        evidence_refs: [
          "RunAuditVerdictCommandV1",
          "AuditVerdictIssuedEventV1",
        ],
      },
      {
        capability_id: "context.catalog.search",
        name: "Context catalog search",
        category: "context_discovery",
        access: "read_only",
        contract_ref: "context.catalog",
        evidence_refs: ["SearchCellsQueryV1", "CellDescriptorV1"],
      },
      {
        capability_id: "context.engine.resolve",
        name: "Role context resolver",
        category: "context_discovery",
        access: "read_only",
        contract_ref: "context.engine",
        evidence_refs: ["ResolveRoleContextQueryV1", "RoleContextResultV1"],
      },
      {
        capability_id: "director.deterministic_repair_strategy_catalog.read",
        name: "Director hard-coded repair strategy catalog",
        category: "director_repair_strategy",
        access: "read_only",
        contract_ref: "director.deterministic_repair_strategy_catalog.v1",
        risk_level: "low",
        evidence_refs: [
          "director.deterministic_repair_profile_summary.v1",
          "director.deterministic_repair_strategy_catalog.v1",
        ],
      },
      {
        capability_id: "director.repair_coverage.read",
        name: "Director repair diagnostic coverage",
        category: "director_repair_strategy",
        access: "read_only",
        contract_ref: "director.repair_coverage_report.v1",
        risk_level: "low",
        evidence_refs: ["director.repair_coverage_report.v1"],
      },
      {
        capability_id: "director.repair_advisory_policy.read",
        name: "Director AGI repair advisory policy",
        category: "director_repair_advisory",
        access: "read_only",
        contract_ref: "director.repair_advisory_policy.v1",
        risk_level: "low",
        evidence_refs: ["director.repair_advisory_policy.v1"],
      },
      {
        capability_id: "verifier.policy.read",
        name: "Verifier policy read model",
        category: "verification_policy",
        access: "read_only",
        contract_ref: "control_plane.verifier_policy",
        evidence_refs: ["ReadVerifierPolicyQueryV1", "VerifierPolicyResultV1"],
      },
      {
        capability_id: "verifier.execution.execute",
        name: "Verifier execution request",
        category: "verification_policy",
        access: "execute_through_control_plane_contract",
        contract_ref: "control_plane.verifier_execution",
        risk_level: "high",
        evidence_refs: [
          "RunVerifierPolicyCommandV1",
          "VerifierExecutionResultV1",
        ],
      },
      {
        capability_id: "resident.goal_bridge.execute",
        name: "Resident governed goal bridge",
        category: "controlled_execution",
        access: "execute_through_pm_ce_director_chain",
        contract_ref: "resident.goal_bridge",
        risk_level: "high",
        guardrails: ["No shortcut from PM directly to Director."],
        evidence_refs: ["PM runtime contract"],
      },
    ],
    decision_boundaries: [
      {
        boundary_id: "role.runtime.foundation",
        name: "Shared role runtime foundation",
        authority: "platform_hard_rule",
        platform_hard_rule:
          "Resident AGI uses the same RoleRuntime, ContextOS, and TurnEngine.",
        agi_decision_scope:
          "Every AGI turn remains observable as resident_agi.",
        evidence_required: ["resident_agi role_result"],
      },
      {
        boundary_id: "platform.invariants",
        name: "Platform hard invariants",
        authority: "platform_hard_rule",
        platform_hard_rule:
          "Security, runtime topology, and final request audit are enforced by code.",
        agi_decision_scope:
          "AGI may detect missing evidence and propose remediation.",
        evidence_required: ["final_request_context_audit", "runtime.v2 events"],
      },
      {
        boundary_id: "architecture.options",
        name: "Architecture and dependency choice",
        authority: "agi_recommendation",
        platform_hard_rule:
          "Preserve Cell/KernelOne reuse and role handoff contracts.",
        agi_decision_scope:
          "AGI may compare architecture options and library choices using task evidence.",
        evidence_required: [
          "task.execution_profile.v1",
          "chief_engineer.blueprint",
        ],
      },
      {
        boundary_id: "goal.execution",
        name: "Goal promotion and unattended execution",
        authority: "agi_governed_execution",
        platform_hard_rule:
          "Approved goals may only execute through the governed role chain.",
        agi_decision_scope:
          "AGI may prioritize, stage, and promote evidence-backed goals.",
        evidence_required: ["decision_trace.jsonl", "PM runtime contract"],
      },
      {
        boundary_id: "audit.interface.selection",
        name: "Audit and evidence interface selection",
        authority: "agi_recommendation",
        platform_hard_rule:
          "AGI may request audit, verifier, ContextOS, and catalog evidence only through public Cell contracts.",
        agi_decision_scope:
          "AGI may choose which existing audit interface to query or execute.",
        evidence_required: [
          "AuditDiagnosisResultV1",
          "AuditVerdictResultV1",
          "VerifierPolicyResultV1",
          "RoleContextResultV1",
        ],
      },
    ],
    decision_capability_schema: "resident.agi_decision_capability.v1",
    decision_capabilities: [
      {
        decision_id: "platform.invariant.blocker",
        name: "Platform invariant enforcement",
        owner: "platform_hard_rule",
        decision_scope:
          "Detect whether non-negotiable platform invariants block AGI judgement.",
        risk_level: "high",
        required_evidence_interfaces: [
          "roles.registry.read",
          "contextos.final_request_audit.read",
        ],
        candidate_actions: ["block", "request_evidence"],
        hard_constraints: ["hard_platform_invariants_non_overridable"],
        platform_enforced: true,
        llm_decision_required: false,
      },
      {
        decision_id: "evidence.interface.selection",
        name: "Evidence interface selection",
        owner: "resident_agi",
        decision_scope:
          "Choose which audit, verifier, ContextOS, catalog, and Run Ledger interfaces should be queried.",
        risk_level: "medium",
        required_evidence_interfaces: [
          "contextos.final_request_audit.read",
          "run_ledger.read",
          "audit.diagnosis.read",
          "verifier.policy.read",
        ],
        optional_evidence_interfaces: ["verifier.execution.execute"],
        candidate_actions: ["request_evidence", "block", "escalate"],
        hard_constraints: ["evidence_execution_must_use_public_cell_contracts"],
        llm_decision_required: true,
      },
      {
        decision_id: "goal.promotion.readiness",
        name: "Goal promotion readiness",
        owner: "resident_agi_governed_execution",
        decision_scope:
          "Decide whether a Resident goal is ready to enter the governed PM chain.",
        risk_level: "high",
        required_evidence_interfaces: [
          "resident.decision_trace.read_write",
          "run_ledger.read",
        ],
        candidate_actions: ["continue", "request_evidence", "block"],
        hard_constraints: [
          "goal_execution_must_enter_pm_chief_engineer_director_chain",
        ],
        llm_decision_required: true,
      },
      {
        decision_id: "director.repair.advisory",
        name: "Director repair advisory",
        owner: "resident_agi",
        decision_scope:
          "Suggest non-authoritative future repair rules for Director Runtime.",
        risk_level: "medium",
        required_evidence_interfaces: [
          "director.deterministic_repair_strategy_catalog.read",
          "director.repair_coverage.read",
          "director.repair_advisory_policy.read",
        ],
        optional_evidence_interfaces: ["audit.diagnosis.read"],
        candidate_actions: ["suggest_repair_rule", "request_evidence"],
        hard_constraints: [
          "repair_rule_suggestions_are_non_authoritative",
          "suggested_rules_must_pass_advisory_policy",
        ],
        llm_decision_required: true,
      },
    ],
    decision_capability_registry: {
      schema_version: "resident.agi_decision_capability_registry.v1",
      role_id: "resident_agi",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      platform_owned_decisions: ["platform.invariant.blocker"],
      agi_owned_decisions: [
        "evidence.interface.selection",
        "director.repair.advisory",
      ],
      governed_execution_decisions: ["goal.promotion.readiness"],
      evidence_interface_ids: [
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "audit.diagnosis.read",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
        "verifier.policy.read",
        "verifier.execution.execute",
      ],
      candidate_actions: [
        "block",
        "continue",
        "request_evidence",
        "suggest_repair_rule",
      ],
      counts: {
        decisions: 4,
        platform_owned: 1,
        agi_owned: 2,
        governed_execution: 1,
        evidence_interfaces: 5,
      },
      decision_policy: {
        platform_hard_rules: "code_enforced_before_llm",
        agi_judgement: "resident_agi_role_turn_with_audit_pack",
        governed_execution: "pm_chief_engineer_director_chain_only",
        evidence_execution: "public_cell_contracts_only",
      },
    },
    capability_access_registry_schema:
      "resident.agi_capability_access_registry.v1",
    capability_access_registry: {
      schema_version: "resident.agi_capability_access_registry.v1",
      role_id: "resident_agi",
      source: "resident.autonomy.capability_surface",
      execution_policy: {
        agi_direct_tool_execution_allowed: false,
        agi_direct_writes_allowed: false,
        director_runtime_remains_authoritative: true,
        governed_execution_requires_public_contract: true,
        pm_chief_engineer_director_chain_required_for_code_changes: true,
      },
      groups: {
        read_only_capabilities: [
          "roles.registry.read",
          "contextos.final_request_audit.read",
          "run_ledger.read",
        ],
        advisory_only_capabilities: [
          "director.deterministic_repair_strategy_catalog.read",
          "director.repair_coverage.read",
          "director.repair_advisory_policy.read",
        ],
        governed_execution_capabilities: [
          "audit.diagnosis.execute",
          "verifier.execution.execute",
          "resident.goal_bridge.execute",
        ],
        governed_write_capabilities: [],
        high_risk_capabilities: [
          "verifier.execution.execute",
          "resident.goal_bridge.execute",
        ],
      },
      interface_domains: [
        {
          domain_id: "audit",
          capability_ids: ["audit.diagnosis.read", "audit.diagnosis.execute"],
          read_only: 1,
          advisory_only: 0,
          governed_execution: 1,
          governed_write: 0,
          high_risk: 0,
        },
        {
          domain_id: "director_repair",
          capability_ids: [
            "director.deterministic_repair_strategy_catalog.read",
            "director.repair_coverage.read",
            "director.repair_advisory_policy.read",
          ],
          read_only: 3,
          advisory_only: 3,
          governed_execution: 0,
          governed_write: 0,
          high_risk: 0,
        },
        {
          domain_id: "run_ledger",
          capability_ids: ["run_ledger.read"],
          read_only: 1,
          advisory_only: 0,
          governed_execution: 0,
          governed_write: 0,
          high_risk: 0,
        },
      ],
      canonical_contracts: [
        "roles.registry",
        "control_plane.run_ledger",
        "control_plane.verifier_execution",
        "director.repair_advisory_policy.v1",
      ],
      counts: {
        capabilities: 16,
        read_only: 10,
        advisory_only: 3,
        governed_execution: 5,
        governed_write: 0,
        high_risk: 2,
        domains: 3,
        canonical_contracts: 4,
      },
    },
    evidence_interface_contract: {
      schema_version: "resident.agi_evidence_interface_contract.v1",
      role_id: "resident_agi",
      source: "resident.autonomy.capability_surface",
      coverage_complete: true,
      supported_interface_ids: [
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "audit.diagnosis.read",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
        "verifier.execution.execute",
      ],
      declared_interface_ids: [
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "audit.diagnosis.read",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
        "verifier.execution.execute",
      ],
      required_interface_ids: [
        "contextos.final_request_audit.read",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
      ],
      optional_interface_ids: ["verifier.execution.execute"],
      missing_interface_ids: [],
      missing_required_interface_ids: [],
      missing_optional_interface_ids: [],
      interfaces: [
        {
          interface_id: "director.deterministic_repair_strategy_catalog.read",
          status: "available",
          required_by_decisions: ["quality.gate.response"],
          access: "read_only",
          category: "director_repair_strategy",
          contract_ref: "director.deterministic_repair_strategy_catalog.v1",
          risk_level: "low",
        },
        {
          interface_id: "director.repair_coverage.read",
          status: "available",
          required_by_decisions: ["quality.gate.response"],
          access: "read_only",
          category: "director_repair_strategy",
          contract_ref: "director.repair_coverage_report.v1",
          risk_level: "low",
        },
        {
          interface_id: "director.repair_advisory_policy.read",
          status: "available",
          required_by_decisions: ["quality.gate.response"],
          access: "read_only",
          category: "director_repair_advisory",
          contract_ref: "director.repair_advisory_policy.v1",
          risk_level: "low",
        },
      ],
      decision_policy: {
        declared_interfaces_must_exist: "fail_closed_before_agi_decision",
      },
    },
    authority_matrix: {
      schema_version: "resident.agi_authority_matrix.v1",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      role_id: "resident_agi",
      chain: "PM → Chief Engineer → Director",
      chain_required: true,
      platform_enforced: true,
      llm_decision_required: true,
      platform_hard_rules: ["role.runtime.foundation", "platform.invariants"],
      agi_recommendation_boundaries: [
        "architecture.options",
        "quality.response",
        "audit.interface.selection",
      ],
      governed_execution_boundaries: ["goal.execution"],
      read_only_capabilities: [
        "roles.registry.read",
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "audit.diagnosis.read",
        "audit.verdict.read",
        "context.catalog.search",
        "context.engine.resolve",
        "director.deterministic_repair_strategy_catalog.read",
        "director.repair_coverage.read",
        "director.repair_advisory_policy.read",
        "verifier.policy.read",
      ],
      governed_operation_capabilities: [
        "resident.agi_decision_turn.execute",
        "audit.diagnosis.execute",
        "audit.verdict.execute",
        "verifier.execution.execute",
        "resident.goal_bridge.execute",
      ],
      high_risk_capabilities: [
        "resident.goal_bridge.execute",
        "verifier.execution.execute",
      ],
      canonical_contracts: [
        "resident.agi_decision_turn",
        "resident.goal_bridge",
        "roles.runtime",
        "audit.diagnosis",
        "audit.verdict",
        "context.catalog",
        "context.engine",
        "director.deterministic_repair_strategy_catalog.v1",
        "director.repair_coverage_report.v1",
        "director.repair_advisory_policy.v1",
        "control_plane.verifier_policy",
        "control_plane.verifier_execution",
      ],
      counts: {
        platform_hard_rules: 2,
        agi_recommendations: 3,
        governed_execution_boundaries: 1,
        read_only_capabilities: 8,
        governed_operation_capabilities: 5,
        high_risk_capabilities: 2,
        canonical_contracts: 9,
      },
      decision_policy: {
        hard_rules: "platform_enforced_non_overridable",
        governed_execution: "canonical_role_chain_only",
        code_changes: "director_authorized_tools_only",
      },
    },
    decision_boundary_policy: {
      schema_version: "resident.agi_decision_boundary_policy.v1",
      role_id: "resident_agi",
      source: "resident.autonomy.capability_surface",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      chain: "PM → Chief Engineer → Director",
      decision_modes: {
        platform_hard_rule: {
          owner: "platform_code",
          llm_decision_allowed: false,
          llm_may_explain_or_request_evidence: true,
          override_allowed: false,
          execution_authority: "none",
          write_authority: false,
          default_action: "block_or_request_governed_remediation",
        },
        agi_recommendation: {
          owner: "resident_agi",
          llm_decision_allowed: true,
          llm_may_explain_or_request_evidence: true,
          override_allowed: false,
          execution_authority: "advisory_only",
          write_authority: false,
          default_action: "recommend_request_evidence_or_escalate",
        },
        agi_governed_execution: {
          owner: "resident_agi_with_pm_chief_engineer_director_handoff",
          llm_decision_allowed: true,
          llm_may_explain_or_request_evidence: true,
          override_allowed: false,
          execution_authority: "governed_handoff_only",
          write_authority: false,
          default_action:
            "handoff_to_canonical_role_chain_when_evidence_passes",
        },
      },
      boundary_policies: [
        {
          boundary_id: "platform.invariants",
          name: "Platform hard invariants",
          authority: "platform_hard_rule",
          decision_owner: "platform_code",
          llm_decision_allowed: false,
          override_allowed: false,
          execution_authority: "none",
          write_authority: false,
          platform_enforced: true,
          advisory_only: false,
          default_action: "block_or_request_governed_remediation",
        },
        {
          boundary_id: "architecture.options",
          name: "Architecture and dependency choice",
          authority: "agi_recommendation",
          decision_owner: "resident_agi",
          llm_decision_allowed: true,
          override_allowed: false,
          execution_authority: "advisory_only",
          write_authority: false,
          platform_enforced: false,
          advisory_only: true,
          default_action: "recommend_request_evidence_or_escalate",
        },
        {
          boundary_id: "goal.execution",
          name: "Goal promotion and unattended execution",
          authority: "agi_governed_execution",
          decision_owner:
            "resident_agi_with_pm_chief_engineer_director_handoff",
          llm_decision_allowed: true,
          override_allowed: false,
          execution_authority: "governed_handoff_only",
          write_authority: false,
          requires_pm_chief_engineer_director_chain: true,
          default_action:
            "handoff_to_canonical_role_chain_when_evidence_passes",
        },
      ],
      capability_execution_policy: {
        read_only_capabilities: ["run_ledger.read"],
        governed_request_capabilities: ["verifier.execution.execute"],
        write_contract_capabilities: ["resident.goal_governance.write"],
        high_risk_capabilities: [
          "resident.goal_bridge.execute",
          "verifier.execution.execute",
        ],
        advisory_evidence_capabilities: [
          "director.repair_advisory_policy.read",
        ],
        agi_direct_writes_allowed: false,
        agi_direct_tool_execution_allowed: false,
        director_runtime_remains_authoritative: true,
        pm_chief_engineer_director_chain_required: true,
      },
      non_overridable_rules: ["role.runtime.foundation", "platform.invariants"],
      agi_judgement_boundaries: [
        "architecture.options",
        "quality.response",
        "audit.interface.selection",
      ],
      governed_execution_boundaries: ["goal.execution"],
      counts: {
        boundary_policies: 6,
        platform_hard_rules: 2,
        agi_judgement: 3,
        governed_execution: 1,
        read_only_capabilities: 8,
        governed_request_capabilities: 5,
        write_contract_capabilities: 2,
        high_risk_capabilities: 2,
      },
    },
    hardcoded_repair_strategy_catalog: {
      schema_version: "director.deterministic_repair_strategy_catalog.v1",
      source: "director.runtime.repair_kernel.strategy_catalog",
      access: "read_only",
      owner_cell: "director.runtime",
      execution_boundary: "director_authorized_tools_only",
      chain: "PM → Chief Engineer → Director",
      unknown_source_tool_policy: "fail_closed_high_risk",
      agi_execution_authority: false,
      director_tool_execution_required: true,
      summary: {
        total: 3,
        returned: 3,
        by_language: { typescript: 2, python: 1 },
        by_phase: { quality_repair: 2, test_contract: 1 },
        by_concern: { missing_symbol_or_file: 2, module_boundary: 1 },
        by_risk: { low: 2, medium: 1 },
      },
      items: [
        {
          source_tool: "deterministic_typescript_missing_export_repair",
          language: "typescript",
          phase: "quality_repair",
          concern: "missing_symbol_or_file",
          risk_level: "low",
        },
        {
          source_tool: "deterministic_python_unittest_missing_target_repair",
          language: "python",
          phase: "test_contract",
          concern: "missing_symbol_or_file",
          risk_level: "low",
        },
        {
          source_tool: "deterministic_typescript_reexport_repair",
          language: "typescript",
          phase: "quality_repair",
          concern: "module_boundary",
          risk_level: "medium",
        },
      ],
    },
    director_repair_advisory_policy: {
      schema_version: "director.repair_advisory_policy.v1",
      source: "director.runtime.repair_kernel.advisory_policy",
      access: "read_only",
      owner_cell: "director.runtime",
      execution_boundary: "read_only_advisory_no_writes_no_registration",
      agi_execution_authority: false,
      writes_allowed: false,
      registration_allowed: false,
      authoritative_receipts_allowed: false,
      allowed_suggested_rule_fields: [
        "pattern",
        "fix_template",
        "confidence",
        "evidence",
      ],
      forbidden_suggested_rule_fields: [
        "write_file",
        "patch",
        "repair_plan",
        "policy_override",
      ],
      summary: {
        suggested_rules_allowed: true,
        director_runtime_remains_authoritative: true,
      },
    },
  },
  residentAgiAuditPack: {
    schema_version: "resident.agi_audit_pack.v1",
    workspace: "X:/Git/polaris",
    role_id: "resident_agi",
    runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
    truth_sources: [
      "resident.status",
      "resident.agi_capability_surface",
      "resident.decision_trace",
      "runtime.v2.status.resident",
      "roles.registry",
      "director.runtime.repair_kernel.strategy_catalog",
      "director.runtime.repair_kernel.registry",
      "director.runtime.repair_kernel.advisory_policy",
      "director.repair_receipts",
    ],
    role_registry: {
      schema_version: "resident.agi_role_registry.v1",
      dialogue_roles: [
        "pm",
        "chief_engineer",
        "director",
        "qa",
        "resident_agi",
      ],
      adapter_roles: ["pm", "chief_engineer", "director", "qa", "resident_agi"],
      required_roles: [
        "pm",
        "chief_engineer",
        "director",
        "qa",
        "resident_agi",
      ],
      missing_required_roles: [],
      resident_agi_available: true,
    },
    boundary_summary: {
      schema: "resident.agi_decision_boundary.v1",
      boundary_ids: ["role.runtime.foundation", "platform.invariants"],
    },
    authority_matrix: {
      schema_version: "resident.agi_authority_matrix.v1",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      role_id: "resident_agi",
      chain: "PM → Chief Engineer → Director",
      chain_required: true,
      platform_enforced: true,
      llm_decision_required: true,
      counts: {
        platform_hard_rules: 2,
        agi_recommendations: 2,
        governed_execution_boundaries: 1,
        read_only_capabilities: 3,
        governed_operation_capabilities: 2,
        high_risk_capabilities: 1,
        canonical_contracts: 3,
      },
      decision_policy: {
        hard_rules: "platform_enforced_non_overridable",
        governed_execution: "canonical_role_chain_only",
        code_changes: "director_authorized_tools_only",
      },
    },
    director_repair_contract: {
      schema_version: "resident.agi_director_repair_contract.v1",
      owner_cell: "director.runtime",
      source: "director.runtime.repair_kernel.strategy_catalog",
      catalog_schema: "director.deterministic_repair_strategy_catalog.v1",
      coverage_schema: "director.repair_coverage_report.v1",
      advisory_policy_schema: "director.repair_advisory_policy.v1",
      profile_summary_schema:
        "director.deterministic_repair_profile_summary.v1",
      unknown_source_tool_policy: "fail_closed_high_risk",
      execution_boundary: "director_authorized_tools_only",
      chain: "PM → Chief Engineer → Director",
      agi_advisory: {
        active: true,
        authoritative: false,
        writes_allowed: false,
        registration_allowed: false,
        suggested_rules_allowed: true,
        allowed_suggested_rule_fields: ["pattern", "fix_template"],
        forbidden_suggested_rule_fields: ["write_file", "patch"],
      },
      agi_execution_authority: false,
      director_tool_execution_required: true,
      strategy_count: 67,
      summary: {
        total: 67,
        by_language: { typescript: 38, python: 3 },
      },
    },
    hard_rule_gate: {
      schema_version: "resident.agi_hard_rule_gate.v1",
      status: "pass",
      checks: [
        {
          check_id: "role_registry.resident_agi_available",
          passed: true,
          detail: "resident_agi must exist in dialogue and adapter registries.",
        },
        {
          check_id: "topology.pm_ce_director_preserved",
          passed: true,
          detail:
            "Downstream execution must preserve PM → Chief Engineer → Director.",
        },
      ],
      failed_check_ids: [],
      platform_enforced: true,
      llm_override_allowed: false,
    },
    run_ledger_summary: {
      schema_version: "resident.agi_run_ledger_summary.v1",
      source: "run_ledger_projection",
      available: false,
      ok: false,
      status: "pending",
      projected: 0,
      total: 0,
      failed: 0,
      missing: 0,
      detail: "run ledger projection is not available yet",
    },
    evidence_gate: {
      schema_version: "resident.agi_evidence_gate.v1",
      status: "hold",
      recommended_verdict: "request_evidence",
      reason: "Run Ledger projection is not available yet.",
      run_ledger_available: false,
      run_ledger_ok: false,
      context_snapshot_ref_count: 1,
      platform_enforced: false,
      llm_decision_required: true,
    },
    capability_surface: {
      schema_version: "resident.agi_capability_surface.v1",
      items: [
        {
          capability_id: "resident.agi_decision_turn.execute",
          name: "Resident AGI role decision turn",
        },
      ],
      decision_capabilities: [
        {
          decision_id: "evidence.interface.selection",
          name: "Evidence interface selection",
        },
      ],
      decision_capability_registry: {
        schema_version: "resident.agi_decision_capability_registry.v1",
        platform_owned_decisions: ["platform.invariant.blocker"],
        agi_owned_decisions: ["evidence.interface.selection"],
        governed_execution_decisions: ["goal.promotion.readiness"],
      },
    },
    autonomy_boundary: {
      schema_version: "resident.tick_autonomy_boundary.v1",
      tick_role: "deterministic_evidence_producer",
      goal_proposal_semantics: "pending_proposals_only",
      agi_judgement_entrypoint: "resident_agi_decision_turn",
      agi_judgement_endpoint: "/v2/resident/agi/decide",
      execution_impacting_decision_policy:
        "requires_resident_agi_runtime_contract_gate",
      sidecar_llm_allowed: false,
    },
    recent_decisions: [
      {
        decision_id: "decision-1",
        actor: "pm",
        stage: "goal_staging",
        summary: "Selected bounded decomposition strategy",
      },
    ],
    evidence_refs: ["runtime/contracts/plan.md"],
    execution_constraints: [
      "AGI decisions must execute as resident_agi role turns.",
      "Resident tick/labs are deterministic evidence producers, not AGI judgement turns.",
      "Downstream work must preserve PM → Chief Engineer → Director.",
    ],
    decision_endpoint: "/v2/resident/agi/decide",
    decision_profile: {
      schema_version: "resident.agi_decision_profile.v1",
      role_id: "resident_agi",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      role_turn_allowed: true,
      downstream_precheck: "hold_for_evidence",
      recommended_verdict: "request_evidence",
      recommended_next_action:
        "request_missing_contextos_or_run_ledger_evidence",
      candidate_actions: ["request_evidence", "block", "escalate"],
      required_constraints: [
        "resident_agi_role_runtime_required",
        "contextos_expected",
        "turn_engine_expected",
        "preserve_pm_chief_engineer_director_qa_chain",
        "resident_tick_is_deterministic_evidence_only",
        "execution_impacting_agi_judgement_requires_runtime_contract_gate",
      ],
      required_evidence: [
        "ContextOS context snapshot",
        "Run Ledger projection",
      ],
      evidence_interface_recommendations: [
        {
          capability_id: "contextos.final_request_audit.read",
          name: "Final provider-request audit",
          contract_ref: "roles.final_request_context_audit",
          access: "read_only",
          risk_level: "low",
          priority: 10,
          recommended_now: true,
          reason: "Request missing evidence before continuing.",
        },
        {
          capability_id: "run_ledger.read",
          name: "Run Ledger projection",
          contract_ref: "control_plane.run_ledger",
          access: "read_only",
          risk_level: "low",
          priority: 20,
          recommended_now: true,
          reason: "Request missing evidence before continuing.",
        },
        {
          capability_id: "verifier.execution.execute",
          name: "Verifier execution request",
          contract_ref: "control_plane.verifier_execution",
          access: "execute_through_control_plane_contract",
          risk_level: "high",
          priority: 60,
          recommended_now: true,
          reason: "Request missing evidence before continuing.",
        },
      ],
      decision_capability_registry: {
        schema_version: "resident.agi_decision_capability_registry.v1",
        platform_owned_decisions: ["platform.invariant.blocker"],
        agi_owned_decisions: ["evidence.interface.selection"],
        governed_execution_decisions: ["goal.promotion.readiness"],
      },
      decision_capability_ids: [
        "platform.invariant.blocker",
        "evidence.interface.selection",
        "goal.promotion.readiness",
      ],
      contract_refs: ["resident.agi_decision_turn", "roles.runtime"],
      authority_policy: {
        hard_rules: "platform_enforced_non_overridable",
        governed_execution: "canonical_role_chain_only",
        code_changes: "director_authorized_tools_only",
      },
      platform_permission_counts: {
        read_only: 3,
        governed_operations: 2,
        high_risk: 1,
      },
      gate_refs: {
        hard_rule_gate: "resident.agi_hard_rule_gate.v1",
        evidence_gate: "resident.agi_evidence_gate.v1",
        authority_matrix: "resident.agi_authority_matrix.v1",
        decision_capability_registry:
          "resident.agi_decision_capability_registry.v1",
        autonomy_boundary: "resident.tick_autonomy_boundary.v1",
      },
      llm_decision_required: true,
      llm_override_allowed: false,
      audit_pack_schema: "resident.agi_audit_pack.v1",
    },
  },
  residentAgiEvidenceInterfaces: {
    schema_version: "resident.agi_evidence_interfaces.v1",
    decision_type: "quality_gate_response",
    selected_decision_capability: {
      decision_id: "quality.gate.response",
      name: "Quality gate response",
    },
    interfaces: [
      {
        interface_id: "run_ledger.read",
        name: "Run Ledger projection",
        status: "unavailable",
        callable: true,
        source: "control_plane.run_ledger.public.read_run_ledger_projection",
        recommended_next_action: "request_run_ledger_evidence",
        gaps: ["run ledger projection is not available yet"],
      },
      {
        interface_id: "verifier.policy.read",
        name: "Verifier policy read model",
        status: "available",
        callable: true,
        source: "control_plane.verifier_policy.public.read_verifier_policy",
        recommended_next_action: "use_verifier_policy_snapshot",
      },
      {
        interface_id: "audit.verdict.read",
        name: "Audit verdict read model",
        status: "empty",
        source: "audit.verdict.public.query_audit_verdict",
        recommended_next_action: "use_audit_verdict_snapshot",
      },
      {
        interface_id: "director.repair_coverage.read",
        name: "Director repair diagnostic coverage",
        status: "available",
        callable: true,
        source: "director.runtime.public.query_director_repair_coverage",
        recommended_next_action:
          "use_repair_coverage_to_choose_retry_escalate_or_suggest_rule",
      },
      {
        interface_id: "director.repair_advisory_policy.read",
        name: "Director AGI repair advisory policy",
        status: "available",
        callable: true,
        source: "director.runtime.public.query_director_repair_advisory_policy",
        recommended_next_action:
          "use_repair_advisory_policy_before_accepting_agi_suggested_rules",
      },
    ],
    capability_matrix: {
      schema_version: "resident.agi_evidence_capability_matrix.v1",
      decision_type: "quality_gate_response",
      selected_decision_id: "quality.gate.response",
      groups: [
        {
          group_id: "run_ledger",
          name: "Run ledger",
          interface_ids: ["run_ledger.read"],
          total: 1,
          available: 0,
          required: 1,
          missing_required: 1,
          recommended_now: 1,
          high_risk: 0,
          governed_execute: 0,
        },
        {
          group_id: "verifier",
          name: "Verifier",
          interface_ids: ["verifier.policy.read"],
          total: 1,
          available: 1,
          required: 1,
          missing_required: 0,
          recommended_now: 1,
          high_risk: 0,
          governed_execute: 0,
        },
        {
          group_id: "director_repair",
          name: "Director repair",
          interface_ids: [
            "director.repair_coverage.read",
            "director.repair_advisory_policy.read",
          ],
          total: 2,
          available: 2,
          required: 0,
          missing_required: 0,
          recommended_now: 2,
          high_risk: 0,
          governed_execute: 0,
        },
      ],
      summary: {
        total: 5,
        available: 3,
        required: 2,
        required_available: 1,
        missing_required: 1,
        missing_required_interface_ids: ["run_ledger.read"],
        recommended_now: 4,
        callable: 4,
        high_risk: 0,
        governed_execute: 0,
        status_counts: {
          available: 3,
          unavailable: 1,
          empty: 1,
        },
        advisory_only: true,
        authoritative: false,
        agi_execution_authority: false,
      },
    },
    summary: {
      total: 5,
      available: 3,
      unavailable: 1,
      needs_public_facade: 0,
      metadata_only: 0,
      governed_execute_only: 0,
    },
  },
  residentAgiHandoffs: {
    schema_version: "resident.agi_handoff_inbox.v1",
    workspace: "/tmp/polaris-demo",
    role_id: "resident_agi",
    items: [
      {
        schema_version: "resident.agi_handoff_inbox_item.v1",
        decision_id: "decision-handoff-1",
        summary: "Quality gate can proceed through governed handoff.",
        verdict: "success",
        handoff: {
          schema_version: "resident.agi_decision_handoff.v1",
          decision_type: "quality_gate_response",
          decision_capability_id: "quality.gate.response",
          handoff_status: "ready",
          target_roles: ["chief_engineer", "director", "qa"],
          downstream_allowed: true,
          reason: "Quality gate can proceed through governed handoff.",
          required_chain: "PM → Chief Engineer → Director",
          advisory_only: true,
          agi_execution_authority: false,
        },
      },
    ],
    count: 1,
    summary: {
      total: 1,
      by_status: { ready: 1 },
      by_target_role: { chief_engineer: 1, director: 1, qa: 1 },
      advisory_only: true,
      agi_execution_authority: false,
      required_chain: "PM → Chief Engineer → Director",
    },
  },
  residentAgiActionCatalog: {
    schema_version: "resident.agi_tactical_action_catalog.v1",
    source: "resident.autonomy.internal.agi_tactical_actions",
    items: [
      {
        action_id: "refresh_evidence_interfaces",
        label: "刷新证据接口",
        mode: "read_only",
        status: "available",
        reason:
          "只刷新 Resident AGI evidence interface read model，不改变项目状态。",
        ui_handler: "refresh_evidence_interfaces",
        capability_id: "audit.evidence_interface_selection",
        contract_ref:
          "resident.autonomy.public.query_resident_agi_evidence_interfaces",
        risk_level: "low",
        requires_participation: false,
        agi_direct_execution_allowed: false,
      },
      {
        action_id: "open_operator_settings",
        label: "打开值守设定",
        mode: "local_navigation",
        status: "available",
        reason: "打开常驻 AGI 参与范围设置，不自动修改权限。",
        ui_handler: "open_operator_settings",
        capability_id: "resident.agi_participation_policy.read",
        contract_ref: "resident.workspace.local_operator_settings",
        risk_level: "low",
        requires_participation: false,
        agi_direct_execution_allowed: false,
      },
      {
        action_id: "request_resident_agi_judgement",
        label: "请求 AGI 判断",
        mode: "execute_through_role_runtime",
        status: "preview_only",
        reason:
          "通过 resident_agi 角色回合执行一次受控判断；只产出决策和证据。",
        ui_handler: "execute_governed_action",
        capability_id: "resident.agi_decision_turn.execute",
        contract_ref: "resident.autonomy.public.run_resident_agi_decision_turn",
        risk_level: "medium",
        requires_participation: true,
        agi_direct_execution_allowed: false,
      },
      {
        action_id: "request_director_controlled_repair",
        label: "请求 Director 受控修复",
        mode: "controlled_execution",
        status: "preview_only",
        reason:
          "AGI 聊天不能直接写文件，只能建议进入 PM → Chief Engineer → Director → QA。",
        ui_handler: "execute_governed_action",
        capability_id: "resident.goal_governance.commands",
        contract_ref: "resident.goal_governance.commands",
        risk_level: "high",
        requires_participation: true,
        agi_direct_execution_allowed: false,
      },
    ],
    summary: {
      total: 4,
      requires_participation: 2,
      agi_direct_execution_allowed: false,
      required_chain: "PM → Chief Engineer → Director → QA",
    },
  },
  lastAgiDecisionResult: {
    ok: true,
    workspace: "/tmp/polaris-demo",
    decision_handoff: {
      schema_version: "resident.agi_decision_handoff.v1",
      source_role: "resident_agi",
      decision_type: "quality_gate_response",
      decision_capability_id: "quality.gate.response",
      handoff_status: "ready",
      target_roles: ["chief_engineer", "director", "qa"],
      allowed_actions: [
        "record_decision_trace",
        "handoff_to_pm_chief_engineer_director_chain",
      ],
      blocked_actions: [
        "direct_file_write_by_agi",
        "director_tool_execution_by_agi",
        "pm_to_director_shortcut",
      ],
      downstream_allowed: true,
      reason: "Quality gate can proceed through governed handoff.",
      required_chain: "PM → Chief Engineer → Director",
      advisory_only: true,
      agi_execution_authority: false,
    },
    repair_advisory_overlay: {
      schema_version: "resident.agi_repair_advisory_overlay.v1",
      source:
        "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
      status: "ready",
      active: true,
      eligible_for_director_injection: true,
      advisory_only: true,
      authoritative: false,
      agi_execution_authority: false,
      director_runtime_contract: "director.repair_advisory_policy.v1",
      decision_capability_id: "director.repair.advisory",
      participation_enabled: true,
      reason: "Resident AGI repair advisory is valid and non-authoritative.",
      advisor_notes: [
        {
          advisor_source: "resident_agi",
          message: "Suggest future deterministic repair rule.",
          confidence: 0.7,
          authoritative: false,
          suggested_rules: [
            {
              name: "rust_receiver_self",
              pattern: "found `&)` near method receiver",
              fix_template: "replace receiver",
            },
          ],
          metadata: { source_role: "resident_agi" },
        },
      ],
    },
  },
  refresh: vi.fn(),
  isActing: vi.fn(() => false),
  start: vi.fn(),
  stop: vi.fn(),
  tick: vi.fn(),
  saveIdentity: vi.fn(),
  createGoal: vi.fn(async () => ({ goal_id: "goal-new" })),
  approveGoal: vi.fn(async () => null),
  rejectGoal: vi.fn(async () => null),
  materializeGoal: vi.fn(async () => null),
  stageGoal: vi.fn(async () => null),
  runGoal: vi.fn(async () => null),
  recordDecision: vi.fn(async () => ({ decision_id: "decision-console" })),
  executeAgiAction: vi.fn(async () => ({
    schema_version: "resident.agi_tactical_action_result.v1",
    action_id: "request_director_controlled_repair",
    status: "executed",
    reason: "created governed Resident goal and recorded decision trace",
    goal: {
      goal_id: "goal-repair",
      title: "请求 Director 受控修复当前阻塞",
    },
    decision: { decision_id: "decision-console" },
    tool_trace: {
      schema_version: "resident.agi_tactical_action_tool_trace.v1",
      items: [
        {
          step_id: "resident.goal_governance.commands",
          label: "Resident 目标治理",
          mode: "write_through_resident_contract",
          status: "executed",
          contract: "resident.goal_governance.commands",
          summary: "已创建待治理目标；没有直接调用 Director 修复。",
        },
        {
          step_id: "resident.decision_trace.write",
          label: "写入决策轨迹",
          mode: "write_through_resident_contract",
          status: "recorded",
          contract: "resident.decision_trace",
          summary: "已记录 AGI 战术动作和治理链路。",
        },
      ],
    },
    follow_up_actions: [
      {
        action_id: "open_goals_tab",
        label: "查看治理目标",
        mode: "local_navigation",
        status: "available",
        reason: "打开 Resident 目标队列。",
        ui_handler: "open_goals_tab",
      },
      {
        action_id: "request_resident_agi_judgement",
        label: "请求 AGI 复核",
        mode: "execute_through_role_runtime",
        status: "preview_only",
        reason: "让 resident_agi 角色回合复核下一步。",
        ui_handler: "execute_governed_action",
      },
    ],
    receipt: {
      status: "EXECUTED",
      title: "受控动作执行凭证",
      summary:
        "已通过 Resident public contract 创建目标并写入 decision trace。",
      rows: [
        { label: "目标", value: "goal-repair" },
        { label: "决策", value: "decision-console" },
        { label: "动作", value: "request_director_controlled_repair" },
        { label: "角色链", value: "PM→CE→Director→QA preserved" },
      ],
    },
  })),
  runAgiDecision: vi.fn(async () => null),
  chatAgi: vi.fn(async () => ({
    schema_version: "resident.agi_tactical_chat.v1",
    intent: "status_summary",
    status: "ready",
    message: "后端 AGI 已读取 Polaris 元项目事实源。",
    flow: [
      "[事实源] resident.status + resident.agi_audit_pack.v1",
      "[边界] 受控动作必须进入 PM → Chief Engineer → Director → QA",
    ],
    mission_brief: {
      schema_version: "resident.agi_tactical_mission_brief.v1",
      title: "项目态势",
      severity: "warn",
      status_label: "受限值守",
      progress_percent: 45,
      current_focus: "Stabilize PM contract quality",
      current_stage: "goal_staging",
      latest_verdict: "success",
      blockers: ["证据门禁为 hold，等待补齐。"],
      next_actions: ["请求 AGI 角色回合判断下一步。"],
      metrics: [
        { label: "目标", value: "2" },
        { label: "决策", value: "1" },
        { label: "证据", value: "2/4" },
        { label: "门禁", value: "pass/hold" },
      ],
    },
    tool_trace: {
      schema_version: "resident.agi_tactical_tool_trace.v1",
      items: [
        {
          step_id: "resident.status.read",
          label: "Resident 状态投影",
          mode: "read_only",
          status: "available",
          contract: "resident.autonomy.public.query_resident_status",
          summary: "读取 runtime、目标、决策、身份和 agenda 快照。",
        },
        {
          step_id: "resident.agi_controlled_actions.boundary",
          label: "受控动作边界",
          mode: "controlled_action",
          status: "blocked",
          contract: "resident.agi_tactical_chat_participation.v1",
          summary: "configured participation scopes do not cover this intent",
        },
      ],
      summary: {
        total: 2,
        direct_execution_allowed: false,
      },
    },
    participation_gate: {
      schema_version: "resident.agi_tactical_participation_gate.v1",
      status: "disabled",
      enabled: false,
      allowed_for_intent: false,
      intent: "status_summary",
      summary: "AGI 参与总开关关闭；只允许只读解释和本地导航。",
      required_scope_ids: ["capability_surface"],
      configured_scope_ids: [],
      missing_scope_ids: ["capability_surface"],
      settings_action_available: false,
      governed_actions_available: false,
      agi_direct_permission_change_allowed: false,
    },
    decision_route: {
      schema_version: "resident.agi_tactical_decision_route.v1",
      source: "resident.autonomy.internal.agi_tactical_chat",
      intent: "status_summary",
      route_status: "read_only_explanation",
      route_reason:
        "AGI participation does not permit an execution-impacting recommendation",
      recommended_action_ids: [
        "open_evidence_black_box",
        "refresh_evidence_interfaces",
      ],
      read_only_action_ids: [
        "open_evidence_black_box",
        "refresh_evidence_interfaces",
      ],
      governed_action_ids: [],
      blocked_reasons: ["resident_agi_participation.enabled is false"],
      hard_rules: { status: "pass", llm_override_allowed: false },
      governed_execution: {
        allowed: false,
        agi_direct_execution_allowed: false,
      },
    },
    suggested_actions: [
      {
        action_id: "open_evidence_black_box",
        label: "查看证据黑匣子",
        mode: "read_only",
        status: "available",
        reason: "查看审计证据",
        requires_participation: false,
      },
      {
        action_id: "refresh_evidence_interfaces",
        label: "刷新证据",
        mode: "read_only",
        status: "available",
        reason: "刷新证据接口是只读动作。",
        ui_handler: "refresh_evidence_interfaces",
        requires_participation: false,
      },
    ],
    receipt: {
      status: "READ",
      title: "战术问答凭证",
      summary: "已通过 Resident public contract 组合答复。",
      rows: [
        { label: "意图", value: "status_summary" },
        { label: "事实源", value: "resident.autonomy.public" },
      ],
    },
  })),
  refreshAgiEvidenceInterfaces: vi.fn(async () => null),
  extractSkills: vi.fn(async () => null),
  runExperiments: vi.fn(async () => null),
  runImprovements: vi.fn(async () => null),
};

vi.mock("@/hooks/useResident", () => ({
  useResident: () => mockResidentState,
}));

describe("ResidentWorkspace", () => {
  beforeEach(() => {
    Object.values(mockResidentState)
      .filter((value) => typeof value === "function" && "mockClear" in value)
      .forEach((fn) => (fn as ReturnType<typeof vi.fn>).mockClear());
  });

  it("renders the AGI workspace shell", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    expect(screen.getByText("AGI 工作区")).toBeInTheDocument();
    expect(screen.getByText("Resident AGI Supervisor")).toBeInTheDocument();
    expect(screen.getByTestId("agi-cockpit-overview")).toHaveTextContent(
      "驻场 AGI",
    );
    expect(screen.getByTestId("agi-tactical-console")).toHaveTextContent(
      "战术控制台",
    );
    expect(screen.getByTestId("agi-cockpit-overview")).toHaveTextContent(
      "受限值守",
    );
    expect(screen.getByTestId("agi-cockpit-overview")).toHaveTextContent("1/2");
    expect(screen.getByTestId("agi-cockpit-overview")).toHaveTextContent(
      "Run Ledger projection is not available yet.",
    );
    expect(screen.getByTestId("agi-cockpit-overview")).toHaveTextContent(
      "1 个必需证据接口尚未满足。",
    );
    expect(screen.getByTestId("agi-action-timeline")).toHaveTextContent(
      "最近行动轨迹",
    );
    expect(screen.getByTestId("agi-action-timeline")).toHaveTextContent(
      "等待用户指令",
    );
    expect(screen.getByTestId("agi-role-track-pm")).toHaveTextContent(
      "目标就绪",
    );
    expect(screen.getByTestId("agi-role-track-ce")).toHaveTextContent(
      "等待蓝图",
    );
    expect(screen.getByTestId("agi-role-track-director")).toHaveTextContent(
      "待受控执行",
    );
    expect(screen.getByTestId("agi-role-track-qa")).toHaveTextContent(
      "请求证据",
    );
    expect(
      screen.queryByTestId("resident-runtime-evidence"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("AGI 角色能力面")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agi-toggle-advanced-audit"));

    expect(screen.getByTestId("resident-runtime-evidence")).toHaveTextContent(
      "resident.runtime_projection_evidence.v1",
    );
    expect(screen.getByTestId("resident-runtime-evidence")).toHaveTextContent(
      "runtime.v2.status.resident",
    );
    expect(screen.getByTestId("resident-runtime-evidence")).toHaveTextContent(
      "runtime.v2.status.snapshot",
    );
    expect(screen.getByTestId("resident-runtime-evidence")).toHaveTextContent(
      "snapshot.resident",
    );
    expect(screen.getByTestId("resident-runtime-evidence")).toHaveTextContent(
      "runtime.v2_snapshot+http_details",
    );
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("resident.tick_autonomy_boundary.v1");
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("轮次角色：deterministic_evidence_producer");
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("判断入口：resident_agi_decision_turn");
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("旁路模型：已阻断");
    expect(screen.getByText("最新元认知")).toBeInTheDocument();
    expect(screen.getByText("Task decomposition")).toBeInTheDocument();
    expect(screen.getByText("AGI 角色能力面")).toBeInTheDocument();
    expect(
      screen.getByText("Resident AGI role decision turn"),
    ).toBeInTheDocument();
    expect(screen.getByText("Canonical role registry")).toBeInTheDocument();
    expect(
      screen.getAllByText("Final provider-request audit").length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByTestId("resident-agi-role-foundation"),
    ).toHaveTextContent("resident_agi");
    expect(
      screen.getByTestId("resident-agi-role-foundation"),
    ).toHaveTextContent("RoleRuntime / ContextOS / TurnEngine");
    expect(
      screen.getByTestId("resident-agi-role-foundation"),
    ).toHaveTextContent("权威契约");
    expect(
      screen.getByTestId("resident-agi-role-foundation"),
    ).toHaveTextContent("项目经理 → 总工程师 → 执行官");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("能力治理矩阵");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("项目经理 → 总工程师 → 执行官");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("受控操作");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("高风险");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("resident.agi_authority_matrix.v1");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("硬规则 2");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("AGI 判断 2");
    expect(
      screen.getByTestId("resident-agi-capability-access-registry"),
    ).toHaveTextContent("resident.agi_capability_access_registry.v1");
    expect(
      screen.getByTestId("resident-agi-capability-access-registry"),
    ).toHaveTextContent("直接工具 已阻断");
    expect(
      screen.getByTestId("resident-agi-capability-access-registry"),
    ).toHaveTextContent("直接写入 已阻断");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("control_plane.verifier_execution");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("director_repair:r3/g0");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("audit:r1/g1");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("canonical_role_chain_only");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("platform_enforced_non_overridable");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog"),
    ).toHaveTextContent("Director 确定性修复策略目录");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog"),
    ).toHaveTextContent("director.deterministic_repair_strategy_catalog.v1");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog"),
    ).toHaveTextContent("director.runtime.repair_kernel.strategy_catalog");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog-summary"),
    ).toHaveTextContent("director_authorized_tools_only");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog-summary"),
    ).toHaveTextContent("项目经理 → 总工程师 → 执行官");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog-summary"),
    ).toHaveTextContent("AGI 执行：已阻断");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog-summary"),
    ).toHaveTextContent("fail_closed_high_risk");
    expect(
      screen.getAllByTestId("resident-agi-repair-strategy-catalog-item")[0],
    ).toHaveTextContent("deterministic_typescript_missing_export_repair");
    expect(
      screen.getAllByTestId("resident-agi-repair-strategy-catalog-item")[0],
    ).toHaveTextContent("typescript");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent("AGI 修复建议边界");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent("director.repair_advisory_policy.v1");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent("建议规则 允许");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent(/写入\s*已阻断/);
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent("允许字段：pattern");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-policy"),
    ).toHaveTextContent("禁止字段：write_file");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-overlay"),
    ).toHaveTextContent("AGI 修复建议覆盖层");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-overlay"),
    ).toHaveTextContent("就绪");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-overlay"),
    ).toHaveTextContent("可注入");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-overlay"),
    ).toHaveTextContent("规则");
    expect(
      screen.getByTestId("resident-agi-repair-advisory-overlay"),
    ).toHaveTextContent("仅建议：是");
    expect(
      screen.queryByRole("button", { name: /修复|执行/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("AGI 决策能力注册表");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("resident.agi_decision_capability.v1");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("Platform invariant enforcement");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("Evidence interface selection");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("Goal promotion readiness");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("public_cell_contracts_only");
    expect(
      screen.getByTestId("resident-agi-decision-capability-registry"),
    ).toHaveTextContent("verifier.policy.read");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("AGI 证据接口矩阵");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-contract"),
    ).toHaveTextContent("resident.agi_evidence_interface_contract.v1");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-contract"),
    ).toHaveTextContent("必需 4");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-contract"),
    ).toHaveTextContent("缺失 0");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("Audit diagnosis trail");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("Audit verdict runner");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("Context catalog search");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("Director hard-coded repair strategy catalog");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("director.deterministic_repair_strategy_catalog.v1");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("Verifier execution request");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("control_plane.verifier_execution");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-matrix"),
    ).toHaveTextContent("execute_through_control_plane_contract");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("AGI 证据接口可用性");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("resident.agi_evidence_interfaces.v1");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("quality_gate_response");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("Run Ledger projection");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("audit.verdict.public.query_audit_verdict");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("use_audit_verdict_snapshot");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("Director repair diagnostic coverage");
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent(
      "director.runtime.public.query_director_repair_coverage",
    );
    expect(
      screen.getByTestId("resident-agi-evidence-interface-readiness"),
    ).toHaveTextContent("Director AGI repair advisory policy");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("resident.agi_evidence_capability_matrix.v1");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("必需 1/2");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("推荐 4");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("Run ledger");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("Director repair");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("仅建议：是");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("权威：否");
    expect(
      screen.getByTestId("resident-agi-evidence-runtime-matrix"),
    ).toHaveTextContent("AGI 执行：已阻断");
    expect(screen.getAllByText("风险 高").length).toBeGreaterThan(1);
    expect(
      screen.getByText("No shortcut from PM directly to Director."),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("AGI 决策边界");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("平台硬规则");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("AGI 智能判断");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("AGI 受控执行");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("Architecture and dependency choice");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("Audit and evidence interface selection");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("AuditDiagnosisResultV1");
    expect(
      screen.getByTestId("resident-agi-decision-boundaries"),
    ).toHaveTextContent("final_request_context_audit");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("AGI 决策边界策略");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("resident.agi_decision_boundary_policy.v1");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("platform_hard_rule");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("LLM：已阻断");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("agi_recommendation");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("执行：仅建议");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("AGI 直接写入：已阻断");
    expect(
      screen.getByTestId("resident-agi-decision-boundary-policy"),
    ).toHaveTextContent("Director 权威：保留");
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "AGI 审计包",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "resident.agi_audit_pack.v1",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "硬规则门禁 通过",
    );
    expect(
      screen.getByTestId("resident-agi-audit-authority-matrix"),
    ).toHaveTextContent("resident.agi_authority_matrix.v1");
    expect(
      screen.getByTestId("resident-agi-audit-authority-matrix"),
    ).toHaveTextContent("受控操作 2");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("resident.agi_director_repair_contract.v1");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director.runtime");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director_authorized_tools_only");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("项目经理 → 总工程师 → 执行官");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("fail_closed_high_risk");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("AGI 执行：已阻断");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("写入：已阻断");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("建议：已激活");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director.deterministic_repair_strategy_catalog.v1");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director.deterministic_repair_profile_summary.v1");
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "暂缓 → 请求证据",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "运行账本 待处理",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "LLM 覆盖：已阻断",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "resident.agi_decision_turn.execute",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "Resident tick/labs are deterministic evidence producers, not AGI judgement turns.",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "PM → Chief Engineer → Director",
    );
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("AGI 执行画像");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("resident.agi_decision_profile.v1");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("角色回合 允许");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("hold_for_evidence");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("request_missing_contextos_or_run_ledger_evidence");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("动作：请求证据");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("preserve_pm_chief_engineer_director_qa_chain");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("resident_tick_is_deterministic_evidence_only");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent(
      "execution_impacting_agi_judgement_requires_runtime_contract_gate",
    );
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("证据接口");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("Final provider-request audit");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("Run Ledger projection");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("Verifier execution request");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("control_plane.verifier_execution");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("Request missing evidence before continuing.");
  });

  it("runs tactical console commands through governed Resident actions", async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /刷新证据/ }));

    await waitFor(() => {
      expect(
        mockResidentState.refreshAgiEvidenceInterfaces,
      ).toHaveBeenCalledWith("evidence.interface.selection");
    });
    expect(screen.getByText("[EXECUTED]")).toBeInTheDocument();
    expect(screen.getAllByText("证据刷新凭证").length).toBeGreaterThan(0);
    expect(screen.getByText("read_only_public_contract")).toBeInTheDocument();
  });

  it("routes tactical console questions through the Resident AGI chat contract", async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "检查进度" }));

    await waitFor(() => {
      expect(mockResidentState.chatAgi).toHaveBeenCalledWith(
        expect.objectContaining({
          message: "/检查进度",
          decision_type: "evidence.interface.selection",
        }),
      );
    });
    expect(
      screen.getByText("后端 AGI 已读取 Polaris 元项目事实源。"),
    ).toBeInTheDocument();
    expect(screen.getByText("项目态势")).toBeInTheDocument();
    expect(
      screen.getAllByText("Stabilize PM contract quality").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("证据门禁为 hold，等待补齐。")).toBeInTheDocument();
    expect(
      screen.getByText("请求 AGI 角色回合判断下一步。"),
    ).toBeInTheDocument();
    expect(screen.getByText("指令流")).toBeInTheDocument();
    expect(screen.getByText("Resident 状态投影")).toBeInTheDocument();
    expect(screen.getByText("受控动作边界")).toBeInTheDocument();
    expect(screen.getByTestId("agi-decision-route")).toHaveTextContent(
      "决策路线",
    );
    expect(screen.getByTestId("agi-decision-route")).toHaveTextContent(
      "read_only_explanation",
    );
    expect(screen.getByTestId("agi-decision-route")).toHaveTextContent(
      "open_evidence_black_box",
    );
    expect(screen.getByText("[READ]")).toBeInTheDocument();
    expect(screen.getByText("resident.autonomy.public")).toBeInTheDocument();
    const actionTimeline = screen.getByTestId("agi-action-timeline");
    expect(actionTimeline).toHaveTextContent("战术问答凭证");
    expect(actionTimeline).toHaveTextContent(
      "resident.agi_tactical_tool_trace.v1",
    );
    expect(actionTimeline).toHaveTextContent("open_evidence_black_box");
  });

  it("keeps tactical quick commands focused on the current AGI state", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    const quickCommandBar = screen.getByTestId("agi-quick-command-bar");
    expect(
      within(quickCommandBar).getByRole("button", { name: "检查进度" }),
    ).toBeInTheDocument();
    expect(
      within(quickCommandBar).getByRole("button", { name: "解释卡住" }),
    ).toBeInTheDocument();
    expect(
      within(quickCommandBar).getByRole("button", { name: /刷新证据/ }),
    ).toBeInTheDocument();
    expect(
      within(quickCommandBar).getByRole("button", { name: "刷新证据接口" }),
    ).toBeInTheDocument();
    expect(
      within(quickCommandBar).queryByRole("button", {
        name: "请求 AGI 判断",
      }),
    ).not.toBeInTheDocument();
  });

  it("shows an AGI judgement quick command when participation and model binding are ready", async () => {
    const identityRecord = mockResidentState.residentIdentity as Record<
      string,
      unknown
    >;
    const previousParticipation = identityRecord.resident_agi_participation;
    identityRecord.resident_agi_participation = {
      enabled: true,
      scopes: ["quality_gate_response"],
      participation: { quality_gate_response: true },
    };

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
          residentAgiLlmStatus={{
            ready: true,
            providerId: "openai",
            providerName: "OpenAI",
            model: "gpt-5",
          }}
        />,
      );

      fireEvent.click(
        within(screen.getByTestId("agi-quick-command-bar")).getByRole(
          "button",
          {
            name: "请求 AGI 判断",
          },
        ),
      );

      await waitFor(() => {
        expect(mockResidentState.chatAgi).toHaveBeenCalledWith(
          expect.objectContaining({
            message: "请让 AGI 基于当前证据判断下一步怎么办。",
            decision_type: "evidence.interface.selection",
          }),
        );
      });
    } finally {
      if (previousParticipation === undefined) {
        delete identityRecord.resident_agi_participation;
      } else {
        identityRecord.resident_agi_participation = previousParticipation;
      }
    }
  });

  it("creates a governed Resident goal from a tactical repair action", async () => {
    mockResidentState.chatAgi.mockResolvedValueOnce({
      schema_version: "resident.agi_tactical_chat.v1",
      intent: "director_repair_request",
      status: "ready",
      message: "已整理为受控修复预案。",
      flow: ["[边界] 受控动作必须进入 PM → Chief Engineer → Director → QA"],
      suggested_actions: [
        {
          action_id: "request_director_controlled_repair",
          label: "请求 Director 受控修复",
          mode: "controlled_execution",
          status: "preview_only",
          reason: "进入 Resident goal governance",
          endpoint: "/v2/resident/goals",
          ui_handler: "execute_governed_action",
          capability_id: "resident.goal_governance.commands",
          contract_ref: "resident.goal_governance.commands",
          risk_level: "high",
          requires_participation: true,
          agi_direct_execution_allowed: false,
          goal_draft: {
            goal_type: "maintenance",
            title: "请求 Director 受控修复当前阻塞",
            motivation: "需要通过受控链路修复失败门禁。",
            source: "resident_agi_tactical_console",
            scope: ["resident.agi_tactical_chat", "director.controlled_repair"],
            evidence_refs: ["run_ledger.read"],
            derived_from: ["runtime/contexts/context-1"],
            budget: {
              handoff_chain: "PM → Chief Engineer → Director → QA",
              agi_direct_repair_allowed: false,
            },
            expected_value: 0.72,
            risk_score: 0.42,
          },
        },
      ],
      receipt: {
        status: "READ",
        title: "战术问答凭证",
        summary: "已生成受控动作草案。",
        rows: [{ label: "意图", value: "director_repair_request" }],
      },
    });
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("给驻场 AGI 下达指令"), {
      target: { value: "交给 Director 修复这个阻塞" },
    });
    fireEvent.click(screen.getByTestId("agi-console-submit"));

    const actionButton = await screen.findByRole("button", {
      name: "请求 Director 受控修复",
    });
    fireEvent.click(actionButton);
    expect(mockResidentState.executeAgiAction).not.toHaveBeenCalled();
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "受控动作确认",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "resident.goal_governance.commands",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "参与开关",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "必需",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "AGI 直接执行：已阻断",
    );
    fireEvent.click(screen.getByRole("button", { name: "提交受控动作" }));

    await waitFor(() => {
      expect(mockResidentState.executeAgiAction).toHaveBeenCalledWith(
        expect.objectContaining({
          message: "交给 Director 修复这个阻塞",
          action_id: "request_director_controlled_repair",
          decision_type: "evidence.interface.selection",
          evidence_refs: ["runtime/contracts/plan.md"],
          context_refs: ["runtime/contexts/abc123"],
        }),
      );
    });
    expect(mockResidentState.createGoal).not.toHaveBeenCalled();
    expect(mockResidentState.recordDecision).not.toHaveBeenCalled();
    expect(screen.getAllByText("受控动作执行凭证").length).toBeGreaterThan(0);
    expect(screen.getByText("goal-repair")).toBeInTheDocument();
    expect(screen.getByText("decision-console")).toBeInTheDocument();
    expect(screen.getByText("PM→CE→Director→QA preserved")).toBeInTheDocument();
    expect(screen.getByText("Resident 目标治理")).toBeInTheDocument();
    expect(screen.getAllByText("写入决策轨迹").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "请求 AGI 复核" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "查看治理目标" }));
    expect(
      screen.getByRole("button", { name: "新建目标" }),
    ).toBeInTheDocument();
  });

  it("opens AGI operator settings from a tactical permission action without executing backend writes", async () => {
    mockResidentState.chatAgi.mockResolvedValueOnce({
      schema_version: "resident.agi_tactical_chat.v1",
      intent: "director_repair_request",
      status: "ready",
      message:
        "当前 AGI 参与范围不允许这个意图，我只能给出只读解释并提供设定入口。",
      participation_gate: {
        schema_version: "resident.agi_tactical_participation_gate.v1",
        status: "disabled",
        enabled: false,
        allowed_for_intent: false,
        intent: "director_repair_request",
        summary: "AGI 参与总开关关闭；只允许只读解释和本地导航。",
        required_scope_ids: ["director_repair_advisory_policy"],
        configured_scope_ids: [],
        missing_scope_ids: ["director_repair_advisory_policy"],
        settings_action_available: true,
        governed_actions_available: false,
        agi_direct_permission_change_allowed: false,
      },
      suggested_actions: [
        {
          action_id: "open_operator_settings",
          label: "打开值守设定",
          mode: "local_navigation",
          status: "available",
          reason: "打开常驻 AGI 参与范围设置，不自动修改权限。",
          ui_handler: "open_operator_settings",
          capability_id: "resident.agi_participation_policy.read",
          contract_ref: "resident.workspace.local_operator_settings",
          requires_participation: false,
          agi_direct_execution_allowed: false,
        },
      ],
      receipt: {
        status: "READ",
        title: "战术问答凭证",
        summary: "已生成本地设定入口。",
        rows: [{ label: "意图", value: "director_repair_request" }],
      },
    });
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    expect(screen.queryByTestId("agi-operator-settings")).toBeNull();
    fireEvent.change(screen.getByLabelText("给驻场 AGI 下达指令"), {
      target: { value: "交给 Director 修复这个阻塞" },
    });
    fireEvent.click(screen.getByTestId("agi-console-submit"));

    const gate = await screen.findByTestId("agi-participation-gate");
    expect(gate).toHaveTextContent("权限闸门");
    expect(gate).toHaveTextContent("已停用");
    expect(gate).toHaveTextContent("Director 修复建议边界");
    expect(gate).toHaveTextContent("可打开");
    fireEvent.click(
      await screen.findByRole("button", { name: "打开值守设定" }),
    );

    expect(screen.getByTestId("agi-operator-settings")).toBeInTheDocument();
    expect(mockResidentState.executeAgiAction).not.toHaveBeenCalled();
    expect(mockResidentState.saveIdentity).not.toHaveBeenCalled();
  });

  it("dispatches tactical actions by registry handler instead of fixed action ids", async () => {
    mockResidentState.chatAgi.mockResolvedValueOnce({
      schema_version: "resident.agi_tactical_chat.v1",
      intent: "resident_agi_judgement",
      status: "ready",
      message: "后端 registry 提供了一个受控动作。",
      suggested_actions: [
        {
          action_id: "registry_defined_governed_action",
          label: "执行 Registry 动作",
          mode: "controlled_execution",
          status: "preview_only",
          reason: "由后端 action catalog 声明为受控执行。",
          endpoint: "/v2/resident/agi/actions/execute",
          ui_handler: "execute_governed_action",
          capability_id: "resident.agi_decision_turn.execute",
          contract_ref:
            "resident.autonomy.public.run_resident_agi_decision_turn",
          risk_level: "medium",
          requires_participation: true,
          agi_direct_execution_allowed: false,
        },
      ],
      receipt: {
        status: "READ",
        title: "战术问答凭证",
        summary: "已生成 registry 动作。",
        rows: [
          {
            label: "动作目录",
            value: "resident.agi_tactical_action_catalog.v1",
          },
        ],
      },
    });

    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("给驻场 AGI 下达指令"), {
      target: { value: "请执行后端动作目录里的受控动作" },
    });
    fireEvent.click(screen.getByTestId("agi-console-submit"));

    fireEvent.click(
      await screen.findByRole("button", { name: "执行 Registry 动作" }),
    );
    expect(mockResidentState.executeAgiAction).not.toHaveBeenCalled();
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "受控动作确认",
    );
    fireEvent.click(screen.getByRole("button", { name: "提交受控动作" }));

    await waitFor(() => {
      expect(mockResidentState.executeAgiAction).toHaveBeenCalledWith(
        expect.objectContaining({
          action_id: "registry_defined_governed_action",
          message: "请执行后端动作目录里的受控动作",
        }),
      );
    });
  });

  it("runs a Resident AGI judgement action through the tactical console", async () => {
    mockResidentState.chatAgi.mockResolvedValueOnce({
      schema_version: "resident.agi_tactical_chat.v1",
      intent: "resident_agi_judgement",
      status: "ready",
      message: "可以提交给 resident_agi 做受控判断。",
      flow: ["[角色] resident_agi role runtime + ContextOS + TurnEngine"],
      suggested_actions: [
        {
          action_id: "request_resident_agi_judgement",
          label: "请求 AGI 判断",
          mode: "execute_through_role_runtime",
          status: "preview_only",
          reason: "进入 resident_agi 角色回合",
          endpoint: "/v2/resident/agi/actions/execute",
          requires_participation: true,
        },
      ],
      receipt: {
        status: "READ",
        title: "战术问答凭证",
        summary: "已生成 AGI 判断动作草案。",
        rows: [{ label: "意图", value: "resident_agi_judgement" }],
      },
    });
    mockResidentState.executeAgiAction.mockResolvedValueOnce({
      schema_version: "resident.agi_tactical_action_result.v1",
      action_id: "request_resident_agi_judgement",
      status: "executed",
      reason:
        "ran Resident AGI judgement through the shared role runtime contract",
      goal: null,
      decision: {
        decision_id: "decision-agi-judgement",
        verdict: "request_evidence",
      },
      role_result: { ok: true },
      follow_up_actions: [
        {
          action_id: "refresh_evidence_interfaces",
          label: "刷新证据",
          mode: "read_only",
          status: "available",
          reason: "AGI 判断需要更多证据。",
        },
      ],
      tool_trace: {
        schema_version: "resident.agi_tactical_action_tool_trace.v1",
        items: [
          {
            step_id: "resident.agi_decision_turn.execute",
            label: "AGI 判断回合",
            mode: "execute_through_role_runtime",
            status: "executed",
            contract: "resident.autonomy.public.run_resident_agi_decision_turn",
            summary: "resident_agi 角色回合产出 request_evidence 判断。",
          },
          {
            step_id: "resident.decision_trace.write",
            label: "写入决策轨迹",
            mode: "write_through_resident_contract",
            status: "recorded",
            contract: "resident.decision_trace",
            summary: "判断结果已进入 Resident decision trace。",
          },
        ],
      },
      receipt: {
        status: "JUDGED",
        title: "AGI 判断凭证",
        summary: "已通过 resident_agi 角色回合完成受控判断。",
        rows: [
          { label: "结论", value: "request_evidence" },
          { label: "决策", value: "decision-agi-judgement" },
          { label: "动作", value: "request_resident_agi_judgement" },
          { label: "角色回合", value: "resident_agi" },
        ],
      },
    });
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.change(screen.getByLabelText("给驻场 AGI 下达指令"), {
      target: { value: "请判断下一步怎么办" },
    });
    fireEvent.click(screen.getByTestId("agi-console-submit"));

    const actionButton = await screen.findByRole("button", {
      name: "请求 AGI 判断",
    });
    fireEvent.click(actionButton);
    expect(mockResidentState.executeAgiAction).not.toHaveBeenCalled();
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "受控动作确认",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "execute_through_role_runtime",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "参与开关",
    );
    expect(screen.getByTestId("agi-action-confirmation")).toHaveTextContent(
      "必需",
    );
    fireEvent.click(screen.getByRole("button", { name: "提交受控动作" }));

    await waitFor(() => {
      expect(mockResidentState.executeAgiAction).toHaveBeenCalledWith(
        expect.objectContaining({
          message: "请判断下一步怎么办",
          action_id: "request_resident_agi_judgement",
          decision_type: "evidence.interface.selection",
          evidence_refs: ["runtime/contracts/plan.md"],
          context_refs: ["runtime/contexts/abc123"],
        }),
      );
    });
    expect(mockResidentState.createGoal).not.toHaveBeenCalled();
    expect(screen.getAllByText("AGI 判断凭证").length).toBeGreaterThan(0);
    expect(screen.getByText("decision-agi-judgement")).toBeInTheDocument();
    expect(screen.getByText("resident_agi")).toBeInTheDocument();
    expect(screen.getByText("AGI 判断回合")).toBeInTheDocument();
    expect(screen.getAllByText("写入决策轨迹").length).toBeGreaterThan(0);
  });

  it("creates a goal from the AGI console", async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    expect(screen.getByText("目标生成台")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("目标标题"), {
      target: { value: "Tighten Director retries" },
    });
    fireEvent.change(screen.getByLabelText("目标描述"), {
      target: { value: "Retry storms are causing noise." },
    });
    fireEvent.click(screen.getByRole("button", { name: /创建 AGI 目标/i }));

    await waitFor(() => {
      expect(mockResidentState.createGoal).toHaveBeenCalledTimes(1);
    });
    expect(mockResidentState.createGoal).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Tighten Director retries" }),
    );
  });

  it("governs and runs approved goals", async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText("Stabilize PM contract quality"));
    expect(screen.getByRole("button", { name: "暂存" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "暂存" }));
    fireEvent.click(screen.getByRole("button", { name: "写入 PM" }));
    fireEvent.click(screen.getByRole("button", { name: "交给 PM" }));

    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(
      1,
      "goal-approved",
      false,
    );
    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(
      2,
      "goal-approved",
      true,
    );
    expect(mockResidentState.runGoal).toHaveBeenCalledWith(
      "goal-approved",
      false,
      1,
    );
  });

  it("triggers a reflection tick from the header", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId("resident-tick"));
    expect(mockResidentState.tick).toHaveBeenCalledTimes(1);
  });

  it("surfaces evolution lab actions", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId("resident-tab-evolution"));
    expect(screen.getByText(/技能工坊/)).toBeInTheDocument();
    expect(screen.getByText(/反事实实验/)).toBeInTheDocument();
    expect(screen.getByText(/自改提案/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("resident-extract-skills"));
    fireEvent.click(screen.getByTestId("resident-run-experiments"));
    fireEvent.click(screen.getByTestId("resident-run-improvements"));

    expect(mockResidentState.extractSkills).toHaveBeenCalledTimes(1);
    expect(mockResidentState.runExperiments).toHaveBeenCalledTimes(1);
    expect(mockResidentState.runImprovements).toHaveBeenCalledTimes(1);
  });

  it("surfaces the AGI decision audit timeline", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="decisions"
      />,
    );

    expect(screen.getByText("决策审计面")).toBeInTheDocument();
    expect(
      screen.getByText("唯一事实源：decision_trace.jsonl"),
    ).toBeInTheDocument();
    expect(screen.getByText("resident.decision_event.v1")).toBeInTheDocument();
    expect(screen.getAllByText("运行时").length).toBeGreaterThan(0);
    expect(
      screen.getByText("项目经理 → 总工程师 → 执行官"),
    ).toBeInTheDocument();
    expect(screen.getByText("validation_passed")).toBeInTheDocument();
    expect(screen.getByText("bounded decomposition")).toBeInTheDocument();
    expect(screen.getByText("分数 91%")).toBeInTheDocument();
    expect(screen.getByText("运行时契约：通过")).toBeInTheDocument();
    expect(
      screen.getByText("运行时：roles.runtime.execute_role_session"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("AGI 画像：resident.agi_decision_profile.v1"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("AGI 决策：goal.promotion.readiness"),
    ).toBeInTheDocument();
    expect(screen.getByText("证据接口：run_ledger.read")).toBeInTheDocument();
    expect(
      screen.getByText(/证据引用：runtime\/contracts\/plan.md/),
    ).toBeInTheDocument();
    expect(screen.getByText(/符号：record_decision/)).toBeInTheDocument();
  });

  it("runs a governed AGI decision turn from the decisions tab", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="decisions"
      />,
    );

    expect(screen.getByTestId("resident-agi-decision-turn")).toHaveTextContent(
      "AGI 决策回合",
    );
    expect(
      screen.getByTestId("resident-agi-decision-turn-profile"),
    ).toHaveTextContent("AGI 执行画像");
    expect(
      screen.getByTestId("resident-agi-decision-handoff"),
    ).toHaveTextContent("resident.agi_decision_handoff.v1");
    expect(
      screen.getByTestId("resident-agi-decision-handoff"),
    ).toHaveTextContent("chief_engineer → director → qa");
    expect(
      screen.getByTestId("resident-agi-decision-handoff"),
    ).toHaveTextContent("AGI 执行");
    expect(
      screen.getByTestId("resident-agi-decision-handoff"),
    ).toHaveTextContent("已阻断：director_tool_execution_by_agi");
    expect(screen.getByTestId("resident-agi-handoff-inbox")).toHaveTextContent(
      "resident.agi_handoff_inbox.v1",
    );
    expect(screen.getByTestId("resident-agi-handoff-inbox")).toHaveTextContent(
      "1 个交接",
    );
    expect(screen.getByTestId("resident-agi-handoff-inbox")).toHaveTextContent(
      "chief_engineer → director → qa",
    );
    expect(
      screen.getByTestId("resident-agi-decision-turn-profile"),
    ).toHaveTextContent("动作：请求证据");
    expect(screen.getByLabelText("AGI 决策类型")).toHaveValue(
      "evidence.interface.selection",
    );
    fireEvent.change(screen.getByLabelText("AGI 决策类型"), {
      target: { value: "director.repair.advisory" },
    });
    expect(
      screen.getByTestId("resident-agi-selected-decision-meta"),
    ).toHaveTextContent("director.repair.advisory");
    expect(
      screen.getByTestId("resident-agi-selected-decision-meta"),
    ).toHaveTextContent("风险：中");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("当前决策证据预检");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("契约兜底");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("运行态证据已过期：quality_gate_response");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("director.deterministic_repair_strategy_catalog.read");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("director.repair_coverage.read");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("director.repair_advisory_policy.read");
    expect(
      screen.getByTestId("resident-agi-selected-decision-evidence"),
    ).toHaveTextContent("audit.diagnosis.read");
    fireEvent.click(
      screen.getByTestId("resident-refresh-agi-evidence-interfaces"),
    );
    expect(mockResidentState.refreshAgiEvidenceInterfaces).toHaveBeenCalledWith(
      "director.repair.advisory",
    );
    fireEvent.change(screen.getByLabelText("AGI 决策目标"), {
      target: { value: "Decide whether the current run can proceed." },
    });
    fireEvent.click(screen.getByTestId("resident-run-agi-decision"));

    expect(mockResidentState.runAgiDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        decision_type: "director.repair.advisory",
        objective: "Decide whether the current run can proceed.",
        candidate_actions: expect.arrayContaining([
          "suggest_repair_rule",
          "request_evidence",
          "block",
          "escalate",
          "continue",
        ]),
        constraints: expect.arrayContaining([
          "preserve_pm_chief_engineer_director_qa_chain",
          "repair_rule_suggestions_are_non_authoritative",
          "suggested_rules_must_pass_advisory_policy",
          "resident_agi_role_runtime_required",
          "contextos_expected",
          "turn_engine_expected",
        ]),
        evidence_refs: ["runtime/contracts/plan.md"],
        include_audit_pack: true,
        audit_pack_decision_limit: 12,
        evidence: expect.objectContaining({
          resident_agi_audit_pack_loaded: true,
          resident_agi_audit_pack_schema: "resident.agi_audit_pack.v1",
          resident_agi_available: true,
          resident_agi_hard_rule_gate_status: "pass",
          resident_agi_evidence_gate_status: "hold",
          resident_agi_evidence_gate_recommended_verdict: "request_evidence",
          resident_agi_authority_matrix_schema:
            "resident.agi_authority_matrix.v1",
          resident_agi_chain_required: true,
          resident_agi_decision_profile_schema:
            "resident.agi_decision_profile.v1",
          resident_agi_decision_profile_recommended_verdict: "request_evidence",
          resident_agi_decision_profile_next_action:
            "request_missing_contextos_or_run_ledger_evidence",
          resident_agi_role_turn_allowed: true,
          resident_agi_downstream_precheck: "hold_for_evidence",
          selected_decision_capability_id: "director.repair.advisory",
          selected_decision_capability_name: "Director repair advisory",
          selected_decision_capability_owner: "resident_agi",
          selected_decision_capability_risk: "medium",
          selected_decision_required_evidence_interfaces: [
            "director.deterministic_repair_strategy_catalog.read",
            "director.repair_coverage.read",
            "director.repair_advisory_policy.read",
          ],
          selected_decision_optional_evidence_interfaces: [
            "audit.diagnosis.read",
          ],
        }),
      }),
    );
  });

  it("rejects a pending goal", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText("Investigate flaky retries"));
    fireEvent.click(screen.getByTestId("resident-reject-goal"));
    expect(mockResidentState.rejectGoal).toHaveBeenCalledWith("goal-pending");
  });

  it("materializes an approved goal", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText("Stabilize PM contract quality"));
    fireEvent.click(screen.getByTestId("resident-materialize-goal"));
    expect(mockResidentState.materializeGoal).toHaveBeenCalledWith(
      "goal-approved",
    );
  });

  it("warns when AGI participation is enabled without a resident_agi model binding", () => {
    const state = mockResidentState as unknown as {
      residentIdentity: Record<string, unknown>;
    };
    const originalIdentity = state.residentIdentity;
    state.residentIdentity = {
      ...originalIdentity,
      resident_agi_participation: {
        enabled: true,
        scopes: ["final_request_audit"],
        participation: { final_request_audit: true },
      },
    };

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
          residentAgiLlmStatus={{
            blocked: true,
            readinessIssue: "missing resident_agi binding",
          }}
        />,
      );

      const bindingStatus = screen.getByTestId(
        "resident-agi-llm-binding-status",
      );
      expect(bindingStatus).toHaveTextContent(
        "常驻 AGI 参与已开启但模型不可用",
      );
      expect(bindingStatus).toHaveTextContent(
        "请在 LLM 视觉配置编辑器中为常驻 AGI 绑定模型。",
      );
      expect(bindingStatus).toHaveTextContent("missing resident_agi binding");
    } finally {
      state.residentIdentity = originalIdentity;
    }
  });

  it("restores AGI repair advisory overlay from the persisted decision trace", () => {
    const state = mockResidentState as unknown as {
      decisions: Array<Record<string, unknown>>;
      lastAgiDecisionResult: unknown;
    };
    const originalDecisions = state.decisions;
    const originalLastResult = state.lastAgiDecisionResult;
    state.lastAgiDecisionResult = null;
    state.decisions = [
      {
        decision_id: "decision-overlay-history",
        actor: "resident_agi",
        stage: "director.repair.advisory",
        summary: "Historical advisory overlay",
        timestamp: "2026-03-08T00:00:00Z",
        verdict: "success",
        actual_outcome: {
          resident_agi_repair_advisory_overlay: {
            schema_version: "resident.agi_repair_advisory_overlay.v1",
            status: "ready",
            eligible_for_director_injection: true,
            participation_enabled: true,
            advisory_only: true,
            authoritative: false,
            agi_execution_authority: false,
            director_runtime_contract: "director.repair_advisory_policy.v1",
            advisor_notes: [
              {
                advisor_source: "resident_agi",
                message: "Historical repair rule suggestion",
                suggested_rules: [
                  {
                    pattern: "borrow marker diagnostic",
                    fix_template: "replace (&) with (&self)",
                  },
                ],
              },
            ],
          },
        },
      },
    ];

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
        />,
      );

      fireEvent.click(screen.getByTestId("agi-toggle-advanced-audit"));

      const overlay = screen.getByTestId(
        "resident-agi-repair-advisory-overlay",
      );
      expect(overlay).toHaveTextContent("就绪");
      expect(overlay).toHaveTextContent("可注入");
      expect(overlay).toHaveTextContent("规则");
      expect(
        screen.getByTestId("resident-agi-repair-advisory-overlay-source"),
      ).toHaveTextContent("decision_trace:decision-o...tory");
    } finally {
      state.decisions = originalDecisions;
      state.lastAgiDecisionResult = originalLastResult;
    }
  });

  it("uses the public repair advisory overlay query before local decision scanning", () => {
    const state = mockResidentState as unknown as {
      decisions: Array<Record<string, unknown>>;
      lastAgiDecisionResult: unknown;
      residentAgiRepairAdvisoryOverlay: unknown;
    };
    const originalDecisions = state.decisions;
    const originalLastResult = state.lastAgiDecisionResult;
    const originalQuery = state.residentAgiRepairAdvisoryOverlay;
    state.lastAgiDecisionResult = null;
    state.decisions = [];
    state.residentAgiRepairAdvisoryOverlay = {
      schema_version: "resident.agi_repair_advisory_overlay_query.v1",
      status: "found",
      found: true,
      decision_ref: { decision_id: "decision-query-overlay" },
      overlay: {
        schema_version: "resident.agi_repair_advisory_overlay.v1",
        status: "ready",
        eligible_for_director_injection: true,
        participation_enabled: true,
        advisory_only: true,
        authoritative: false,
        agi_execution_authority: false,
        director_runtime_contract: "director.repair_advisory_policy.v1",
        advisor_notes: [
          {
            advisor_source: "resident_agi",
            message: "Query repair rule suggestion",
            suggested_rules: [
              {
                pattern: "query overlay diagnostic",
                fix_template: "query overlay fix",
              },
            ],
          },
        ],
      },
    };

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
        />,
      );

      fireEvent.click(screen.getByTestId("agi-toggle-advanced-audit"));

      expect(
        screen.getByTestId("resident-agi-repair-advisory-overlay-source"),
      ).toHaveTextContent("public_query:decision-q...rlay");
      expect(
        screen.getByTestId("resident-agi-repair-advisory-overlay"),
      ).toHaveTextContent("就绪");
    } finally {
      state.decisions = originalDecisions;
      state.lastAgiDecisionResult = originalLastResult;
      state.residentAgiRepairAdvisoryOverlay = originalQuery;
    }
  });

  it("uses audit pack repair advisory overlay when public query is empty", () => {
    const state = mockResidentState as unknown as {
      decisions: Array<Record<string, unknown>>;
      lastAgiDecisionResult: unknown;
      residentAgiAuditPack: Record<string, unknown>;
      residentAgiRepairAdvisoryOverlay: unknown;
    };
    const originalDecisions = state.decisions;
    const originalLastResult = state.lastAgiDecisionResult;
    const originalAuditPack = state.residentAgiAuditPack;
    const originalQuery = state.residentAgiRepairAdvisoryOverlay;
    state.lastAgiDecisionResult = null;
    state.decisions = [];
    state.residentAgiRepairAdvisoryOverlay = {
      schema_version: "resident.agi_repair_advisory_overlay_query.v1",
      status: "missing",
      found: false,
      overlay: null,
    };
    state.residentAgiAuditPack = {
      ...originalAuditPack,
      repair_advisory_overlay_query: {
        schema_version: "resident.agi_repair_advisory_overlay_query.v1",
        status: "found",
        found: true,
        decision_ref: { decision_id: "decision-audit-pack-overlay" },
        advisory_only: true,
        authoritative: false,
        agi_execution_authority: false,
        overlay: {
          schema_version: "resident.agi_repair_advisory_overlay.v1",
          status: "ready",
          eligible_for_director_injection: true,
          participation_enabled: true,
          advisory_only: true,
          authoritative: false,
          agi_execution_authority: false,
          director_runtime_contract: "director.repair_advisory_policy.v1",
          advisor_notes: [
            {
              advisor_source: "resident_agi",
              message: "Audit pack repair rule suggestion",
              suggested_rules: [
                {
                  pattern: "audit pack overlay diagnostic",
                  fix_template: "audit pack overlay fix",
                },
              ],
            },
          ],
        },
      },
    };

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
        />,
      );

      fireEvent.click(screen.getByTestId("agi-toggle-advanced-audit"));

      expect(
        screen.getByTestId("resident-agi-repair-advisory-overlay-source"),
      ).toHaveTextContent("audit_pack_query:decision-a...rlay");
      expect(
        screen.getByTestId("resident-agi-repair-advisory-overlay"),
      ).toHaveTextContent("规则1");
    } finally {
      state.decisions = originalDecisions;
      state.lastAgiDecisionResult = originalLastResult;
      state.residentAgiAuditPack = originalAuditPack;
      state.residentAgiRepairAdvisoryOverlay = originalQuery;
    }
  });

  it("edits the AGI identity", () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId("resident-edit-identity"));
    fireEvent.change(screen.getByLabelText("AGI 名称"), {
      target: { value: "Polaris Resident" },
    });
    fireEvent.change(screen.getByLabelText("AGI 任务宣言"), {
      target: { value: "Keep main green" },
    });
    fireEvent.click(screen.getByTestId("resident-save-identity"));
    expect(mockResidentState.saveIdentity).toHaveBeenCalledWith({
      name: "Polaris Resident",
      mission: "Keep main green",
    });
  });

  it("saves dynamic AGI participation scopes from the backend policy", () => {
    const originalStatus = mockResidentState.status;
    (mockResidentState as { status: unknown }).status = {
      agi_participation_policy: {
        schema_version: "resident.agi_participation_policy.v1",
        role_id: "resident_agi",
        participation_flags: [
          "final_request_audit",
          "goal_promotion_readiness",
          "director_repair_advisory_policy",
        ],
        available_scopes: [
          {
            scope_id: "goal.promotion.readiness",
            name: "Goal promotion readiness",
            category: "decision_capability",
            risk_level: "high",
          },
          {
            scope_id: "director_repair_advisory_policy",
            name: "Director AGI repair advisory policy",
            category: "director_repair_advisory",
            risk_level: "low",
          },
        ],
      },
    };

    try {
      render(
        <ResidentWorkspace
          workspace="X:/Git/polaris"
          onBackToMain={vi.fn()}
          residentSnapshot={null}
        />,
      );

      fireEvent.click(screen.getByTestId("agi-open-operator-settings"));
      fireEvent.click(screen.getByTestId("agi-participation-master"));
      expect(screen.queryByText("goal_promotion_readiness")).toBeNull();
      fireEvent.click(
        screen.getByTestId("agi-participation-quick-goal-promotion-readiness"),
      );
      fireEvent.click(screen.getByTestId("agi-participation-repair-advisory"));
      fireEvent.click(screen.getByTestId("agi-save-participation"));

      expect(mockResidentState.saveIdentity).toHaveBeenCalledWith({
        resident_agi_participation: {
          enabled: true,
          scopes: [
            "goal.promotion.readiness",
            "director.repair.advisory",
            "director_repair_advisory_policy",
            "director_repair_coverage",
            "director_repair_strategy_catalog",
          ],
          participation: {
            "director.repair.advisory": true,
            director_repair_advisory_policy: true,
            director_repair_coverage: true,
            director_repair_strategy_catalog: true,
            "goal.promotion.readiness": true,
            final_request_audit: false,
          },
          custom_scopes_allowed: true,
        },
      });
    } finally {
      (mockResidentState as { status: unknown }).status = originalStatus;
    }
  });
});
