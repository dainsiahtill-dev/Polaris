import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();

vi.mock("@/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock("@/app/utils/devLogger", () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

import { residentService } from "../api";

describe("residentService", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads the Resident AGI audit pack from the read-only endpoint", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema_version: "resident.agi_audit_pack.v1",
          role_id: "resident_agi",
        }),
        { status: 200 },
      ),
    );

    const result = await residentService.getAgiAuditPack(
      "/tmp/polaris-demo",
      12,
    );

    expect(result.ok).toBe(true);
    expect(result.data?.schema_version).toBe("resident.agi_audit_pack.v1");
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/v2/resident/agi/audit-pack?workspace=%2Ftmp%2Fpolaris-demo&decision_limit=12",
    );
  });

  it("loads the Resident AGI capability surface from the canonical endpoint", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema_version: "resident.agi_capability_surface.v1",
          authority_matrix_schema: "resident.agi_authority_matrix.v1",
          authority_matrix: {
            schema_version: "resident.agi_authority_matrix.v1",
            decision_policy: {
              governed_execution: "canonical_role_chain_only",
            },
          },
          hardcoded_repair_strategy_catalog: {
            schema_version: "director.deterministic_repair_strategy_catalog.v1",
            source: "director.runtime.repair_kernel.strategy_catalog",
            agi_execution_authority: false,
            items: [
              {
                source_tool: "deterministic_typescript_missing_export_repair",
                language: "typescript",
              },
            ],
          },
        }),
        { status: 200 },
      ),
    );

    const result = await residentService.getCapabilities("/tmp/polaris-demo");

    expect(result.ok).toBe(true);
    expect(
      result.data?.authority_matrix?.decision_policy?.governed_execution,
    ).toBe("canonical_role_chain_only");
    expect(
      result.data?.hardcoded_repair_strategy_catalog?.items?.[0]?.source_tool,
    ).toBe("deterministic_typescript_missing_export_repair");
    expect(
      result.data?.hardcoded_repair_strategy_catalog?.agi_execution_authority,
    ).toBe(false);
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/v2/resident/capabilities?workspace=%2Ftmp%2Fpolaris-demo",
    );
  });

  it("loads Resident AGI evidence-interface readiness from the read-only endpoint", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          schema_version: "resident.agi_evidence_interfaces.v1",
          interfaces: [
            { interface_id: "run_ledger.read", status: "unavailable" },
            { interface_id: "verifier.policy.read", status: "available" },
          ],
        }),
        { status: 200 },
      ),
    );

    const result = await residentService.getAgiEvidenceInterfaces(
      "/tmp/polaris-demo",
      {
        decisionType: "quality_gate_response",
        interfaceIds: ["run_ledger.read", "verifier.policy.read"],
        maxRuns: 5,
      },
    );

    expect(result.ok).toBe(true);
    expect(result.data?.schema_version).toBe(
      "resident.agi_evidence_interfaces.v1",
    );
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/v2/resident/agi/evidence-interfaces?workspace=%2Ftmp%2Fpolaris-demo&decision_type=quality_gate_response&interface_ids=run_ledger.read%2Cverifier.policy.read&max_runs=5",
    );
  });

  it("posts Resident AGI decisions with audit-pack and governance evidence", async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          decision: { verdict: "request_evidence" },
          audit_pack: { schema_version: "resident.agi_audit_pack.v1" },
          runtime_contract_gate: {
            schema_version: "resident.agi_runtime_contract_gate.v1",
            status: "pass",
            passed: true,
            required: true,
          },
        }),
        { status: 200 },
      ),
    );

    const result = await residentService.decide("/tmp/polaris-demo", {
      objective: "Decide whether the run can proceed.",
      decision_type: "platform_supervision",
      include_audit_pack: true,
      candidate_actions: ["continue", "block", "request_evidence", "escalate"],
      constraints: ["preserve_pm_chief_engineer_director_qa_chain"],
      evidence: {
        resident_agi_authority_matrix_schema:
          "resident.agi_authority_matrix.v1",
      },
    });

    expect(result.ok).toBe(true);
    expect(result.data?.runtime_contract_gate?.schema_version).toBe(
      "resident.agi_runtime_contract_gate.v1",
    );
    expect(result.data?.runtime_contract_gate?.status).toBe("pass");
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/v2/resident/agi/decide",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace: "/tmp/polaris-demo",
          objective: "Decide whether the run can proceed.",
          decision_type: "platform_supervision",
          include_audit_pack: true,
          candidate_actions: [
            "continue",
            "block",
            "request_evidence",
            "escalate",
          ],
          constraints: ["preserve_pm_chief_engineer_director_qa_chain"],
          evidence: {
            resident_agi_authority_matrix_schema:
              "resident.agi_authority_matrix.v1",
          },
        }),
      }),
    );
  });
});
