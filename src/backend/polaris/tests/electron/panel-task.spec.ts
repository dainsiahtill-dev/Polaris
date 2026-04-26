import { type Locator, type Page } from "@playwright/test";
import { expect, test, type MainProcessLogs } from "./fixtures";

type NavigationStep = {
  name: string;
  selectorCandidates?: string[];
  textCandidates?: string[];
  role?: "button" | "tab" | "link" | "menuitem";
  timeoutMs?: number;
};

type FieldAction = {
  name: string;
  selectorCandidates?: string[];
  labelCandidates?: string[];
  placeholderCandidates?: string[];
  inputValue: string;
  expectContains: string;
};

type PanelTask = {
  prompt: string;
  gateConfig?: {
    strictErrors?: boolean;
    strictTerminalErrors?: boolean;
    startupSettleMs?: number;
    postActionSettleMs?: number;
  };
  navigationSteps: NavigationStep[];
  fieldAction: FieldAction;
};

const ignoreConsoleRegexRaw = process.env.E2E_PANEL_IGNORE_CONSOLE_REGEX?.trim();
const ignoreTerminalRegexRaw = process.env.E2E_PANEL_IGNORE_TERMINAL_REGEX?.trim();
const terminalErrorRegexRaw = process.env.E2E_PANEL_TERMINAL_ERROR_REGEX?.trim();
const debugPanelFlow = process.env.E2E_PANEL_DEBUG === "1";
const requireAriaSnapshot = (() => {
  const raw = String(process.env.E2E_PANEL_REQUIRE_ARIA_SNAPSHOT || "").trim().toLowerCase();
  if (!raw) return true;
  return raw === "1" || raw === "true" || raw === "yes";
})();
const semanticClickEnabled = (() => {
  const raw = String(process.env.E2E_PANEL_SEMANTIC_CLICK || "").trim().toLowerCase();
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

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function toXPathLiteral(value: string): string {
  if (!value.includes("'")) {
    return `'${value}'`;
  }
  if (!value.includes('"')) {
    return `"${value}"`;
  }
  const parts = value.split("'");
  return `concat('${parts.join(`', "'", '`)}')`;
}

function matchesAnyPattern(message: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(message));
}

function getActionableConsoleErrors(errors: string[]): string[] {
  const ignorePatterns = [
    ...defaultIgnoredConsoleErrorPatterns,
    ...parseRegexList(ignoreConsoleRegexRaw),
  ];
  return errors.filter((error) => !matchesAnyPattern(error, ignorePatterns));
}

function getActionableTerminalErrors(mainProcessLogs: MainProcessLogs): string[] {
  const terminalErrorPatterns = (() => {
    const custom = parseRegexList(terminalErrorRegexRaw);
    return custom.length > 0 ? custom : defaultTerminalErrorPatterns;
  })();

  const ignoredTerminalPatterns = [
    ...defaultIgnoredTerminalErrorPatterns,
    ...parseRegexList(ignoreTerminalRegexRaw),
  ];

  const matched = [
    ...mainProcessLogs.stdout
      .filter((line) => matchesAnyPattern(line, terminalErrorPatterns))
      .map((line) => `stdout: ${line}`),
    ...mainProcessLogs.stderr
      .filter((line) => matchesAnyPattern(line, terminalErrorPatterns))
      .map((line) => `stderr: ${line}`),
  ];

  return matched.filter((line) => !matchesAnyPattern(line, ignoredTerminalPatterns));
}

function loadTaskFromEnv(): PanelTask {
  const base64 = String(process.env.E2E_PANEL_TASK_JSON_BASE64 || "").trim();
  if (!base64) {
    throw new Error("Missing E2E_PANEL_TASK_JSON_BASE64. Use npm run test:e2e:task -- \"<prompt>\".");
  }
  const raw = Buffer.from(base64, "base64").toString("utf-8");
  const parsed = JSON.parse(raw);
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Invalid task payload.");
  }
  return parsed as PanelTask;
}

async function tryClick(locator: Locator, timeoutMs: number): Promise<boolean> {
  try {
    await locator.first().click({ timeout: timeoutMs });
    return true;
  } catch {
    return false;
  }
}

async function clickFirstMatchingSelector(
  scope: Page | Locator,
  selectors: string[] | undefined,
  timeoutMs: number,
  routePrefix = "selector",
): Promise<string | null> {
  for (const selector of selectors || []) {
    const clicked = await tryClick(scope.locator(selector), timeoutMs);
    if (clicked) {
      return `${routePrefix}:${selector}`;
    }
  }
  return null;
}

async function clickProviderEditByText(window: Page, texts: string[] | undefined, timeoutMs: number): Promise<string | null> {
  const allTexts = (texts || []).filter((item) => String(item || "").trim().length > 0);
  const tried = new Set<string>();
  for (let textIndex = 0; textIndex < allTexts.length; textIndex += 1) {
    const text = allTexts[textIndex];
    const normalized = text.trim().toLowerCase();
    if (!normalized || tried.has(normalized)) {
      continue;
    }
    tried.add(normalized);

    const regex = new RegExp(escapeRegex(text.trim()), "i");
    const isLastCandidate = textIndex === allTexts.length - 1;
    const budgetMs = isLastCandidate ? timeoutMs : Math.min(timeoutMs, 1200);
    const deadline = Date.now() + budgetMs;
    while (Date.now() < deadline) {
      const candidates = window.getByText(regex, { exact: false });
      const candidateCount = await candidates.count();
      if (debugPanelFlow) {
        console.log(`[panel-task][provider] candidate="${text}" count=${candidateCount}`);
      }

      for (let index = 0; index < candidateCount; index += 1) {
        const candidateNode = candidates.nth(index);
        const hasExactNode = await candidateNode.isVisible().catch(() => false);
        if (debugPanelFlow) {
          console.log(`[panel-task][provider] candidate="${text}" index=${index} hasExactNode=${hasExactNode}`);
        }
        if (!hasExactNode) {
          continue;
        }

        const providerCard = candidateNode.locator(
          "xpath=ancestor::*[.//button[contains(@title,'编辑') or contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'edit')]][1]",
        );
        const cardCount = await providerCard.count();
        if (debugPanelFlow) {
          console.log(`[panel-task][provider] candidate="${text}" index=${index} providerCard.count=${cardCount}`);
        }
        const editButton = providerCard.locator(
          "xpath=.//button[contains(@title,'编辑') or contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'edit')][1]",
        );
        const editButtonCount = await editButton.count();
        if (debugPanelFlow) {
          console.log(`[panel-task][provider] candidate="${text}" index=${index} editButton.count=${editButtonCount}`);
        }

        const clicked = await tryClick(editButton, Math.max(500, Math.min(timeoutMs, 2000)));
        if (debugPanelFlow) {
          console.log(`[panel-task][provider] candidate="${text}" index=${index} clicked=${clicked}`);
        }
        if (!clicked) {
          continue;
        }

        const activeEditButton = providerCard.locator(
          "xpath=.//button[contains(@title,'完成') or contains(translate(@title,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'done')][1]",
        );
        const active = await activeEditButton.isVisible({ timeout: Math.max(500, Math.min(timeoutMs, 2000)) }).catch(() => false);
        if (debugPanelFlow) {
          console.log(`[panel-task][provider] candidate="${text}" index=${index} active=${active}`);
        }
        if (active) {
          return `provider-edit:${normalized}:${index}`;
        }
      }

      await window.waitForTimeout(150);
    }
  }

  return null;
}

function normalizeMatchText(value: string): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "");
}

function collectSemanticTokens(step: NavigationStep): string[] {
  const tokens = new Set<string>();
  for (const text of step.textCandidates || []) {
    const normalized = normalizeMatchText(text);
    if (normalized.length >= 2) tokens.add(normalized);
  }

  const stepName = String(step.name || "").trim().toLowerCase();
  const nameParts = stepName
    .split(/[^a-z0-9\u4e00-\u9fa5]+/gi)
    .map((part) => normalizeMatchText(part))
    .filter((part) => part.length >= 2);
  for (const part of nameParts) tokens.add(part);

  return [...tokens];
}

async function captureAriaSnapshot(window: Page): Promise<string> {
  try {
    const snapshot = await window.locator("body").ariaSnapshot();
    return String(snapshot || "");
  } catch {
    return "";
  }
}

async function clickBySemanticHeuristic(window: Page, step: NavigationStep, timeoutMs: number): Promise<string | null> {
  if (!semanticClickEnabled) return null;
  const tokens = collectSemanticTokens(step);
  if (tokens.length === 0) return null;

  const candidateLocator = window.locator("button,[role='button'],[role='tab'],a,[role='link'],[role='menuitem']");
  const candidateMeta = await candidateLocator.evaluateAll((nodes) => {
    return nodes.map((node, index) => {
      const element = node as HTMLElement;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const visible = rect.width > 0
        && rect.height > 0
        && style.visibility !== "hidden"
        && style.display !== "none";
      const ariaLabel = element.getAttribute("aria-label") || "";
      const title = element.getAttribute("title") || "";
      const role = element.getAttribute("role") || "";
      const text = (element.innerText || element.textContent || "").trim();
      return {
        index,
        visible,
        role: role.toLowerCase(),
        text,
        ariaLabel,
        title,
      };
    });
  });

  let best: { index: number; score: number; label: string } | null = null;
  const expectedRole = String(step.role || "").trim().toLowerCase();
  for (const item of candidateMeta) {
    if (!item.visible) continue;
    const merged = normalizeMatchText(`${item.text} ${item.ariaLabel} ${item.title}`);
    if (!merged) continue;
    let score = 0;
    for (const token of tokens) {
      if (merged.includes(token)) {
        score += 2;
      }
    }
    if (expectedRole && item.role === expectedRole) {
      score += 1;
    }
    if (score <= 0) continue;

    if (!best || score > best.score) {
      best = {
        index: item.index,
        score,
        label: `${item.role || "unknown"}:${item.text || item.ariaLabel || item.title || "(empty)"}`,
      };
    }
  }

  if (!best) return null;
  const clicked = await tryClick(candidateLocator.nth(best.index), Math.max(500, timeoutMs));
  if (!clicked) return null;
  return `semantic:${best.label}:score${best.score}`;
}

async function executeNavigationStep(window: Page, step: NavigationStep): Promise<string> {
  const timeoutMs = step.timeoutMs ?? 3000;
  if (step.name.startsWith("open-provider")) {
    const modalScope = window.locator("[data-settings-modal]").first();
    const providerSummary = modalScope.getByText(/配置状态：/);
    await providerSummary.isVisible({ timeout: timeoutMs }).catch(() => undefined);
    const savingIndicator = modalScope.getByText(/Saving LLM configuration/i);
    if (await savingIndicator.isVisible().catch(() => false)) {
      await savingIndicator.waitFor({ state: "hidden", timeout: timeoutMs }).catch(() => undefined);
    }

    const anyEditButton = modalScope.locator("button[title='编辑提供商']").first();
    if (await anyEditButton.count()) {
      await expect(anyEditButton).toBeEnabled({ timeout: timeoutMs });
    }

    const selectorRoute = await clickFirstMatchingSelector(modalScope, step.selectorCandidates, timeoutMs, "provider-selector");
    if (selectorRoute) {
      const providerEditDone = modalScope.locator(
        "button[data-provider-action='edit'][title*='完成'],button[data-provider-action='edit'][title*='done' i],button[title*='完成'],button[title*='done' i]",
      ).first();
      const active = await providerEditDone.isVisible({
        timeout: Math.max(500, Math.min(timeoutMs, 2000)),
      }).catch(() => false);
      if (active) {
        return selectorRoute;
      }
    }

    const route = await clickProviderEditByText(window, step.textCandidates, timeoutMs);
    if (route) {
      return route;
    }
    throw new Error(`Navigation step failed: ${step.name}`);
  }

  if (step.name === "open-settings") {
    const settingsModal = window.locator("[data-settings-modal]").first();
    if (await settingsModal.isVisible().catch(() => false)) {
      return "already-open:[data-settings-modal]";
    }
  }

  const selectorRoute = await clickFirstMatchingSelector(window, step.selectorCandidates, timeoutMs);
  if (selectorRoute) {
    return selectorRoute;
  }

  const role = step.role || "button";
  for (const text of step.textCandidates || []) {
    const regex = new RegExp(escapeRegex(text), "i");
    const clickedByRole = await tryClick(window.getByRole(role, { name: regex }), timeoutMs);
    if (clickedByRole) {
      return `role:${role}:${text}`;
    }

    const clickedByText = await tryClick(window.getByText(regex, { exact: false }), timeoutMs);
    if (clickedByText) {
      return `text:${text}`;
    }
  }

  if (step.name === "open-llm-settings") {
    const llmTabFallback = window
      .locator("button[role='tab'],button")
      .filter({ hasText: /(LLM\s*设置|模型设置|LLM settings)/i });
    const clicked = await tryClick(llmTabFallback, timeoutMs);
    if (clicked) {
      return "heuristic:llm-settings-tab";
    }
  }

  const semanticRoute = await clickBySemanticHeuristic(window, step, timeoutMs);
  if (semanticRoute) {
    return semanticRoute;
  }

  const ariaSnapshot = await captureAriaSnapshot(window);
  const compactAria = ariaSnapshot
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 20)
    .join(" | ");
  throw new Error(`Navigation step failed: ${step.name} | aria=${compactAria}`);
}

async function resolveFieldLocator(window: Page, action: FieldAction): Promise<Locator> {
  const settingsModal = window.locator("[data-settings-modal]").first();
  const scope = (await settingsModal.count()) > 0 ? settingsModal : window.locator("body");

  for (const selector of action.selectorCandidates || []) {
    const candidate = scope.locator(selector).first();
    if (await candidate.count()) {
      return candidate;
    }
  }

  for (const label of action.labelCandidates || []) {
    const regex = new RegExp(escapeRegex(label), "i");
    const byLabel = scope.getByLabel(regex).first();
    if (await byLabel.count()) {
      return byLabel;
    }
    const labelFollowingField = scope.locator(
      `xpath=.//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), ${toXPathLiteral(
        label.trim().toLowerCase(),
      )})]/following::*[self::textarea or self::input][1]`,
    ).first();
    if (await labelFollowingField.count()) {
      return labelFollowingField;
    }
  }

  for (const placeholder of action.placeholderCandidates || []) {
    const regex = new RegExp(escapeRegex(placeholder), "i");
    const byPlaceholder = scope.getByPlaceholder(regex).first();
    if (await byPlaceholder.count()) {
      return byPlaceholder;
    }
  }

  if (action.name.includes("custom-headers")) {
    for (const selector of [
      "textarea[placeholder*='header' i]",
      "textarea[aria-label*='Custom Headers' i]",
    ]) {
      const candidate = scope.locator(selector).first();
      if (await candidate.count()) {
        return candidate;
      }
    }

    for (const label of action.labelCandidates || []) {
      const labelBased = scope.locator(
        `xpath=.//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), ${toXPathLiteral(
          label.trim().toLowerCase(),
        )})]/following::textarea[1]`,
      ).first();
      if (await labelBased.count()) {
        return labelBased;
      }
    }

    throw new Error(`Field locator not found for custom headers action: ${action.name}`);
  }

  const fallback = scope.locator("textarea,input[type='text']").first();
  if (await fallback.count()) {
    return fallback;
  }

  throw new Error(`Field locator not found for action: ${action.name}`);
}

function toInt(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

test("one-line task automation with ordered gates", async ({ window, mainProcessLogs }) => {
  test.skip(
    !String(process.env.E2E_PANEL_TASK_JSON_BASE64 || "").trim(),
    "Missing E2E_PANEL_TASK_JSON_BASE64. Use npm run test:e2e:task -- \"<prompt>\".",
  );
  const task = loadTaskFromEnv();

  const strictErrorsEnv = process.env.E2E_PANEL_STRICT_ERRORS;
  const strictTerminalErrorsEnv = process.env.E2E_PANEL_STRICT_TERMINAL_ERRORS;
  const strictErrors = strictErrorsEnv ? strictErrorsEnv === "1" : Boolean(task.gateConfig?.strictErrors ?? true);
  const strictTerminalErrors = strictTerminalErrorsEnv
    ? strictTerminalErrorsEnv === "1"
    : strictErrors || Boolean(task.gateConfig?.strictTerminalErrors ?? true);

  const startupSettleMs = toInt(process.env.E2E_PANEL_STARTUP_SETTLE_MS, task.gateConfig?.startupSettleMs ?? 1200);
  const postActionSettleMs = toInt(process.env.E2E_PANEL_POST_ACTION_SETTLE_MS, task.gateConfig?.postActionSettleMs ?? 800);

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

  // Gate 1: terminal
  await window.waitForTimeout(startupSettleMs);
  const baselineTerminalErrorCount = getActionableTerminalErrors(mainProcessLogs).length;
  const startupTerminalErrors = getActionableTerminalErrors(mainProcessLogs);
  if (strictTerminalErrors) {
    expect(startupTerminalErrors, "terminal actionable errors should be empty before console gate").toEqual([]);
  }

  // Gate 2: console baseline
  const baselinePageErrors = [...pageErrors];
  const baselineConsoleErrorCount = consoleErrors.length;
  const baselineActionableConsoleErrors = getActionableConsoleErrors(consoleErrors);
  const baselineAriaSnapshot = await captureAriaSnapshot(window);
  if (strictErrors) {
    expect(baselinePageErrors, "pageerror should be empty before panel action").toEqual([]);
    expect(baselineActionableConsoleErrors, "console actionable errors should be empty before panel action").toEqual([]);
  }
  if (requireAriaSnapshot) {
    expect(baselineAriaSnapshot.trim().length, "ARIA snapshot should exist before panel action").toBeGreaterThan(0);
  }

  // Gate 3: panel navigation + field action
  const executedNavigation: string[] = [];
  for (const step of task.navigationSteps || []) {
    const route = await executeNavigationStep(window, step);
    executedNavigation.push(`${step.name}=>${route}`);
  }

  const field = await resolveFieldLocator(window, task.fieldAction);
  await field.click({ timeout: 3000 });
  await field.fill("");
  await field.type(task.fieldAction.inputValue || "", { delay: 8 });

  const currentValue = await field.inputValue();
  expect(currentValue, `Field ${task.fieldAction.name} should contain expected marker`).toContain(
    task.fieldAction.expectContains,
  );

  await window.waitForTimeout(postActionSettleMs);

  const postPanelPageErrors = pageErrors.slice(baselinePageErrors.length);
  const postPanelConsoleErrors = consoleErrors.slice(baselineConsoleErrorCount);
  const postPanelActionableConsoleErrors = getActionableConsoleErrors(postPanelConsoleErrors);
  const postPanelTerminalErrors = getActionableTerminalErrors(mainProcessLogs).slice(baselineTerminalErrorCount);
  const postPanelAriaSnapshot = await captureAriaSnapshot(window);

  const summaryLines = [
    `[prompt] ${task.prompt}`,
    `[gate-order] terminal -> console -> panel`,
    `[strictErrors] ${strictErrors ? "1" : "0"}`,
    `[strictTerminalErrors] ${strictTerminalErrors ? "1" : "0"}`,
    `[requireAriaSnapshot] ${requireAriaSnapshot ? "1" : "0"}`,
    `[semanticClickEnabled] ${semanticClickEnabled ? "1" : "0"}`,
    `[startupSettleMs] ${startupSettleMs}`,
    `[postActionSettleMs] ${postActionSettleMs}`,
    `[aria.baseline.length] ${baselineAriaSnapshot.length}`,
    `[aria.postpanel.length] ${postPanelAriaSnapshot.length}`,
    `[navigation] ${executedNavigation.join(" | ")}`,
    `[field] ${task.fieldAction.name}`,
    `[field.value] ${currentValue}`,
    ...startupTerminalErrors.map((line, idx) => `terminal.startup.actionable[${idx}]: ${line}`),
    ...baselinePageErrors.map((line, idx) => `console.baseline.pageerror[${idx}]: ${line}`),
    ...baselineActionableConsoleErrors.map((line, idx) => `console.baseline.actionable[${idx}]: ${line}`),
    ...postPanelTerminalErrors.map((line, idx) => `terminal.postpanel.actionable[${idx}]: ${line}`),
    ...postPanelPageErrors.map((line, idx) => `console.postpanel.pageerror[${idx}]: ${line}`),
    ...postPanelActionableConsoleErrors.map((line, idx) => `console.postpanel.actionable[${idx}]: ${line}`),
  ];

  await test.info().attach("renderer-errors", {
    body: Buffer.from(summaryLines.join("\n"), "utf-8"),
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
    expect(postPanelPageErrors, "pageerror should remain empty after panel action").toEqual([]);
    expect(postPanelActionableConsoleErrors, "console actionable errors should remain empty after panel action").toEqual([]);
  }
  if (strictTerminalErrors) {
    expect(postPanelTerminalErrors, "terminal actionable errors should remain empty after panel action").toEqual([]);
  }
  if (requireAriaSnapshot) {
    expect(postPanelAriaSnapshot.trim().length, "ARIA snapshot should exist after panel action").toBeGreaterThan(0);
  }
});
