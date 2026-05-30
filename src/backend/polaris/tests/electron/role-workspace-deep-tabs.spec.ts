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

async function expectNoElementChildOverlap(locator: Locator, label: string): Promise<void> {
  const overlaps = await locator.evaluate((container) => {
    const rows = Array.from(container.children) as HTMLElement[];
    const result: string[] = [];

    for (const [rowIndex, row] of rows.entries()) {
      const boxes = Array.from(row.children)
        .map((element, itemIndex) => {
          const htmlElement = element as HTMLElement;
          const rect = htmlElement.getBoundingClientRect();
          const style = window.getComputedStyle(htmlElement);
          if (rect.width <= 0 || rect.height <= 0 || style.display === "none" || style.visibility === "hidden") {
            return null;
          }
          return {
            id: htmlElement.getAttribute("data-testid")
              || htmlElement.getAttribute("aria-label")
              || htmlElement.textContent?.trim().slice(0, 24)
              || `item-${itemIndex}`,
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
          };
        })
        .filter(Boolean) as Array<{ id: string; left: number; top: number; right: number; bottom: number }>;

      for (let i = 0; i < boxes.length; i += 1) {
        for (let j = i + 1; j < boxes.length; j += 1) {
          const a = boxes[i];
          const b = boxes[j];
          const overlapWidth = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
          const overlapHeight = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
          if (overlapWidth > 2 && overlapHeight > 2) {
            result.push(`row ${rowIndex}: ${a.id} overlaps ${b.id} by ${Math.round(overlapWidth)}x${Math.round(overlapHeight)}`);
          }
        }
      }
    }

    return result;
  });

  expect(overlaps, `${label} child controls should not overlap`).toEqual([]);
}

async function expectNoDirectChildOverlap(locator: Locator, label: string): Promise<void> {
  const overlaps = await locator.evaluate((container) => {
    const boxes = Array.from(container.children)
      .map((element, itemIndex) => {
        const htmlElement = element as HTMLElement;
        const rect = htmlElement.getBoundingClientRect();
        const style = window.getComputedStyle(htmlElement);
        if (rect.width <= 0 || rect.height <= 0 || style.display === "none" || style.visibility === "hidden") {
          return null;
        }
        return {
          id: htmlElement.getAttribute("data-testid")
            || htmlElement.getAttribute("aria-label")
            || htmlElement.textContent?.trim().slice(0, 24)
            || `item-${itemIndex}`,
          left: rect.left,
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
        };
      })
      .filter(Boolean) as Array<{ id: string; left: number; top: number; right: number; bottom: number }>;

    const result: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const overlapWidth = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const overlapHeight = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        if (overlapWidth > 2 && overlapHeight > 2) {
          result.push(`${a.id} overlaps ${b.id} by ${Math.round(overlapWidth)}x${Math.round(overlapHeight)}`);
        }
      }
    }
    return result;
  });

  expect(overlaps, `${label} direct controls should not overlap`).toEqual([]);
}

async function expectRoleSessionStripContained(workspace: Locator, label: string): Promise<void> {
  const strip = workspace.getByTestId("ai-role-session-strip").first();
  if (await strip.count() === 0) {
    return;
  }

  await expect(strip).toBeVisible();
  await expectNoElementChildOverlap(strip, `${label} RoleSession strip`);
  await expectNoDirectChildOverlap(strip.getByTestId("ai-role-session-status-row"), `${label} RoleSession status row`);
  await expectNoDirectChildOverlap(strip.getByTestId("ai-role-session-actions"), `${label} RoleSession actions`);
  const metrics = await strip.evaluate((element) => {
    const html = element as HTMLElement;
    const rect = html.getBoundingClientRect();
    const statusRow = html.querySelector("[data-testid='ai-role-session-status-row']") as HTMLElement | null;
    const actions = html.querySelector("[data-testid='ai-role-session-actions']") as HTMLElement | null;
    return {
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
      rectWidth: rect.width,
      statusRowExists: Boolean(statusRow),
      actionsExists: Boolean(actions),
      statusRowClientWidth: statusRow?.clientWidth ?? 0,
      statusRowScrollWidth: statusRow?.scrollWidth ?? 0,
      actionsClientWidth: actions?.clientWidth ?? 0,
      actionsScrollWidth: actions?.scrollWidth ?? 0,
    };
  });
  expect(metrics.statusRowExists, `${label} RoleSession strip should expose a status zone`).toBe(true);
  expect(metrics.actionsExists, `${label} RoleSession strip should expose an action zone`).toBe(true);
  expect(metrics.rectWidth, `${label} RoleSession strip should be visible`).toBeGreaterThan(240);
  expect(metrics.scrollWidth, `${label} RoleSession strip should not escape its panel`).toBeLessThanOrEqual(metrics.clientWidth + 4);
  expect(metrics.statusRowScrollWidth, `${label} RoleSession status zone should remain contained`).toBeLessThanOrEqual(metrics.statusRowClientWidth + 4);
  expect(metrics.actionsScrollWidth, `${label} RoleSession actions should remain contained`).toBeLessThanOrEqual(metrics.actionsClientWidth + 4);
}

async function clickWorkspaceTab(window: Page, workspace: Locator, label: string): Promise<void> {
  await workspace.locator(`nav button[title="${label}"]`).click();
  await window.waitForTimeout(100);
}

async function exerciseWorkspaceTabs(
  window: Page,
  testInfo: TestInfo,
  workspace: Locator,
  workspaceLabel: string,
  tabs: Array<{ label: string; screenshotName: string }>,
): Promise<void> {
  for (const tab of tabs) {
    await clickWorkspaceTab(window, workspace, tab.label);
    await expectNoDocumentHorizontalOverflow(window, `${workspaceLabel} ${tab.label}`);
    await expectRoleSessionStripContained(workspace, `${workspaceLabel} ${tab.label}`);
    await attachScreenshot(window, testInfo, tab.screenshotName);
  }
}

test("PM and Director deep workspace tabs remain contained", async ({ window }, testInfo) => {
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
  await window.getByTestId("enter-pm-workspace").click();
  const pmWorkspace = window.getByTestId("pm-workspace");
  await expect(pmWorkspace).toBeVisible();
  await exerciseWorkspaceTabs(window, testInfo, pmWorkspace, "PM", [
    { label: "任务", screenshotName: "pm-tab-tasks" },
    { label: "实时", screenshotName: "pm-tab-activity" },
    { label: "文档", screenshotName: "pm-tab-documents" },
    { label: "需求", screenshotName: "pm-tab-requirements" },
    { label: "历史", screenshotName: "pm-tab-history" },
    { label: "统计", screenshotName: "pm-tab-analytics" },
    { label: "编排", screenshotName: "pm-tab-workbench" },
  ]);
  await window.getByTestId("pm-workspace-back").click();
  await expect(pmWorkspace).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
  const directorWorkspace = window.getByTestId("director-workspace");
  await expect(directorWorkspace).toBeVisible();
  await exerciseWorkspaceTabs(window, testInfo, directorWorkspace, "Director", [
    { label: "任务", screenshotName: "director-tab-tasks" },
    { label: "实时", screenshotName: "director-tab-activity" },
    { label: "代码", screenshotName: "director-tab-code" },
    { label: "终端", screenshotName: "director-tab-terminal" },
    { label: "调试", screenshotName: "director-tab-debug" },
    { label: "策略", screenshotName: "director-tab-strategy" },
    { label: "工作台", screenshotName: "director-tab-workbench" },
  ]);

  expect(pageErrors, "renderer pageerror should remain empty during role deep tab sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during role deep tab sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
