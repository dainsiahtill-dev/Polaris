import { describe, expect, it } from "vitest";

import {
  DEFAULT_LLM_BINDING_ROLE_IDS,
  getLlmRoleDefinition,
  getVisibleLlmBindingRoleIds,
  GOVERNANCE_ADVISOR_LLM_ROLE_IDS,
  normalizeLlmRoleId,
  OPTIONAL_GOVERNANCE_LLM_ROLE_IDS,
  REQUIRED_LLM_ASSIGNMENT_ROLE_IDS,
} from "./roleDefinitions";

describe("LLM role definitions", () => {
  it("keeps Resident AGI as a default bindable role without promoting CFO or HR", () => {
    expect(DEFAULT_LLM_BINDING_ROLE_IDS).toContain("resident_agi");
    expect(DEFAULT_LLM_BINDING_ROLE_IDS).toContain("scout");
    expect(DEFAULT_LLM_BINDING_ROLE_IDS).not.toContain("cfo");
    expect(DEFAULT_LLM_BINDING_ROLE_IDS).not.toContain("hr");
    expect(OPTIONAL_GOVERNANCE_LLM_ROLE_IDS).toEqual(["cfo", "hr"]);
    expect(GOVERNANCE_ADVISOR_LLM_ROLE_IDS).toEqual(["cfo", "hr"]);
    expect(getLlmRoleDefinition("cfo").bindingKind).toBe(
      "governance_advisor",
    );
    expect(getLlmRoleDefinition("hr").bindingKind).toBe(
      "governance_advisor",
    );
    expect(getLlmRoleDefinition("cfo").label).toBe("Cost Advisor");
    expect(getLlmRoleDefinition("hr").label).toBe(
      "Model Governance Advisor",
    );
  });

  it("requires only the core delivery roles for baseline LLM readiness", () => {
    expect(REQUIRED_LLM_ASSIGNMENT_ROLE_IDS).toEqual([
      "pm",
      "chief_engineer",
      "director",
      "qa",
      "architect",
    ]);
  });

  it("shows optional governance roles only when they are configured or have status", () => {
    expect(getVisibleLlmBindingRoleIds({}, {})).not.toContain("cfo");
    expect(
      getVisibleLlmBindingRoleIds(
        { cfo: { provider_id: "openai", model: "gpt-4.1-mini" } },
        {},
      ),
    ).toContain("cfo");
    expect(getVisibleLlmBindingRoleIds({}, { hr: { ready: true } })).toContain(
      "hr",
    );
  });

  it("normalizes legacy docs role to architect", () => {
    expect(normalizeLlmRoleId("docs")).toBe("architect");
    expect(normalizeLlmRoleId("resident_agi")).toBe("resident_agi");
    expect(normalizeLlmRoleId("unknown")).toBeNull();
  });
});
