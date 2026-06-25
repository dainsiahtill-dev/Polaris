import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResidentStatusDetailsPayload } from "@/app/types/appContracts";

const residentServiceMock = vi.hoisted(() => ({
  decide: vi.fn(),
  getAgiAuditPack: vi.fn(),
  getAgiEvidenceInterfaces: vi.fn(),
  getAgiHandoffs: vi.fn(),
  getAgiRepairAdvisoryOverlay: vi.fn(),
  getStatus: vi.fn(),
  updateAgiParticipation: vi.fn(),
  updateIdentity: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/services/api", () => ({
  residentService: residentServiceMock,
}));

import { useResident } from "./useResident";

const LIVE_RESIDENT: ResidentStatusDetailsPayload = {
  workspace: "/tmp/polaris-demo",
  identity: {
    name: "Resident AGI Supervisor",
    mission: "Govern unattended development decisions.",
  },
  runtime: {
    active: true,
    mode: "observe",
  },
  agenda: {
    current_focus: ["audit AGI role handoff"],
  },
  counts: {
    decisions: 1,
    goals: 1,
  },
  decisions: [
    {
      decision_id: "decision-1",
      actor: "ResidentAGI",
      stage: "context_handoff",
      summary: "Keep AGI decision trace in RoleSignalPlane.",
    },
  ],
  goals: [
    {
      goal_id: "goal-1",
      title: "Harden AGI capability surface",
      status: "approved",
    },
  ],
  capability_graph: {
    generated_at: "2026-06-25T00:00:00Z",
    capabilities: [],
    gaps: [],
  },
  agi_capability_surface: {
    schema_version: "resident.agi_capability_surface.v1",
    authority_matrix_schema: "resident.agi_authority_matrix.v1",
    role_id: "resident_agi",
    runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
    implementation_cell: "resident.autonomy",
    count: 1,
    items: [
      {
        capability_id: "contextos.final_request_audit.read",
        name: "Final provider-request audit",
        category: "llm_audit",
        access: "read_only",
        contract_ref: "roles.final_request_context_audit",
      },
    ],
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
        read_only_capabilities: 1,
        governed_operation_capabilities: 1,
        high_risk_capabilities: 0,
        canonical_contracts: 2,
      },
      decision_policy: {
        governed_execution: "canonical_role_chain_only",
      },
    },
    hardcoded_repair_strategy_catalog: {
      schema_version: "director.deterministic_repair_strategy_catalog.v1",
      source: "director.runtime.repair_kernel.strategy_catalog",
      access: "read_only",
      execution_boundary: "director_authorized_tools_only",
      chain: "PM → Chief Engineer → Director",
      agi_execution_authority: false,
      director_tool_execution_required: true,
      summary: {
        total: 1,
        returned: 1,
        by_language: { typescript: 1 },
        by_phase: { quality_repair: 1 },
        by_concern: { missing_symbol_or_file: 1 },
        by_risk: { low: 1 },
      },
      items: [
        {
          source_tool: "deterministic_typescript_missing_export_repair",
          language: "typescript",
          phase: "quality_repair",
          concern: "missing_symbol_or_file",
          risk_level: "low",
        },
      ],
    },
  },
};

const LIVE_AUDIT_PACK = {
  schema_version: "resident.agi_audit_pack.v1",
  workspace: "/tmp/polaris-demo",
  role_id: "resident_agi",
  runtime_foundation: "roles.runtime + ContextOS + TurnEngine",
  role_registry: {
    schema_version: "resident.agi_role_registry.v1",
    dialogue_roles: ["pm", "chief_engineer", "director", "qa", "resident_agi"],
    adapter_roles: ["pm", "chief_engineer", "director", "qa", "resident_agi"],
    required_roles: ["pm", "chief_engineer", "director", "qa", "resident_agi"],
    missing_required_roles: [],
    resident_agi_available: true,
  },
  boundary_summary: {
    schema: "resident.agi_decision_boundary.v1",
    boundary_ids: ["role.runtime.foundation"],
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
      read_only_capabilities: 1,
      governed_operation_capabilities: 1,
      high_risk_capabilities: 0,
      canonical_contracts: 2,
    },
    decision_policy: {
      governed_execution: "canonical_role_chain_only",
    },
  },
  run_ledger_summary: {
    schema_version: "resident.agi_run_ledger_summary.v1",
    source: "run_ledger_projection",
    available: false,
    ok: false,
    status: "pending",
  },
  evidence_gate: {
    schema_version: "resident.agi_evidence_gate.v1",
    status: "hold",
    recommended_verdict: "request_evidence",
  },
  recent_decisions: LIVE_RESIDENT.decisions,
  evidence_refs: ["runtime/contexts/context-1.json"],
  execution_constraints: [
    "Downstream work must preserve PM → Chief Engineer → Director.",
  ],
  decision_endpoint: "/v2/resident/agi/decide",
};

const LIVE_EVIDENCE_INTERFACES = {
  schema_version: "resident.agi_evidence_interfaces.v1",
  decision_type: "quality_gate_response",
  interfaces: [
    {
      interface_id: "run_ledger.read",
      status: "unavailable",
      callable: true,
    },
    {
      interface_id: "verifier.policy.read",
      status: "available",
      callable: true,
    },
  ],
  summary: {
    total: 2,
    available: 1,
    unavailable: 1,
  },
};

const LIVE_HANDOFFS = {
  schema_version: "resident.agi_handoff_inbox.v1",
  workspace: "/tmp/polaris-demo",
  role_id: "resident_agi",
  items: [
    {
      decision_id: "decision-agi-1",
      summary: "Quality gate handoff",
      handoff: {
        schema_version: "resident.agi_decision_handoff.v1",
        handoff_status: "ready",
        target_roles: ["chief_engineer", "director", "qa"],
        downstream_allowed: true,
        agi_execution_authority: false,
      },
    },
  ],
  count: 1,
  summary: {
    total: 1,
    by_status: { ready: 1 },
    by_target_role: { director: 1 },
    advisory_only: true,
    agi_execution_authority: false,
    required_chain: "PM → Chief Engineer → Director",
  },
};

const LIVE_REPAIR_ADVISORY_OVERLAY = {
  schema_version: "resident.agi_repair_advisory_overlay_query.v1",
  status: "found",
  found: true,
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
        message: "Suggest future deterministic repair rule.",
        suggested_rules: [
          {
            pattern: "found `&)` near method receiver",
            fix_template: "replace receiver",
          },
        ],
      },
    ],
  },
  decision_ref: {
    decision_id: "decision-agi-repair",
    stage: "director.repair.advisory",
  },
};

describe("useResident", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    residentServiceMock.getAgiRepairAdvisoryOverlay.mockResolvedValue({
      ok: true,
      data: LIVE_REPAIR_ADVISORY_OVERLAY,
    });
  });

  it("keeps live AGI capability-surface evidence when detailed refresh is unavailable", async () => {
    residentServiceMock.getStatus.mockResolvedValueOnce({
      ok: false,
      error: "backend offline",
    });
    residentServiceMock.getAgiAuditPack.mockResolvedValue({
      ok: true,
      data: LIVE_AUDIT_PACK,
    });
    residentServiceMock.getAgiEvidenceInterfaces.mockResolvedValue({
      ok: true,
      data: LIVE_EVIDENCE_INTERFACES,
    });
    residentServiceMock.getAgiHandoffs.mockResolvedValue({
      ok: true,
      data: LIVE_HANDOFFS,
    });

    const { result } = renderHook(() =>
      useResident({
        workspace: "/tmp/polaris-demo",
        liveResident: LIVE_RESIDENT,
      }),
    );

    await waitFor(() => {
      expect(residentServiceMock.getStatus).toHaveBeenCalledWith(
        "/tmp/polaris-demo",
        true,
      );
    });

    expect(result.current.error).toBe("backend offline");
    expect(result.current.residentAgiCapabilitySurface?.schema_version).toBe(
      "resident.agi_capability_surface.v1",
    );
    expect(result.current.residentAgiCapabilitySurface?.role_id).toBe(
      "resident_agi",
    );
    expect(
      result.current.residentAgiCapabilitySurface?.runtime_foundation,
    ).toContain("ContextOS");
    expect(
      result.current.residentAgiCapabilitySurface?.items?.[0]?.capability_id,
    ).toBe("contextos.final_request_audit.read");
    expect(
      result.current.residentAgiCapabilitySurface
        ?.hardcoded_repair_strategy_catalog?.items?.[0]?.source_tool,
    ).toBe("deterministic_typescript_missing_export_repair");
    expect(
      result.current.residentAgiCapabilitySurface
        ?.hardcoded_repair_strategy_catalog?.agi_execution_authority,
    ).toBe(false);
    expect(
      result.current.residentAgiCapabilitySurface?.authority_matrix
        ?.schema_version,
    ).toBe("resident.agi_authority_matrix.v1");
    expect(
      result.current.residentAgiCapabilitySurface?.authority_matrix
        ?.chain_required,
    ).toBe(true);
    expect(result.current.residentRuntimeEvidence.live_snapshot_available).toBe(
      true,
    );
    expect(result.current.residentRuntimeEvidence.http_details_loaded).toBe(
      false,
    );
    expect(result.current.residentRuntimeEvidence.realtime_channel).toBe(
      "runtime.v2.status.resident",
    );
    expect(result.current.residentRuntimeEvidence.snapshot_channel).toBe(
      "runtime.v2.status.snapshot",
    );
    expect(result.current.residentRuntimeEvidence.source).toBe(
      "runtime.v2_snapshot",
    );
    expect(result.current.decisions[0]?.actor).toBe("ResidentAGI");
    expect(result.current.goals[0]?.goal_id).toBe("goal-1");
    expect(residentServiceMock.getAgiAuditPack).not.toHaveBeenCalled();
    expect(residentServiceMock.getAgiEvidenceInterfaces).not.toHaveBeenCalled();
    expect(residentServiceMock.getAgiHandoffs).not.toHaveBeenCalled();
    expect(
      residentServiceMock.getAgiRepairAdvisoryOverlay,
    ).not.toHaveBeenCalled();
  });

  it("runs a Resident AGI decision turn through the service and refreshes", async () => {
    residentServiceMock.getStatus.mockResolvedValue({
      ok: true,
      data: LIVE_RESIDENT,
    });
    residentServiceMock.getAgiAuditPack.mockResolvedValue({
      ok: true,
      data: LIVE_AUDIT_PACK,
    });
    residentServiceMock.getAgiEvidenceInterfaces.mockResolvedValue({
      ok: true,
      data: LIVE_EVIDENCE_INTERFACES,
    });
    residentServiceMock.getAgiHandoffs.mockResolvedValue({
      ok: true,
      data: LIVE_HANDOFFS,
    });
    residentServiceMock.decide.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        recorded_decision: {
          decision_id: "decision-agi-1",
          actor: "resident_agi",
          verdict: "success",
        },
      },
    });

    const { result } = renderHook(() =>
      useResident({
        workspace: "/tmp/polaris-demo",
        liveResident: LIVE_RESIDENT,
      }),
    );

    await waitFor(() => {
      expect(result.current.status?.workspace).toBe("/tmp/polaris-demo");
    });
    expect(result.current.residentAgiAuditPack?.schema_version).toBe(
      "resident.agi_audit_pack.v1",
    );
    expect(
      result.current.residentAgiAuditPack?.role_registry
        ?.resident_agi_available,
    ).toBe(true);
    expect(result.current.residentAgiAuditPack?.evidence_gate?.status).toBe(
      "hold",
    );
    expect(
      result.current.residentAgiAuditPack?.authority_matrix?.decision_policy
        ?.governed_execution,
    ).toBe("canonical_role_chain_only");
    expect(result.current.residentAgiEvidenceInterfaces?.schema_version).toBe(
      "resident.agi_evidence_interfaces.v1",
    );
    expect(
      result.current.residentAgiEvidenceInterfaces?.interfaces?.[1]?.status,
    ).toBe("available");
    expect(result.current.residentAgiHandoffs?.schema_version).toBe(
      "resident.agi_handoff_inbox.v1",
    );
    expect(
      result.current.residentAgiHandoffs?.items?.[0]?.handoff?.target_roles,
    ).toContain("director");
    expect(
      result.current.residentAgiRepairAdvisoryOverlay?.overlay?.status,
    ).toBe("ready");
    expect(result.current.residentRuntimeEvidence.realtime_channel).toBe(
      "runtime.v2.status.resident",
    );
    expect(result.current.residentRuntimeEvidence.source).toBe(
      "runtime.v2_snapshot+http_details",
    );

    await act(async () => {
      await result.current.runAgiDecision({
        objective: "Decide whether the run can proceed.",
        decision_type: "platform_supervision",
      });
    });

    expect(residentServiceMock.decide).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      {
        objective: "Decide whether the run can proceed.",
        decision_type: "platform_supervision",
      },
    );
    expect(residentServiceMock.getStatus).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      true,
    );
    expect(residentServiceMock.getAgiAuditPack).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      12,
    );
    expect(residentServiceMock.getAgiEvidenceInterfaces).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      {
        decisionType: "quality_gate_response",
        maxRuns: 20,
      },
    );
    expect(residentServiceMock.getAgiHandoffs).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      { limit: 50 },
    );
    expect(
      residentServiceMock.getAgiRepairAdvisoryOverlay,
    ).toHaveBeenCalledWith("/tmp/polaris-demo", { limit: 50 });
  });

  it("refreshes AGI evidence interfaces for the selected decision type", async () => {
    residentServiceMock.getStatus.mockResolvedValue({
      ok: true,
      data: LIVE_RESIDENT,
    });
    residentServiceMock.getAgiAuditPack.mockResolvedValue({
      ok: true,
      data: LIVE_AUDIT_PACK,
    });
    residentServiceMock.getAgiEvidenceInterfaces
      .mockResolvedValueOnce({
        ok: true,
        data: LIVE_EVIDENCE_INTERFACES,
      })
      .mockResolvedValueOnce({
        ok: true,
        data: {
          ...LIVE_EVIDENCE_INTERFACES,
          decision_type: "director.repair.advisory",
        },
      });
    residentServiceMock.getAgiHandoffs.mockResolvedValue({
      ok: true,
      data: LIVE_HANDOFFS,
    });

    const { result } = renderHook(() =>
      useResident({
        workspace: "/tmp/polaris-demo",
        liveResident: LIVE_RESIDENT,
      }),
    );

    await waitFor(() => {
      expect(result.current.residentAgiEvidenceInterfaces?.decision_type).toBe(
        "quality_gate_response",
      );
    });

    await act(async () => {
      await result.current.refreshAgiEvidenceInterfaces(
        "director.repair.advisory",
      );
    });

    expect(residentServiceMock.getAgiEvidenceInterfaces).toHaveBeenLastCalledWith(
      "/tmp/polaris-demo",
      {
        decisionType: "director.repair.advisory",
        maxRuns: 20,
      },
    );
    expect(result.current.residentAgiEvidenceInterfaces?.decision_type).toBe(
      "director.repair.advisory",
    );
  });

  it("saves identity and AGI participation through separate scoped APIs", async () => {
    residentServiceMock.getStatus.mockResolvedValue({
      ok: true,
      data: LIVE_RESIDENT,
    });
    residentServiceMock.getAgiAuditPack.mockResolvedValue({
      ok: true,
      data: LIVE_AUDIT_PACK,
    });
    residentServiceMock.getAgiEvidenceInterfaces.mockResolvedValue({
      ok: true,
      data: LIVE_EVIDENCE_INTERFACES,
    });
    residentServiceMock.getAgiHandoffs.mockResolvedValue({
      ok: true,
      data: LIVE_HANDOFFS,
    });
    residentServiceMock.updateIdentity.mockResolvedValueOnce({
      ok: true,
      data: {
        ...LIVE_RESIDENT.identity,
        name: "Resident AGI Supervisor v2",
      },
    });
    residentServiceMock.updateAgiParticipation.mockResolvedValueOnce({
      ok: true,
      data: {
        enabled: true,
        scopes: ["final_request_audit", "director.repair.advisory"],
        participation: {
          final_request_audit: true,
          director_repair_advisory: true,
        },
        custom_scopes_allowed: false,
      },
    });

    const { result } = renderHook(() =>
      useResident({
        workspace: "/tmp/polaris-demo",
        liveResident: LIVE_RESIDENT,
      }),
    );

    await waitFor(() => {
      expect(result.current.status?.workspace).toBe("/tmp/polaris-demo");
    });

    await act(async () => {
      await result.current.saveIdentity({
        name: "Resident AGI Supervisor v2",
        resident_agi_participation: {
          enabled: true,
          scopes: ["final_request_audit", "director.repair.advisory"],
          participation: {
            final_request_audit: true,
            director_repair_advisory: true,
          },
          custom_scopes_allowed: false,
        },
      });
    });

    expect(residentServiceMock.updateIdentity).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      { name: "Resident AGI Supervisor v2" },
    );
    expect(residentServiceMock.updateAgiParticipation).toHaveBeenCalledWith(
      "/tmp/polaris-demo",
      {
        enabled: true,
        scopes: ["final_request_audit", "director.repair.advisory"],
        participation: {
          final_request_audit: true,
          director_repair_advisory: true,
        },
        custom_scopes_allowed: false,
      },
    );
  });
});
