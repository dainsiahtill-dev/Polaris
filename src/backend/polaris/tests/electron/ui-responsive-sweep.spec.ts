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

async function expectOpaqueSurface(window: Page, locator: Locator, label: string): Promise<void> {
  const alpha = await locator.evaluate((element) => {
    const color = window.getComputedStyle(element as HTMLElement).backgroundColor;
    const rgba = color.match(/rgba?\(([^)]+)\)/i)?.[1]
      .split(",")
      .map((part) => Number.parseFloat(part.trim()));
    if (!rgba) return 0;
    return rgba.length >= 4 ? rgba[3] : 1;
  });
  expect(alpha, `${label} background should be opaque enough to hide underlying UI`).toBeGreaterThanOrEqual(0.98);
}

async function expectNoControlOverlap(window: Page): Promise<void> {
  const overlaps = await window.evaluate(() => {
    const ids = [
      "control-panel-toggle-monitor",
      "control-panel-pm-toggle",
      "control-panel-pm-run-once",
      "control-panel-director-toggle",
      "control-panel-enter-factory",
      "control-panel-health-ping",
      "control-panel-open-logs",
      "control-panel-more-menu",
      "control-panel-refresh",
      "control-panel-open-settings",
      "control-panel-toggle-terminal",
    ];
    const boxes = ids
      .map((id) => {
        const element = document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
        if (!element) return null;
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        if (rect.width <= 0 || rect.height <= 0 || style.display === "none" || style.visibility === "hidden") {
          return null;
        }
        return {
          id,
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

  expect(overlaps, "top control bar actions should not overlap").toEqual([]);
}

async function expectNoRowChildOverlap(window: Page, containerTestId: string, label: string): Promise<void> {
  const overlaps = await window.evaluate((testId) => {
    const container = document.querySelector(`[data-testid="${testId}"]`) as HTMLElement | null;
    if (!container) return [`${testId} not found`];
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
            id: htmlElement.getAttribute("data-testid") || htmlElement.getAttribute("aria-label") || htmlElement.textContent?.trim().slice(0, 24) || `item-${itemIndex}`,
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
  }, containerTestId);

  expect(overlaps, `${label} row items should not overlap`).toEqual([]);
}

async function expectNoTransientConnectionToast(window: Page): Promise<void> {
  await expect(window.getByText("正在重连...")).toHaveCount(0);
  await expect(window.getByText("连接已恢复")).toHaveCount(0);
}

async function openMoreMenu(window: Page): Promise<void> {
  await window.getByTestId("control-panel-more-menu").click();
  await expect(window.getByRole("menu")).toBeVisible();
  await expectWithinViewport(window, window.getByRole("menu"), "more menu");
}

test("compact desktop UI surfaces stay readable and contained", async ({ window }, testInfo) => {
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

  await window.setViewportSize({ width: 1366, height: 768 });
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expectNoControlOverlap(window);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "responsive-main");

  await openMoreMenu(window);
  for (const itemId of [
    "enter-pm-workspace",
    "enter-chief-engineer-workspace",
    "enter-director-workspace",
    "enter-agi-workspace",
    "enter-runtime-diagnostics",
    "open-brain-menu-item",
    "open-intervention-center-menu-item",
  ]) {
    await expect(window.getByTestId(itemId)).toBeVisible();
  }
  await attachScreenshot(window, testInfo, "responsive-more-menu");
  await window.keyboard.press("Escape");

  await window.getByTestId("control-panel-toggle-monitor").click();
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expectNoControlOverlap(window);
  await attachScreenshot(window, testInfo, "responsive-monitor-hidden");
  await window.getByTestId("control-panel-toggle-monitor").click();

  await window.getByRole("button", { name: "案卷历史" }).click();
  await expect(window.getByTestId("history-drawer")).toBeVisible();
  await window.waitForTimeout(350);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await expectWithinViewport(window, window.getByTestId("history-drawer"), "history drawer");
  await attachScreenshot(window, testInfo, "responsive-history-drawer");
  await window.keyboard.press("Escape");
  await expect(window.getByTestId("history-drawer")).toHaveCount(0);

  await window.getByTestId("control-panel-open-settings").click();
  await expect(window.getByText("系统配置")).toBeVisible();
  for (const tabId of ["settings-tab-general", "settings-tab-llm", "settings-tab-arsenal", "settings-tab-services"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
    await expectNoTransientConnectionToast(window);
    expect(actionableConsoleErrors(consoleErrors), `${tabId} should not emit actionable console errors`).toEqual([]);
  }
  await expectNoDocumentHorizontalOverflow(window);
  await expectOpaqueSurface(window, window.getByTestId("settings-modal"), "settings modal");
  await attachScreenshot(window, testInfo, "responsive-settings");
  expect(actionableConsoleErrors(consoleErrors), "settings modal should not emit actionable console errors").toEqual([]);
  await window.getByRole("button", { name: "取消" }).click();
  await expect(window.getByText("系统配置")).toHaveCount(0);

  await window.getByTestId("control-panel-open-logs").click();
  await expect(window.getByTestId("logs-modal")).toBeVisible();
  await expect(window.getByTestId("logs-modal")).toHaveClass(/bg-black\/85/);
  await expectWithinViewport(window, window.getByTestId("logs-modal-panel"), "logs modal");
  await expectOpaqueSurface(window, window.getByTestId("logs-modal-panel"), "logs modal panel");
  await attachScreenshot(window, testInfo, "responsive-logs-modal");
  expect(actionableConsoleErrors(consoleErrors), "logs modal should not emit actionable console errors").toEqual([]);
  await window.getByTestId("logs-modal-close").click();
  await expect(window.getByTestId("logs-modal")).toHaveCount(0);

  await window.getByTestId("control-panel-toggle-terminal").click();
  await expect(window.getByTestId("terminal-panel")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await attachScreenshot(window, testInfo, "responsive-terminal");
  await window.locator("[title='Close Terminal']").click();
  await expect(window.getByTestId("terminal-panel")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-pm-workspace").click();
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expectNoRowChildOverlap(window, "ai-role-session-strip", "PM RoleSession strip");
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "responsive-pm-workspace");
  await window.getByTestId("pm-workspace-back").click();

  await openMoreMenu(window);
  await window.getByTestId("enter-chief-engineer-workspace").click();
  await expect(window.getByTestId("chief-engineer-workspace")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "responsive-chief-engineer-workspace");
  await window.getByTestId("chief-engineer-workspace-back").click();

  await openMoreMenu(window);
  await window.getByTestId("enter-runtime-diagnostics").click();
  await expect(window.getByTestId("runtime-diagnostics-workspace")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "responsive-runtime-diagnostics");
  await window.getByTestId("runtime-diagnostics-back").click();

  await openMoreMenu(window);
  await window.getByTestId("open-intervention-center-menu-item").click();
  await expect(window.getByTestId("intervention-center")).toBeVisible();
  await expect(window.getByTestId("open-intervention-center-menu-item")).toHaveCount(0);
  await expectNoTransientConnectionToast(window);
  await attachScreenshot(window, testInfo, "responsive-intervention-center");
  expect(actionableConsoleErrors(consoleErrors), "intervention center should not emit actionable console errors").toEqual([]);
  await window.keyboard.press("Escape");

  expect(pageErrors, "renderer pageerror should remain empty during responsive UI sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during responsive UI sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});

test("narrow desktop shell essentials stay contained", async ({ window }, testInfo) => {
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
  await expectNoDocumentHorizontalOverflow(window);
  await expectNoControlOverlap(window);
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await attachScreenshot(window, testInfo, "narrow-main");

  await openMoreMenu(window);
  await expectWithinViewport(window, window.getByRole("menu"), "narrow more menu");
  await attachScreenshot(window, testInfo, "narrow-more-menu");
  await window.keyboard.press("Escape");

  await window.getByRole("button", { name: "案卷历史" }).click();
  await expect(window.getByTestId("history-drawer")).toBeVisible();
  await window.waitForTimeout(350);
  await expectWithinViewport(window, window.getByTestId("history-drawer"), "narrow history drawer");
  await attachScreenshot(window, testInfo, "narrow-history-drawer");
  await window.keyboard.press("Escape");
  await expect(window.getByTestId("history-drawer")).toHaveCount(0);

  await window.getByTestId("control-panel-open-settings").click();
  await expect(window.getByText("系统配置")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expectWithinViewport(window, window.getByTestId("settings-modal"), "narrow settings modal");
  await expectOpaqueSurface(window, window.getByTestId("settings-modal"), "narrow settings modal");
  await attachScreenshot(window, testInfo, "narrow-settings");
  await window.getByRole("button", { name: "取消" }).click();

  await openMoreMenu(window);
  await window.getByTestId("enter-pm-workspace").click();
  await expect(window.getByTestId("pm-workspace")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await expectNoRowChildOverlap(window, "ai-role-session-strip", "narrow PM RoleSession strip");
  await attachScreenshot(window, testInfo, "narrow-pm-workspace");
  await window.getByTestId("pm-workspace-back").click();

  await openMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window);
  await attachScreenshot(window, testInfo, "narrow-director-workspace");

  expect(pageErrors, "renderer pageerror should remain empty during narrow UI sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during narrow UI sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
