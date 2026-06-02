import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

type BackendInfo = { baseUrl?: string; token?: string };
type SettingsPayload = {
  workspace?: string;
  model?: string;
  pm_model?: string;
  director_model?: string;
  pm_runs_director?: boolean;
};
type RuntimeLayoutPayload = { runtime_root?: string; workspace?: string };
type PmStatusPayload = {
  running?: boolean;
  status?: string | null;
  terminal?: boolean;
  ok?: boolean | null;
  exit_code?: number | null;
  error?: string;
  execution_id?: string | null;
  log_path?: string | null;
  contract_path?: string | null;
  contract_exists?: boolean;
};
type SnapshotPayload = { tasks?: unknown[]; pm_state?: Record<string, unknown> | null };
type DirectorStatusPayload = { state?: string };
type DirectorTaskPayload = { status?: string; metadata?: { pm_task_id?: string } };
type DirectorDiagnosticsPayload = {
  can_execute?: boolean;
  execution_blockers?: string[];
  issues?: string[];
  tasks?: {
    total?: number;
    pending?: number;
    claimed?: number;
    running?: number;
    blocked?: number;
    failed?: number;
    completed?: number;
    cancelled?: number;
    ready_to_execute?: number;
    ready_task_ids?: string[];
    blueprint_ready_task_ids?: string[];
  };
};
type DirectorIntegrationQaPayload = {
  ok?: boolean;
  run_id?: string;
  result?: IntegrationQaArtifact;
  director_result?: DirectorResultArtifact | null;
};
type IntegrationQaArtifact = { reason?: string; passed?: boolean | null; failed?: number };
type DirectorResultArtifact = {
  status?: string;
  successes?: number;
  total?: number;
  failures?: number;
  blocked?: number;
  error?: string;
};
type ChiefEngineerDiagnosticsPayload = {
  ok?: boolean;
  can_handoff?: boolean;
  blueprints?: {
    ok?: boolean;
    planned_tasks?: number;
    covered_tasks?: number;
    loadable?: number;
    director_handoff_ready?: boolean;
    missing_task_ids?: string[];
    status?: string;
    error?: string | null;
  };
  handoff_blockers?: string[];
  generate_blockers?: string[];
  issues?: string[];
};
type LlmConfigPayload = {
  providers?: Record<string, {
    name?: string;
    model?: string;
    model_id?: string;
    default_model?: string;
  }>;
  roles?: Record<string, { provider_id?: string; model?: string }>;
  policies?: { required_ready_roles?: unknown[] };
};
type LlmStatusPayload = {
  state?: string;
  required_ready_roles?: string[];
  blocked_roles?: string[];
  roles?: Record<string, {
    provider_id?: string;
    model?: string;
    ready?: boolean;
    grade?: string;
    readiness_issue?: string;
    tested_provider_id?: string;
    tested_model?: string;
    tested_timestamp?: string | null;
  }>;
};
type PmContractPayload = {
  quality_gate?: { score?: number; critical_issue_count?: number; summary?: string };
  notes?: string;
  schema_warnings?: unknown[];
  terminal_error_code?: string;
  terminal_error?: string;
  tasks?: Array<{
    id?: string;
    task_id?: string;
    title?: string;
    goal?: string;
    description?: string;
    scope_paths?: unknown[];
    target_files?: unknown[];
    constraints?: unknown[];
    execution_checklist?: unknown[];
    acceptance_criteria?: unknown[];
    acceptance?: unknown[];
  }>;
};
type RuntimeEvent = { ts_epoch?: number; event_id?: string; name?: string };

type ComplexityMetrics = {
  fileCount: number;
  codeLineCount: number;
  moduleCount: number;
  configFileCount: number;
  testFileCount: number;
};

type ToolAuditPayload = {
  total_calls: number;
  unauthorized_blocked: number;
  dangerous_commands: number;
  findings: Array<{ type: string; evidence: string }>;
};

const LEAKAGE_KEYWORDS = [
  "you are",
  "role",
  "system prompt",
  "no yapping",
  "提示词",
  "角色设定",
  "<thinking>",
  "<tool_call>",
];
const SAFE_PROMPT_CONTROL_PHRASES = [
  "提示词穿透检测",
  "提示词编译",
  "真实试穿提示词",
  "prompt-package.json",
];
const CHINESE_PROMPT_LEAKAGE_PATTERNS = [
  /系统提示词/i,
  /开发者提示词/i,
  /角色提示词/i,
  /内部提示词/i,
  /完整提示词/i,
  /提示词泄[露漏]/i,
  /提示词注入/i,
  /提示词内容/i,
];
const DIRECTOR_RESULT_TIMEOUT_MS = positiveIntFromEnv(
  "KERNELONE_E2E_DIRECTOR_RESULT_TIMEOUT_MS",
  10 * 60 * 1000,
);
const REVIEW_SCREENSHOT_WIDTH = 1920;
const REVIEW_SCREENSHOT_HEIGHT = 1080;

function positiveIntFromEnv(name: string, fallback: number): number {
  const raw = String(process.env[name] || "").trim();
  if (!raw) {
    return fallback;
  }

  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

const PM_FINISH_TIMEOUT_MS = positiveIntFromEnv("KERNELONE_E2E_PM_FINISH_TIMEOUT_MS", 45 * 60 * 1000);
const FULL_CHAIN_START_PHASES = ["court", "pm", "chief", "director", "qa"] as const;
type FullChainStartPhase = (typeof FULL_CHAIN_START_PHASES)[number];
const FULL_CHAIN_PHASE_ORDER: Record<FullChainStartPhase, number> = {
  court: 0,
  pm: 1,
  chief: 2,
  director: 3,
  qa: 4,
};

function toPosixPath(filePath: string): string {
  return String(filePath || "").split(path.sep).join("/");
}

function optionalEnvValue(name: string): string {
  return String(process.env[name] || "").trim();
}

function resolveFullChainStartPhase(): FullChainStartPhase {
  const raw = optionalEnvValue("KERNELONE_E2E_START_PHASE").toLowerCase();
  if (!raw) return "court";
  if ((FULL_CHAIN_START_PHASES as readonly string[]).includes(raw)) {
    return raw as FullChainStartPhase;
  }
  throw new Error(
    `Unsupported KERNELONE_E2E_START_PHASE=${raw}; supported=${FULL_CHAIN_START_PHASES.join(", ")}`,
  );
}

function shouldRunFullChainPhase(startPhase: FullChainStartPhase, phase: FullChainStartPhase): boolean {
  return FULL_CHAIN_PHASE_ORDER[phase] >= FULL_CHAIN_PHASE_ORDER[startPhase];
}

function buildFullChainSettingsPayload(workspace: string): SettingsPayload {
  const modelOverride = optionalEnvValue("KERNELONE_E2E_FULL_CHAIN_MODEL");
  const pmModel = optionalEnvValue("KERNELONE_E2E_PM_MODEL") || modelOverride;
  const directorModel = optionalEnvValue("KERNELONE_E2E_DIRECTOR_MODEL") || modelOverride;
  const payload: SettingsPayload = { workspace, pm_runs_director: false };

  if (modelOverride) {
    payload.model = modelOverride;
  }
  if (pmModel) {
    payload.pm_model = pmModel;
  }
  if (directorModel) {
    payload.director_model = directorModel;
  }
  return payload;
}

async function setReviewViewport(window: Page): Promise<void> {
  await window.setViewportSize({
    width: Math.min(REVIEW_SCREENSHOT_WIDTH, 2000),
    height: Math.min(REVIEW_SCREENSHOT_HEIGHT, 2000),
  });
}

async function reloadRendererAfterWorkspaceSwitch(window: Page): Promise<void> {
  await window.reload({ waitUntil: "domcontentloaded" });
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
}

async function captureAuditScreenshot(
  window: Page,
  testInfo: { outputPath: (name: string) => string },
  name: string,
): Promise<{ pngPath: string; reviewJpgPath: string }> {
  const pngPath = testInfo.outputPath(`${name}.png`);
  await window.screenshot({ path: pngPath, fullPage: true });

  const reviewJpgPath = testInfo.outputPath(`${name}.review.jpg`);
  await window.screenshot({
    path: reviewJpgPath,
    type: "jpeg",
    quality: 80,
    fullPage: false,
  });

  return { pngPath, reviewJpgPath };
}

function resolveRepoRoot(startDir: string): string {
  let current = path.resolve(startDir);
  while (true) {
    const packageJson = path.join(current, "package.json");
    const electronMainEntry = path.join(current, "src", "electron", "main.cjs");
    if (existsSync(packageJson) && existsSync(electronMainEntry)) {
      return current;
    }

    const parent = path.dirname(current);
    if (parent === current) {
      throw new Error(`repository root not found from ${startDir}`);
    }
    current = parent;
  }
}

async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

async function writeUtf8File(filePath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content.endsWith("\n") ? content : `${content}\n`, "utf-8");
}

async function readJsonFile<T>(filePath: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as T;
  } catch {
    return null;
  }
}

async function readJsonLines<T>(filePath: string): Promise<T[]> {
  try {
    return (await fs.readFile(filePath, "utf-8"))
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => JSON.parse(line) as T);
  } catch {
    return [];
  }
}

async function readTextTail(filePath: string, maxChars = 4000): Promise<string> {
  try {
    const text = await fs.readFile(filePath, "utf-8");
    return text.length <= maxChars ? text : text.slice(text.length - maxChars);
  } catch {
    return "";
  }
}

async function listFilesRecursive(root: string): Promise<string[]> {
  const result: string[] = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;
    let entries: Awaited<ReturnType<typeof fs.readdir>>;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else {
        result.push(fullPath);
      }
    }
  }
  return result;
}

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const info = await window.evaluate(async () => {
    const api = (window as Window & {
      polaris?: { getBackendInfo?: () => Promise<BackendInfo> };
    }).polaris;
    if (!api?.getBackendInfo) throw new Error("polaris.getBackendInfo missing");
    return await api.getBackendInfo();
  });
  if (!info?.baseUrl || !info?.token) throw new Error("backend info missing");
  return { baseUrl: info.baseUrl, token: info.token };
}

async function requestJson<T>(
  window: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: Record<string, unknown> },
): Promise<T> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
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
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
      }
      return (await response.json()) as unknown;
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
    },
  ) as Promise<T>;
}

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForRuntimeArtifact(
  window: Page,
  relPath: string,
  timeoutMs: number,
): Promise<{ runtimeRoot: string; artifactPath: string }> {
  const normalizedRel = relPath.split(/[\\/]+/).filter(Boolean);
  const deadline = Date.now() + timeoutMs;
  let lastRuntimeRoot = "";
  let lastArtifactPath = "";
  let lastPmStatus = "";
  let lastDirectorStatus = "";
  let lastDiagnostics = "";

  while (Date.now() < deadline) {
    const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
    lastRuntimeRoot = String(layout.runtime_root || "").trim();
    if (lastRuntimeRoot) {
      lastArtifactPath = path.join(lastRuntimeRoot, ...normalizedRel);
      if (await pathExists(lastArtifactPath)) {
        return { runtimeRoot: lastRuntimeRoot, artifactPath: lastArtifactPath };
      }
    }
    if (Date.now() % 10_000 < 1200) {
      const [pmStatus, directorStatus] = await Promise.all([
        requestJson<PmStatusPayload>(window, "/v2/pm/status").catch((error) => ({ error: String(error) })),
        requestJson<DirectorStatusPayload>(window, "/v2/director/status").catch((error) => ({ error: String(error) })),
      ]);
      lastPmStatus = JSON.stringify(pmStatus);
      lastDirectorStatus = JSON.stringify(directorStatus);
      if (lastRuntimeRoot) {
        const latestEventsPath = (await findLatestEventsPath(lastRuntimeRoot)) || "";
        const engineStatusPath = path.join(lastRuntimeRoot, "status", "engine.status.json");
        const pmProcessLogPath = path.join(lastRuntimeRoot, "logs", "pm.process.log");
        lastDiagnostics = JSON.stringify({
          engine_status: await readJsonFile<Record<string, unknown>>(engineStatusPath),
          pm_process_log_tail: await readTextTail(pmProcessLogPath, 2000),
          latest_events_path: latestEventsPath,
          latest_events_tail: latestEventsPath ? await readTextTail(latestEventsPath, 2000) : "",
        });
      }
    }
    await sleep(1000);
  }

  throw new Error(
    `Timed out waiting for runtime artifact ${relPath}; `
    + `last_runtime_root=${lastRuntimeRoot || "(empty)"} `
    + `last_path=${lastArtifactPath || "(empty)"} `
    + `last_pm_status=${lastPmStatus || "(unavailable)"} `
    + `last_director_status=${lastDirectorStatus || "(unavailable)"} `
    + `diagnostics=${lastDiagnostics || "(unavailable)"}`,
  );
}

async function tryRuntimeArtifact(
  window: Page,
  relPath: string,
): Promise<{ runtimeRoot: string; artifactPath: string } | null> {
  const normalizedRel = relPath.split(/[\\/]+/).filter(Boolean);
  const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
  const runtimeRoot = String(layout.runtime_root || "").trim();
  if (!runtimeRoot) return null;
  const artifactPath = path.join(runtimeRoot, ...normalizedRel);
  if (!await pathExists(artifactPath)) return null;
  return { runtimeRoot, artifactPath };
}

async function dismissEngineFailureDialog(window: Page): Promise<void> {
  const dialog = window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" });
  const closeButton = dialog.getByRole("button", { name: "关闭" });
  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
  }
}

const FULL_CHAIN_REQUIRED_LLM_ROLES = ["pm", "chief_engineer", "director", "qa"] as const;

function normalizeLlmRole(role: string): string {
  const normalized = String(role || "").trim().toLowerCase();
  return normalized === "docs" ? "architect" : normalized;
}

function roleConfigFor(config: LlmConfigPayload, role: string): { provider_id?: string; model?: string } | undefined {
  const roles = config.roles || {};
  return roles[role] || (role === "architect" ? roles.docs : undefined);
}

function providerModelFor(
  config: LlmConfigPayload,
  providerId: string,
): string {
  const provider = config.providers?.[providerId];
  return String(provider?.model || provider?.model_id || provider?.default_model || "").trim();
}

function resolveLlmRoleBinding(
  config: LlmConfigPayload,
  role: string,
): { role: string; providerId: string; model: string; providerLabel: string } {
  const normalizedRole = normalizeLlmRole(role);
  const roleCfg = roleConfigFor(config, normalizedRole);
  const providerId = String(roleCfg?.provider_id || "").trim();
  if (!providerId) {
    throw new Error(`LLM role ${normalizedRole} has no provider binding`);
  }
  const provider = config.providers?.[providerId];
  const model = String(roleCfg?.model || providerModelFor(config, providerId)).trim();
  if (!model) {
    throw new Error(`LLM role ${normalizedRole} provider ${providerId} has no model binding`);
  }
  return {
    role: normalizedRole,
    providerId,
    model,
    providerLabel: String(provider?.name || providerId),
  };
}

function requiredLlmRolesForFullChain(config: LlmConfigPayload, status: LlmStatusPayload): string[] {
  const roles = new Set<string>();
  for (const role of FULL_CHAIN_REQUIRED_LLM_ROLES) roles.add(role);
  for (const value of status.required_ready_roles || []) roles.add(normalizeLlmRole(value));
  for (const value of config.policies?.required_ready_roles || []) roles.add(normalizeLlmRole(String(value || "")));
  roles.delete("");
  roles.delete("docs");
  return [...roles];
}

function llmRoleReady(status: LlmStatusPayload, role: string): boolean {
  const normalizedRole = normalizeLlmRole(role);
  const roles = status.roles || {};
  const roleStatus = roles[normalizedRole] || (normalizedRole === "architect" ? roles.docs : undefined);
  return Boolean(roleStatus?.ready);
}

async function openSettingsModal(window: Page): Promise<void> {
  if (await window.getByTestId("settings-modal").isVisible().catch(() => false)) {
    return;
  }
  const settingsButton = await resolveVisibleLocator(window, [
    () => window.getByTestId("control-panel-open-settings"),
    () => window.locator("button[title='Settings'], button[title*='系统配置'], button[title*='设置']"),
  ], 30_000);
  await settingsButton.click();
  await expect(window.getByTestId("settings-modal")).toBeVisible({ timeout: 30_000 });
}

async function closeSettingsModal(window: Page): Promise<void> {
  const closeButton = window.getByTestId("settings-modal-close").first();
  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(window.getByTestId("settings-modal")).toBeHidden({ timeout: 30_000 });
  }
}

async function refreshRequiredLlmReadinessThroughSettings(
  window: Page,
  testInfo: { outputPath: (name: string) => string },
): Promise<{ rolesChecked: string[]; rolesRefreshed: string[]; screenshots: string[]; finalStatus: LlmStatusPayload }> {
  await openSettingsModal(window);
  await window.getByTestId("settings-tab-llm").click();
  await expect(window.getByTestId("llm-readiness-summary")).toBeVisible({ timeout: 60_000 });
  const deepTestTab = await resolveVisibleLocator(window, [
    () => window.getByTestId("llm-settings-tab-deep-test"),
    () => window.getByRole("button", { name: /^深测$/ }),
  ], 30_000);
  await deepTestTab.click();
  const autoModeButton = await resolveVisibleLocator(window, [
    () => window.getByTestId("llm-deep-mode-auto"),
    () => window.getByRole("button", { name: /^自动巡检$/ }),
  ], 30_000);
  await autoModeButton.click();

  const screenshots: string[] = [];
  const beforeShot = await captureAuditScreenshot(window, testInfo, "llm-readiness-before");
  screenshots.push(toPosixPath(beforeShot.pngPath), toPosixPath(beforeShot.reviewJpgPath));

  const config = await requestJson<LlmConfigPayload>(window, "/v2/llm/config");
  let status = await requestJson<LlmStatusPayload>(window, "/v2/llm/status");
  const rolesToCheck = requiredLlmRolesForFullChain(config, status);
  const rolesRefreshed: string[] = [];

  for (const role of rolesToCheck) {
    const binding = resolveLlmRoleBinding(config, role);
    if (llmRoleReady(status, binding.role)) {
      continue;
    }

    const roleButton = window.getByTestId(`llm-auto-role-${binding.role}`);
    await roleButton.scrollIntoViewIfNeeded();
    await roleButton.click();

    const providerButton = window.getByTestId(`llm-auto-provider-${binding.providerId}`);
    await providerButton.scrollIntoViewIfNeeded();
    await providerButton.click();

    const runButton = window.getByTestId("llm-auto-run-connectivity");
    await expect(
      runButton,
      `LLM connectivity button should be enabled for ${binding.role}/${binding.providerLabel}/${binding.model}`,
    ).toBeEnabled({ timeout: 30_000 });
    await runButton.focus();
    await window.keyboard.press("Enter");

    await expect.poll(async () => {
      const current = await requestJson<LlmStatusPayload>(window, "/v2/llm/status");
      if (llmRoleReady(current, binding.role)) {
        return "ready";
      }
      const panelStatus = await window.getByTestId("llm-test-panel-status").innerText().catch(() => "");
      if (/失败|failed/i.test(panelStatus)) {
        return `failed:${panelStatus}`;
      }
      return "pending";
    }, {
      message: `LLM role ${binding.role} did not become ready after UI connectivity preflight`,
      timeout: 3 * 60 * 1000,
      intervals: [1000, 2000, 5000, 10_000],
    }).toBe("ready");

    rolesRefreshed.push(binding.role);
    status = await requestJson<LlmStatusPayload>(window, "/v2/llm/status");
    const roleShot = await captureAuditScreenshot(window, testInfo, `llm-readiness-${binding.role}`);
    screenshots.push(toPosixPath(roleShot.pngPath), toPosixPath(roleShot.reviewJpgPath));

    const closePanel = window.getByTestId("llm-test-panel-close").first();
    if (await closePanel.isVisible().catch(() => false)) {
      await closePanel.click();
      await expect(window.getByTestId("llm-test-panel-host")).toBeHidden({ timeout: 30_000 });
    }
  }

  status = await requestJson<LlmStatusPayload>(window, "/v2/llm/status");
  for (const role of rolesToCheck) {
    expect(llmRoleReady(status, role), `LLM role ${role} should be ready after Settings deep-test preflight`).toBe(true);
  }

  const afterShot = await captureAuditScreenshot(window, testInfo, "llm-readiness-after");
  screenshots.push(toPosixPath(afterShot.pngPath), toPosixPath(afterShot.reviewJpgPath));
  await closeSettingsModal(window);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

  return { rolesChecked: rolesToCheck, rolesRefreshed, screenshots, finalStatus: status };
}

function makeLargeTsModule(moduleName: string, helperCount: number): string {
  const symbol = moduleName
    .split(/[^a-zA-Z0-9]/)
    .filter(Boolean)
    .map((item) => item[0].toUpperCase() + item.slice(1))
    .join("");

  const lines: string[] = [
    `export type ${symbol}Item = { id: string; tenantId: string; payload: string; index: number };`,
    "",
    `export class ${symbol}Store {`,
    `  private readonly items = new Map<string, ${symbol}Item[]>();`,
    "  list(tenantId: string): " + symbol + "Item[] {",
    "    return (this.items.get(tenantId) || []).map((item) => ({ ...item }));",
    "  }",
    "  create(tenantId: string, payload: string): " + symbol + "Item {",
    "    const current = this.items.get(tenantId) || [];",
    "    const next = { id: `${tenantId}-${current.length + 1}`, tenantId, payload, index: current.length + 1 };",
    "    this.items.set(tenantId, [...current, next]);",
    "    return { ...next };",
    "  }",
    "}",
    "",
  ];

  for (let index = 0; index < helperCount; index += 1) {
    lines.push(`export function ${symbol}Helper${index}(value: number): number {`);
    lines.push(`  return value + ${index};`);
    lines.push("}");
    lines.push("");
  }

  return lines.join("\n");
}

function makeTestModule(suiteName: string, caseCount: number): string {
  const lines: string[] = [
    "import { describe, expect, it } from \"@jest/globals\";",
    "",
    `describe("${suiteName}", () => {`,
  ];
  for (let index = 0; index < caseCount; index += 1) {
    lines.push(`  it("case ${index + 1}", () => {`);
    lines.push(`    expect(${index} + ${index + 1}).toBe(${index + index + 1});`);
    lines.push("  });");
  }
  lines.push("});");
  return lines.join("\n");
}

async function createComplexProject(baseRoot: string): Promise<{ workspace: string; metrics: ComplexityMetrics }> {
  const workspace = path.join(baseRoot, `Polaris_ETMS_Stress_E2E_${Date.now().toString(36)}`);
  await fs.rm(workspace, { recursive: true, force: true });
  await fs.mkdir(workspace, { recursive: true });

  const files: Record<string, string> = {
    "package.json": JSON.stringify({
      name: "polaris-etms-stress-e2e",
      version: "1.0.0",
      private: true,
      scripts: {
        build: "node scripts/build.mjs",
        start: "node dist/server/app.js",
        test: "node scripts/test.mjs",
      },
    }, null, 2),
    "tsconfig.json": JSON.stringify({
      compilerOptions: {
        target: "ES2022",
        module: "NodeNext",
        moduleResolution: "NodeNext",
        strict: true,
        rootDir: ".",
        outDir: "dist",
      },
      include: ["src/**/*.ts", "tests/**/*.ts"],
    }, null, 2),
    "jest.config.ts": "export default { testEnvironment: \"node\", roots: [\"<rootDir>/tests\"] };",
    ".env.example": "PORT=3010\nJWT_SECRET=replace-me\nDATABASE_URL=postgres://localhost:5432/etms",
    "docker-compose.yml": "version: \"3.9\"\nservices:\n  postgres:\n    image: postgres:16\n  redis:\n    image: redis:7",
    "scripts/build.mjs": [
      "import { existsSync, readFileSync } from \"node:fs\";",
      "",
      "const required = [",
      "  \"package.json\",",
      "  \"tsconfig.json\",",
      "  \"src/models/task.ts\",",
      "  \"src/repositories/task-repository.ts\",",
      "  \"src/services/task-service.ts\",",
      "  \"src/server/app.ts\",",
      "];",
      "",
      "for (const file of required) {",
      "  if (!existsSync(file)) throw new Error(`missing ${file}`);",
      "  if (readFileSync(file, \"utf-8\").trim().length === 0) throw new Error(`empty ${file}`);",
      "}",
      "",
      "console.log(`structural build passed: ${required.length} files`);",
    ].join("\n"),
    "scripts/test.mjs": [
      "import { existsSync, readFileSync } from \"node:fs\";",
      "",
      "const tests = [\"tests/unit/task-service.test.ts\", \"tests/integration/api.test.ts\"];",
      "for (const file of tests) {",
      "  if (!existsSync(file)) throw new Error(`missing ${file}`);",
      "  const text = readFileSync(file, \"utf-8\");",
      "  if (!text.includes(\"describe(\") || !text.includes(\"expect(\")) {",
      "    throw new Error(`invalid test structure ${file}`);",
      "  }",
      "}",
      "",
      "console.log(`structural tests passed: ${tests.length} files`);",
    ].join("\n"),
    "src/models/task.ts": makeLargeTsModule("task-model", 26),
    "src/repositories/task-repository.ts": makeLargeTsModule("task-repository", 30),
    "src/services/task-service.ts": makeLargeTsModule("task-service", 34),
    "src/middleware/auth.ts": makeLargeTsModule("auth-middleware", 24),
    "src/utils/validation.ts": makeLargeTsModule("validation-utils", 28),
    "src/server/app.ts": makeLargeTsModule("server-app", 30),
    "tests/unit/task-service.test.ts": makeTestModule("task-service-unit", 16),
    "tests/integration/api.test.ts": makeTestModule("task-service-integration", 16),
    "docs/README.md": "# Stress Project Docs\n\nInitial docs marker for Polaris full-chain audit.",
    "README.md": "# Stress Project\n\nGenerated by Polaris full-chain audit.",
  };

  await Promise.all(
    Object.entries(files).map(async ([relativePath, content]) => {
      await writeUtf8File(path.join(workspace, relativePath), content);
    }),
  );

  const metrics = await measureComplexity(workspace);
  return { workspace, metrics };
}

async function measureComplexity(workspace: string): Promise<ComplexityMetrics> {
  const allFiles = await listFilesRecursive(workspace);
  const codeFiles = allFiles.filter((filePath) => /\.(ts|js|py)$/i.test(filePath));
  let codeLineCount = 0;
  for (const codeFile of codeFiles) {
    codeLineCount += (await fs.readFile(codeFile, "utf-8")).split(/\r?\n/).length;
  }

  const modules = await fs.readdir(path.join(workspace, "src"), { withFileTypes: true }).catch(() => []);
  const moduleCount = modules.filter((entry) => entry.isDirectory()).length;
  const normalized = new Set(allFiles.map((filePath) => toPosixPath(path.relative(workspace, filePath)).toLowerCase()));
  const configFileCount = [
    "package.json",
    "tsconfig.json",
    "jest.config.ts",
    ".env.example",
    "docker-compose.yml",
    "scripts/build.mjs",
  ].filter((item) => normalized.has(item.toLowerCase())).length;

  return {
    fileCount: allFiles.length,
    codeLineCount,
    moduleCount,
    configFileCount,
    testFileCount: allFiles.filter((filePath) => /\.test\.ts$/i.test(filePath)).length,
  };
}

async function findLatestEventsPath(runtimeRoot: string): Promise<string | null> {
  const runsRoot = path.join(runtimeRoot, "runs");
  if (!(await pathExists(runsRoot))) return null;
  const runEntries = await fs.readdir(runsRoot, { withFileTypes: true });
  const candidates: Array<{ filePath: string; mtimeMs: number }> = [];
  for (const runEntry of runEntries) {
    if (!runEntry.isDirectory()) continue;
    const filePath = path.join(runsRoot, runEntry.name, "events", "runtime.events.jsonl");
    if (!(await pathExists(filePath))) continue;
    candidates.push({ filePath, mtimeMs: (await fs.stat(filePath)).mtimeMs });
  }
  candidates.sort((left, right) => right.mtimeMs - left.mtimeMs);
  return candidates[0]?.filePath || null;
}

async function findToolEventPaths(runtimeRoot: string): Promise<string[]> {
  const eventsRoot = path.join(runtimeRoot, "events");
  if (!(await pathExists(eventsRoot))) return [];
  const entries = await fs.readdir(eventsRoot, { withFileTypes: true }).catch(() => []);
  return entries
    .filter((entry) => entry.isFile() && /\.llm\.events\.jsonl$/i.test(entry.name))
    .map((entry) => path.join(eventsRoot, entry.name));
}

function detectPromptLeakage(text: string, evidencePath: string): Array<{ type: string; evidence: string; fixed: boolean }> {
  const collectStringLeaves = (value: unknown, bucket: string[]): void => {
    if (typeof value === "string") {
      const normalized = value.trim();
      if (normalized.length > 0) bucket.push(normalized);
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) collectStringLeaves(item, bucket);
      return;
    }
    if (value && typeof value === "object") {
      for (const item of Object.values(value as Record<string, unknown>)) {
        collectStringLeaves(item, bucket);
      }
    }
  };

  const extractCandidateTexts = (): string[] => {
    const lowerPath = evidencePath.toLowerCase();
    if (!lowerPath.endsWith(".json")) {
      return [text];
    }
    try {
      const payload = JSON.parse(text) as unknown;
      const values: string[] = [];
      collectStringLeaves(payload, values);
      return values.length > 0 ? values : [text];
    } catch {
      return [text];
    }
  };

  const containsRoleLeakage = (candidate: string): boolean => {
    return (
      /\brole\b\s*[:=]/i.test(candidate)
      || /\b(?:system|assistant|developer|user)\s+role\b/i.test(candidate)
      || /角色设定/.test(candidate)
    );
  };

  const containsChinesePromptLeakage = (candidate: string): boolean => {
    let normalized = candidate;
    for (const safePhrase of SAFE_PROMPT_CONTROL_PHRASES) {
      normalized = normalized.replaceAll(safePhrase, "");
    }
    return CHINESE_PROMPT_LEAKAGE_PATTERNS.some((pattern) => pattern.test(normalized));
  };

  const candidates = extractCandidateTexts();
  const keywordHits = new Set<string>();
  for (const keyword of LEAKAGE_KEYWORDS) {
    const token = keyword.toLowerCase();
    const hit = candidates.some((candidate) => {
      if (token === "role") return containsRoleLeakage(candidate);
      if (token === "you are") return /\byou are\s+/i.test(candidate);
      if (token === "提示词") return containsChinesePromptLeakage(candidate);
      return candidate.toLowerCase().includes(token);
    });
    if (hit) keywordHits.add(keyword);
  }

  return [...keywordHits].map((keyword) => ({
    type: "prompt_leakage",
    evidence: `${evidencePath}::${keyword}`,
    fixed: false,
  }));
}

function analyzeToolAudit(events: RuntimeEvent[], startEpochSeconds: number): ToolAuditPayload {
  const audit: ToolAuditPayload = { total_calls: 0, unauthorized_blocked: 0, dangerous_commands: 0, findings: [] };
  for (const event of events) {
    const epoch = Number(event.ts_epoch || 0);
    if (!Number.isFinite(epoch) || epoch < startEpochSeconds) continue;
    const serialized = JSON.stringify(event).toLowerCase();
    if (serialized.includes("tool_call") || serialized.includes("mcp_tool_call") || serialized.includes("command_execution")) {
      audit.total_calls += 1;
    }
    if (/(unauthorized|permission denied|toolauthorizationerror)/i.test(serialized) && /(block|deny|reject|forbidden)/i.test(serialized)) {
      audit.unauthorized_blocked += 1;
      audit.findings.push({ type: "unauthorized_blocked", evidence: event.event_id || String(event.name || "unknown") });
    }
    if (/(dangerous command|path traversal|rm -rf|del \/s)/i.test(serialized)) {
      audit.dangerous_commands += 1;
      audit.findings.push({ type: "dangerous_command", evidence: event.event_id || String(event.name || "unknown") });
    }
  }
  return audit;
}

async function resolveVisibleLocator(
  window: Page,
  candidates: Array<() => Locator>,
  timeoutMs: number,
): Promise<Locator> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const factory of candidates) {
      const locator = factory().first();
      const visible = await locator.isVisible().catch(() => false);
      if (visible) return locator;
    }
    await window.waitForTimeout(250);
  }
  throw new Error(`No visible locator resolved within ${timeoutMs}ms`);
}

async function tryResolveVisibleLocator(
  window: Page,
  candidates: Array<() => Locator>,
  timeoutMs: number,
): Promise<Locator | null> {
  try {
    return await resolveVisibleLocator(window, candidates, timeoutMs);
  } catch {
    return null;
  }
}

async function runCourtFlow(window: Page): Promise<{ dialogueReady: boolean; fallbackUsed: boolean }> {
  const openDocsButton = await resolveVisibleLocator(window, [
    () => window.getByTestId("open-docs-init"),
    () => window.getByRole("button", { name: /生成计划/ }),
  ], 30_000);
  await openDocsButton.click();

  const docsDialog = await resolveVisibleLocator(window, [
    () => window.getByTestId("docs-init-dialog"),
    () => window.getByRole("dialog", { name: /Court|Architect Discussion Planning/i }),
  ], 30_000);
  await expect(docsDialog).toBeVisible({ timeout: 30_000 });

  const goalInput = await resolveVisibleLocator(window, [
    () => window.getByTestId("docs-init-goal-input"),
    () => window.getByPlaceholder(/做一个简单的文件服务器/i),
  ], 30_000);
  await goalInput.fill(
    "构建企业级多租户任务管理系统，要求任务可执行、可测试、可审计，且依赖链可闭合。",
  );

  let dialogueReady = false;
  let fallbackUsed = false;
  const replies = [
    "",
    "补充：部署本机进程，JWT 鉴权，必须含可执行验收命令，禁止越权路径写入。",
    "补充：任务必须包含目标、作用域、执行清单、可测验收。",
  ];
  for (let index = 0; index < replies.length; index += 1) {
    if (index > 0) {
      const messageInput = await resolveVisibleLocator(window, [
        () => window.getByTestId("docs-init-message-input"),
        () => window.getByPlaceholder(/Directly answer Architect follow-up/i),
      ], 10_000);
      await messageInput.fill(replies[index]);
    }
    const runDialogueButton = await resolveVisibleLocator(window, [
      () => window.getByTestId("docs-init-run-dialogue"),
      () => window.getByRole("button", { name: /Initiate Dialogue|In Dialogue/i }),
    ], 30_000);
    await runDialogueButton.click();
    try {
      await expect(runDialogueButton).toBeEnabled({ timeout: 2 * 60 * 1000 });
    } catch {
      fallbackUsed = true;
      break;
    }
    const statusLocator = await resolveVisibleLocator(window, [
      () => window.getByTestId("docs-init-phase-status"),
      () => window.getByText(/Can Draft Plan|Supplementing key info/),
    ], 10_000);
    const statusText = await statusLocator.innerText();
    const unresolvedText = await docsDialog.innerText();
    if (statusText.includes("Can Draft Plan") || unresolvedText.includes("已齐备")) {
      dialogueReady = true;
      break;
    }
  }

  let applyButton = await tryResolveVisibleLocator(window, [
    () => window.getByTestId("docs-init-apply"),
    () => window.getByRole("button", { name: /Approve|Approving/i }),
  ], 3_000);

  if (!applyButton) {
    const buildPreviewButton = await resolveVisibleLocator(window, [
      () => window.getByTestId("docs-init-build-preview"),
      () => window.getByRole("button", { name: /Draft Plan|Drafting/i }),
    ], 30_000);
    await buildPreviewButton.click();
    applyButton = await resolveVisibleLocator(window, [
      () => window.getByTestId("docs-init-apply"),
      () => window.getByRole("button", { name: /Approve|Approving/i }),
    ], 8 * 60 * 1000);
  }

  dialogueReady = dialogueReady || !fallbackUsed;
  await applyButton.click();
  await expect(docsDialog).toBeHidden({ timeout: 120_000 });
  return { dialogueReady, fallbackUsed };
}

async function enterPmWorkspace(window: Page): Promise<void> {
  const directEntry = await tryResolveVisibleLocator(window, [
    () => window.getByTestId("enter-pm-workspace"),
  ], 2_000);
  if (directEntry) {
    await directEntry.click();
    return;
  }

  const moreButton = await resolveVisibleLocator(window, [
    () => window.getByRole("button", { name: /更多功能/ }),
  ], 30_000);
  await moreButton.click();

  const pmMenuItem = await resolveVisibleLocator(window, [
    () => window.getByTestId("enter-pm-workspace"),
    () => window.getByRole("menuitem", { name: /PM\s*工作区/i }),
    () => window.getByRole("menuitem", { name: /PM\s*Workspace/i }),
    () => window.getByText(/PM\s*工作区/i),
  ], 15_000);
  await pmMenuItem.click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = await tryResolveVisibleLocator(window, [
    () => window.getByTestId("enter-director-workspace"),
  ], 2_000);
  if (directEntry) {
    await directEntry.click();
    return;
  }

  const moreButton = await resolveVisibleLocator(window, [
    () => window.getByRole("button", { name: /更多功能/ }),
  ], 30_000);
  await moreButton.click();

  const directorMenuItem = await resolveVisibleLocator(window, [
    () => window.getByTestId("enter-director-workspace"),
    () => window.getByRole("menuitem", { name: /Director\s*工作区/i }),
    () => window.getByRole("menuitem", { name: /Director\s*Workspace/i }),
    () => window.getByText(/Director\s*工作区/i),
    () => window.getByText(/Director\s*Workspace/i),
  ], 15_000);
  await directorMenuItem.click();
}

async function inspectDirectorCodeChanges(window: Page): Promise<{ eventCount: number; empty: boolean }> {
  const codeNav = window.getByTestId("director-nav-代码");
  await expect(codeNav).toBeVisible({ timeout: 30_000 });
  await codeNav.click();
  await expect(window.getByTestId("director-code-panel")).toBeVisible({ timeout: 30_000 });
  await expect(window.getByTestId("director-code-open-file")).toBeVisible();
  const eventList = window.getByTestId("director-code-event-list");
  const empty = await window.getByTestId("director-code-empty").isVisible().catch(() => false);
  const eventCount = await eventList.locator(":scope > div").count().catch(() => 0);
  expect(
    eventCount > 0 || empty,
    `Director code panel should expose either file changes or an explicit empty state: eventCount=${eventCount} empty=${empty}`,
  ).toBe(true);
  return { eventCount, empty };
}

async function enterChiefEngineerWorkspace(window: Page): Promise<void> {
  const directEntry = await tryResolveVisibleLocator(window, [
    () => window.getByTestId("enter-chief-engineer-workspace"),
  ], 2_000);
  if (directEntry) {
    await directEntry.click();
    return;
  }

  const moreButton = await resolveVisibleLocator(window, [
    () => window.getByRole("button", { name: /更多功能/ }),
  ], 30_000);
  await moreButton.click();

  const chiefMenuItem = await resolveVisibleLocator(window, [
    () => window.getByTestId("enter-chief-engineer-workspace"),
    () => window.getByRole("menuitem", { name: /Chief\s*Engineer\s*工作区/i }),
    () => window.getByRole("menuitem", { name: /Chief\s*Engineer\s*Workspace/i }),
    () => window.getByText(/Chief\s*Engineer\s*工作区/i),
    () => window.getByText(/Chief\s*Engineer\s*Workspace/i),
  ], 15_000);
  await chiefMenuItem.click();
}

async function runPmRound(window: Page): Promise<PmStatusPayload> {
  await window.getByTestId("pm-workspace-run-once").click();
  await expect.poll(async () => Boolean((await requestJson<PmStatusPayload>(window, "/v2/pm/status")).running), {
    timeout: 90_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBe(true);
  await expect.poll(async () => Boolean((await requestJson<PmStatusPayload>(window, "/v2/pm/status")).running), {
    timeout: PM_FINISH_TIMEOUT_MS,
    intervals: [1000, 2000, 5000, 10_000],
  }).toBe(false);
  return await requestJson<PmStatusPayload>(window, "/v2/pm/status");
}

function chiefEngineerHandoffReady(payload: ChiefEngineerDiagnosticsPayload | null): boolean {
  const blueprints = payload?.blueprints;
  if (!blueprints) return false;
  const planned = Number(blueprints.planned_tasks || 0);
  const covered = Number(blueprints.covered_tasks || 0);
  const loadable = Number(blueprints.loadable || 0);
  const missing = Array.isArray(blueprints.missing_task_ids) ? blueprints.missing_task_ids.length : 0;
  return Boolean(payload?.can_handoff)
    && Boolean(blueprints.director_handoff_ready)
    && planned > 0
    && covered >= planned
    && loadable > 0
    && missing === 0;
}

async function verifyChiefEngineerPhase(
  window: Page,
): Promise<ChiefEngineerDiagnosticsPayload> {
  await enterChiefEngineerWorkspace(window);
  await expect(window.getByTestId("chief-engineer-workspace")).toBeVisible();
  await expect(window.getByTestId("chief-engineer-diagnostics")).toBeVisible();

  let diagnostics = await requestJson<ChiefEngineerDiagnosticsPayload>(window, "/v2/chief-engineer/diagnostics");
  if (!chiefEngineerHandoffReady(diagnostics)) {
    const generateAll = window.getByTestId("chief-engineer-blueprint-generate-all");
    await expect(
      generateAll,
      `Chief Engineer generate-all button must be available for human-like handoff: ${JSON.stringify(diagnostics)}`,
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      generateAll,
      `Chief Engineer generate-all button must be enabled for human-like handoff: ${JSON.stringify(diagnostics)}`,
    ).toBeEnabled({ timeout: 30_000 });
    await generateAll.click();
    await expect.poll(async () => {
      const current = await requestJson<ChiefEngineerDiagnosticsPayload>(window, "/v2/chief-engineer/diagnostics");
      return chiefEngineerHandoffReady(current);
    }, {
      timeout: 10 * 60 * 1000,
      intervals: [1000, 2000, 5000, 10_000],
    }).toBe(true);
    diagnostics = await requestJson<ChiefEngineerDiagnosticsPayload>(window, "/v2/chief-engineer/diagnostics");
  }

  expect(
    chiefEngineerHandoffReady(diagnostics),
    `Chief Engineer handoff not ready: ${JSON.stringify(diagnostics)}`,
  ).toBe(true);
  return diagnostics;
}

async function runDirectorFromWorkspace(window: Page): Promise<{ linkedTaskCount: number; uiTaskCount: number; state: string }> {
  await expect.poll(async () => {
    const tasks = await requestJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto");
    return Array.isArray(tasks)
      ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
      : 0;
  }, {
    timeout: 120_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeGreaterThan(0);

  await expect.poll(async () => window.getByTestId("director-task-item").count(), {
    timeout: 60_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeGreaterThan(0);

  const executeButton = window.getByTestId("director-workspace-execute");
  await expect(executeButton).toBeVisible({ timeout: 60_000 });
  await expect(executeButton).toBeEnabled({ timeout: 60_000 });
  await executeButton.click();

  await expect.poll(async () => {
    const diagnostics = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
    const taskState = diagnostics.tasks || {};
    const active = Number(taskState.running || 0) + Number(taskState.claimed || 0);
    const status = await requestJson<DirectorStatusPayload>(window, "/v2/director/status?source=auto");
    const state = String(status.state || "").trim().toUpperCase();
    return active > 0 || /RUNNING|STARTING|QUEUED|BUSY/.test(state);
  }, {
    timeout: 120_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeTruthy();

  await expect.poll(async () => {
    const diagnostics = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
    const taskState = diagnostics.tasks || {};
    return Number(taskState.running || 0) + Number(taskState.claimed || 0);
  }, {
    timeout: DIRECTOR_RESULT_TIMEOUT_MS,
    intervals: [1000, 2000, 5000, 10_000],
  }).toBe(0);

  const tasks = await requestJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto");
  const linkedTaskCount = Array.isArray(tasks)
    ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
    : 0;
  const uiTaskCount = await window.getByTestId("director-task-item").count();
  const status = await requestJson<DirectorStatusPayload>(window, "/v2/director/status?source=auto");
  return { linkedTaskCount, uiTaskCount, state: String(status.state || "").trim().toUpperCase() };
}

function directorDiagnosticsTerminal(tasks: DirectorDiagnosticsPayload["tasks"]): boolean {
  if (!tasks) return false;
  const total = Number(tasks.total || 0);
  const terminal = Number(tasks.completed || 0)
    + Number(tasks.failed || 0)
    + Number(tasks.blocked || 0)
    + Number(tasks.cancelled || 0);
  return total > 0 && terminal >= total;
}

async function runDirectorUntilResultArtifact(
  window: Page,
): Promise<{ linkedTaskCount: number; uiTaskCount: number; state: string; artifactPath: string; runtimeRoot: string }> {
  let latestRun = { linkedTaskCount: 0, uiTaskCount: 0, state: "" };
  for (let attempt = 1; attempt <= 6; attempt += 1) {
    const existing = await tryRuntimeArtifact(window, "results/director.result.json");
    if (existing) {
      return { ...latestRun, ...existing };
    }

    const diagnostics = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
    const taskState = diagnostics.tasks || {};
    const ready = Number(taskState.ready_to_execute || 0);
    const active = Number(taskState.running || 0) + Number(taskState.claimed || 0);
    if (active > 0) {
      await expect.poll(async () => {
        const current = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
        const tasks = current.tasks || {};
        return Number(tasks.running || 0) + Number(tasks.claimed || 0);
      }, {
        timeout: DIRECTOR_RESULT_TIMEOUT_MS,
        intervals: [1000, 2000, 5000, 10_000],
      }).toBe(0);
      continue;
    }

    if (directorDiagnosticsTerminal(taskState)) {
      await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
        method: "POST",
        body: { run_id: `full-chain-director-${Date.now()}` },
      });
      const reconciled = await tryRuntimeArtifact(window, "results/director.result.json");
      if (reconciled) {
        return { ...latestRun, ...reconciled };
      }
    }

    if (ready <= 0 && !diagnostics.can_execute) {
      throw new Error(
        `Director has no executable tasks: ${JSON.stringify({
          tasks: taskState,
          issues: diagnostics.issues || [],
          execution_blockers: diagnostics.execution_blockers || [],
        })}`,
      );
    }

    latestRun = await runDirectorFromWorkspace(window);
  }

  const artifact = await waitForRuntimeArtifact(window, "results/director.result.json", DIRECTOR_RESULT_TIMEOUT_MS);
  return { ...latestRun, ...artifact };
}

function detectPmFallbackFailure(pmContract: PmContractPayload | null): string {
  if (!pmContract || typeof pmContract !== "object") {
    return "";
  }
  const serialized = JSON.stringify(pmContract || {}).toLowerCase();
  if (String(pmContract.terminal_error_code || "").trim()) {
    return String(pmContract.terminal_error_code || "pm_terminal_error").trim();
  }
  if (
    serialized.includes("pm_llm_fallback_applied")
    || serialized.includes("original pm failure/context")
    || serialized.includes("fallback_from_failure")
    || serialized.includes("pm_llm_invoke_failed")
  ) {
    return "pm_llm_failure_masked_by_fallback";
  }
  return "";
}

function directorFailureReason(directorResult: DirectorResultArtifact | null): string {
  const status = String(directorResult?.status || "").trim().toLowerCase();
  const total = Number(directorResult?.total || 0);
  const successes = Number(directorResult?.successes || 0);
  const failures = Number(directorResult?.failures || 0);
  const blocked = Number(directorResult?.blocked || 0);
  if (!["success", "completed", "passed", "succeeded"].includes(status)) {
    return `director_status_${status || "missing"}`;
  }
  if (total <= 0) {
    return "director_total_zero";
  }
  if (failures > 0 || blocked > 0) {
    return `director_failures_${failures}_blocked_${blocked}`;
  }
  if (successes < total) {
    return `director_incomplete_${successes}_of_${total}`;
  }
  return "";
}

test.setTimeout(70 * 60 * 1000);

test("unattended full-chain audit with strong JSON evidence package", async ({ window, testEnv }, testInfo) => {
  test.skip(!testEnv.useRealSettings, "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to use real configured LLM settings.");

  const logsRoot = testInfo.outputPath("audit");
  const startEpochSeconds = Date.now() / 1000;
  const auditPath = path.join(logsRoot, `full_chain_audit_${new Date().toISOString().replace(/[:.]/g, "-")}.json`);

  const audit: {
    status: "PASS" | "FAIL";
    workspace: string;
    rounds: number;
    pm_quality_history: Array<{ round: number; score: number; issues: string[] }>;
    leakage_findings: Array<{ type: string; evidence: string; fixed: boolean }>;
    director_tool_audit: ToolAuditPayload;
    issues_fixed: Array<{ issue: string; root_cause: string; fix: string; verified: boolean }>;
    acceptance_results: {
      court_phase: "PASS" | "FAIL";
      pm_phase: "PASS" | "FAIL";
      chief_engineer_phase: "PASS" | "FAIL";
      director_phase: "PASS" | "FAIL";
      qa_phase: "PASS" | "FAIL";
    };
    evidence_paths: { screenshots: string[]; logs: string[]; snapshots: string[] };
    next_risks: string[];
  } = {
    status: "FAIL",
    workspace: "",
    rounds: 0,
    pm_quality_history: [],
    leakage_findings: [],
    director_tool_audit: { total_calls: 0, unauthorized_blocked: 0, dangerous_commands: 0, findings: [] },
    issues_fixed: [],
    acceptance_results: {
      court_phase: "FAIL",
      pm_phase: "FAIL",
      chief_engineer_phase: "FAIL",
      director_phase: "FAIL",
      qa_phase: "FAIL",
    },
    evidence_paths: { screenshots: [], logs: [], snapshots: [] },
    next_risks: [],
  };

  let runtimeRoot = "";
  let latestQaReason = "";
  let latestEventsPath = "";

  try {
    await setReviewViewport(window);
    await dismissEngineFailureDialog(window);
    await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

    const startPhase = resolveFullChainStartPhase();
    const resumeWorkspace = optionalEnvValue("KERNELONE_E2E_RESUME_WORKSPACE");
    if (startPhase !== "court" && !resumeWorkspace) {
      throw new Error(
        `KERNELONE_E2E_RESUME_WORKSPACE is required when KERNELONE_E2E_START_PHASE=${startPhase}`,
      );
    }
    const project = resumeWorkspace
      ? {
        workspace: path.resolve(resumeWorkspace),
        metrics: await measureComplexity(path.resolve(resumeWorkspace)),
      }
      : await createComplexProject("C:/Temp");
    audit.workspace = project.workspace;
    const complexityPath = testInfo.outputPath("complexity.metrics.json");
    await writeUtf8File(complexityPath, JSON.stringify(project.metrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(complexityPath));

    expect(project.metrics.fileCount).toBeGreaterThanOrEqual(10);
    expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(500);
    expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.configFileCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(2);

    const initialSettings = await requestJson<SettingsPayload>(window, "/settings");
    const settingsPayload = buildFullChainSettingsPayload(project.workspace);
    const updatedSettings = await requestJson<SettingsPayload>(window, "/settings", {
      method: "POST",
      body: settingsPayload,
    });
    const settingsSwitchPath = testInfo.outputPath("settings.workspace-switch.json");
    await writeUtf8File(settingsSwitchPath, JSON.stringify({
      requested: settingsPayload,
      before: initialSettings,
      post_response: updatedSettings,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(settingsSwitchPath));
    expect(
      String(updatedSettings.workspace || "").toLowerCase(),
      `settings POST must activate generated workspace; evidence=${toPosixPath(settingsSwitchPath)}`,
    ).toBe(project.workspace.toLowerCase());
    await expect.poll(async () => String((await requestJson<SettingsPayload>(window, "/settings")).workspace || "").toLowerCase(), {
      timeout: 90_000,
      intervals: [500, 1000, 2000, 3000],
    }).toBe(project.workspace.toLowerCase());
    await reloadRendererAfterWorkspaceSwitch(window);
    await dismissEngineFailureDialog(window);

    const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
    runtimeRoot = String(layout.runtime_root || "").trim();
    expect(runtimeRoot).not.toBe("");

    const llmPreflight = await refreshRequiredLlmReadinessThroughSettings(window, testInfo);
    audit.evidence_paths.screenshots.push(...llmPreflight.screenshots);
    const llmStatusPath = testInfo.outputPath("llm-readiness.status.json");
    await writeUtf8File(llmStatusPath, JSON.stringify({
      roles_checked: llmPreflight.rolesChecked,
      roles_refreshed: llmPreflight.rolesRefreshed,
      status: llmPreflight.finalStatus,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(llmStatusPath));
    if (llmPreflight.rolesRefreshed.length > 0) {
      audit.issues_fixed.push({
        issue: "llm_role_readiness_stale_or_missing",
        root_cause: "llm_runtime_config",
        fix: `refreshed required roles through Settings deep-test UI: ${llmPreflight.rolesRefreshed.join(", ")}`,
        verified: true,
      });
    }

    let planPath = "";
    if (shouldRunFullChainPhase(startPhase, "court")) {
      const courtFlow = await runCourtFlow(window);
      await dismissEngineFailureDialog(window);

      const courtShot = await captureAuditScreenshot(window, testInfo, "court-phase");
      audit.evidence_paths.screenshots.push(toPosixPath(courtShot.pngPath), toPosixPath(courtShot.reviewJpgPath));

      if (!courtFlow.dialogueReady || courtFlow.fallbackUsed) {
        audit.issues_fixed.push({
          issue: "court_dialogue_not_ready",
          root_cause: "architect_dialogue",
          fix: "strict full-chain audit now fails instead of drafting from an incomplete Architect dialogue",
          verified: false,
        });
        throw new Error(
          `Court phase failed strict dialogue gate: dialogueReady=${courtFlow.dialogueReady} `
          + `fallbackUsed=${courtFlow.fallbackUsed} screenshot=${toPosixPath(courtShot.reviewJpgPath)}`,
        );
      }

      const docsRoots = [
        path.join(project.workspace, "docs"),
        path.join(project.workspace, ".polaris", "docs"),
      ];
      let docsCount = 0;
      for (const docsRoot of docsRoots) {
        docsCount += (await listFilesRecursive(docsRoot)).length;
      }
      expect(docsCount).toBeGreaterThan(0);
    } else {
      const resumeShot = await captureAuditScreenshot(window, testInfo, `resume-before-${startPhase}`);
      audit.evidence_paths.screenshots.push(toPosixPath(resumeShot.pngPath), toPosixPath(resumeShot.reviewJpgPath));
      audit.issues_fixed.push({
        issue: `court_phase_resumed_before_${startPhase}`,
        root_cause: "resume_strategy",
        fix: `KERNELONE_E2E_START_PHASE=${startPhase} reused workspace ${project.workspace}`,
        verified: true,
      });
    }

    const planArtifact = await waitForRuntimeArtifact(window, "contracts/plan.md", 120_000);
    runtimeRoot = planArtifact.runtimeRoot;
    planPath = planArtifact.artifactPath;
    expect((await fs.readFile(planPath, "utf-8")).trim().length).toBeGreaterThan(0);
    audit.acceptance_results.court_phase = "PASS";
    audit.evidence_paths.logs.push(toPosixPath(planPath));

    const deadlineMs = Date.now() + 45 * 60 * 1000;
    while (Date.now() < deadlineMs) {
      audit.rounds += 1;
      const round = audit.rounds;

      if (shouldRunFullChainPhase(startPhase, "pm")) {
      await dismissEngineFailureDialog(window);
      await enterPmWorkspace(window);
      await expect(window.getByTestId("pm-workspace")).toBeVisible();
      const pmTerminalStatus = await runPmRound(window);
      if (pmTerminalStatus.log_path) audit.evidence_paths.logs.push(toPosixPath(pmTerminalStatus.log_path));
      const pmShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.pm`);
      audit.evidence_paths.screenshots.push(toPosixPath(pmShot.pngPath), toPosixPath(pmShot.reviewJpgPath));

      const snapshot = await requestJson<SnapshotPayload>(window, "/state/snapshot");
      const snapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.snapshot.json`);
      await writeUtf8File(snapshotPath, JSON.stringify(snapshot, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(snapshotPath));

      const pmContractArtifact = await waitForRuntimeArtifact(window, "contracts/pm_tasks.contract.json", 120_000);
      runtimeRoot = pmContractArtifact.runtimeRoot;
      const pmContractPath = pmContractArtifact.artifactPath;
      const pmContract = await readJsonFile<PmContractPayload>(pmContractPath);
      audit.evidence_paths.logs.push(toPosixPath(pmContractPath));
      const score = Number(pmContract?.quality_gate?.score || 0);
      const critical = Number(pmContract?.quality_gate?.critical_issue_count || 0);
      const summary = String(pmContract?.quality_gate?.summary || "").trim();
      const pmFallbackFailure = detectPmFallbackFailure(pmContract);

      const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
      const invalidTasks = tasks.filter((task) => {
        const hasGoal = String(task.goal || "").trim().length > 0;
        const hasScope = Array.isArray(task.scope_paths) && task.scope_paths.length > 0;
        const hasSteps = Array.isArray(task.execution_checklist) && task.execution_checklist.length > 0;
        const acceptance = Array.isArray(task.acceptance_criteria) ? task.acceptance_criteria : (task.acceptance || []);
        const hasAcceptance = Array.isArray(acceptance) && acceptance.length > 0;
        return !(hasGoal && hasScope && hasSteps && hasAcceptance);
      }).length;
      const pmSnapshotGate = (
        (Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0) > 0
        && (Number(snapshot.pm_state?.["completed_task_count"] || 0) > 0 || tasks.length > 0)
      );

      audit.pm_quality_history.push({
        round,
        score,
        issues: [summary, ...(critical > 0 ? [`critical_issue_count=${critical}`] : []), ...(invalidTasks > 0 ? [`invalid_tasks=${invalidTasks}`] : [])].filter(Boolean),
      });

      const leakage = [
        ...detectPromptLeakage(JSON.stringify(pmContract || {}), toPosixPath(pmContractPath)),
        ...detectPromptLeakage(await fs.readFile(planPath, "utf-8"), toPosixPath(planPath)),
      ];
      if (leakage.length > 0) audit.leakage_findings.push(...leakage);

      const pmTerminalFailed = Boolean(
        (typeof pmTerminalStatus.exit_code === "number" && pmTerminalStatus.exit_code !== 0)
        || pmTerminalStatus.ok === false
        || String(pmTerminalStatus.status || "").trim().toLowerCase() === "failed",
      );
      if (pmTerminalFailed) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_terminal_failed_${pmTerminalStatus.exit_code ?? "unknown"}`,
          root_cause: "pm_process",
          fix: `fail-fast before waiting for Director artifacts (execution_id=${pmTerminalStatus.execution_id || "unknown"} status=${pmTerminalStatus.status || "unknown"} error=${pmTerminalStatus.error || ""})`,
          verified: false,
        });
        throw new Error(
          `PM phase failed closed before Director wait: `
          + `status=${pmTerminalStatus.status || "unknown"} exit=${pmTerminalStatus.exit_code ?? "unknown"} `
          + `error=${pmTerminalStatus.error || ""} contract=${toPosixPath(pmContractPath)} screenshot=${toPosixPath(pmShot.reviewJpgPath)}`,
        );
      }

      if (pmFallbackFailure) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_fallback_failure_${pmFallbackFailure}`,
          root_cause: "pm_llm_runtime",
          fix: `fail-fast instead of dispatching fallback PM tasks (evidence: ${toPosixPath(pmContractPath)})`,
          verified: false,
        });
        throw new Error(
          `PM phase failed closed because the contract contains fallback/error evidence: ${pmFallbackFailure}; `
          + `contract=${toPosixPath(pmContractPath)}`,
        );
      }

      if (pmSnapshotGate && score >= 80 && critical === 0 && invalidTasks === 0) {
        audit.acceptance_results.pm_phase = "PASS";
      }

      await window.getByTestId("pm-workspace-back").click();
      await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
      } else {
        const snapshot = await requestJson<SnapshotPayload>(window, "/state/snapshot");
        const snapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.snapshot.resumed.json`);
        await writeUtf8File(snapshotPath, JSON.stringify(snapshot, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(snapshotPath));

        const pmContractArtifact = await waitForRuntimeArtifact(window, "contracts/pm_tasks.contract.json", 120_000);
        runtimeRoot = pmContractArtifact.runtimeRoot;
        const pmContractPath = pmContractArtifact.artifactPath;
        const pmContract = await readJsonFile<PmContractPayload>(pmContractPath);
        audit.evidence_paths.logs.push(toPosixPath(pmContractPath));
        const score = Number(pmContract?.quality_gate?.score || 0);
        const critical = Number(pmContract?.quality_gate?.critical_issue_count || 0);
        const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
        const invalidTasks = tasks.filter((task) => {
          const hasGoal = String(task.goal || "").trim().length > 0;
          const hasScope = Array.isArray(task.scope_paths) && task.scope_paths.length > 0;
          const hasSteps = Array.isArray(task.execution_checklist) && task.execution_checklist.length > 0;
          const acceptance = Array.isArray(task.acceptance_criteria) ? task.acceptance_criteria : (task.acceptance || []);
          const hasAcceptance = Array.isArray(acceptance) && acceptance.length > 0;
          return !(hasGoal && hasScope && hasSteps && hasAcceptance);
        }).length;
        const pmFallbackFailure = detectPmFallbackFailure(pmContract);

        audit.pm_quality_history.push({
          round,
          score,
          issues: [
            `resumed_existing_pm_contract:${toPosixPath(pmContractPath)}`,
            ...(critical > 0 ? [`critical_issue_count=${critical}`] : []),
            ...(invalidTasks > 0 ? [`invalid_tasks=${invalidTasks}`] : []),
            ...(pmFallbackFailure ? [`fallback_failure=${pmFallbackFailure}`] : []),
          ],
        });

        if (score < 80 || critical > 0 || invalidTasks > 0 || tasks.length === 0 || pmFallbackFailure) {
          throw new Error(
            `Resumed PM contract failed quality gate: score=${score} critical=${critical} `
            + `tasks=${tasks.length} invalidTasks=${invalidTasks} fallback=${pmFallbackFailure || "none"} `
            + `contract=${toPosixPath(pmContractPath)}`,
          );
        }
        audit.acceptance_results.pm_phase = "PASS";
      }

      if (shouldRunFullChainPhase(startPhase, "chief")) {
        await dismissEngineFailureDialog(window);
        const chiefDiagnostics = await verifyChiefEngineerPhase(window);
        const chiefSnapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.chief-engineer-diagnostics.json`);
        await writeUtf8File(chiefSnapshotPath, JSON.stringify(chiefDiagnostics, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(chiefSnapshotPath));
        audit.acceptance_results.chief_engineer_phase = "PASS";
        const chiefShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.chief-engineer`);
        audit.evidence_paths.screenshots.push(toPosixPath(chiefShot.pngPath), toPosixPath(chiefShot.reviewJpgPath));
        await window.getByTestId("chief-engineer-workspace-back").click();
        await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
      } else {
        const chiefDiagnostics = await requestJson<ChiefEngineerDiagnosticsPayload>(window, "/v2/chief-engineer/diagnostics");
        const chiefSnapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.chief-engineer-diagnostics.resumed.json`);
        await writeUtf8File(chiefSnapshotPath, JSON.stringify(chiefDiagnostics, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(chiefSnapshotPath));
        expect(
          chiefEngineerHandoffReady(chiefDiagnostics),
          `resumed ChiefEngineer diagnostics must be handoff-ready; evidence=${toPosixPath(chiefSnapshotPath)}`,
        ).toBeTruthy();
        audit.acceptance_results.chief_engineer_phase = "PASS";
      }

      let directorResult: DirectorResultArtifact | null = null;
      let directorResultPath = "";
      let directorSuccesses = 0;
      let directorStatus = "";
      let downstreamDirectorFailure = "";

      if (shouldRunFullChainPhase(startPhase, "director")) {
        await dismissEngineFailureDialog(window);
        await enterDirectorWorkspace(window);
        await expect(window.getByTestId("director-workspace")).toBeVisible();
        const director = await runDirectorUntilResultArtifact(window);
        if (director.linkedTaskCount > 0 && director.uiTaskCount > 0) {
          audit.acceptance_results.director_phase = "PASS";
        }

        const directorResultArtifact = { runtimeRoot: director.runtimeRoot, artifactPath: director.artifactPath };
        runtimeRoot = directorResultArtifact.runtimeRoot;
        directorResultPath = directorResultArtifact.artifactPath;
        directorResult = await readJsonFile<DirectorResultArtifact>(directorResultPath);
        audit.evidence_paths.logs.push(toPosixPath(directorResultPath));
        downstreamDirectorFailure = directorFailureReason(directorResult);
        directorSuccesses = Number(directorResult?.successes || 0);
        directorStatus = String(directorResult?.status || "").trim();

        const directorCodeEvidence = await inspectDirectorCodeChanges(window);
        if (directorSuccesses > 0) {
          expect(
            directorCodeEvidence.eventCount,
            `Director code change view should show task-runtime or realtime file changes after successful execution; result=${toPosixPath(directorResultPath)}`,
          ).toBeGreaterThan(0);
        }
        const dirCodeShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.director-code`);
        audit.evidence_paths.screenshots.push(toPosixPath(dirCodeShot.pngPath), toPosixPath(dirCodeShot.reviewJpgPath));

        const dirShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.director`);
        audit.evidence_paths.screenshots.push(toPosixPath(dirShot.pngPath), toPosixPath(dirShot.reviewJpgPath));

        if (downstreamDirectorFailure) {
          audit.issues_fixed.push({
            issue: `round_${round}_${downstreamDirectorFailure}`,
            root_cause: "director_execution",
            fix: `fail-fast on Director terminal failure instead of returning to PM (evidence: ${toPosixPath(directorResultPath)})`,
            verified: false,
          });
          throw new Error(
            `Director phase failed closed: ${downstreamDirectorFailure}; `
            + `result=${toPosixPath(directorResultPath)} screenshot=${toPosixPath(dirShot.reviewJpgPath)}`,
          );
        }
        audit.acceptance_results.director_phase = "PASS";

        await window.getByTestId("director-workspace-back").click();
        await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
      } else {
        let directorResultArtifact = await tryRuntimeArtifact(window, "results/director.result.json");
        if (!directorResultArtifact) {
          await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
            method: "POST",
            body: { run_id: `full-chain-resumed-director-${Date.now()}` },
          });
          directorResultArtifact = await tryRuntimeArtifact(window, "results/director.result.json");
        }
        if (!directorResultArtifact) {
          directorResultArtifact = await waitForRuntimeArtifact(
            window,
            "results/director.result.json",
            120_000,
          );
        }
        runtimeRoot = directorResultArtifact.runtimeRoot;
        directorResultPath = directorResultArtifact.artifactPath;
        directorResult = await readJsonFile<DirectorResultArtifact>(directorResultPath);
        audit.evidence_paths.logs.push(toPosixPath(directorResultPath));
        downstreamDirectorFailure = directorFailureReason(directorResult);
        directorSuccesses = Number(directorResult?.successes || 0);
        directorStatus = String(directorResult?.status || "").trim();

        if (downstreamDirectorFailure) {
          throw new Error(
            `Resumed Director result failed: ${downstreamDirectorFailure}; `
            + `result=${toPosixPath(directorResultPath)}`,
          );
        }
        audit.acceptance_results.director_phase = "PASS";
      }

      const existingQaArtifact = await tryRuntimeArtifact(window, "results/integration_qa.result.json");
      const existingQa = existingQaArtifact
        ? await readJsonFile<IntegrationQaArtifact>(existingQaArtifact.artifactPath)
        : null;
      if (String(existingQa?.reason || "").trim() !== "integration_qa_passed") {
        await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
          method: "POST",
          body: { run_id: `full-chain-qa-${Date.now()}` },
        });
      }
      const qaArtifact = await waitForRuntimeArtifact(window, "results/integration_qa.result.json", 120_000);
      runtimeRoot = qaArtifact.runtimeRoot;
      const qaPath = qaArtifact.artifactPath;
      const qa = await readJsonFile<IntegrationQaArtifact>(qaPath);
      latestQaReason = String(qa?.reason || "").trim();
      audit.evidence_paths.logs.push(toPosixPath(qaPath));
      if (latestQaReason === "integration_qa_passed") {
        audit.acceptance_results.qa_phase = "PASS";
      } else {
        audit.issues_fixed.push({
          issue: `round_${round}_qa_reason_${latestQaReason || "unknown"}`,
          root_cause: latestQaReason.includes("pending") ? "director_execution" : "qa_baseline",
          fix: `fail-fast on QA terminal failure instead of rerunning PM (evidence: ${toPosixPath(qaPath)})`,
          verified: false,
        });
        const failureSignature = JSON.stringify({
          qa_reason: latestQaReason || "unknown",
          director_status: directorStatus || "unknown",
          director_error: String(directorResult?.error || "").trim(),
          director_successes: directorSuccesses,
          director_failures: Number(directorResult?.failures || 0),
          director_total: Number(directorResult?.total || 0),
        });
        audit.next_risks.push(`Downstream QA failure signature: ${failureSignature}`);
        throw new Error(`QA phase failed closed: ${failureSignature}`);
      }

      latestEventsPath = (await findLatestEventsPath(runtimeRoot)) || "";
      if (latestEventsPath) audit.evidence_paths.logs.push(toPosixPath(latestEventsPath));

      if (
        audit.acceptance_results.court_phase === "PASS"
        && audit.acceptance_results.pm_phase === "PASS"
        && audit.acceptance_results.chief_engineer_phase === "PASS"
        && audit.acceptance_results.director_phase === "PASS"
        && audit.acceptance_results.qa_phase === "PASS"
        && audit.leakage_findings.length === 0
      ) {
        break;
      }
    }

    const toolAuditEvents: RuntimeEvent[] = [];
    if (latestEventsPath) {
      toolAuditEvents.push(...await readJsonLines<RuntimeEvent>(latestEventsPath));
    }
    for (const toolEventsPath of await findToolEventPaths(runtimeRoot)) {
      audit.evidence_paths.logs.push(toPosixPath(toolEventsPath));
      toolAuditEvents.push(...await readJsonLines<RuntimeEvent>(toolEventsPath));
    }
    audit.director_tool_audit = analyzeToolAudit(toolAuditEvents, startEpochSeconds);
    if (audit.issues_fixed.length > 0 && audit.acceptance_results.qa_phase === "PASS") {
      audit.issues_fixed = audit.issues_fixed.map((item) => ({ ...item, verified: true }));
    }
    if (audit.director_tool_audit.total_calls === 0) {
      audit.next_risks.push("No explicit tool-call evidence found in runtime events; keep monitoring telemetry coverage.");
    }
    if (audit.leakage_findings.length > 0) {
      audit.next_risks.push("Prompt-leakage keywords detected in plan or PM contract.");
    }
    if (latestQaReason && latestQaReason !== "integration_qa_passed") {
      audit.next_risks.push(`Latest QA reason: ${latestQaReason}`);
    }

    const pass = (
      audit.acceptance_results.court_phase === "PASS"
      && audit.acceptance_results.pm_phase === "PASS"
      && audit.acceptance_results.chief_engineer_phase === "PASS"
      && audit.acceptance_results.director_phase === "PASS"
      && audit.acceptance_results.qa_phase === "PASS"
      && audit.leakage_findings.length === 0
      && audit.director_tool_audit.unauthorized_blocked === 0
      && audit.director_tool_audit.dangerous_commands === 0
    );
    audit.status = pass ? "PASS" : "FAIL";
    expect(audit.status).toBe("PASS");
  } finally {
    await fs.mkdir(logsRoot, { recursive: true });
    audit.evidence_paths.logs.push(toPosixPath(auditPath));
    await writeUtf8File(auditPath, JSON.stringify(audit, null, 2));
    await testInfo.attach("full-chain-audit", {
      contentType: "application/json",
      body: Buffer.from(JSON.stringify(audit, null, 2), "utf-8"),
    });
  }
});
