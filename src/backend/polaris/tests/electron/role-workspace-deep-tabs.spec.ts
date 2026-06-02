import fs from "fs";
import path from "path";
import type { Locator, Page, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";

const ignoredConsoleErrorPatterns = [
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /Unable to preload CSS for \/assets\//i,
];

function actionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error)));
}

type BackendInfo = { baseUrl?: string; token?: string };
type ImageDimensions = { width: number; height: number };

function readJpegDimensions(filePath: string): ImageDimensions {
  const bytes = fs.readFileSync(filePath);
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
    throw new Error(`not a JPEG file: ${filePath}`);
  }

  let offset = 2;
  while (offset + 9 < bytes.length) {
    while (offset < bytes.length && bytes[offset] === 0xff) {
      offset += 1;
    }
    const marker = bytes[offset];
    offset += 1;

    if (marker === 0xd9 || marker === 0xda) {
      break;
    }
    if (offset + 2 > bytes.length) {
      break;
    }

    const segmentLength = bytes.readUInt16BE(offset);
    if (segmentLength < 2 || offset + segmentLength > bytes.length) {
      break;
    }

    const isStartOfFrame = marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker);
    if (isStartOfFrame) {
      return {
        height: bytes.readUInt16BE(offset + 3),
        width: bytes.readUInt16BE(offset + 5),
      };
    }

    offset += segmentLength;
  }

  throw new Error(`JPEG dimensions not found: ${filePath}`);
}

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const backend = await window.evaluate(async () => {
    const api = (window as unknown as { polaris?: { getBackendInfo?: () => Promise<BackendInfo> } }).polaris;
    if (!api?.getBackendInfo) {
      throw new Error("polaris.getBackendInfo missing");
    }
    return await api.getBackendInfo();
  });
  if (!backend.baseUrl || !backend.token) {
    throw new Error(`backend info incomplete: ${JSON.stringify(backend)}`);
  }
  return { baseUrl: backend.baseUrl, token: backend.token };
}

async function backendJson<T>(
  window: Page,
  endpoint: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const backend = await getBackendInfo(window);
  return await window.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers: {
          authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(`${method} ${apiPath} failed: ${response.status} ${await response.text()}`);
      }
      return (await response.json()) as unknown;
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: init.method || "GET",
      body: init.body,
    },
  ) as T;
}

async function activateResumeWorkspaceIfRequested(window: Page): Promise<void> {
  const resumeWorkspace = String(process.env.KERNELONE_E2E_RESUME_WORKSPACE || "").trim();
  if (!resumeWorkspace) {
    return;
  }
  const workspace = path.resolve(resumeWorkspace);
  await backendJson(window, "/settings", {
    method: "POST",
    body: { workspace },
  });
  await expect.poll(async () => {
    const settings = await backendJson<{ workspace?: string }>(window, "/settings");
    return path.resolve(String(settings.workspace || ""));
  }, {
    timeout: 90_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBe(workspace);
  await window.reload({ waitUntil: "domcontentloaded" });
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
}

async function attachScreenshot(window: Page, testInfo: TestInfo, name: string): Promise<void> {
  const screenshotPath = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path: screenshotPath, fullPage: true });
  await testInfo.attach(name, { path: screenshotPath, contentType: "image/png" });
  expect(fs.existsSync(screenshotPath)).toBe(true);

  const reviewPath = testInfo.outputPath(`${name}.review.jpg`);
  await window.screenshot({ path: reviewPath, type: "jpeg", quality: 80, fullPage: false });
  await testInfo.attach(`${name}.review`, { path: reviewPath, contentType: "image/jpeg" });
  expect(fs.existsSync(reviewPath)).toBe(true);
  expect(fs.statSync(reviewPath).size, `${name}.review.jpg should not be empty`).toBeGreaterThan(1024);
  const dimensions = readJpegDimensions(reviewPath);
  expect(dimensions.width, `${name}.review.jpg width should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.height, `${name}.review.jpg height should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.width, `${name}.review.jpg width should be visible`).toBeGreaterThan(0);
  expect(dimensions.height, `${name}.review.jpg height should be visible`).toBeGreaterThan(0);
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

type ScrollCapturePosition = "start" | "middle" | "end";

async function scrollRegionTo(
  locator: Locator,
  label: string,
  position: ScrollCapturePosition,
): Promise<void> {
  await expect(locator, `${label} scroll region should be visible`).toBeVisible();
  const metrics = await locator.evaluate((element, targetPosition) => {
    const html = element as HTMLElement;
    const maxScrollTop = Math.max(0, html.scrollHeight - html.clientHeight);
    const targetScrollTop = targetPosition === "end"
      ? maxScrollTop
      : targetPosition === "middle"
        ? Math.round(maxScrollTop / 2)
        : 0;
    html.scrollTop = targetScrollTop;
    return {
      clientHeight: html.clientHeight,
      scrollHeight: html.scrollHeight,
      scrollTop: html.scrollTop,
      maxScrollTop,
      targetScrollTop,
    };
  }, position);

  expect(metrics.clientHeight, `${label} should have measurable height`).toBeGreaterThan(0);
  expect(metrics.scrollHeight, `${label} should have measurable content`).toBeGreaterThan(0);
  if (position !== "start" && metrics.maxScrollTop > 8) {
    expect(
      metrics.scrollTop,
      `${label} should scroll to ${position}: ${JSON.stringify(metrics)}`,
    ).toBeGreaterThan(0);
  }
}

async function captureScrollableRegionPositions(
  window: Page,
  testInfo: TestInfo,
  locator: Locator,
  label: string,
  screenshotPrefix: string,
  positions: ScrollCapturePosition[],
): Promise<void> {
  for (const position of positions) {
    await scrollRegionTo(locator, label, position);
    await window.waitForTimeout(100);
    await expectNoDocumentHorizontalOverflow(window, `${label} ${position}`);
    await attachScreenshot(window, testInfo, `${screenshotPrefix}-${position}`);
  }
}

async function clickWorkspaceTab(window: Page, workspace: Locator, label: string): Promise<void> {
  const tabButton = workspace.locator(`nav button[title="${label}"]`);
  await expect(tabButton, `${label} tab button should exist`).toBeVisible();
  await tabButton.click();
  await expect(tabButton, `${label} tab should become active`).toHaveClass(/bg-(amber|indigo)-500\/15/, {
    timeout: 5_000,
  });
  await window.waitForTimeout(100);
}

async function exerciseWorkspaceTabs(
  window: Page,
  testInfo: TestInfo,
  workspace: Locator,
  workspaceLabel: string,
  tabs: Array<{ label: string; screenshotName: string; afterOpen?: () => Promise<void> }>,
): Promise<void> {
  for (const tab of tabs) {
    await clickWorkspaceTab(window, workspace, tab.label);
    await expectNoDocumentHorizontalOverflow(window, `${workspaceLabel} ${tab.label}`);
    await expectRoleSessionStripContained(workspace, `${workspaceLabel} ${tab.label}`);
    if (tab.afterOpen) {
      await tab.afterOpen();
    }
    await attachScreenshot(window, testInfo, tab.screenshotName);
  }
}

async function expandDirectorCodeChangeDetails(
  window: Page,
  directorWorkspace: Locator,
  label: string,
): Promise<void> {
  const panel = directorWorkspace.getByTestId("director-code-panel");
  await expect(panel, `${label} panel should be visible`).toBeVisible({ timeout: 15_000 });
  const eventList = panel.getByTestId("director-code-event-list");
  const empty = await panel.getByTestId("director-code-empty").isVisible().catch(() => false);
  const eventCount = await eventList.locator(":scope > div").count().catch(() => 0);
  expect(
    eventCount > 0 || empty,
    `${label} should expose either file changes or an explicit empty state: eventCount=${eventCount} empty=${empty}`,
  ).toBe(true);
  if (eventCount === 0) {
    return;
  }

  const diffToggle = eventList.getByText("展开 Diff").first();
  if (await diffToggle.isVisible().catch(() => false)) {
    await diffToggle.click();
  } else {
    const summaryToggle = eventList.getByText("展开统计").first();
    if (await summaryToggle.isVisible().catch(() => false)) {
      await summaryToggle.click();
    } else {
      await eventList.locator(":scope > div").first().click();
    }
  }

  let detailKind: "diff" | "summary" | "none" = "none";
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (await panel.getByTestId("real-time-file-diff").first().isVisible().catch(() => false)) {
      detailKind = "diff";
      break;
    }
    if (await panel.getByTestId("director-file-edit-summary").first().isVisible().catch(() => false)) {
      detailKind = "summary";
      break;
    }
    await window.waitForTimeout(250);
  }
  expect(detailKind, `${label} should expand diff or statistics details`).not.toBe("none");
}

test("PM, Chief Engineer, and Director deep workspace tabs remain contained", async ({ window }, testInfo) => {
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
  await activateResumeWorkspaceIfRequested(window);

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
  await window.getByTestId("enter-chief-engineer-workspace").click();
  const chiefWorkspace = window.getByTestId("chief-engineer-workspace");
  await expect(chiefWorkspace).toBeVisible();
  await expect(window.getByTestId("chief-engineer-backend-strip")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-diagnostics")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-director-task-pool")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-toggle-workbench")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-toggle-dialogue")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-open-settings")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-start-director")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-enter-director")).toBeVisible();
  await expectNoDocumentHorizontalOverflow(window, "Chief Engineer control");
  await expectRoleSessionStripContained(chiefWorkspace, "Chief Engineer control");
  await attachScreenshot(window, testInfo, "chief-engineer-control");
  const chiefControlMain = chiefWorkspace.locator("main").first();
  const chiefBlueprintPane = chiefControlMain.locator(":scope > section").first();
  const chiefSideRail = chiefControlMain.locator(":scope > aside").first();
  await captureScrollableRegionPositions(window, testInfo, chiefBlueprintPane, "Chief Engineer blueprint pane", "chief-engineer-blueprints", [
    "start",
    "end",
  ]);
  await captureScrollableRegionPositions(window, testInfo, chiefSideRail, "Chief Engineer side rail", "chief-engineer-side-rail", [
    "start",
    "middle",
    "end",
  ]);
  await window.getByTestId("chief-engineer-toggle-workbench").click();
  await expect(window.getByTestId("chief-engineer-workbench-panel")).toBeVisible({ timeout: 15_000 });
  await expectNoDocumentHorizontalOverflow(window, "Chief Engineer workbench");
  await expectRoleSessionStripContained(chiefWorkspace, "Chief Engineer workbench");
  await attachScreenshot(window, testInfo, "chief-engineer-workbench");
  const chiefDialogueToggle = window.getByTestId("chief-engineer-toggle-dialogue");
  if (await chiefDialogueToggle.isEnabled()) {
    await chiefDialogueToggle.click();
    await expect(window.getByTestId("chief-engineer-dialogue")).toBeVisible({ timeout: 15_000 });
    await expectNoDocumentHorizontalOverflow(window, "Chief Engineer dialogue");
    await expectRoleSessionStripContained(chiefWorkspace, "Chief Engineer dialogue");
    await attachScreenshot(window, testInfo, "chief-engineer-dialogue");
  } else {
    await expect(chiefDialogueToggle).toHaveAttribute("title", /工作台内置对话面板/);
    await expect(window.getByTestId("chief-engineer-workbench-panel")).toBeVisible();
    await attachScreenshot(window, testInfo, "chief-engineer-dialogue-embedded");
  }
  await window.getByTestId("chief-engineer-workspace-back").click();
  await expect(chiefWorkspace).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-director-workspace").click();
  const directorWorkspace = window.getByTestId("director-workspace");
  await expect(directorWorkspace).toBeVisible();
  await exerciseWorkspaceTabs(window, testInfo, directorWorkspace, "Director", [
    { label: "任务", screenshotName: "director-tab-tasks" },
    { label: "实时", screenshotName: "director-tab-activity" },
    {
      label: "代码",
      screenshotName: "director-tab-code",
      afterOpen: async () => {
        await expandDirectorCodeChangeDetails(window, directorWorkspace, "Director code");
      },
    },
    { label: "终端", screenshotName: "director-tab-terminal" },
    { label: "调试", screenshotName: "director-tab-debug" },
    { label: "策略", screenshotName: "director-tab-strategy" },
    { label: "工作台", screenshotName: "director-tab-workbench" },
  ]);

  expect(pageErrors, "renderer pageerror should remain empty during role deep tab sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during role deep tab sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
