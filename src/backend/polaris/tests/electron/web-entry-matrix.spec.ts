import {
  assertExpandedTechEvidenceMatrix,
  collectExpandedTechEvidenceMatrix,
  writeExpandedTechEvidenceMatrix,
} from "./helpers/expandedTechEvidenceMatrix";
import { expect, test } from "./webFixtures";

test.setTimeout(180_000);

test("web entry collects expanded runtime evidence matrix without Electron preload", async ({ webPage, webTestEnv }, testInfo) => {
  const report = await collectExpandedTechEvidenceMatrix(webPage, {
    requireRealChain: false,
    workspaceOverride: webTestEnv.isolatedWorkspace,
  });
  await writeExpandedTechEvidenceMatrix(testInfo, report, "web-entry-expanded-tech-evidence-matrix.json");

  const backendProbe = report.probes.find((probe) => probe.id === "backend_connection");
  expect(backendProbe?.status).toBe("PASS");
  expect(JSON.stringify(backendProbe?.evidence || [])).toContain("browser_dev_backend");
  expect(report.core_runtime_integrations.actual_count).toBe(16);
  expect(report.core_runtime_integrations.missing_ids).toEqual([]);
  expect(report.core_runtime_evidence_placement).toBeNull();
  assertExpandedTechEvidenceMatrix(report);
});
