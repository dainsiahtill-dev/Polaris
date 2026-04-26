import { type Page } from "@playwright/test";
import { expect, test, type MainProcessLogs } from "./fixtures";

const panelTriggerSelector = process.env.E2E_PANEL_TRIGGER_SELECTOR?.trim();
const panelTriggerText = process.env.E2E_PANEL_TRIGGER_TEXT?.trim();
const panelTargetSelector = process.env.E2E_PANEL_TARGET_SELECTOR?.trim();
const panelTargetText = process.env.E2E_PANEL_TARGET_TEXT?.trim();
const strictErrors = process.env.E2E_PANEL_STRICT_ERRORS === "1";
const strictTerminalErrors = process.env.E2E_PANEL_STRICT_TERMINAL_ERRORS === "1" || strictErrors;

const startupSettleMsRaw = Number(process.env.E2E_PANEL_STARTUP_SETTLE_MS || "1200");
const startupSettleMs = Number.isFinite(startupSettleMsRaw) && startupSettleMsRaw >= 0 ? startupSettleMsRaw : 1200;
const postActionSettleMsRaw = Number(process.env.E2E_PANEL_POST_ACTION_SETTLE_MS || "800");
const postActionSettleMs = Number.isFinite(postActionSettleMsRaw) && postActionSettleMsRaw >= 0 ? postActionSettleMsRaw : 800;

const ignoreConsoleRegexRaw = process.env.E2E_PANEL_IGNORE_CONSOLE_REGEX?.trim();
const ignoreTerminalRegexRaw = process.env.E2E_PANEL_IGNORE_TERMINAL_REGEX?.trim();
const terminalErrorRegexRaw = process.env.E2E_PANEL_TERMINAL_ERROR_REGEX?.trim();
const requireAriaSnapshot = (() => {
  const raw = String(process.env.E2E_PANEL_REQUIRE_ARIA_SNAPSHOT || "").trim().toLowerCase();
  if (!raw) return true;
  return raw === "1" || raw === "true" || raw === "yes";
})();

const defaultIgnoredConsoleErrorPatterns = [
  /has been blocked by CORS policy/i,
  /Failed to load resource: net::ERR_FAILED/i,
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /TypeError: Failed to fetch/i,
  /Unable to preload CSS for \/assets\//i,
];

const defaultTerminalErrorPatterns = [
  /(?:^|\b)(error|exception|fatal|traceback|unhandled|uncaught)(?:\b|:)/i,
  /net::err_/i,
];

const defaultIgnoredTerminalErrorPatterns: RegExp[] = [
  /cache_util_win\.cc/i,
  /disk_cache\.cc/i,
  /gpu_disk_cache\.cc/i,
  /Unable to move the cache/i,
  /Unable to create cache/i,
  /Autofill\.enable failed/i,
  /source:\s*devtools:\/\//i,
];

function parseRegexList(raw: string | undefined): RegExp[] {
  if (!raw) {
    return [];
  }
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .flatMap((pattern) => {
      try {
        return [new RegExp(pattern, "i")];
      } catch {
        return [];
      }
    });
}

const ignoredConsoleErrorPatterns = [
  ...defaultIgnoredConsoleErrorPatterns,
  ...parseRegexList(ignoreConsoleRegexRaw),
];

const terminalErrorPatterns = (() => {
  const custom = parseRegexList(terminalErrorRegexRaw);
  return custom.length > 0 ? custom : defaultTerminalErrorPatterns;
})();

const ignoredTerminalErrorPatterns = [
  ...defaultIgnoredTerminalErrorPatterns,
  ...parseRegexList(ignoreTerminalRegexRaw),
];

function matchesAnyPattern(message: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(message));
}

function getActionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !matchesAnyPattern(error, ignoredConsoleErrorPatterns));
}

function getActionableTerminalErrors(mainProcessLogs: MainProcessLogs): string[] {
  const stdoutMatches = mainProcessLogs.stdout
    .filter((line) => matchesAnyPattern(line, terminalErrorPatterns))
    .map((line) => `stdout: ${line}`);

  const stderrMatches = mainProcessLogs.stderr
    .filter((line) => matchesAnyPattern(line, terminalErrorPatterns))
    .map((line) => `stderr: ${line}`);

  return [...stdoutMatches, ...stderrMatches].filter(
    (line) => !matchesAnyPattern(line, ignoredTerminalErrorPatterns),
  );
}

async function captureAriaSnapshot(window: Page): Promise<string> {
  try {
    const snapshot = await window.locator("body").ariaSnapshot();
    return String(snapshot || "");
  } catch {
    return "";
  }
}

async function openPanel(window: Page): Promise<void> {
  if (panelTriggerSelector) {
    const trigger = window.locator(panelTriggerSelector).first();
    await expect(trigger, `未找到面板触发器选择器: ${panelTriggerSelector}`).toBeVisible();
    await trigger.click();
    return;
  }

  if (panelTriggerText) {
    const trigger = window.getByRole("button", { name: new RegExp(panelTriggerText, "i") }).first();
    await expect(trigger, `未找到面板触发按钮文案: ${panelTriggerText}`).toBeVisible();
    await trigger.click();
    return;
  }

  await window.getByRole("button", { name: "任务历史" }).click({ timeout: 2_000 }).catch(() => undefined);
}

test("panel gate order: terminal -> console -> panel", async ({ window, mainProcessLogs }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });

  window.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);

  // Gate 1: terminal output from electron main process
  await window.waitForTimeout(startupSettleMs);
  const startupTerminalErrors = getActionableTerminalErrors(mainProcessLogs);
  if (strictTerminalErrors) {
    expect(startupTerminalErrors, "electron terminal actionable errors should be empty before console gate").toEqual([]);
  }

  // Gate 2: renderer console/page errors before any panel interaction
  const baselinePageErrors = [...pageErrors];
  const baselineConsoleErrorCount = consoleErrors.length;
  const baselineActionableConsoleErrors = getActionableConsoleErrors(consoleErrors);
  const baselineAriaSnapshot = await captureAriaSnapshot(window);
  if (strictErrors) {
    expect(baselinePageErrors, "renderer pageerror should be empty before panel action").toEqual([]);
    expect(baselineActionableConsoleErrors, "renderer actionable console.error should be empty before panel action").toEqual([]);
  }
  if (requireAriaSnapshot) {
    expect(baselineAriaSnapshot.trim().length, "ARIA snapshot should exist before panel action").toBeGreaterThan(0);
  }

  // Gate 3: open target panel and validate
  await openPanel(window);

  if (panelTargetSelector) {
    await expect(
      window.locator(panelTargetSelector).first(),
      `未找到面板目标选择器: ${panelTargetSelector}`,
    ).toBeVisible();
  } else if (panelTargetText) {
    await expect(window.getByText(panelTargetText, { exact: false }).first()).toBeVisible();
  } else {
    await expect(window.locator("#root")).toBeVisible();
  }

  await window.waitForTimeout(postActionSettleMs);

  const postPanelPageErrors = pageErrors.slice(baselinePageErrors.length);
  const postPanelConsoleErrors = consoleErrors.slice(baselineConsoleErrorCount);
  const postPanelActionableConsoleErrors = getActionableConsoleErrors(postPanelConsoleErrors);
  const postPanelAriaSnapshot = await captureAriaSnapshot(window);

  const summaryLines = [
    "[gate-order] terminal -> console -> panel",
    `[strictErrors] ${strictErrors ? "1" : "0"}`,
    `[strictTerminalErrors] ${strictTerminalErrors ? "1" : "0"}`,
    `[requireAriaSnapshot] ${requireAriaSnapshot ? "1" : "0"}`,
    `[startupSettleMs] ${startupSettleMs}`,
    `[postActionSettleMs] ${postActionSettleMs}`,
    `[aria.baseline.length] ${baselineAriaSnapshot.length}`,
    `[aria.postpanel.length] ${postPanelAriaSnapshot.length}`,
    `[terminal.stdout.lines] ${mainProcessLogs.stdout.length}`,
    `[terminal.stderr.lines] ${mainProcessLogs.stderr.length}`,
    ...startupTerminalErrors.map((error, index) => `terminal.actionable[${index}]: ${error}`),
    ...baselinePageErrors.map((error, index) => `baseline.pageerror[${index}]: ${error}`),
    ...baselineActionableConsoleErrors.map((error, index) => `baseline.console.actionable[${index}]: ${error}`),
    ...postPanelPageErrors.map((error, index) => `postpanel.pageerror[${index}]: ${error}`),
    ...postPanelActionableConsoleErrors.map((error, index) => `postpanel.console.actionable[${index}]: ${error}`),
  ];

  await test.info().attach("renderer-errors", {
    body: Buffer.from(summaryLines.length ? summaryLines.join("\n") : "none", "utf-8"),
    contentType: "text/plain",
  });
  await test.info().attach("aria-snapshot-baseline", {
    body: Buffer.from(baselineAriaSnapshot || "(empty)", "utf-8"),
    contentType: "text/plain",
  });
  await test.info().attach("aria-snapshot-postpanel", {
    body: Buffer.from(postPanelAriaSnapshot || "(empty)", "utf-8"),
    contentType: "text/plain",
  });

  if (strictErrors) {
    expect(postPanelPageErrors, "renderer pageerror should remain empty after panel action").toEqual([]);
    expect(postPanelActionableConsoleErrors, "renderer actionable console.error should remain empty after panel action").toEqual([]);
  }
  if (requireAriaSnapshot) {
    expect(postPanelAriaSnapshot.trim().length, "ARIA snapshot should exist after panel action").toBeGreaterThan(0);
  }
});
