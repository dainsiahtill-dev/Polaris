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

function cssAlpha(color: string): number {
  const rgbaMatch = color.match(/rgba?\(([^)]+)\)/i);
  if (!rgbaMatch) {
    return color === "transparent" ? 0 : 1;
  }
  const parts = rgbaMatch[1].split(",").map((part) => part.trim());
  if (parts.length < 4) return 1;
  const alpha = Number(parts[3]);
  return Number.isFinite(alpha) ? alpha : 1;
}

async function expectModalLayerOpaque(window: Page, modal: Locator, label: string): Promise<void> {
  const styles = await modal.evaluate((element) => {
    const modalElement = element as HTMLElement;
    const overlayElement = modalElement.parentElement?.parentElement as HTMLElement | null;
    const modalStyle = window.getComputedStyle(modalElement);
    const overlayStyle = overlayElement ? window.getComputedStyle(overlayElement) : null;
    return {
      modalBackground: modalStyle.backgroundColor,
      modalOpacity: modalStyle.opacity,
      overlayBackground: overlayStyle?.backgroundColor || "",
      overlayOpacity: overlayStyle?.opacity || "",
    };
  });

  expect(Number(styles.modalOpacity), `${label} modal opacity should stay fully opaque`).toBe(1);
  expect(cssAlpha(styles.modalBackground), `${label} modal background should be opaque`).toBe(1);
  expect(Number(styles.overlayOpacity || "1"), `${label} overlay opacity should not fade child content`).toBe(1);
  expect(cssAlpha(styles.overlayBackground), `${label} overlay should strongly dim the workspace`).toBeGreaterThanOrEqual(0.85);
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

async function expectRoleSessionStripContained(root: Locator, label: string): Promise<void> {
  const strip = root.getByTestId("ai-role-session-strip").first();
  if (await strip.count() === 0) {
    return;
  }

  await expect(strip).toBeVisible();
  await expectNoElementChildOverlap(strip, `${label} RoleSession strip`);
  await expectNoDirectChildOverlap(strip.getByTestId("ai-role-session-status-row"), `${label} RoleSession status row`);
  await expectNoDirectChildOverlap(strip.getByTestId("ai-role-session-actions"), `${label} RoleSession actions`);
  const metrics = await strip.evaluate((element) => {
    const html = element as HTMLElement;
    const statusRow = html.querySelector("[data-testid='ai-role-session-status-row']") as HTMLElement | null;
    const actions = html.querySelector("[data-testid='ai-role-session-actions']") as HTMLElement | null;
    return {
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
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
  expect(metrics.scrollWidth, `${label} RoleSession strip should remain contained`).toBeLessThanOrEqual(metrics.clientWidth + 4);
  expect(metrics.statusRowScrollWidth, `${label} RoleSession status zone should remain contained`).toBeLessThanOrEqual(metrics.statusRowClientWidth + 4);
  expect(metrics.actionsScrollWidth, `${label} RoleSession action zone should remain contained`).toBeLessThanOrEqual(metrics.actionsClientWidth + 4);
}

test("secondary role and Architect surfaces stay readable and contained", async ({ window }, testInfo) => {
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
  await expectNoDocumentHorizontalOverflow(window, "main shell before secondary sweep");

  await window.getByTestId("open-docs-init").click();
  const docsDialog = window.getByTestId("docs-init-dialog");
  await expect(docsDialog).toBeVisible();
  await expectWithinViewport(window, docsDialog, "Architect docs dialog");
  await window.getByTestId("docs-init-goal-input").fill("Secondary UI sweep Architect layout check");
  await window.getByTestId("docs-init-message-input").fill("Only verify layout. Do not start LLM dialogue.");
  await docsDialog.locator("details summary").first().click();
  await expect(window.getByText("In Scope")).toBeVisible();
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await expectNoDocumentHorizontalOverflow(window, "Architect docs dialog");
  await attachScreenshot(window, testInfo, "secondary-docs-architect-dialog");
  await window.getByRole("button", { name: "Close" }).click();
  await expect(docsDialog).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-chief-engineer-workspace").click();
  const chiefWorkspace = window.getByTestId("chief-engineer-workspace");
  await expect(chiefWorkspace).toBeVisible();
  await expect(window.getByTestId("chief-engineer-backend-strip")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-diagnostics")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-runtime-activity")).toHaveCount(1);
  await expect(window.getByTestId("chief-engineer-director-task-pool")).toHaveCount(1);
  await expectRoleSessionStripContained(chiefWorkspace, "Chief Engineer control");
  await expectNoDocumentHorizontalOverflow(window, "Chief Engineer control");
  await attachScreenshot(window, testInfo, "secondary-chief-control");

  await window.getByTestId("chief-engineer-toggle-dialogue").click();
  await expect(window.getByTestId("chief-engineer-dialogue")).toHaveCount(0);
  await expectNoDocumentHorizontalOverflow(window, "Chief Engineer without side dialogue");
  await attachScreenshot(window, testInfo, "secondary-chief-dialogue-hidden");

  await window.getByTestId("chief-engineer-toggle-workbench").click();
  await expect(chiefWorkspace.getByText("Chief Engineer 工作台", { exact: true }).first()).toBeVisible();
  await expectRoleSessionStripContained(chiefWorkspace, "Chief Engineer workbench");
  await expectNoDocumentHorizontalOverflow(window, "Chief Engineer workbench");
  await attachScreenshot(window, testInfo, "secondary-chief-workbench");

  await window.getByTestId("chief-engineer-open-settings").click();
  await expect(window.getByTestId("settings-modal")).toBeVisible();
  await expectWithinViewport(window, window.getByTestId("settings-modal"), "settings modal from Chief Engineer");
  await expectModalLayerOpaque(window, window.getByTestId("settings-modal"), "settings modal from Chief Engineer");
  await attachScreenshot(window, testInfo, "secondary-chief-settings");
  await window.getByRole("button", { name: "取消" }).click();
  await expect(window.getByTestId("settings-modal")).toHaveCount(0);

  await window.getByTestId("chief-engineer-workspace-back").click();
  await expect(chiefWorkspace).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-agi-workspace").click();
  const agiWorkspace = window.getByTestId("resident-workspace");
  await expect(agiWorkspace).toBeVisible();
  for (const tabId of ["resident-tab-overview", "resident-tab-goals", "resident-tab-decisions"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
    await expectNoDocumentHorizontalOverflow(window, `AGI ${tabId}`);
    await attachScreenshot(window, testInfo, `secondary-${tabId}`);
  }
  await window.getByTestId("resident-tab-goals").click();
  await window.getByRole("button", { name: "新建目标" }).click();
  await expect(window.getByLabel("目标标题")).toBeVisible();
  await expectWithinViewport(window, agiWorkspace, "AGI workspace with goal composer");
  await expectNoDocumentHorizontalOverflow(window, "AGI goal composer");
  await attachScreenshot(window, testInfo, "secondary-agi-goal-composer");
  await agiWorkspace.locator("header button").first().click();
  await expect(agiWorkspace).toHaveCount(0);

  expect(pageErrors, "renderer pageerror should remain empty during secondary surface sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during secondary surface sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
