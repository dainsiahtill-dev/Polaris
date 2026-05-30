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
}

async function clickIfEnabled(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  if (await locator.isEnabled()) {
    await locator.click();
  }
}

async function expectNoTransientConnectionToast(window: Page): Promise<void> {
  await expect(window.getByText("正在重连...")).toHaveCount(0);
  await expect(window.getByText("连接已恢复")).toHaveCount(0);
}

async function expectOpaqueModalSurface(locator: Locator, label: string): Promise<void> {
  const alpha = await locator.evaluate((element) => {
    const color = window.getComputedStyle(element as HTMLElement).backgroundColor;
    const rgba = color.match(/rgba?\(([^)]+)\)/i)?.[1]
      .split(",")
      .map((part) => Number.parseFloat(part.trim()));
    if (!rgba) return 0;
    return rgba.length >= 4 ? rgba[3] : 1;
  });
  expect(alpha, `${label} should be opaque enough to avoid layered UI noise`).toBeGreaterThanOrEqual(0.98);
}

test("human-style desktop UI sweep covers safe navigation surfaces", async ({ window }, testInfo) => {
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
  await attachScreenshot(window, testInfo, "ui-sweep-main");

  await window.getByTestId("open-docs-init").click();
  await expect(window.getByTestId("docs-init-dialog")).toBeVisible();
  await window.getByTestId("docs-init-goal-input").fill("UI sweep safe navigation check");
  await window.getByTestId("docs-init-message-input").fill("Do not start LLM work; this test only verifies form and layout stability.");
  await expect(window.getByTestId("llm-runtime-overlay")).toHaveCount(0);
  await expectOpaqueModalSurface(window.getByTestId("docs-init-dialog"), "docs init dialog");
  await expectNoTransientConnectionToast(window);
  await attachScreenshot(window, testInfo, "ui-sweep-docs-dialog");
  await window.getByRole("button", { name: "Close" }).click();
  await expect(window.getByTestId("docs-init-dialog")).toHaveCount(0);

  await window.getByTestId("control-panel-open-settings").click();
  await expect(window.getByText("系统配置")).toBeVisible();
  for (const tabId of ["settings-tab-general", "settings-tab-llm", "settings-tab-arsenal", "settings-tab-services"]) {
    await window.getByTestId(tabId).click();
    await expect(window.getByTestId(tabId)).toBeVisible();
  }
  await window.getByTestId("settings-tab-llm").click();
  await expect(window.getByTestId("llm-config-view-list")).toBeVisible({ timeout: 30000 });
  const llmDiagnostics = window.getByTestId("llm-readiness-diagnostics");
  const settingsText = await window.locator("body").innerText();
  const hasLlmBlockedSummary = settingsText.includes("未通过测试:");
  if (hasLlmBlockedSummary) {
    await expect(llmDiagnostics).toBeVisible();
  }
  if (await llmDiagnostics.count()) {
    await expect(llmDiagnostics).toContainText("Provider:");
    await expect(llmDiagnostics).toContainText("Model:");
    await expect(llmDiagnostics).toContainText("最近测试:");
  }
  await expectNoTransientConnectionToast(window);
  await attachScreenshot(window, testInfo, "ui-sweep-settings-tabs");
  await window.getByRole("button", { name: "取消" }).click();
  await expect(window.getByText("系统配置")).toHaveCount(0);

  await window.getByTestId("control-panel-open-logs").click();
  await expect(window.getByTestId("logs-modal")).toBeVisible();
  await expect(window.getByTestId("logs-modal")).toHaveClass(/bg-black\/85/);
  await expect(window.getByTestId("logs-modal-panel")).toBeVisible();
  await expectOpaqueModalSurface(window.getByTestId("logs-modal-panel"), "logs modal panel");
  await window.getByTestId("logs-modal-refresh").click();
  await clickIfEnabled(window.getByTestId("logs-modal-view-raw"));
  await clickIfEnabled(window.getByTestId("logs-modal-view-smart"));
  await clickIfEnabled(window.getByTestId("logs-modal-view-json"));
  await attachScreenshot(window, testInfo, "ui-sweep-logs-modal");
  await window.getByTestId("logs-modal-close").click();
  await expect(window.getByTestId("logs-modal")).toHaveCount(0);

  await window.getByTestId("control-panel-toggle-terminal").click();
  await expect(window.getByTestId("terminal-panel")).toBeVisible({ timeout: 10000 });
  await attachScreenshot(window, testInfo, "ui-sweep-terminal");
  await window.locator("[title='Close Terminal']").click();
  await expect(window.getByTestId("terminal-panel")).toHaveCount(0);

  await window.getByTestId("control-panel-enter-factory").click();
  await expect(window.getByTestId("factory-layered-layout")).toBeVisible();
  await window.getByTestId("factory-role-layer-chief_engineer").click();
  await expect(window.getByTestId("factory-chief-layer")).toBeVisible();
  await window.getByTestId("factory-role-layer-director").click();
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  await attachScreenshot(window, testInfo, "ui-sweep-factory-director");
  await window.getByLabel("返回主界面").click();
  await expect(window.getByTestId("factory-layered-layout")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByTestId("enter-runtime-diagnostics").click();
  await expect(window.getByTestId("runtime-diagnostics-workspace")).toBeVisible();
  await attachScreenshot(window, testInfo, "ui-sweep-runtime-diagnostics");
  await window.getByTestId("runtime-diagnostics-back").click();
  await expect(window.getByTestId("runtime-diagnostics-workspace")).toHaveCount(0);

  await openMoreMenu(window);
  await window.getByRole("menuitem", { name: /干预中心/ }).click();
  await expect(window.getByTestId("intervention-center")).toBeVisible();
  await expect(window.getByTestId("open-intervention-center-menu-item")).toHaveCount(0);
  await expect(window.getByText("暂无介入事项。")).toBeVisible({ timeout: 10000 });
  await expectNoTransientConnectionToast(window);
  await attachScreenshot(window, testInfo, "ui-sweep-intervention-center");
  await window.keyboard.press("Escape");
  await expect(window.getByTestId("intervention-center")).toHaveCount(0);

  expect(pageErrors, "renderer pageerror should remain empty during UI sweep").toEqual([]);
  expect(failedResponses, "HTTP failures should remain empty during UI sweep").toEqual([]);
  expect(actionableConsoleErrors(consoleErrors), "actionable console errors should remain empty during UI sweep").toEqual([]);
});
