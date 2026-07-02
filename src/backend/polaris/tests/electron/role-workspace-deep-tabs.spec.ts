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
type DirectorStrategySettings = {
  director_execution_mode?: string;
  director_iterations?: number;
  director_max_parallel_tasks?: number;
};

function normalizedText(value: string | null | undefined): string {
  return (value || "").replace(/\s+/g, " ").trim();
}

function firstTraceabilityToken(value: string): string {
  return value
    .split(/[·,，]/)
    .map((part) => part.trim())
    .find((part) => part.length > 0) || "";
}

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
  await backendJson(window, "/v2/settings", {
    method: "POST",
    body: { workspace },
  });
  await expect.poll(async () => {
    const settings = await backendJson<{ workspace?: string }>(window, "/v2/settings");
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
  const eventRows = eventList.getByTestId("director-code-event-row");
  const eventCount = await eventRows.count().catch(() => 0);
  expect(
    eventCount > 0 || empty,
    `${label} should expose either file changes or an explicit empty state: eventCount=${eventCount} empty=${empty}`,
  ).toBe(true);
  if (eventCount === 0) {
    return;
  }

  const latestRow = eventRows.first();
  const latestPath = await latestRow.getAttribute("data-file-path");
  const latestHasDiff = (await latestRow.textContent())?.includes("Diff") ?? false;
  const defaultDiff = panel.getByTestId("real-time-file-diff").first();
  const defaultSummary = panel.getByTestId("director-file-edit-summary").first();
  if (latestHasDiff) {
    await expect(defaultDiff, `${label} should default-open the latest diff`).toBeVisible({ timeout: 10_000 });
    if (latestPath) {
      await expect(defaultDiff, `${label} default diff should belong to latest file`).toHaveAttribute("data-file-path", latestPath);
    }
    return;
  }
  if (await defaultSummary.isVisible().catch(() => false)) {
    return;
  }

  await latestRow.click();

  if (latestHasDiff) {
    await expect(defaultDiff, `${label} clicked diff should open`).toBeVisible({ timeout: 10_000 });
    return;
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

async function expectRealtimeActivityFunctional(workspace: Locator, label: string): Promise<void> {
  const panel = workspace.getByTestId("realtime-activity-panel");
  await expect(panel, `${label} realtime activity panel should be visible`).toBeVisible({ timeout: 15_000 });
  await expect(panel, `${label} realtime activity should expose record count`).toContainText(/条记录/);
  await expect(panel, `${label} realtime activity should expose monitor footer`).toContainText(/监控/);

  for (const tabLabel of ["思考", "工具", "日志", "文件"]) {
    const tab = panel.getByLabel(`查看${tabLabel}记录`);
    await expect(tab, `${label} realtime ${tabLabel} tab should be visible`).toBeVisible();
    await tab.click();
  }
}

async function expectPMTasksFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-task-panel");
  await expect(panel, "PM task panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("pm-task-toolbar"), "PM task toolbar should be visible").toBeVisible();
  await expect(panel.getByPlaceholder("搜索任务..."), "PM task search should be usable").toBeVisible();
  await expect(panel.getByTestId("pm-task-create-toggle"), "PM task create control should be present").toBeVisible();
  await expect(panel.getByTestId("pm-task-list"), "PM task list region should be visible").toBeVisible();

  const rows = panel.getByTestId("pm-task-item");
  const rowCount = await rows.count();
  const empty = await panel.getByTestId("pm-task-empty").isVisible().catch(() => false);
  expect(
    rowCount > 0 || empty,
    `PM task panel should expose task rows or an explicit empty state: rows=${rowCount} empty=${empty}`,
  ).toBe(true);

  if (rowCount === 0) {
    return;
  }

  await rows.first().click();
  const detail = panel.getByTestId("pm-task-detail");
  await expect(detail, "PM task detail should open from a task row").toBeVisible({ timeout: 15_000 });
  await expect(detail.getByTestId("pm-task-detail-provenance"), "PM task detail should expose provenance").toBeVisible();
  await expect(detail.getByTestId("pm-task-backend-detail"), "PM task detail should expose backend read evidence").toBeVisible();
}

async function expectPMDocumentsFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-document-panel");
  await expect(panel, "PM documents panel should be visible").toBeVisible({ timeout: 15_000 });
  const tree = panel.getByTestId("pm-document-tree");
  await expect(tree, "PM document tree should be visible").toBeVisible({ timeout: 15_000 });
  expect(normalizedText(await tree.textContent()).length, "PM document tree should expose document names or an explicit empty state")
    .toBeGreaterThan(0);

  const versionPanel = panel.getByTestId("pm-document-version-panel");
  if (await versionPanel.isVisible().catch(() => false)) {
    await expect(versionPanel, "PM document version panel should expose version controls").toContainText(/版本|version|v\d+/i);

    const versionButtons = versionPanel.getByTestId("pm-document-version-open");
    if (await versionButtons.count() > 0) {
      await versionButtons.first().click();
      await expect(
        versionPanel.getByTestId("pm-document-version-read-evidence"),
        "PM document historical version read should show backend evidence",
      ).toContainText(/version=/i, { timeout: 15_000 });

      const currentVersionButton = versionPanel.getByTestId("pm-document-current-version");
      if (await currentVersionButton.isVisible().catch(() => false)) {
        await currentVersionButton.click();
      }
    }

    const compareLatest = versionPanel.getByRole("button", { name: /比较最新|比较中/ });
    if (await compareLatest.isVisible().catch(() => false)) {
      await compareLatest.click();
      const diff = versionPanel.getByTestId("pm-document-diff");
      const diffError = versionPanel.getByTestId("pm-document-diff-error");
      await expect(async () => {
        const hasDiff = await diff.isVisible().catch(() => false);
        const hasDiffError = await diffError.isVisible().catch(() => false);
        expect(hasDiff || hasDiffError, "PM document compare should render diff evidence or an explicit diff error").toBe(true);
      }).toPass({ timeout: 15_000 });
      if (await diff.isVisible().catch(() => false)) {
        await expect(diff, "PM document diff should include version direction and impact summary").toContainText(/->|impact|sections/i);
      }
    }
  }
}

async function expectPMRequirementsFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-requirements-panel");
  await expect(panel, "PM requirements panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("pm-requirements-endpoint"), "PM requirements endpoint evidence should exist")
    .toHaveAttribute("data-endpoint", "/v2/pm/requirements");
  const matrix = panel.getByTestId("pm-requirement-matrix");
  await expect(matrix, "PM requirement matrix should be visible").toBeVisible({ timeout: 15_000 });

  const rows = matrix.getByTestId("pm-requirement-matrix-row");
  const rowCount = await rows.count();
  const empty = await matrix.getByTestId("pm-requirement-matrix-empty").isVisible().catch(() => false);
  expect(
    rowCount > 0 || empty,
    `PM requirements should expose traceability rows or explicit empty state: rows=${rowCount} empty=${empty}`,
  ).toBe(true);
  if (rowCount === 0) {
    return;
  }

  const firstRow = rows.first();
  const selectedRequirementId = (await firstRow.getAttribute("data-requirement-id")) || "";
  expect(selectedRequirementId.trim().length, "PM requirement matrix row should carry a backend requirement id").toBeGreaterThan(0);
  const rowSource = normalizedText(await firstRow.getByTestId("pm-requirement-matrix-source").textContent());
  const rowAcceptance = normalizedText(await firstRow.getByTestId("pm-requirement-matrix-acceptance").textContent());
  const rowRelatedTask = normalizedText(await firstRow.getByTestId("pm-requirement-matrix-related-task").textContent());
  expect(rowSource.length, "PM requirement matrix source cell should not be blank").toBeGreaterThan(0);
  expect(rowAcceptance.length, "PM requirement matrix acceptance cell should not be blank").toBeGreaterThan(0);
  expect(rowRelatedTask.length, "PM requirement matrix related-task cell should not be blank").toBeGreaterThan(0);

  await firstRow.click();
  const detail = panel.getByTestId("pm-requirement-detail");
  await expect(detail, "PM requirement detail should open from matrix row").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("pm-requirement-detail-endpoint"), "PM requirement detail endpoint should match the selected matrix row")
    .toHaveAttribute("data-endpoint", `/v2/pm/requirements/${selectedRequirementId}`);
  await expect(panel.getByTestId("pm-requirement-detail-body-endpoint"), "PM requirement detail body should keep backend evidence")
    .toHaveAttribute("data-endpoint", `/v2/pm/requirements/${selectedRequirementId}`);
  await expect(detail, "PM requirement detail should show acceptance criteria").toContainText("Acceptance Criteria");
  await expect(detail, "PM requirement detail should show related tasks").toContainText("Related Tasks");
  await expect(detail, "PM requirement detail should show source provenance").toContainText("Source");

  if (rowSource !== "unlinked") {
    await expect(detail, "PM requirement detail source should match the matrix row").toContainText(rowSource);
  }
  if (!/未记录验收条件/.test(rowAcceptance)) {
    await expect(detail, "PM requirement detail acceptance should match the matrix row")
      .toContainText(firstTraceabilityToken(rowAcceptance));
  }
  if (!/未关联 PM 任务/.test(rowRelatedTask)) {
    await expect(detail, "PM requirement detail related task should match the matrix row")
      .toContainText(firstTraceabilityToken(rowRelatedTask));
  }
}

async function expectPMAnalyticsFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-analytics-panel");
  await expect(panel, "PM analytics panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel, "PM analytics panel should expose task statistics or explicit empty state").toContainText(/任务统计|暂无任务数据/);
}

async function expectPMHistoryFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-history-panel");
  await expect(panel, "PM history panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("pm-history-refresh"), "PM history refresh should be visible").toBeVisible();
  await expect(panel.getByTestId("pm-history-task-count"), "PM task history count should be numeric").toContainText(/\d+/);
  await expect(panel.getByTestId("pm-history-director-count"), "PM director dispatch count should be numeric").toContainText(/\d+/);
  await expect(panel.getByTestId("pm-history-task-list"), "PM task history list should be visible").toBeVisible();
  await expect(panel.getByTestId("pm-history-director-list"), "PM director dispatch list should be visible").toBeVisible();

  const taskRows = await panel.getByTestId("pm-history-task-row").count();
  const taskEmpty = await panel.getByTestId("pm-history-task-empty").isVisible().catch(() => false);
  const directorRows = await panel.getByTestId("pm-history-director-row").count();
  const directorEmpty = await panel.getByTestId("pm-history-director-empty").isVisible().catch(() => false);
  expect(taskRows > 0 || taskEmpty, `PM task history should show rows or empty state: rows=${taskRows} empty=${taskEmpty}`).toBe(true);
  expect(
    directorRows > 0 || directorEmpty,
    `PM director dispatch history should show rows or empty state: rows=${directorRows} empty=${directorEmpty}`,
  ).toBe(true);

  const snapshotToggle = panel.getByText("PM 状态快照");
  await expect(snapshotToggle, "PM history should expose state snapshot toggle").toBeVisible();
  await snapshotToggle.click();
  const snapshot = panel.getByTestId("pm-history-state-snapshot");
  const snapshotEmpty = panel.getByTestId("pm-history-state-empty");
  expect(
    await snapshot.isVisible().catch(() => false) || await snapshotEmpty.isVisible().catch(() => false),
    "PM history should expose a state snapshot or explicit empty state",
  ).toBe(true);
}

async function expectPMWorkbenchFunctional(pmWorkspace: Locator): Promise<void> {
  const panel = pmWorkspace.getByTestId("pm-workbench-panel");
  await expect(panel, "PM workbench panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("pm-workbench-run-directive"), "PM workbench directive input should be visible").toBeVisible();
  await expect(panel.getByTestId("pm-workbench-run-stage"), "PM workbench stage selector should be visible").toBeVisible();
  await expect(panel.getByTestId("pm-workbench-run-director"), "PM workbench Director handoff toggle should be visible").toBeVisible();
  await expect(panel.getByTestId("pm-workbench-run-pm"), "PM workbench orchestration button should be visible").toBeVisible();
  await expect(panel.getByTestId("ai-role-session-new"), "PM workbench should allow creating RoleSessions").toBeVisible();
}

async function expectChiefEngineerBlueprintFunctional(chiefWorkspace: Locator): Promise<void> {
  const openButtons = chiefWorkspace.locator('[data-testid^="chief-engineer-blueprint-open-"]');
  const openCount = await openButtons.count();
  if (openCount === 0) {
    await expect(chiefWorkspace.getByTestId("chief-engineer-director-task-pool")).toBeVisible();
    return;
  }
  const firstOpenButton = openButtons.first();
  const openTestId = (await firstOpenButton.getAttribute("data-testid")) || "";
  expect(openTestId, "Chief Engineer blueprint open control should carry a blueprint id").toMatch(/^chief-engineer-blueprint-open-.+/);
  await firstOpenButton.click();
  const detail = chiefWorkspace.getByTestId("chief-engineer-blueprint-detail");
  await expect(detail, "Chief Engineer blueprint detail should open")
    .toBeVisible({ timeout: 15_000 });
  expect(normalizedText(await detail.textContent()).length, "Chief Engineer blueprint detail should not be blank").toBeGreaterThan(0);
  await expect(
    chiefWorkspace.getByTestId("chief-engineer-blueprint-provenance").first(),
    "Chief Engineer blueprint list should expose provenance",
  ).toContainText(/source/i);
}

async function expectChiefEngineerDirectorHandoffFunctional(chiefWorkspace: Locator): Promise<void> {
  const startDirector = chiefWorkspace.getByTestId("chief-engineer-start-director");
  await expect(startDirector, "Chief Engineer Director handoff control should be visible").toBeVisible();
  await expect(chiefWorkspace.getByTestId("chief-engineer-enter-director"), "Chief Engineer should expose Director board handoff").toBeVisible();

  if (await startDirector.isDisabled()) {
    const disabledReason = (await startDirector.getAttribute("title")) || "";
    expect(disabledReason.trim().length, "Disabled Director handoff should explain the blocker").toBeGreaterThan(0);
  } else {
    await expect(startDirector, "Chief Engineer Director handoff should be enabled when no blockers exist").toBeEnabled();
  }
}

async function expectChiefEngineerWorkbenchFunctional(chiefWorkspace: Locator): Promise<void> {
  const panel = chiefWorkspace.getByTestId("chief-engineer-workbench-panel");
  await expect(panel, "Chief Engineer workbench should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("ai-role-session-new"), "Chief Engineer workbench should allow creating RoleSessions").toBeVisible();
  const exportToDirector = panel.getByTestId("ai-role-session-export");
  await expect(exportToDirector, "Chief Engineer RoleSession export control should be visible").toBeVisible();
  await expect(exportToDirector, "Chief Engineer RoleSession export should target Director").toHaveAttribute("aria-label", /director/i);
}

async function expectDirectorTasksFunctional(directorWorkspace: Locator): Promise<void> {
  await expect(directorWorkspace.getByTestId("director-workspace-bulk-execute"), "Director bulk execute control should be visible")
    .toBeVisible({ timeout: 15_000 });
  await expect(directorWorkspace.getByTestId("director-task-create-panel"), "Director task create panel should be visible").toBeVisible();
  await expect(directorWorkspace.getByTestId("director-task-create-subject"), "Director task subject input should be visible").toBeVisible();
  await expect(directorWorkspace.getByTestId("director-task-create-description"), "Director task description input should be visible").toBeVisible();
  await expect(directorWorkspace.getByTestId("director-task-board"), "Director task board should be visible").toBeVisible();
  await expect(directorWorkspace.getByTestId("director-task-detail"), "Director task detail region should be visible").toBeVisible();
  await expect(directorWorkspace.getByTestId("director-worker-strip"), "Director worker evidence strip should be visible").toBeVisible();

  const groups = await directorWorkspace.locator('[data-testid^="director-task-group-"]').count();
  const empty = await directorWorkspace.getByTestId("director-task-board").getByText("当前没有可执行任务").isVisible().catch(() => false);
  expect(groups > 0 || empty, `Director task board should expose grouped tasks or an explicit empty state: groups=${groups} empty=${empty}`).toBe(true);

  const taskItems = directorWorkspace.getByTestId("director-task-item");
  if (await taskItems.count() === 0) {
    return;
  }

  const firstTask = taskItems.first();
  const firstTaskText = firstTraceabilityToken(normalizedText(await firstTask.textContent()));
  await firstTask.click();
  const detail = directorWorkspace.getByTestId("director-task-detail");
  await expect(detail, "Director task detail should update when selecting a task").toBeVisible({ timeout: 15_000 });
  if (firstTaskText) {
    await expect(detail, "Director task detail should reflect the selected task").toContainText(firstTaskText);
  }
  await expect(detail.getByTestId("director-task-provenance"), "Director task detail should expose provenance").toBeVisible();
  await expect(detail.getByTestId("director-task-backend-detail"), "Director task detail should expose backend contract detail").toBeVisible();
  await expect(detail.getByTestId("director-task-execute-selected"), "Director selected task execute control should be present").toBeVisible();
  await expect(detail.getByTestId("director-task-cancel-selected"), "Director selected task cancel control should be present").toBeVisible();
}

async function expectDirectorTerminalFunctional(directorWorkspace: Locator): Promise<void> {
  const panel = directorWorkspace.getByTestId("director-terminal-panel");
  await expect(panel, "Director terminal panel should be visible").toBeVisible({ timeout: 15_000 });
  const output = panel.getByTestId("director-terminal-output");
  const empty = panel.getByTestId("director-terminal-empty");
  const hasOutput = await output.isVisible().catch(() => false);
  const hasEmpty = await empty.isVisible().catch(() => false);
  expect(hasOutput || hasEmpty, `Director terminal should expose output or explicit empty state: output=${hasOutput} empty=${hasEmpty}`).toBe(true);
  const clearButton = panel.getByTestId("director-terminal-clear");
  await expect(clearButton, "Director terminal clear control should be visible").toBeVisible();
  if (hasOutput) {
    const text = (await output.textContent())?.trim() || "";
    expect(text.length, "Director terminal output should not be blank").toBeGreaterThan(0);
    await expect(clearButton, "Director terminal clear should be enabled when output exists").toBeEnabled();
    await clearButton.click();
    await expect(empty, "Director terminal should show empty state after clearing output").toBeVisible({ timeout: 15_000 });
  } else {
    await expect(clearButton, "Director terminal clear should be disabled when output is empty").toBeDisabled();
  }
}

async function expectDirectorDebugFunctional(directorWorkspace: Locator): Promise<void> {
  const panel = directorWorkspace.getByTestId("director-debug-panel");
  await expect(panel, "Director debug panel should be visible").toBeVisible({ timeout: 15_000 });
  const taskCount = await panel.getByTestId("director-debug-task").count();
  const empty = await panel.getByTestId("director-debug-empty").isVisible().catch(() => false);
  expect(taskCount > 0 || empty, `Director debug should expose issue cards or explicit empty state: tasks=${taskCount} empty=${empty}`).toBe(true);
}

async function expectDirectorStrategyFunctional(window: Page, directorWorkspace: Locator): Promise<void> {
  const panel = directorWorkspace.getByTestId("director-strategy-panel");
  await expect(panel, "Director strategy panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("director-strategy-message"), "Director strategy status message should be visible").toBeVisible();
  await expect(panel.getByTestId("strategy-editor-panel"), "Director strategy editor should be visible").toBeVisible({ timeout: 15_000 });
  const saveButton = panel.getByTestId("strategy-editor-save");
  await expect(saveButton, "Director strategy save control should exist").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("strategy-template-default"), "Director strategy default template should be visible").toBeVisible();
  await expect(panel.getByTestId("strategy-template-serial"), "Director strategy serial template should be visible").toBeVisible();
  await expect(panel.getByTestId("strategy-template-fast"), "Director strategy fast template should be visible").toBeVisible();

  const candidates = [
    {
      templateId: "serial",
      expected: {
        director_execution_mode: "serial",
        director_iterations: 1,
        director_max_parallel_tasks: 1,
      },
    },
    {
      templateId: "fast",
      expected: {
        director_execution_mode: "parallel",
        director_iterations: 2,
        director_max_parallel_tasks: 5,
      },
    },
    {
      templateId: "default",
      expected: {
        director_execution_mode: "parallel",
        director_iterations: 1,
        director_max_parallel_tasks: 3,
      },
    },
  ] satisfies Array<{ templateId: string; expected: Required<DirectorStrategySettings> }>;

  let selected: (typeof candidates)[number] | null = null;
  for (const candidate of candidates) {
    await panel.getByTestId(`strategy-template-${candidate.templateId}`).click();
    await expect(panel.getByTestId("strategy-editor-panel"), `Director strategy template ${candidate.templateId} should remain valid`)
      .toContainText("有效", { timeout: 5_000 });
    if (!(await saveButton.isDisabled())) {
      selected = candidate;
      break;
    }
  }
  expect(selected, "Director strategy templates should make the editor dirty").not.toBeNull();
  await expect(saveButton, "Director strategy save should become enabled after selecting a different template").toBeEnabled();
  await saveButton.click();
  await expect(panel.getByTestId("strategy-editor-save-message"), "Director strategy save should show backend sync evidence")
    .toContainText(/已同步到 \/settings/, { timeout: 30_000 });
  await expect(panel.getByTestId("director-strategy-message"), "Director strategy status should reflect backend sync")
    .toContainText(/已同步到 \/settings/, { timeout: 30_000 });

  const savedSettings = await backendJson<DirectorStrategySettings>(window, "/v2/settings");
  expect(savedSettings.director_execution_mode, "Director strategy save should persist execution mode")
    .toBe(selected?.expected.director_execution_mode);
  expect(savedSettings.director_iterations, "Director strategy save should persist iterations")
    .toBe(selected?.expected.director_iterations);
  expect(savedSettings.director_max_parallel_tasks, "Director strategy save should persist max parallel tasks")
    .toBe(selected?.expected.director_max_parallel_tasks);

  await panel.getByRole("button", { name: "对比" }).click();
  await expect(panel.getByTestId("strategy-diff-viewer"), "Director strategy diff viewer should be visible").toBeVisible({ timeout: 15_000 });
}

async function expectDirectorWorkbenchFunctional(directorWorkspace: Locator): Promise<void> {
  const panel = directorWorkspace.getByTestId("director-workbench-panel");
  await expect(panel, "Director workbench panel should be visible").toBeVisible({ timeout: 15_000 });
  await expect(panel.getByTestId("ai-role-session-new"), "Director workbench should allow creating RoleSessions").toBeVisible();
  await expect(panel.getByPlaceholder(/输入消息|状态检查中|请先配置|状态异常/), "Director workbench AI input should be present")
    .toBeVisible({ timeout: 15_000 });
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
    {
      label: "任务",
      screenshotName: "pm-tab-tasks",
      afterOpen: async () => {
        await expectPMTasksFunctional(pmWorkspace);
      },
    },
    {
      label: "实时",
      screenshotName: "pm-tab-activity",
      afterOpen: async () => {
        await expectRealtimeActivityFunctional(pmWorkspace, "PM");
      },
    },
    {
      label: "文档",
      screenshotName: "pm-tab-documents",
      afterOpen: async () => {
        await expectPMDocumentsFunctional(pmWorkspace);
      },
    },
    {
      label: "需求",
      screenshotName: "pm-tab-requirements",
      afterOpen: async () => {
        await expectPMRequirementsFunctional(pmWorkspace);
      },
    },
    {
      label: "历史",
      screenshotName: "pm-tab-history",
      afterOpen: async () => {
        await expectPMHistoryFunctional(pmWorkspace);
      },
    },
    {
      label: "统计",
      screenshotName: "pm-tab-analytics",
      afterOpen: async () => {
        await expectPMAnalyticsFunctional(pmWorkspace);
      },
    },
    {
      label: "编排",
      screenshotName: "pm-tab-workbench",
      afterOpen: async () => {
        await expectPMWorkbenchFunctional(pmWorkspace);
      },
    },
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
  await expectChiefEngineerBlueprintFunctional(chiefWorkspace);
  await expectChiefEngineerDirectorHandoffFunctional(chiefWorkspace);
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
  await expectChiefEngineerWorkbenchFunctional(chiefWorkspace);
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
    {
      label: "任务",
      screenshotName: "director-tab-tasks",
      afterOpen: async () => {
        await expectDirectorTasksFunctional(directorWorkspace);
      },
    },
    {
      label: "实时",
      screenshotName: "director-tab-activity",
      afterOpen: async () => {
        await expectRealtimeActivityFunctional(directorWorkspace, "Director");
      },
    },
    {
      label: "代码",
      screenshotName: "director-tab-code",
      afterOpen: async () => {
        await expandDirectorCodeChangeDetails(window, directorWorkspace, "Director code");
      },
    },
    {
      label: "终端",
      screenshotName: "director-tab-terminal",
      afterOpen: async () => {
        await expectDirectorTerminalFunctional(directorWorkspace);
      },
    },
    {
      label: "调试",
      screenshotName: "director-tab-debug",
      afterOpen: async () => {
        await expectDirectorDebugFunctional(directorWorkspace);
      },
    },
    {
      label: "策略",
      screenshotName: "director-tab-strategy",
      afterOpen: async () => {
        await expectDirectorStrategyFunctional(window, directorWorkspace);
      },
    },
    {
      label: "工作台",
      screenshotName: "director-tab-workbench",
      afterOpen: async () => {
        await expectDirectorWorkbenchFunctional(directorWorkspace);
      },
    },
  ]);

  expect(pageErrors, "renderer pageerror should remain empty during role deep tab sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during role deep tab sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty").toEqual([]);
});
