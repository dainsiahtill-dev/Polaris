import path from "node:path";
import {
  assertExpandedTechEvidenceMatrix,
  collectExpandedTechEvidenceMatrix,
  requestJson,
  writeExpandedTechEvidenceMatrix,
} from "./helpers/expandedTechEvidenceMatrix";
import { expect, test } from "./webFixtures";

type SettingsPayload = {
  workspace?: string;
  pm_runs_director?: boolean;
};

type PmStatusPayload = {
  running?: boolean;
  status?: string | null;
  ok?: boolean | null;
  exit_code?: number | null;
  error?: string | null;
  contract_path?: string | null;
};

type RuntimeLayoutPayload = {
  runtime_root?: string;
};

type SnapshotPayload = {
  tasks?: unknown[];
  pm_state?: Record<string, unknown> | null;
};

type IntegrationQaArtifact = {
  reason?: string;
  ran?: boolean;
  passed?: boolean | null;
};

type DirectorResultArtifact = {
  status?: string;
  successes?: number;
  failures?: number;
  blocked?: number;
};

type DirectorTaskPayload = {
  metadata?: {
    pm_task_id?: string;
  };
};

const REAL_FLOW_TEST_TIMEOUT_MS = 30 * 60 * 1000;

test.skip(
  process.env.KERNELONE_E2E_USE_REAL_SETTINGS !== "1",
  "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to run the browser-entry PM/Director/QA chain with real configured LLM settings.",
);

test.setTimeout(REAL_FLOW_TEST_TIMEOUT_MS);

async function waitForPmFinish(page: Parameters<typeof requestJson>[0]): Promise<PmStatusPayload> {
  await expect
    .poll(async () => Boolean((await requestJson<PmStatusPayload>(page, "/v2/pm/status")).running), {
      timeout: 60_000,
      intervals: [500, 1000, 2000],
    })
    .toBe(true);
  await expect
    .poll(async () => Boolean((await requestJson<PmStatusPayload>(page, "/v2/pm/status")).running), {
      timeout: 20 * 60 * 1000,
      intervals: [1000, 2000, 5000],
    })
    .toBe(false);
  return await requestJson<PmStatusPayload>(page, "/v2/pm/status");
}

test("web entry triggers PM -> Director -> QA and verifies 16x4 runtime evidence placement", async ({ webPage, webTestEnv }, testInfo) => {
  const settings = await requestJson<SettingsPayload>(webPage, "/settings");
  expect(path.resolve(String(settings.workspace || ""))).toBe(path.resolve(webTestEnv.isolatedWorkspace));
  if (settings.pm_runs_director !== true) {
    await requestJson<SettingsPayload>(webPage, "/settings", {
      method: "POST",
      body: {
        workspace: webTestEnv.isolatedWorkspace,
        pm_runs_director: true,
      },
    });
  }

  await requestJson<Record<string, unknown>>(webPage, "/v2/pm/run_once", { method: "POST", body: {} });
  const pmStatus = await waitForPmFinish(webPage);
  expect(pmStatus.running).toBe(false);
  expect(pmStatus.ok, JSON.stringify(pmStatus)).not.toBe(false);
  expect(String(pmStatus.error || "")).toBe("");

  const snapshot = await requestJson<SnapshotPayload>(webPage, "/state/snapshot");
  expect(Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0).toBeGreaterThan(0);
  expect(Number(snapshot.pm_state?.completed_task_count || 0)).toBeGreaterThan(0);

  const layout = await requestJson<RuntimeLayoutPayload>(webPage, "/runtime/storage-layout");
  const runtimeRoot = String(layout.runtime_root || "").trim();
  expect(runtimeRoot).not.toBe("");

  const director = await requestJson<DirectorResultArtifact>(webPage, "/v2/director/status?source=auto");
  expect(Number(director.failures || 0), JSON.stringify(director)).toBe(0);
  expect(Number(director.blocked || 0), JSON.stringify(director)).toBe(0);

  const tasks = await requestJson<DirectorTaskPayload[]>(webPage, "/v2/director/tasks?source=auto");
  expect(
    tasks.filter((task) => String(task.metadata?.pm_task_id || "").trim()).length,
    JSON.stringify(tasks.slice(0, 3)),
  ).toBeGreaterThan(0);

  const qa = await requestJson<IntegrationQaArtifact>(webPage, "/v2/director/integration-qa", {
    method: "POST",
    body: { run_id: `web-full-chain-qa-${Date.now()}` },
  });
  expect(qa.ran, JSON.stringify(qa)).toBe(true);
  expect(qa.passed, JSON.stringify(qa)).toBe(true);
  expect(String(qa.reason || "")).toBe("integration_qa_passed");

  const report = await collectExpandedTechEvidenceMatrix(webPage, {
    requireRealChain: true,
    runtimeRootOverride: runtimeRoot,
    workspaceOverride: webTestEnv.isolatedWorkspace,
  });
  await writeExpandedTechEvidenceMatrix(testInfo, report, "web-full-chain-expanded-tech-evidence-matrix.json");
  expect(report.core_runtime_evidence_placement?.rows.length).toBe(16);
  expect(report.core_runtime_evidence_placement?.missing).toEqual([]);
  assertExpandedTechEvidenceMatrix(report);
});
