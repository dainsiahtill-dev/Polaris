import type { Page } from "@playwright/test";
import fs from "fs";
import { expect, test } from "./fixtures";

type BackendFetchInit = {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
};

type BackendFetchResult = {
  ok: boolean;
  status: number;
  text: string;
  json: unknown;
};

const ignoredConsoleErrorPatterns = [
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /Unable to preload CSS for \/assets\//i,
];

function getActionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error)));
}

async function backendFetch(window: Page, route: string, init: BackendFetchInit = {}): Promise<BackendFetchResult> {
  return window.evaluate(
    async ({ route: targetRoute, init: requestInit }) => {
      const api = (window as unknown as { polaris?: { getBackendInfo?: () => Promise<{ baseUrl?: string; token?: string }> } }).polaris;
      const backend = await api?.getBackendInfo?.();
      if (!backend?.baseUrl || !backend?.token) {
        return { ok: false, status: 0, text: "backend info missing", json: null };
      }

      const response = await fetch(`${backend.baseUrl}${targetRoute}`, {
        method: requestInit.method,
        headers: {
          ...(requestInit.headers || {}),
          authorization: `Bearer ${backend.token}`,
        },
        body: requestInit.body,
      });
      const text = await response.text();
      let json: unknown = null;
      try {
        json = text ? JSON.parse(text) : null;
      } catch {
        json = null;
      }
      return { ok: response.ok, status: response.status, text, json };
    },
    { route, init },
  );
}

async function enterMoreMenu(window: Page): Promise<void> {
  await window.getByRole("button", { name: /更多功能/ }).click();
}

async function enterChiefEngineerWorkspace(window: Page): Promise<void> {
  await enterMoreMenu(window);
  await window.getByTestId("enter-chief-engineer-workspace").click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-director-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }
  await enterMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
}

async function enterRuntimeDiagnosticsWorkspace(window: Page): Promise<void> {
  await enterMoreMenu(window);
  await window.getByTestId("enter-runtime-diagnostics").click();
}

async function attachScreenshot(window: Page, testInfo: { outputPath: (name: string) => string; attach: (name: string, options: { path: string; contentType: string }) => Promise<void> }, name: string): Promise<void> {
  const path = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path, fullPage: true });
  await testInfo.attach(name, { path, contentType: "image/png" });
  expect(fs.existsSync(path)).toBe(true);
}

async function attachJson(testInfo: { outputPath: (name: string) => string; attach: (name: string, options: { path: string; contentType: string }) => Promise<void> }, name: string, payload: unknown): Promise<void> {
  const path = testInfo.outputPath(`${name}.json`);
  fs.writeFileSync(path, JSON.stringify(payload, null, 2), { encoding: "utf8" });
  await testInfo.attach(name, { path, contentType: "application/json" });
}

test("settings and role workspaces expose real operational surfaces", async ({ window }, testInfo) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const failedResponses: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });
  window.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  window.on("response", (response) => {
    if ([400, 422, 429, 500].includes(response.status())) {
      failedResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByText("LLM BLOCKED")).toHaveCount(0);

  const seededTask = await backendFetch(window, "/v2/director/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      subject: "E2E Director TaskBoard evidence task",
      description: "Task created by Electron visual audit to verify the Director TaskBoard detail surface.",
      priority: "MEDIUM",
      metadata: {
        pm_task_id: "e2e-taskboard-001",
        goal: "Verify Director task details are rendered from real backend task data.",
        execution_steps: [
          { description: "Open Director TaskBoard" },
          { description: "Select the seeded task" },
        ],
        acceptance_criteria: [
          { description: "Task detail shows PM goal, execution steps, acceptance criteria, and live file activity." },
        ],
        target_files: ["src/frontend/src/app/components/director/DirectorTaskPanel.tsx"],
        current_file_path: "src/frontend/src/app/components/director/DirectorTaskPanel.tsx",
        line_stats: { added: 3, deleted: 1, modified: 2 },
        operation_stats: { create: 0, modify: 1, delete: 0 },
      },
    }),
  });
  expect(seededTask.ok, seededTask.text).toBe(true);
  await attachJson(testInfo, "seeded-director-task-response", seededTask.json);

  const localTasks = await backendFetch(window, "/v2/director/tasks?source=local");
  await attachJson(testInfo, "local-director-tasks-response", localTasks.json);
  expect(localTasks.ok, localTasks.text).toBe(true);
  expect(JSON.stringify(localTasks.json)).toContain("src/frontend/src/app/components/director/DirectorTaskPanel.tsx");

  await window.getByTestId("control-panel-open-settings").click();
  await expect(window.getByText("系统配置")).toBeVisible();
  await window.getByTestId("settings-tab-llm").click();
  await expect(window.getByTestId("llm-config-view-list")).toBeVisible({ timeout: 30000 });
  await expect(window.getByText("正在载入 LLM 配置...")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "settings-llm-before-save");
  await window.getByRole("button", { name: "保存配置" }).click();
  await expect(window.getByText(/LLM 配置保存失败|保存失败/)).toHaveCount(0, { timeout: 8000 });
  await expect(window.getByText("系统配置")).toHaveCount(0, { timeout: 8000 });

  await enterRuntimeDiagnosticsWorkspace(window);
  await expect(window.getByTestId("runtime-diagnostics-workspace")).toBeVisible();
  await expect(window.getByTestId("runtime-diagnostics-card-nats")).toBeVisible();
  await expect(window.getByTestId("runtime-diagnostics-card-websocket")).toBeVisible();
  await expect(window.getByTestId("runtime-diagnostics-card-rate-limit")).toBeVisible();
  await expect(window.getByText("LLM BLOCKED")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "runtime-diagnostics-workspace");
  await window.getByTestId("runtime-diagnostics-back").click();
  await expect(window.getByTestId("runtime-diagnostics-workspace")).toHaveCount(0);

  await enterChiefEngineerWorkspace(window);
  await expect(window.getByTestId("chief-engineer-workspace")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-start-director")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-enter-director")).toBeVisible();
  await expect(window.getByText("施工蓝图证据")).toBeVisible();
  await expect(window.getByText("Director 任务池")).toBeVisible();
  await expect(window.getByText("当前 Director 列表")).toBeVisible();
  await attachScreenshot(window, testInfo, "chief-engineer-workspace");

  await window.getByTestId("chief-engineer-enter-director").click();
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  await expect(window.getByTestId("director-task-filter-unclaimed")).toBeVisible();
  await expect(window.getByTestId("director-task-filter-claimed")).toBeVisible();
  await expect(window.getByTestId("director-task-filter-attention")).toBeVisible();
  const seededTaskCard = window.getByTestId("director-task-item").filter({ hasText: "E2E Director TaskBoard evidence task" }).first();
  await expect(seededTaskCard).toBeVisible({ timeout: 10000 });
  await seededTaskCard.click();
  await expect(window.getByText("实时文件活动")).toBeVisible();
  await expect(window.getByTestId("director-task-detail")).toContainText("PM目标");
  await expect(window.getByTestId("director-task-detail")).toContainText("执行步骤");
  await expect(window.getByTestId("director-task-detail")).toContainText("验收标准");
  await expect(window.getByTestId("director-task-detail")).toContainText("src/frontend/src/app/components/director/DirectorTaskPanel.tsx");
  await attachScreenshot(window, testInfo, "director-taskboard-detail");

  expect(pageErrors, "pageerror should stay empty during settings/role workspace flow").toEqual([]);
  expect(failedResponses, "400/422/429/500 responses should stay empty during settings/role workspace flow").toEqual([]);
  expect(getActionableConsoleErrors(consoleErrors), "console actionable errors should stay empty").toEqual([]);
});
