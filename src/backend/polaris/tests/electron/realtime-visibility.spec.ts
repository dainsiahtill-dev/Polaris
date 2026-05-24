import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const ignoredConsoleErrorPatterns = [
  /has been blocked by CORS policy/i,
  /Failed to load resource: net::ERR_FAILED/i,
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /TypeError: Failed to fetch/i,
  /Unable to preload CSS for \/assets\//i,
];

function getActionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error)));
}

async function enterPmWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-pm-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  const menuEntry = window.getByTestId("enter-pm-workspace");
  if (await menuEntry.isVisible().catch(() => false)) {
    await menuEntry.click();
    return;
  }
  await window.getByRole("menuitem", { name: /PM\s*(工作区|Workspace)/i }).click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='enter-director-workspace']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  const menuEntry = window.getByTestId("enter-director-workspace");
  if (await menuEntry.isVisible().catch(() => false)) {
    await menuEntry.click();
    return;
  }
  await window.getByRole("menuitem", { name: /Director\s*(工作区|Workspace)/i }).click();
}

test("PM + Director show current task/progress visibility", async ({ window }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const http422s: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });
  window.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  window.on("response", (response) => {
    if (response.status() === 422) {
      void response.text().then((body) => {
        const request = response.request();
        const headers = JSON.stringify(request.headers());
        const postData = request.postData() ?? '';
        http422s.push(`${request.method()} ${response.url()} headers=${headers} body=${postData} :: ${body}`);
      });
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible();

  await enterPmWorkspace(window);
  await expect(window.locator("[data-testid='pm-workspace']")).toBeVisible();
  await expect(window.getByText("PM Console", { exact: false }).first()).toBeVisible();
  await expect(
    window.locator("[data-testid='pm-workspace']").getByText(/\d+\s*\/\s*\d+/).first(),
  ).toBeVisible();
  await window.locator("[data-testid='pm-workspace-back']").click();
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible();

  await enterDirectorWorkspace(window);
  await expect(window.locator("[data-testid='director-workspace']")).toBeVisible();
  await expect(window.getByText("Director Console", { exact: false }).first()).toBeVisible();
  await expect(window.locator("[data-testid='director-workspace']").getByText("进度").first()).toBeVisible();
  await expect(
    window.locator("[data-testid='director-workspace']").getByText(/\d+\s*\/\s*\d+/).first(),
  ).toBeVisible();

  expect(pageErrors, "pageerror should remain empty across PM/Director visibility flow").toEqual([]);
  expect(http422s, "422 responses should remain empty across PM/Director visibility flow").toEqual([]);
  expect(getActionableConsoleErrors(consoleErrors), "console actionable errors should remain empty").toEqual([]);
});

test("Factory + Mission Control visibility surfaces are reachable", async ({ window }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const http422s: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });
  window.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  window.on("response", (response) => {
    if (response.status() === 422) {
      void response.text().then((body) => {
        const request = response.request();
        const headers = JSON.stringify(request.headers());
        const postData = request.postData() ?? '';
        http422s.push(`${request.method()} ${response.url()} headers=${headers} body=${postData} :: ${body}`);
      });
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);

  await window.locator("button[title*='Factory 模式']").click();
  await expect(window.getByText("Factory 模式", { exact: false }).first()).toBeVisible();
  await expect(window.getByTestId("factory-layered-layout")).toBeVisible();
  await expect(window.getByTestId("factory-role-layer-pm")).toBeVisible();
  await expect(window.getByTestId("factory-role-layer-chief_engineer")).toBeVisible();
  await expect(window.getByTestId("factory-role-layer-director")).toBeVisible();
  await expect(window.getByTestId("factory-role-flow-rail")).toBeVisible();
  await expect(window.getByTestId("factory-focused-layer")).toBeVisible();
  await expect(window.getByTestId("factory-operations-rail")).toBeVisible();
  await expect(window.getByTestId("factory-operations-rail")).toContainText("运行观测");
  await expect(window.getByTestId("factory-pm-layer")).toContainText("PM 任务合同层");
  await expect(window.getByTestId("factory-pm-layer")).toContainText("交接门禁");
  await window.getByLabel("返回主界面").click();
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible();

  await window.getByRole("button", { name: /更多功能/ }).click();
  await expect(window.getByRole("menuitem", { name: /明镜台\s*\(Brain\)|明镜台/i })).toBeVisible();
  await window.getByRole("menuitem", { name: /明镜台\s*\(Brain\)|明镜台/i }).click();
  await expect(window.getByText("当前批次主战场", { exact: false }).first()).toBeVisible();

  expect(pageErrors, "pageerror should remain empty across Factory/Mission flow").toEqual([]);
  expect(http422s, "422 responses should remain empty across Factory/Mission flow").toEqual([]);
  expect(getActionableConsoleErrors(consoleErrors), "console actionable errors should remain empty").toEqual([]);
});
