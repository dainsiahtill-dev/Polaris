import fs from "fs";
import type { Locator, Page, TestInfo } from "@playwright/test";
import { expect, test } from "./fixtures";

const ignoredConsoleErrorPatterns = [
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /Unable to preload CSS for \/assets\//i,
];

function actionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => {
    if (ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error))) {
      return false;
    }
    return true;
  });
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

async function clickWorkspaceTab(workspace: Locator, label: string): Promise<void> {
  await workspace.locator(`nav button[title="${label}"]`).click();
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

async function expectPanelContained(window: Page, locator: Locator, label: string): Promise<void> {
  await expect(locator).toBeVisible();
  const metrics = await locator.evaluate((element) => {
    const html = element as HTMLElement;
    const rect = html.getBoundingClientRect();
    const viewport = {
      width: document.documentElement.clientWidth,
      height: document.documentElement.clientHeight,
    };
    return {
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      viewport,
    };
  });
  expect(metrics.left, `${label} left edge should stay in viewport`).toBeGreaterThanOrEqual(-4);
  expect(metrics.right, `${label} right edge should stay in viewport`).toBeLessThanOrEqual(metrics.viewport.width + 4);
  expect(metrics.top, `${label} top edge should stay in viewport`).toBeGreaterThanOrEqual(-4);
  expect(metrics.bottom, `${label} bottom edge should stay in viewport`).toBeLessThanOrEqual(metrics.viewport.height + 4);
  expect(metrics.scrollWidth, `${label} should not overflow horizontally`).toBeLessThanOrEqual(metrics.clientWidth + 4);
  await expectNoDocumentHorizontalOverflow(window, label);
}

test("PM task and document secondary panels stay operable and contained", async ({ window }, testInfo) => {
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

  await clickWorkspaceTab(pmWorkspace, "任务");
  const createToggle = pmWorkspace.getByTestId("pm-task-create-toggle");
  if (await createToggle.isEnabled()) {
    await createToggle.click();
    const createPanel = pmWorkspace.getByTestId("pm-task-create-panel");
    await expectPanelContained(window, createPanel, "PM task create panel");
    const taskTitle = `E2E UI secondary PM task ${Date.now()}`;
    await createPanel.getByTestId("pm-task-create-subject").fill(taskTitle);
    await createPanel.getByTestId("pm-task-create-description").fill("Verify PM task creation and backend search panel layout.");
    await createPanel.getByTestId("pm-task-create-acceptance").fill("Task create evidence is visible\nSearch panel stays contained");
    await createPanel.getByTestId("pm-task-create-submit").click();
    const createEvidence = pmWorkspace.getByTestId("pm-task-create-evidence");
    await expect(createEvidence).toBeVisible({ timeout: 15_000 });
    await expect(createEvidence).not.toContainText("Failed to create PM task");
    await expect(createEvidence).toContainText("created", { timeout: 15_000 });
  } else {
    await expect(createToggle).toHaveAttribute(
      "title",
      /PM 启动诊断未通过|PM 后端诊断|PM 任务合同|PM 任务注册表|PM 正在|工厂模式/,
    );
    await expect(pmWorkspace.getByTestId("pm-task-create-panel")).toHaveCount(0);
  }
  await attachScreenshot(window, testInfo, "pm-secondary-task-created");

  await pmWorkspace.getByPlaceholder("搜索任务...").fill("E2E UI secondary");
  const taskSearchPanel = pmWorkspace.getByTestId("pm-task-search-panel");
  await expectPanelContained(window, taskSearchPanel, "PM task search panel");
  await expect(pmWorkspace.getByTestId("pm-task-search-count")).not.toContainText("searching", { timeout: 15_000 });
  const taskSearchResultCount = await pmWorkspace.getByTestId("pm-task-search-result").count();
  if (taskSearchResultCount > 0) {
    await pmWorkspace.getByTestId("pm-task-search-result").first().click();
    await expect(pmWorkspace.getByTestId("pm-task-backend-detail")).toBeVisible({ timeout: 15_000 });
    await expectPanelContained(window, pmWorkspace.getByTestId("pm-task-backend-detail"), "PM task backend detail");
  } else {
    await expect(pmWorkspace.getByTestId("pm-task-search-empty")).toBeVisible();
  }
  await attachScreenshot(window, testInfo, "pm-secondary-task-search-detail");

  await clickWorkspaceTab(pmWorkspace, "文档");
  await expect(pmWorkspace.getByTestId("pm-document-tree")).toBeVisible();
  await pmWorkspace.getByPlaceholder("搜索文档...").fill("README");
  const documentSearchPanel = pmWorkspace.getByTestId("pm-document-search-panel");
  await expectPanelContained(window, documentSearchPanel, "PM document search panel");
  await expect(pmWorkspace.getByTestId("pm-document-search-count")).not.toContainText("searching", { timeout: 15_000 });
  await attachScreenshot(window, testInfo, "pm-secondary-document-search");

  const documentResultCount = await pmWorkspace.getByTestId("pm-document-search-result").count();
  let openedDocument = false;
  if (documentResultCount > 0) {
    await pmWorkspace.getByTestId("pm-document-search-result").first().click();
    openedDocument = true;
  } else {
    const readmeTreeNode = pmWorkspace.getByText("README.md", { exact: true }).first();
    if (await readmeTreeNode.count()) {
      await readmeTreeNode.click();
      openedDocument = true;
    } else {
      await expect(pmWorkspace.getByTestId("pm-document-empty")).toBeVisible();
    }
  }

  if (openedDocument) {
    await expect(pmWorkspace.getByTestId("pm-document-provenance")).toBeVisible({ timeout: 15_000 });
    await expect(pmWorkspace.getByTestId("pm-document-version-panel")).toBeVisible({ timeout: 15_000 });
    await expectPanelContained(window, pmWorkspace.getByTestId("pm-document-version-panel"), "PM document version panel");
    await pmWorkspace.getByTestId("pm-document-delete-toggle").click();
    await expectPanelContained(window, pmWorkspace.getByTestId("pm-document-delete-panel"), "PM document delete panel");
    await attachScreenshot(window, testInfo, "pm-secondary-document-version-delete");
  } else {
    await attachScreenshot(window, testInfo, "pm-secondary-document-empty");
  }

  expect(pageErrors, "renderer pageerror should remain empty during PM panel sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during PM panel sweep").toEqual([]);
  expect(
    actionableConsoleErrors(consoleErrors),
    "actionable console errors should remain empty",
  ).toEqual([]);
});
