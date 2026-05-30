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

async function expectNoDocumentHorizontalOverflow(window: Page): Promise<void> {
  const metrics = await window.evaluate(() => ({
    bodyScrollWidth: document.body.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  const scrollWidth = Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth);
  expect(
    scrollWidth,
    `document should not create page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 4);
}

test("deep human navigation covers PM, AGI, monitor, context, and history surfaces", async ({ window }, testInfo) => {
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
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();

  for (const tabId of [
    "process-monitor-tab-pm",
    "process-monitor-tab-director",
    "process-monitor-tab-files",
    "process-monitor-tab-usage",
  ]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
  }
  await attachScreenshot(window, testInfo, "deep-navigation-monitor-tabs");

  for (const tabId of ["context-tab-dialogue", "context-tab-memos", "context-tab-snapshot", "context-tab-agi"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
    if (tabId === "context-tab-memos") {
      await expect(window.getByTestId("memo-panel")).toBeVisible();
    }
    if (tabId === "context-tab-snapshot") {
      await expect(window.getByTestId("snapshot-panel")).toBeVisible();
    }
    await expectNoDocumentHorizontalOverflow(window);
  }
  await expect(window.getByText("AGI 摘要")).toBeVisible();
  await attachScreenshot(window, testInfo, "deep-navigation-context-agi");

  await openMoreMenu(window);
  await window.getByTestId("open-brain-menu-item").click();
  await expect(window.getByText(/AGI 摘要|忆库/)).toBeVisible();

  await window.getByRole("button", { name: "案卷历史" }).click();
  await expect(window.getByTestId("history-drawer")).toBeVisible();
  await expect(window.getByText("案卷历史").first()).toBeVisible();
  await attachScreenshot(window, testInfo, "deep-navigation-history-drawer");
  await window.keyboard.press("Escape");
  await expect(window.getByTestId("history-drawer")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-pm-workspace").click();
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  await expect(window.getByTestId("pm-backend-evidence-strip")).toBeVisible();
  await attachScreenshot(window, testInfo, "deep-navigation-pm-workspace");
  await window.getByTestId("pm-workspace-back").click();
  await expect(window.getByTestId("pm-workspace")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-agi-workspace").click();
  await expect(window.getByTestId("resident-workspace")).toBeVisible();
  for (const tabId of ["resident-tab-overview", "resident-tab-goals", "resident-tab-decisions"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
  }
  await attachScreenshot(window, testInfo, "deep-navigation-resident-workspace");
  await window.locator("[data-testid='resident-workspace'] header button").first().click();
  await expect(window.getByTestId("resident-workspace")).toHaveCount(0);

  expect(pageErrors, "renderer pageerror should remain empty during deep navigation").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during deep navigation").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
