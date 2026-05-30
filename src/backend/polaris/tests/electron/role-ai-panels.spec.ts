import fs from "fs";
import type { Locator, Page, TestInfo } from "@playwright/test";
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
  await expect(window.getByRole("menu")).toBeVisible();
}

async function expectNoDocumentHorizontalOverflow(window: Page, label: string): Promise<void> {
  const metrics = await window.evaluate(() => ({
    bodyScrollWidth: document.body.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    documentScrollWidth: document.documentElement.scrollWidth,
  }));
  const scrollWidth = Math.max(metrics.bodyScrollWidth, metrics.documentScrollWidth);
  expect(
    scrollWidth,
    `${label} should not create page-level horizontal overflow: ${JSON.stringify(metrics)}`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 4);
}

async function expectWithinViewport(window: Page, locator: Locator, label: string): Promise<void> {
  const viewport = window.viewportSize();
  const box = await locator.boundingBox();
  expect(viewport, `${label} viewport should exist`).not.toBeNull();
  expect(box, `${label} should have a bounding box`).not.toBeNull();
  if (!viewport || !box) return;
  const tolerance = 4;
  expect(box.x, `${label} left edge should stay in viewport`).toBeGreaterThanOrEqual(-tolerance);
  expect(box.x + box.width, `${label} right edge should stay in viewport`).toBeLessThanOrEqual(viewport.width + tolerance);
  expect(box.y, `${label} top edge should stay in viewport`).toBeGreaterThanOrEqual(-tolerance);
  expect(box.y + box.height, `${label} bottom edge should stay in viewport`).toBeLessThanOrEqual(viewport.height + tolerance);
}

async function expectPanelContained(window: Page, locator: Locator, label: string): Promise<void> {
  await expect(locator).toBeVisible();
  await expectWithinViewport(window, locator, label);
  const metrics = await locator.evaluate((element) => {
    const html = element as HTMLElement;
    return {
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
    };
  });
  expect(metrics.scrollWidth, `${label} should not overflow horizontally`).toBeLessThanOrEqual(metrics.clientWidth + 4);
  await expectNoDocumentHorizontalOverflow(window, label);
}

test("AI RoleSession secondary panels stay readable and contained", async ({ window }, testInfo) => {
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

  await window.setViewportSize({ width: 1280, height: 720 });
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();

  await openMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
  const directorWorkspace = window.getByTestId("director-workspace");
  await expect(directorWorkspace).toBeVisible();
  await expect(directorWorkspace.getByTestId("ai-role-session-strip").first()).toBeVisible();

  await expect.poll(async () => {
    const text = await directorWorkspace.getByTestId("ai-role-session-id").first().innerText();
    return text.includes("creating") || text.includes("unavailable") ? "pending" : "ready";
  }, { timeout: 20_000 }).toBe("ready");

  await directorWorkspace.getByTestId("ai-role-session-list").first().click();
  await expectPanelContained(window, directorWorkspace.getByTestId("ai-role-session-list-panel").first(), "RoleSession list panel");
  await attachScreenshot(window, testInfo, "role-ai-session-list");
  await directorWorkspace.getByTestId("ai-role-session-list").first().click();
  await expect(directorWorkspace.getByTestId("ai-role-session-list-panel")).toHaveCount(0);

  await directorWorkspace.getByTestId("ai-role-session-evidence-toggle").first().click();
  await expectPanelContained(window, directorWorkspace.getByTestId("role-session-evidence-panel").first(), "RoleSession evidence panel");
  await attachScreenshot(window, testInfo, "role-ai-session-evidence");
  await directorWorkspace.getByTestId("ai-role-session-evidence-toggle").first().click();
  await expect(directorWorkspace.getByTestId("role-session-evidence-panel")).toHaveCount(0);

  await directorWorkspace.getByTestId("ai-role-session-memory-toggle").first().click();
  const memoryPanel = directorWorkspace.getByTestId("ai-role-session-memory-panel").first();
  await expectPanelContained(window, memoryPanel, "RoleSession memory panel");
  await memoryPanel.getByTestId("ai-role-session-memory-query").fill("task");
  await memoryPanel.getByTestId("ai-role-session-memory-search").click();
  await expect(memoryPanel.getByTestId("ai-role-session-memory-detail")).toBeVisible();
  await attachScreenshot(window, testInfo, "role-ai-session-memory");
  await directorWorkspace.getByTestId("ai-role-session-memory-toggle").first().click();
  await expect(directorWorkspace.getByTestId("ai-role-session-memory-panel")).toHaveCount(0);

  await directorWorkspace.getByTestId("ai-role-session-snapshot-toggle").first().click();
  const snapshotPanel = directorWorkspace.getByTestId("ai-role-session-snapshot-panel").first();
  await expectPanelContained(window, snapshotPanel, "RoleSession snapshot panel");
  await expect(snapshotPanel.getByTestId("ai-role-session-snapshot-preview")).toBeVisible();
  await snapshotPanel.getByTestId("ai-role-session-snapshot-format-markdown").click();
  await expect(snapshotPanel.getByTestId("ai-role-session-snapshot-preview")).toBeVisible();
  await attachScreenshot(window, testInfo, "role-ai-session-snapshot");

  expect(pageErrors, "renderer pageerror should remain empty during AI RoleSession panel sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during AI RoleSession panel sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
