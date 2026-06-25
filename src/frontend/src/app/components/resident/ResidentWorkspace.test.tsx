import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    ],
    decision_capability_registry: {
      schema_version: "resident.agi_decision_capability_registry.v1",
      role_id: "resident_agi",
      runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
      platform_owned_decisions: ["platform.invariant.blocker"],
      agi_owned_decisions: ["evidence.interface.selection"],
      governed_execution_decisions: ["goal.promotion.readiness"],
      evidence_interface_ids: [
        "contextos.final_request_audit.read",
        "run_ledger.read",
        "audit.diagnosis.read",
        "director.deterministic_repair_strategy_catalog.read",
        "verifier.policy.read",
        "verifier.execution.execute",
      ],
      candidate_actions: ["block", "continue", "request_evidence"],
      counts: {
        decisions: 3,
        platform_owned: 1,
        agi_owned: 1,
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
      profile_summary_schema:
        "director.deterministic_repair_profile_summary.v1",
      unknown_source_tool_policy: "fail_closed_high_risk",
      execution_boundary: "director_authorized_tools_only",
      chain: "PM → Chief Engineer → Director",
      agi_advisory: {
        active: false,
        authoritative: false,
        writes_allowed: false,
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
    ],
    summary: {
      total: 3,
      available: 1,
      unavailable: 1,
      needs_public_facade: 0,
      metadata_only: 0,
      governed_execute_only: 0,
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
  runAgiDecision: vi.fn(async () => null),
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
    ).toHaveTextContent("tick:deterministic_evidence_producer");
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("judgement:resident_agi_decision_turn");
    expect(
      screen.getByTestId("resident-tick-autonomy-boundary"),
    ).toHaveTextContent("sidecar:blocked");
    expect(screen.getByText("最新元认知")).toBeInTheDocument();
    expect(screen.getByText("Task decomposition")).toBeInTheDocument();
    expect(screen.getByText("AGI Role 能力面")).toBeInTheDocument();
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
    ).toHaveTextContent("canonical contract");
    expect(
      screen.getByTestId("resident-agi-role-foundation"),
    ).toHaveTextContent("PM → Chief Engineer → Director");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("能力治理矩阵");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("PM → Chief Engineer → Director");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("Governed ops");
    expect(
      screen.getByTestId("resident-agi-governance-matrix"),
    ).toHaveTextContent("High risk");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("resident.agi_authority_matrix.v1");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("hard rules 2");
    expect(
      screen.getByTestId("resident-agi-authority-matrix"),
    ).toHaveTextContent("AGI judgement 2");
    expect(
      screen.getByTestId("resident-agi-governance-tags"),
    ).toHaveTextContent("control_plane.verifier_execution");
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
    ).toHaveTextContent("PM → Chief Engineer → Director");
    expect(
      screen.getByTestId("resident-agi-repair-strategy-catalog-summary"),
    ).toHaveTextContent("AGI execute: blocked");
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
    expect(screen.getAllByText("risk high").length).toBeGreaterThan(1);
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
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "AGI 审计包",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "resident.agi_audit_pack.v1",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "Hard gate pass",
    );
    expect(
      screen.getByTestId("resident-agi-audit-authority-matrix"),
    ).toHaveTextContent("resident.agi_authority_matrix.v1");
    expect(
      screen.getByTestId("resident-agi-audit-authority-matrix"),
    ).toHaveTextContent("governed ops 2");
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
    ).toHaveTextContent("PM → Chief Engineer → Director");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("fail_closed_high_risk");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("AGI execute: blocked");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("writes: blocked");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("advisory: inactive");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director.deterministic_repair_strategy_catalog.v1");
    expect(
      screen.getByTestId("resident-agi-director-repair-contract"),
    ).toHaveTextContent("director.deterministic_repair_profile_summary.v1");
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "hold → request_evidence",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "Run Ledger pending",
    );
    expect(screen.getByTestId("resident-agi-audit-pack")).toHaveTextContent(
      "llm override: blocked",
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
    ).toHaveTextContent("Role turn allowed");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("hold_for_evidence");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("request_missing_contextos_or_run_ledger_evidence");
    expect(
      screen.getByTestId("resident-agi-audit-decision-profile"),
    ).toHaveTextContent("action:request_evidence");
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
    ).toHaveTextContent("Evidence interfaces");
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
      screen.getByText("source of truth: decision_trace.jsonl"),
    ).toBeInTheDocument();
    expect(screen.getByText("resident.decision_event.v1")).toBeInTheDocument();
    expect(screen.getAllByText("Runtime").length).toBeGreaterThan(0);
    expect(
      screen.getByText("PM → Chief Engineer → Director"),
    ).toBeInTheDocument();
    expect(screen.getByText("validation_passed")).toBeInTheDocument();
    expect(screen.getByText("bounded decomposition")).toBeInTheDocument();
    expect(screen.getByText("score 91%")).toBeInTheDocument();
    expect(screen.getByText("runtime contract: pass")).toBeInTheDocument();
    expect(
      screen.getByText("runtime: roles.runtime.execute_role_session"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("agi profile: resident.agi_decision_profile.v1"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("agi decision: goal.promotion.readiness"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("evidence interface: run_ledger.read"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/evidence refs: runtime\/contracts\/plan.md/),
    ).toBeInTheDocument();
    expect(screen.getByText(/symbols: record_decision/)).toBeInTheDocument();
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
      screen.getByTestId("resident-agi-decision-turn-profile"),
    ).toHaveTextContent("action:request_evidence");
    fireEvent.change(screen.getByLabelText("AGI 决策目标"), {
      target: { value: "Decide whether the current run can proceed." },
    });
    fireEvent.click(screen.getByTestId("resident-run-agi-decision"));

    expect(mockResidentState.runAgiDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        decision_type: "platform_supervision",
        objective: "Decide whether the current run can proceed.",
        candidate_actions: [
          "request_evidence",
          "block",
          "escalate",
          "continue",
        ],
        constraints: expect.arrayContaining([
          "preserve_pm_chief_engineer_director_qa_chain",
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
      resident_agi_participation: {
        enabled: false,
        scopes: [],
        participation: {
          final_request_audit: false,
          quality_gate_response: false,
          architecture_option_selection: false,
          evidence_interface_selection: false,
          goal_promotion: false,
          decision_trace: false,
          capability_surface: false,
          decision_boundary: false,
          director_repair_strategy_catalog: false,
        },
        custom_scopes_allowed: true,
      },
    });
  });

  it("saves dynamic AGI participation scopes from the backend policy", () => {
    const originalStatus = mockResidentState.status;
    (mockResidentState as { status: unknown }).status = {
      agi_participation_policy: {
        schema_version: "resident.agi_participation_policy.v1",
        role_id: "resident_agi",
        participation_flags: ["final_request_audit"],
        available_scopes: [
          {
            scope_id: "goal.promotion.readiness",
            name: "Goal promotion readiness",
            category: "decision_capability",
            risk_level: "high",
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

      fireEvent.click(screen.getByTestId("resident-edit-identity"));
      fireEvent.click(screen.getByTestId("resident-agi-participation-enabled"));
      fireEvent.click(screen.getByLabelText(/Goal promotion readiness/));
      fireEvent.click(screen.getByTestId("resident-save-identity"));

      expect(mockResidentState.saveIdentity).toHaveBeenCalledWith({
        name: "Resident AGI Supervisor",
        mission:
          "Supervise unattended Polaris development runs with governed evidence.",
        resident_agi_participation: {
          enabled: true,
          scopes: ["goal.promotion.readiness"],
          participation: {
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
