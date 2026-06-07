import { expect, test } from "./fixtures";
import {
  assertExpandedTechEvidenceMatrix,
  collectExpandedTechEvidenceMatrix,
  writeExpandedTechEvidenceMatrix,
} from "./helpers/expandedTechEvidenceMatrix";

test.setTimeout(300_000);

test("collects expanded technology evidence matrix from the running runtime", async ({ window }, testInfo) => {
  const requireRealChain = String(process.env.KERNELONE_E2E_MATRIX_REQUIRE_REAL || "").trim() === "1";

  const report = await collectExpandedTechEvidenceMatrix(window, { requireRealChain });
  await writeExpandedTechEvidenceMatrix(testInfo, report);

  expect(report.schema).toBe("polaris.e2e.expanded_tech_evidence_matrix.v1");
  expect(report.expanded_candidates.length).toBeGreaterThanOrEqual(60);
  expect(report.core_runtime_integrations.expected_count).toBe(16);
  expect(report.core_runtime_integrations.actual_count).toBe(16);
  expect(report.core_runtime_integrations.missing_ids).toEqual([]);
  expect(report.core_runtime_integrations.entrypoints_verified_count).toBeGreaterThanOrEqual(16);
  expect(report.probes.some((probe) => probe.id === "cognitive_runtime_receipt_handoff_roundtrip" && probe.status === "PASS")).toBe(true);

  assertExpandedTechEvidenceMatrix(report);
});
