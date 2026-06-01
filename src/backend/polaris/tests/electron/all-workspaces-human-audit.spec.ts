import fs from "fs";
import type { Page, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";

const ignoredConsoleErrorPatterns = [
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /Unable to preload CSS for \/assets\//i,
];

function actionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error)));
}

async function attachScreenshot(window: Page, testInfo: TestInfo, name: string): Promise<void> {
  const screenshotPath = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(name, { path: screenshotPath, contentType: "image/png" });
  expect(fs.existsSync(screenshotPath)).toBe(true);
}

async function openMoreMenu(window: Page): Promise<void> {
  await window.getByTestId("control-panel-more-menu").click();
}

async function expectNoDocumentHorizontalOverflow(window: Page, label: string): Promise<void> {
  const metrics = await window.evaluate(() => ({
    bodyScrollWidth: document.body.scrollWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  const scrollWidth = Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth);
  expect(
    scrollWidth,
    `${label} should not create page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 4);
}

async function expectVisibleAndContained(window: Page, testId: string, label: string): Promise<void> {
  const locator = window.getByTestId(testId);
  await expect(locator, `${label} should be visible`).toBeVisible();
  const metrics = await locator.evaluate((element) => {
    const htmlElement = element as HTMLElement;
    const rect = htmlElement.getBoundingClientRect();
    return {
      width: rect.width,
      height: rect.height,
      left: rect.left,
      right: rect.right,
      viewportWidth: window.innerWidth,
      scrollWidth: htmlElement.scrollWidth,
      clientWidth: htmlElement.clientWidth,
    };
  });
  expect(metrics.width, `${label} should have stable visible width`).toBeGreaterThan(20);
  expect(metrics.height, `${label} should have stable visible height`).toBeGreaterThan(20);
  expect(metrics.left, `${label} should not sit outside the left viewport edge`).toBeGreaterThanOrEqual(-2);
  expect(metrics.right, `${label} should not sit outside the right viewport edge`).toBeLessThanOrEqual(metrics.viewportWidth + 2);
  expect(metrics.scrollWidth, `${label} should not internally overflow horizontally`).toBeLessThanOrEqual(metrics.clientWidth + 8);
}

async function enterMainFromRole(window: Page, workspaceTestId: string, backTestId: string): Promise<void> {
  await expect(window.getByTestId(workspaceTestId)).toBeVisible();
  await window.getByTestId(backTestId).click();
  await expect(window.getByTestId(workspaceTestId)).toHaveCount(0);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
}

test("human audit reaches every primary workspace and nested role surface", async ({ window }, testInfo) => {
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
    if (response.status() >= 400) {
      failedResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);
  await expectVisibleAndContained(window, "project-progress-panel", "main project progress panel");
  await expectVisibleAndContained(window, "context-sidebar", "context sidebar");
  await expectNoDocumentHorizontalOverflow(window, "main workspace");
  await attachScreenshot(window, testInfo, "all-workspaces-main");

  await window.getByTestId("control-panel-open-settings").click();
  await expect(window.getByTestId("settings-modal")).toBeVisible();
  for (const tabId of ["settings-tab-general", "settings-tab-llm", "settings-tab-arsenal", "settings-tab-services"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
    await expectNoDocumentHorizontalOverflow(window, `settings ${tabId}`);
  }
  await attachScreenshot(window, testInfo, "all-workspaces-settings");
  await window.keyboard.press("Escape");
  await expect(window.getByTestId("settings-modal")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-pm-workspace").click();
  await expectVisibleAndContained(window, "pm-workspace", "PM workspace");
  for (const item of [
    ["pm-nav-任务", "pm-task-panel", "pm tasks"],
    ["pm-nav-实时", "realtime-activity-panel", "pm realtime"],
    ["pm-nav-文档", "pm-document-panel", "pm documents"],
    ["pm-nav-需求", "pm-requirements-panel", "pm requirements"],
    ["pm-nav-历史", "pm-history-task-list", "pm history"],
    ["pm-nav-统计", "pm-workspace", "pm analytics"],
    ["pm-nav-编排", "pm-workbench-panel", "pm workbench"],
  ] as const) {
    await window.getByTestId(item[0]).click();
    await expect(window.getByTestId(item[1]).first()).toBeVisible({ timeout: 15000 });
    await expectNoDocumentHorizontalOverflow(window, item[2]);
  }
  await attachScreenshot(window, testInfo, "all-workspaces-pm-nested");
  await enterMainFromRole(window, "pm-workspace", "pm-workspace-back");

  await openMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
  await expectVisibleAndContained(window, "director-workspace", "Director workspace");
  for (const item of [
    ["director-nav-任务", "director-task-board", "director tasks"],
    ["director-nav-实时", "realtime-activity-panel", "director realtime"],
    ["director-nav-代码", "director-code-open-file", "director code"],
    ["director-nav-终端", "director-terminal-clear", "director terminal"],
    ["director-nav-调试", "director-workspace", "director debug"],
    ["director-nav-策略", "director-strategy-panel", "director strategy"],
    ["director-nav-工作台", "director-workbench-panel", "director workbench"],
  ] as const) {
    await window.getByTestId(item[0]).click();
    await expect(window.getByTestId(item[1]).first()).toBeVisible({ timeout: 15000 });
    await expectNoDocumentHorizontalOverflow(window, item[2]);
  }
  await attachScreenshot(window, testInfo, "all-workspaces-director-nested");
  await enterMainFromRole(window, "director-workspace", "director-workspace-back");

  await openMoreMenu(window);
  await window.getByTestId("enter-chief-engineer-workspace").click();
  await expectVisibleAndContained(window, "chief-engineer-workspace", "Chief Engineer workspace");
  await expect(window.getByTestId("chief-engineer-backend-strip")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-diagnostics")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-director-task-pool")).toBeVisible();
  await window.getByTestId("chief-engineer-toggle-workbench").click();
  await expect(window.getByTestId("chief-engineer-workbench-panel")).toBeVisible({ timeout: 15000 });
  await attachScreenshot(window, testInfo, "all-workspaces-chief-engineer");
  await enterMainFromRole(window, "chief-engineer-workspace", "chief-engineer-workspace-back");

  await openMoreMenu(window);
  await window.getByTestId("enter-agi-workspace").click();
  await expectVisibleAndContained(window, "resident-workspace", "Resident workspace");
  for (const tabId of ["resident-tab-overview", "resident-tab-goals", "resident-tab-decisions"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
    await expectNoDocumentHorizontalOverflow(window, `resident ${tabId}`);
  }
  await attachScreenshot(window, testInfo, "all-workspaces-resident");
  await window.locator("[data-testid='resident-workspace'] header button").first().click();
  await expect(window.getByTestId("resident-workspace")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-runtime-diagnostics").click();
  await expectVisibleAndContained(window, "runtime-diagnostics-workspace", "runtime diagnostics workspace");
  await window.getByTestId("runtime-diagnostics-refresh").click();
  await expect(window.getByTestId("runtime-diagnostics-card-websocket")).toBeVisible();
  await attachScreenshot(window, testInfo, "all-workspaces-runtime-diagnostics");
  await enterMainFromRole(window, "runtime-diagnostics-workspace", "runtime-diagnostics-back");

  expect(pageErrors, "renderer pageerror should stay empty during all-workspaces human audit").toEqual([]);
  expect(failedResponses, "HTTP failures should stay empty during all-workspaces human audit").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should stay empty").toEqual([]);
});
