import { promises as fs } from "node:fs";
import path from "node:path";
import { type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

type BackendInfo = {
  baseUrl?: string;
  token?: string;
};

type SettingsPayload = {
  workspace?: string;
  pm_runs_director?: boolean;
  pm_model?: string;
  director_model?: string;
};

type PmStatusPayload = {
  running?: boolean;
  pid?: number | null;
};

type SnapshotPayload = {
  tasks?: unknown[];
  pm_state?: Record<string, unknown> | null;
};

type RuntimeLayoutPayload = {
  runtime_root?: string;
};

type IntegrationQaArtifact = {
  reason?: string;
  summary?: string;
  ran?: boolean;
  passed?: boolean | null;
};

type DirectorStatusPayload = {
  state?: string;
  tasks?: {
    total?: number;
  };
};

type DirectorTaskPayload = {
  metadata?: {
    pm_task_id?: string;
  };
};

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const info = await window.evaluate(async () => {
    const api = (window as Window & {
      polaris?: { getBackendInfo?: () => Promise<BackendInfo> };
    }).polaris;
    if (!api?.getBackendInfo) {
      throw new Error("polaris.getBackendInfo missing");
    }
    return await api.getBackendInfo();
  });

  if (!info?.baseUrl || !info?.token) {
    throw new Error("backend info missing");
  }

  return {
    baseUrl: info.baseUrl,
    token: info.token,
  };
}

async function fetchJson<T>(window: Page, endpoint: string): Promise<T> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token, path }) => {
      const response = await fetch(`${baseUrl}${path}`, {
        headers: {
          authorization: `Bearer ${token}`,
        },
      });
      if (!response.ok) {
        throw new Error(`fetch ${path} failed: ${response.status}`);
      }
      return (await response.json()) as unknown;
    },
    { baseUrl: backend.baseUrl, token: backend.token, path: endpoint },
  ) as Promise<T>;
}

async function readJsonFile<T>(filePath: string): Promise<T | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

async function dismissEngineFailureDialog(window: Page): Promise<void> {
  const dialog = window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" });
  const closeButton = dialog.getByRole("button", { name: "关闭" });

  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(dialog).toBeHidden({ timeout: 10_000 });
  }
}

async function clickAndWaitForPmStart(window: Page): Promise<void> {
  await window.getByTestId("pm-workspace-run-once").click();

  await expect
    .poll(async () => {
      const status = await fetchJson<PmStatusPayload>(window, "/v2/pm/status");
      return Boolean(status.running);
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBe(true);
}

async function waitForPmFinish(window: Page): Promise<void> {
  await expect
    .poll(async () => {
      const status = await fetchJson<PmStatusPayload>(window, "/v2/pm/status");
      return Boolean(status.running);
    }, {
      timeout: 20 * 60 * 1000,
      intervals: [1000, 2000, 5000, 10000],
    })
    .toBe(false);
}

async function waitForSnapshotTasks(window: Page): Promise<SnapshotPayload> {
  await expect
    .poll(async () => {
      const snapshot = await fetchJson<SnapshotPayload>(window, "/state/snapshot");
      return Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0;
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBeGreaterThan(0);

  return fetchJson<SnapshotPayload>(window, "/state/snapshot");
}

async function enterPmWorkspace(window: Page): Promise<void> {
  const directEntry = window.getByTestId("enter-pm-workspace");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  await window.getByRole("menuitem", { name: /PM\s*工作区/i }).click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = window.getByTestId("enter-director-workspace");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  await window.getByRole("menuitem", { name: /Director\s*Workspace/i }).click();
}

test.setTimeout(25 * 60 * 1000);

test("real PM -> Director flow reaches PM and Director workspaces", async ({ window, testEnv }) => {
  test.skip(!testEnv.useRealSettings, "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to use real configured LLM settings.");

  const settings = await fetchJson<SettingsPayload>(window, "/settings");
  expect(String(settings.workspace || "").trim(), "real settings workspace should not be empty").not.toBe("");
  expect(settings.pm_runs_director, "real settings should keep PM -> Director enabled").toBe(true);

  await dismissEngineFailureDialog(window);
  await enterPmWorkspace(window);
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  await expect(window.getByTestId("pm-workspace-run-once")).toBeEnabled();

  await clickAndWaitForPmStart(window);
  await window.getByTestId("pm-workspace-back").click();
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
  const runtimeLayout = await fetchJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
  const runtimeRoot = String(runtimeLayout.runtime_root || "").trim();
  expect(runtimeRoot, "runtime storage layout should expose a runtime root").not.toBe("");
  await waitForPmFinish(window);

  const snapshot = await waitForSnapshotTasks(window);
  const taskCount = Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0;
  expect(taskCount).toBeGreaterThan(0);

  const lastDirectorStatus = String(snapshot.pm_state?.["last_director_status"] || "").trim();
  expect(lastDirectorStatus, "PM flow should write a Director result status").not.toBe("");
  expect(Number(snapshot.pm_state?.["completed_task_count"] || 0)).toBeGreaterThan(0);

  const integrationQaPath = path.join(runtimeRoot, "results", "integration_qa.result.json");
  await expect
    .poll(async () => {
      const payload = await readJsonFile<IntegrationQaArtifact>(integrationQaPath);
      return String(payload?.reason || "").trim();
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .not.toBe("");

  const integrationQa = await readJsonFile<IntegrationQaArtifact>(integrationQaPath);

  expect(
    ["pending_director_tasks", "director_failures_present", "integration_qa_passed", "integration_qa_failed"].includes(
      String(integrationQa?.reason || ""),
    ),
    `unexpected integration QA reason: ${String(integrationQa?.reason || "")}`,
  ).toBe(true);

  await dismissEngineFailureDialog(window);
  await dismissEngineFailureDialog(window);
  await enterDirectorWorkspace(window);
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  await expect(window.getByTestId("director-workspace-execute")).toBeVisible();
  await expect.poll(async () => window.getByTestId("director-task-item").count()).toBe(taskCount);

  await window.getByTestId("director-workspace-execute").click();
  await expect
    .poll(async () => {
      const status = await fetchJson<DirectorStatusPayload>(window, "/v2/director/status");
      return String(status.state || "").trim().toUpperCase();
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBe("RUNNING");

  await expect
    .poll(async () => {
      const tasks = await fetchJson<DirectorTaskPayload[]>(window, "/v2/director/tasks");
      return Array.isArray(tasks)
        ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
        : 0;
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBeGreaterThan(0);

  await window.getByTestId("director-workspace-execute").click();
  await expect
    .poll(async () => {
      const status = await fetchJson<DirectorStatusPayload>(window, "/v2/director/status");
      return String(status.state || "").trim().toUpperCase();
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .not.toBe("RUNNING");
});
