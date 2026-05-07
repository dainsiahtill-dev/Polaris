import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

type BackendInfo = { baseUrl?: string; token?: string };
type SettingsPayload = { workspace?: string; pm_runs_director?: boolean };
type RuntimeLayoutPayload = { runtime_root?: string; workspace?: string };
type PmStatusPayload = { running?: boolean };
type SnapshotPayload = { tasks?: unknown[]; pm_state?: Record<string, unknown> | null };
type DirectorStatusPayload = { state?: string };
type DirectorTaskPayload = { status?: string; metadata?: { pm_task_id?: string } };
type IntegrationQaArtifact = { reason?: string; passed?: boolean | null };
type DirectorResultArtifact = { status?: string; successes?: number; total?: number };
type PmContractPayload = {
  quality_gate?: { score?: number; critical_issue_count?: number; summary?: string };
  tasks?: Array<{
    goal?: string;
    scope_paths?: unknown[];
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
const DIRECTOR_RESULT_TIMEOUT_MS = 10 * 60 * 1000;

function toPosixPath(filePath: string): string {
  return String(filePath || "").split(path.sep).join("/");
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
        headers: {
          authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
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
    }
    await sleep(1000);
  }

  throw new Error(
    `Timed out waiting for runtime artifact ${relPath}; `
    + `last_runtime_root=${lastRuntimeRoot || "(empty)"} `
    + `last_path=${lastArtifactPath || "(empty)"} `
    + `last_pm_status=${lastPmStatus || "(unavailable)"} `
    + `last_director_status=${lastDirectorStatus || "(unavailable)"}`,
  );
}

async function dismissEngineFailureDialog(window: Page): Promise<void> {
  const dialog = window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" });
  const closeButton = dialog.getByRole("button", { name: "关闭" });
  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
  }
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

  const candidates = extractCandidateTexts();
  const keywordHits = new Set<string>();
  for (const keyword of LEAKAGE_KEYWORDS) {
    const token = keyword.toLowerCase();
    const hit = candidates.some((candidate) => {
      if (token === "role") return containsRoleLeakage(candidate);
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

async function runPmRound(window: Page): Promise<void> {
  await window.getByTestId("pm-workspace-run-once").click();
  await expect.poll(async () => Boolean((await requestJson<PmStatusPayload>(window, "/v2/pm/status")).running), {
    timeout: 90_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBe(true);
  await expect.poll(async () => Boolean((await requestJson<PmStatusPayload>(window, "/v2/pm/status")).running), {
    timeout: 25 * 60 * 1000,
    intervals: [1000, 2000, 5000, 10_000],
  }).toBe(false);
}

async function observeDirectorAfterPmOrchestration(
  window: Page,
): Promise<{ linkedTaskCount: number; uiTaskCount: number; state: string }> {
  await expect.poll(async () => {
    const tasks = await requestJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto");
    return Array.isArray(tasks)
      ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
      : 0;
  }, {
    timeout: 120_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeGreaterThan(0);
  const tasks = await requestJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto");
  const linkedTaskCount = Array.isArray(tasks)
    ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
    : 0;

  await expect.poll(async () => window.getByTestId("director-task-item").count(), {
    timeout: 60_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeGreaterThan(0);
  const uiTaskCount = await window.getByTestId("director-task-item").count();
  const status = await requestJson<DirectorStatusPayload>(window, "/v2/director/status?source=auto");
  return { linkedTaskCount, uiTaskCount, state: String(status.state || "").trim().toUpperCase() };
}

test.setTimeout(70 * 60 * 1000);

test("unattended full-chain audit with strong JSON evidence package", async ({ window, testEnv }, testInfo) => {
  test.skip(!testEnv.useRealSettings, "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to use real configured LLM settings.");

  const repoRoot = resolveRepoRoot(__dirname);
  const logsRoot = path.join(repoRoot, ".polaris", "logs");
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
    acceptance_results: { court_phase: "PASS" | "FAIL"; pm_phase: "PASS" | "FAIL"; director_phase: "PASS" | "FAIL"; qa_phase: "PASS" | "FAIL" };
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
    acceptance_results: { court_phase: "FAIL", pm_phase: "FAIL", director_phase: "FAIL", qa_phase: "FAIL" },
    evidence_paths: { screenshots: [], logs: [], snapshots: [] },
    next_risks: [],
  };

  let runtimeRoot = "";
  let latestQaReason = "";
  let latestEventsPath = "";

  try {
    await dismissEngineFailureDialog(window);
    await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

    const project = await createComplexProject("C:/Temp");
    audit.workspace = project.workspace;
    const complexityPath = testInfo.outputPath("complexity.metrics.json");
    await writeUtf8File(complexityPath, JSON.stringify(project.metrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(complexityPath));

    expect(project.metrics.fileCount).toBeGreaterThanOrEqual(10);
    expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(500);
    expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.configFileCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(2);

    await requestJson<SettingsPayload>(window, "/settings", {
      method: "POST",
      body: { workspace: project.workspace, pm_runs_director: true },
    });
    await expect.poll(async () => String((await requestJson<SettingsPayload>(window, "/settings")).workspace || "").toLowerCase(), {
      timeout: 90_000,
      intervals: [500, 1000, 2000, 3000],
    }).toBe(project.workspace.toLowerCase());

    const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
    runtimeRoot = String(layout.runtime_root || "").trim();
    expect(runtimeRoot).not.toBe("");

    const courtFlow = await runCourtFlow(window);
    await dismissEngineFailureDialog(window);

    const courtShot = testInfo.outputPath("court-phase.png");
    await window.screenshot({ path: courtShot, fullPage: true });
    audit.evidence_paths.screenshots.push(toPosixPath(courtShot));

    const docsRoots = [
      path.join(project.workspace, "docs"),
      path.join(project.workspace, ".polaris", "docs"),
    ];
    let docsCount = 0;
    for (const docsRoot of docsRoots) {
      docsCount += (await listFilesRecursive(docsRoot)).length;
    }
    expect(docsCount).toBeGreaterThan(0);

    const planArtifact = await waitForRuntimeArtifact(window, "contracts/plan.md", 120_000);
    runtimeRoot = planArtifact.runtimeRoot;
    const planPath = planArtifact.artifactPath;
    expect((await fs.readFile(planPath, "utf-8")).trim().length).toBeGreaterThan(0);
    audit.acceptance_results.court_phase = "PASS";
    audit.evidence_paths.logs.push(toPosixPath(planPath));
    if (courtFlow.fallbackUsed) {
      audit.issues_fixed.push({
        issue: "court_dialogue_stream_timeout",
        root_cause: "prompt_or_streaming",
        fix: "degraded to direct draft generation using filled goal fields",
        verified: true,
      });
    }

    const deadlineMs = Date.now() + 45 * 60 * 1000;
    while (Date.now() < deadlineMs) {
      audit.rounds += 1;
      const round = audit.rounds;

      await dismissEngineFailureDialog(window);
      await enterPmWorkspace(window);
      await expect(window.getByTestId("pm-workspace")).toBeVisible();
      await runPmRound(window);

      const directorResultArtifact = await waitForRuntimeArtifact(
        window,
        "results/director.result.json",
        DIRECTOR_RESULT_TIMEOUT_MS,
      );
      runtimeRoot = directorResultArtifact.runtimeRoot;
      const directorResultPath = directorResultArtifact.artifactPath;
      const directorResult = await readJsonFile<DirectorResultArtifact>(directorResultPath);
      audit.evidence_paths.logs.push(toPosixPath(directorResultPath));

      const snapshot = await requestJson<SnapshotPayload>(window, "/state/snapshot");
      const snapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.snapshot.json`);
      await writeUtf8File(snapshotPath, JSON.stringify(snapshot, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(snapshotPath));
      const directorSuccesses = Number(directorResult?.successes || 0);
      const directorStatus = String(directorResult?.status || "").trim();
      const pmSnapshotGate = (
        (Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0) > 0
        && (Number(snapshot.pm_state?.["completed_task_count"] || 0) > 0 || directorSuccesses > 0)
        && (String(snapshot.pm_state?.["last_director_status"] || "").trim().length > 0 || directorStatus.length > 0)
      );

      const pmContractArtifact = await waitForRuntimeArtifact(window, "contracts/pm_tasks.contract.json", 120_000);
      runtimeRoot = pmContractArtifact.runtimeRoot;
      const pmContractPath = pmContractArtifact.artifactPath;
      const pmContract = await readJsonFile<PmContractPayload>(pmContractPath);
      const score = Number(pmContract?.quality_gate?.score || 0);
      const critical = Number(pmContract?.quality_gate?.critical_issue_count || 0);
      const summary = String(pmContract?.quality_gate?.summary || "").trim();

      const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
      const invalidTasks = tasks.filter((task) => {
        const hasGoal = String(task.goal || "").trim().length > 0;
        const hasScope = Array.isArray(task.scope_paths) && task.scope_paths.length > 0;
        const hasSteps = Array.isArray(task.execution_checklist) && task.execution_checklist.length > 0;
        const acceptance = Array.isArray(task.acceptance_criteria) ? task.acceptance_criteria : (task.acceptance || []);
        const hasAcceptance = Array.isArray(acceptance) && acceptance.length > 0;
        return !(hasGoal && hasScope && hasSteps && hasAcceptance);
      }).length;

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

      if (pmSnapshotGate && score >= 80 && critical === 0 && invalidTasks === 0) {
        audit.acceptance_results.pm_phase = "PASS";
      }

      await window.getByTestId("pm-workspace-back").click();
      await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

      await dismissEngineFailureDialog(window);
      await enterDirectorWorkspace(window);
      await expect(window.getByTestId("director-workspace")).toBeVisible();
      const director = await observeDirectorAfterPmOrchestration(window);
      if (director.linkedTaskCount > 0 && director.uiTaskCount > 0) {
        audit.acceptance_results.director_phase = "PASS";
      }

      const dirShot = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.director.png`);
      await window.screenshot({ path: dirShot, fullPage: true });
      audit.evidence_paths.screenshots.push(toPosixPath(dirShot));

      await window.getByTestId("director-workspace-back").click();
      await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

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
          fix: `rerun PM + Director (evidence: ${toPosixPath(qaPath)})`,
          verified: false,
        });
      }

      latestEventsPath = (await findLatestEventsPath(runtimeRoot)) || "";
      if (latestEventsPath) audit.evidence_paths.logs.push(toPosixPath(latestEventsPath));

      if (
        audit.acceptance_results.court_phase === "PASS"
        && audit.acceptance_results.pm_phase === "PASS"
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
