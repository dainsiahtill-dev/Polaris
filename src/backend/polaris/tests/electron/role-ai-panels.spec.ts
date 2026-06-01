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

async function expectRoleSessionToolbarStable(workspace: Locator, label: string): Promise<void> {
  const roleSessionStrip = workspace.getByTestId("ai-role-session-strip").first();
  await expect(roleSessionStrip, `${label} RoleSession strip should be visible`).toBeVisible();
  await expectChildrenDoNotOverlap(
    roleSessionStrip.getByTestId("ai-role-session-status-row").first(),
    `${label} RoleSession status chips`,
  );
  await expectChildrenDoNotOverlap(
    roleSessionStrip.getByTestId("ai-role-session-actions").first(),
    `${label} RoleSession action toolbar`,
  );

  const metrics = await roleSessionStrip.evaluate((element) => {
    const strip = element as HTMLElement;
    const actionButtons = Array.from(strip.querySelectorAll("[data-testid='ai-role-session-actions'] button"))
      .map((button) => {
        const rect = button.getBoundingClientRect();
        return {
          text: (button.textContent || "").trim(),
          width: rect.width,
          height: rect.height,
        };
      });
    return {
      clientWidth: strip.clientWidth,
      scrollWidth: strip.scrollWidth,
      actionButtons,
    };
  });

  expect(metrics.scrollWidth, `${label} RoleSession strip should not overflow horizontally`).toBeLessThanOrEqual(metrics.clientWidth + 4);
  expect(
    metrics.actionButtons.filter((button) => button.text),
    `${label} RoleSession actions should remain icon-only`,
  ).toEqual([]);
  expect(
    metrics.actionButtons.filter((button) => button.width > 34 || button.height > 34),
    `${label} RoleSession actions should keep compact dimensions`,
  ).toEqual([]);
}

async function expectChildrenDoNotOverlap(locator: Locator, label: string): Promise<void> {
  const overlaps = await locator.evaluate((element) => {
    const tolerance = 1;
    const items = Array.from(element.children).map((child, index) => {
      const rect = child.getBoundingClientRect();
      return {
        index,
        text: (child.textContent || child.getAttribute("aria-label") || child.getAttribute("title") || "").trim(),
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
      };
    }).filter((item) => item.right > item.left && item.bottom > item.top);

    const collisions: Array<{ a: number; b: number; aText: string; bText: string }> = [];
    for (let i = 0; i < items.length; i += 1) {
      for (let j = i + 1; j < items.length; j += 1) {
        const a = items[i];
        const b = items[j];
        const xOverlap = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const yOverlap = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (xOverlap > tolerance && yOverlap > tolerance) {
          collisions.push({ a: a.index, b: b.index, aText: a.text, bText: b.text });
        }
      }
    }
    return collisions;
  });

  expect(overlaps, `${label} children should not visually overlap`).toEqual([]);
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
  const roleSessionStrip = directorWorkspace.getByTestId("ai-role-session-strip").first();
  await expect(roleSessionStrip).toBeVisible();

  await expect.poll(async () => {
    const text = await directorWorkspace.getByTestId("ai-role-session-id").first().innerText();
    return text.includes("creating") || text.includes("unavailable") ? "pending" : "ready";
  }, { timeout: 20_000 }).toBe("ready");

  await expectRoleSessionToolbarStable(directorWorkspace, "1280px");
  await window.setViewportSize({ width: 1024, height: 720 });
  await expectRoleSessionToolbarStable(directorWorkspace, "1024px");
  await expectNoDocumentHorizontalOverflow(window, "Director AI assistant compact RoleSession toolbar");

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
