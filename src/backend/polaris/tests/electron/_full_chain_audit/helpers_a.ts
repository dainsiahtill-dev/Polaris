import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { type Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { findLatestEventsPath, resolveVisibleLocator } from "./helpers_b";
import { REVIEW_SCREENSHOT_HEIGHT, REVIEW_SCREENSHOT_WIDTH, buildResumePlanningTaskSeeds, readJpegDimensions, scenarioRequiredDomains, toPosixPath, type BackendInfo, type DirectorStatusPayload, type FullChainProjectScenario, type LlmConfigPayload, type LlmStatusPayload, type PmStatusPayload, type ResumePlanningSeed, type ResumePlanningWriteResult, type RuntimeArtifactRef, type RuntimeLayoutPayload } from "./types";

function markdownList(items: string[]): string {
  return items.map((item) => `- ${item}`).join("\n");
}

export function buildResumePlanningSeed(workspace: string, scenario: FullChainProjectScenario): ResumePlanningSeed {
  const generatedAt = new Date().toISOString();
  const tasks = buildResumePlanningTaskSeeds(scenario);
  const requiredDomains = scenarioRequiredDomains(scenario).length > 0
    ? scenarioRequiredDomains(scenario).join(", ")
    : "models, repository, service, api, tests, verification";
  const placeholderPathExamples = scenario.key === "card3d"
    ? "`C:/Temp/card3d-placeholder`, `/tmp/card3d-placeholder`, `../`, or another project root"
    : "`C:/Temp/roguelike`, `/tmp/roguelike`, `../`, or another project root";
  const taskRows = tasks.map((task) => (
    `| ${task.id} | ${task.domain} | ${task.title} | ${task.scopePaths.map((item) => `\`${item}\``).join(", ")} | ${task.acceptance.join(" ")} |`
  )).join("\n");
  const taskDetails = tasks.map((task) => [
    `## ${task.id}: ${task.title}`,
    "",
    `- Domain: ${task.domain}`,
    `- Scope paths: ${task.scopePaths.map((item) => `\`${item}\``).join(", ")}`,
    "- Acceptance:",
    markdownList(task.acceptance),
  ].join("\n")).join("\n\n");
  const requirementsMarkdown = [
    "# Polaris Full-Chain Resume Requirements",
    "",
    `Generated at: ${generatedAt}`,
    `Current workspace: \`${workspace}\``,
    `Scenario: ${scenario.key}`,
    "",
    "## Goal",
    scenario.goal,
    "",
    "## Hard PM Contract Rules",
    "",
    `- Every PM task must be bound to the current workspace: \`${workspace}\`.`,
    "- Use relative paths shown below or absolute paths under the current workspace only.",
    `- Do not use placeholder paths such as ${placeholderPathExamples}.`,
    "- Every task must include a concrete goal, scope_paths or target_files, execution_checklist, and acceptance_criteria.",
    "- Every acceptance_criteria entry must include an executable command (`npm run build` / `npm run test`) or a verifiable file evidence path.",
    "- The mandatory decomposition below must become Director implementation tasks, not documentation-editing tasks.",
    "- Do not create tasks whose target_files are only requirements.md, plan.md, workspace/docs, or other Polaris planning documents.",
    `- Required domain coverage for this resume run: ${requiredDomains}.`,
    "- Existing seed complexity is only baseline evidence. PM and Director must plan current-run changes; final complexity alone is not sufficient.",
    "- Final source/test/config files must not retain audit-seed or planning scenario markers.",
    "",
    "## Mandatory Decomposition",
    "",
    "| Task seed | Domain | Required purpose | Required scope paths | Required acceptance anchors |",
    "|---|---|---|---|---|",
    taskRows,
    "",
    "## Additional Constraints",
    "",
    "- Preserve existing package scripts; do not introduce a new package manager or external build/test dependency.",
    "- For game scenarios, do not add Rust/Cargo, Webpack, Jest, Vite, or Vitest.",
    "- For card3d scenarios, preserve the TypeScript + Three.js client and Node.js backend stack; do not replace it with another framework.",
    "- For game PRNG work, test same-seed reproducibility, range, and distribution invariants only; do not assert unverified magic-number outputs.",
    "- Prefer modifying or extending the listed seed files so current-run contribution is auditable.",
  ].join("\n");
  const planMarkdown = [
    "# Polaris Full-Chain Resume Plan",
    "",
    `Generated at: ${generatedAt}`,
    `Workspace: \`${workspace}\``,
    "",
    "## Phase Plan",
    "",
    "- PM must produce a workspace-bound contract from the mandatory decomposition below.",
    "- Chief Engineer must be able to derive handoff-ready blueprints from each PM task.",
    "- Director must apply current-run file changes and surface the latest diff automatically.",
    "- QA must pass with `evidence_grade=real_command_passed` from real verification commands.",
    "",
    taskDetails,
    "",
    "## Verification Matrix",
    "",
    "- `npm run build` proves required files are present and non-empty.",
    "- `npm run test` proves unit/integration test structure remains valid.",
    "- Runtime contribution evidence must show added, modified, or deleted files from this run.",
  ].join("\n");

  return {
    generatedAt,
    requirementsMarkdown,
    planMarkdown,
    pipelinePayload: {
      schema_version: 1,
      generated_at: generatedAt,
      source: "full-chain-audit.resume-planning-seed",
      disabled_reason: "resume-from-pm uses runtime/contracts/requirements.md and plan.md directly",
      single_doc_per_iteration: false,
      advance_rule: "disabled_for_resume_seed",
      stages: [],
    },
    progressPayload: {
      schema_version: 1,
      active_stage_index: 0,
      active_stage_id: "E2E-RESUME-REQ-01",
      last_planned_stage_id: "",
      last_planned_iteration: 0,
      last_tasks_signature_before_plan: "",
      advanced: false,
      advance_reason: "e2e_resume_seed_reset",
      updated_at: generatedAt,
    },
    tasks,
  };
}

export async function writeWorkspacePlanningDocs(workspace: string, seed: ResumePlanningSeed): Promise<string[]> {
  const requirementsPath = path.join(workspace, "docs", "product", "requirements.md");
  const planPath = path.join(workspace, "docs", "product", "plan.md");
  const legacyRequirementsPath = path.join(workspace, "docs", "10_requirements.md");
  await writeUtf8File(requirementsPath, seed.requirementsMarkdown);
  await writeUtf8File(planPath, seed.planMarkdown);
  await writeUtf8File(legacyRequirementsPath, seed.requirementsMarkdown);
  return [requirementsPath, planPath, legacyRequirementsPath];
}

function workspacePersistentRootFromLayout(layout: RuntimeLayoutPayload, workspace: string): string {
  return String(layout.workspace_persistent_root || layout.project_persistent_root || "").trim()
    || path.join(workspace, ".polaris");
}

export async function writeRuntimePlanningSeed(
  layout: RuntimeLayoutPayload,
  workspace: string,
  seed: ResumePlanningSeed,
): Promise<ResumePlanningWriteResult> {
  const runtimeRoot = String(layout.runtime_root || "").trim();
  if (!runtimeRoot) {
    throw new Error("runtime_root is required before writing resume planning seed");
  }
  const persistentRoot = workspacePersistentRootFromLayout(layout, workspace);
  const runtimeRequirementsPath = path.join(runtimeRoot, "contracts", "requirements.md");
  const runtimePlanPath = path.join(runtimeRoot, "contracts", "plan.md");
  const persistentRequirementsPath = path.join(persistentRoot, "docs", "product", "requirements.md");
  const persistentPlanPath = path.join(persistentRoot, "docs", "product", "plan.md");
  const pipelinePath = path.join(runtimeRoot, "contracts", "architect.docs_pipeline.json");
  const progressPath = path.join(runtimeRoot, "state", "pm.docs_progress.json");
  const markerPath = path.join(runtimeRoot, "contracts", "e2e.resume_planning_seed.json");
  const markerPayload = {
    generated_at: seed.generatedAt,
    workspace,
    runtime_requirements_path: runtimeRequirementsPath,
    runtime_plan_path: runtimePlanPath,
    persistent_requirements_path: persistentRequirementsPath,
    persistent_plan_path: persistentPlanPath,
    mandatory_tasks: seed.tasks,
  };

  await writeUtf8File(runtimeRequirementsPath, seed.requirementsMarkdown);
  await writeUtf8File(runtimePlanPath, seed.planMarkdown);
  await writeUtf8File(persistentRequirementsPath, seed.requirementsMarkdown);
  await writeUtf8File(persistentPlanPath, seed.planMarkdown);
  await writeUtf8File(pipelinePath, JSON.stringify(seed.pipelinePayload, null, 2));
  await writeUtf8File(progressPath, JSON.stringify(seed.progressPayload, null, 2));
  await writeUtf8File(markerPath, JSON.stringify(markerPayload, null, 2));

  return {
    writtenPaths: [
      runtimeRequirementsPath,
      runtimePlanPath,
      persistentRequirementsPath,
      persistentPlanPath,
      pipelinePath,
      progressPath,
      markerPath,
    ],
    runtimeRequirementsPath,
    runtimePlanPath,
    pipelinePath,
    progressPath,
  };
}

export async function setReviewViewport(window: Page): Promise<void> {
  await window.setViewportSize({
    width: Math.min(REVIEW_SCREENSHOT_WIDTH, 2000),
    height: Math.min(REVIEW_SCREENSHOT_HEIGHT, 2000),
  });
}

export async function reloadRendererAfterWorkspaceSwitch(window: Page): Promise<void> {
  await window.reload({ waitUntil: "domcontentloaded" });
  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });
}

export async function captureAuditScreenshot(
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
  const reviewStats = await fs.stat(reviewJpgPath);
  expect(reviewStats.size, `${name}.review.jpg should not be empty`).toBeGreaterThan(1024);
  const dimensions = readJpegDimensions(await fs.readFile(reviewJpgPath), reviewJpgPath);
  expect(dimensions.width, `${name}.review.jpg width should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.height, `${name}.review.jpg height should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.width, `${name}.review.jpg width should be visible`).toBeGreaterThan(0);
  expect(dimensions.height, `${name}.review.jpg height should be visible`).toBeGreaterThan(0);

  return { pngPath, reviewJpgPath };
}

export async function clickWorkspaceBack(window: Page, testId: string): Promise<void> {
  const progressPanel = window.getByTestId("project-progress-panel");
  if (await progressPanel.isVisible().catch(() => false)) {
    return;
  }
  const domClicked = await window.evaluate((id) => {
    const target = document.querySelector(`[data-testid="${id}"]`);
    if (!(target instanceof HTMLElement)) {
      return false;
    }
    target.click();
    return true;
  }, testId);
  if (domClicked) {
    await expect(progressPanel).toBeVisible({ timeout: 60_000 });
    return;
  }
  const backButton = window.getByTestId(testId);
  await expect(backButton).toBeVisible({ timeout: 30_000 });
  try {
    await backButton.click({ timeout: 10_000 });
  } catch {
    await window.evaluate((id) => {
      const target = document.querySelector(`[data-testid="${id}"]`);
      if (target instanceof HTMLElement) {
        target.click();
      }
    }, testId);
  }
  await expect(progressPanel).toBeVisible({ timeout: 60_000 });
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

export async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
}

export async function writeUtf8File(filePath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content.endsWith("\n") ? content : `${content}\n`, "utf-8");
}

export async function readJsonFile<T>(filePath: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as T;
  } catch {
    return null;
  }
}

export async function readJsonLines<T>(filePath: string): Promise<T[]> {
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

export async function listFilesRecursive(root: string): Promise<string[]> {
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

export async function requestJson<T>(
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

export async function waitForRuntimeArtifact(
  window: Page,
  relPath: string,
  timeoutMs: number,
  options?: { minMtimeMs?: number },
): Promise<RuntimeArtifactRef> {
  const normalizedRel = relPath.split(/[\\/]+/).filter(Boolean);
  const deadline = Date.now() + timeoutMs;
  let lastRuntimeRoot = "";
  let lastArtifactPath = "";
  let lastPmStatus = "";
  let lastDirectorStatus = "";
  let lastDiagnostics = "";

  while (Date.now() < deadline) {
    const layout = await requestJson<RuntimeLayoutPayload>(window, "/v2/runtime/storage/layout");
    lastRuntimeRoot = String(layout.runtime_root || "").trim();
    if (lastRuntimeRoot) {
      lastArtifactPath = path.join(lastRuntimeRoot, ...normalizedRel);
      if (await pathExists(lastArtifactPath)) {
        const stat = await fs.stat(lastArtifactPath);
        if (!options?.minMtimeMs || stat.mtimeMs >= options.minMtimeMs) {
          return { runtimeRoot: lastRuntimeRoot, artifactPath: lastArtifactPath, mtimeMs: stat.mtimeMs };
        }
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

export async function tryRuntimeArtifact(
  window: Page,
  relPath: string,
  options?: { minMtimeMs?: number },
): Promise<RuntimeArtifactRef | null> {
  const normalizedRel = relPath.split(/[\\/]+/).filter(Boolean);
  const layout = await requestJson<RuntimeLayoutPayload>(window, "/v2/runtime/storage/layout");
  const runtimeRoot = String(layout.runtime_root || "").trim();
  if (!runtimeRoot) return null;
  const artifactPath = path.join(runtimeRoot, ...normalizedRel);
  if (!await pathExists(artifactPath)) return null;
  const stat = await fs.stat(artifactPath);
  if (options?.minMtimeMs && stat.mtimeMs < options.minMtimeMs) return null;
  return { runtimeRoot, artifactPath, mtimeMs: stat.mtimeMs };
}

export async function dismissEngineFailureDialog(window: Page): Promise<void> {
  const dialog = window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" });
  const closeButton = dialog.getByRole("button", { name: "关闭" });
  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
  }
}

const FULL_CHAIN_REQUIRED_LLM_ROLES = ["architect", "pm", "chief_engineer", "director", "qa"] as const;

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

export async function refreshRequiredLlmReadinessThroughSettings(
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
  const variable = symbol[0].toLowerCase() + symbol.slice(1);
  const statuses = ["draft", "active", "blocked", "archived"];
  const lanes = ["planning", "runtime", "quality", "delivery"];

  const lines: string[] = [
    `export type ${symbol}Status = "${statuses.join("\" | \"")}";`,
    `export type ${symbol}Lane = "${lanes.join("\" | \"")}";`,
    `export interface ${symbol}Item {`,
    "  id: string;",
    "  tenantId: string;",
    "  title: string;",
    `  status: ${symbol}Status;`,
    `  lane: ${symbol}Lane;`,
    "  priority: number;",
    "  tags: string[];",
    "  updatedAt: string;",
    "}",
    "",
    `export interface ${symbol}Summary {`,
    "  total: number;",
    "  active: number;",
    "  blocked: number;",
    "  averagePriority: number;",
    "  lanes: Record<string, number>;",
    "}",
    "",
    `export class ${symbol}Store {`,
    `  private readonly items = new Map<string, ${symbol}Item[]>();`,
    "  list(tenantId: string): " + symbol + "Item[] {",
    "    return (this.items.get(tenantId) || []).map((item) => ({ ...item, tags: [...item.tags] }));",
    "  }",
    `  upsert(tenantId: string, item: Omit<${symbol}Item, "tenantId" | "updatedAt">): ${symbol}Item {`,
    "    const current = this.items.get(tenantId) || [];",
    "    const next = { ...item, tenantId, updatedAt: new Date(0).toISOString(), tags: [...item.tags] };",
    "    const others = current.filter((entry) => entry.id !== next.id);",
    "    this.items.set(tenantId, [...others, next].sort((a, b) => b.priority - a.priority));",
    "    return { ...next, tags: [...next.tags] };",
    "  }",
    `  summarize(tenantId: string): ${symbol}Summary {`,
    "    const rows = this.list(tenantId);",
    "    const lanes = rows.reduce<Record<string, number>>((acc, item) => {",
    "      acc[item.lane] = (acc[item.lane] || 0) + 1;",
    "      return acc;",
    "    }, {});",
    "    const priorityTotal = rows.reduce((total, item) => total + item.priority, 0);",
    "    return {",
    "      total: rows.length,",
    "      active: rows.filter((item) => item.status === \"active\").length,",
    "      blocked: rows.filter((item) => item.status === \"blocked\").length,",
    "      averagePriority: rows.length === 0 ? 0 : Number((priorityTotal / rows.length).toFixed(2)),",
    "      lanes,",
    "    };",
    "  }",
    "}",
    "",
    `export const ${variable}PolicyWeights: Record<${symbol}Status, number> = {`,
    "  draft: 1,",
    "  active: 3,",
    "  blocked: -2,",
    "  archived: 0,",
    "};",
    "",
  ];

  for (let index = 0; index < helperCount; index += 1) {
    const status = statuses[index % statuses.length];
    const lane = lanes[index % lanes.length];
    const priority = 1 + (index % 9);
    lines.push(`export const ${variable}Scenario${index}: ${symbol}Item = {`);
    lines.push(`  id: "${moduleName}-${index}",`);
    lines.push(`  tenantId: "seed-${moduleName}",`);
    lines.push(`  title: "${moduleName} ${lane} scenario ${index}",`);
    lines.push(`  status: "${status}",`);
    lines.push(`  lane: "${lane}",`);
    lines.push(`  priority: ${priority},`);
    lines.push(`  tags: ["${lane}", "${status}", "audit-seed"],`);
    lines.push("  updatedAt: \"1970-01-01T00:00:00.000Z\",");
    lines.push("};");
    lines.push("");
    lines.push(`export function score${symbol}Scenario${index}(item: ${symbol}Item): number {`);
    lines.push(`  const statusWeight = ${variable}PolicyWeights[item.status] ?? 0;`);
    lines.push(`  const laneWeight = item.lane === "${lane}" ? ${index % 5 + 1} : 1;`);
    lines.push("  const tagWeight = item.tags.includes(\"audit-seed\") ? 2 : 0;");
    lines.push("  return item.priority * statusWeight + laneWeight + tagWeight;");
    lines.push("}");
    lines.push("");
  }

  return lines.join("\n");
}

function makeTestModule(suiteName: string, caseCount: number): string {
  const lines: string[] = [
    "import { describe, expect, it } from \"@jest/globals\";",
    "",
    "const coverageCases = [",
  ];
  for (let index = 0; index < caseCount; index += 1) {
    const lane = index % 3 === 0 ? "unit" : index % 3 === 1 ? "integration" : "e2e";
    lines.push(
      `  { id: "${suiteName}-case-${index + 1}", lane: "${lane}", priority: ${1 + (index % 7)}, tags: ["${suiteName}", "${lane}"] },`,
    );
  }
  lines.push("];");
  lines.push("");
  lines.push(
    `describe("${suiteName}", () => {`,
  );
  for (let index = 0; index < caseCount; index += 1) {
    lines.push(`  it("case ${index + 1}", () => {`);
    lines.push(`    const item = coverageCases[${index}];`);
    lines.push(`    expect(item.id).toBe("${suiteName}-case-${index + 1}");`);
    lines.push("    expect(item.priority).toBeGreaterThan(0);");
    lines.push("    expect(item.tags).toContain(item.lane);");
    lines.push("  });");
  }
  lines.push("});");
  return lines.join("\n");
}

function makeStructuralBuildScript(requiredFiles: string[]): string {
  return [
    "import { existsSync, readFileSync } from \"node:fs\";",
    "",
    `const required = ${JSON.stringify(requiredFiles, null, 2)};`,
    "",
    "for (const file of required) {",
    "  if (!existsSync(file)) throw new Error(`missing ${file}`);",
    "  const text = readFileSync(file, \"utf-8\");",
    "  if (text.trim().length === 0) throw new Error(`empty ${file}`);",
    "  if (/function\\s+\\w+Helper\\d+\\s*\\(value:\\s*number\\):\\s*number\\s*\\{\\s*return\\s+value\\s*\\+\\s*\\d+;\\s*\\}/.test(text)) {",
    "    throw new Error(`numeric helper filler ${file}`);",
    "  }",
    "}",
    "",
    "console.log(`build verification completed: ${required.length} files`);",
  ].join("\n");
}

function makeStructuralTestScript(testFiles: string[]): string {
  return [
    "import { existsSync, readFileSync } from \"node:fs\";",
    "",
    `const tests = ${JSON.stringify(testFiles, null, 2)};`,
    "for (const file of tests) {",
    "  if (!existsSync(file)) throw new Error(`missing ${file}`);",
    "  const text = readFileSync(file, \"utf-8\");",
    "  if (!text.includes(\"describe(\") || !text.includes(\"expect(\")) {",
    "    throw new Error(`invalid test structure ${file}`);",
    "  }",
    "  if (/expect\\(\\s*\\d+\\s*(?:[+\\-*/])\\s*\\d+\\s*\\)\\.to(?:Be|Equal)\\(\\s*\\d+\\s*\\)/.test(text)) {",
    "    throw new Error(`trivial arithmetic placeholder test ${file}`);",
    "  }",
    "}",
    "",
    "console.log(`test verification completed: ${tests.length} files`);",
  ].join("\n");
}

export function buildEnterpriseProjectScenario(): FullChainProjectScenario {
  const buildRequiredFiles = [
    "package.json",
    "tsconfig.json",
    "src/models/task.ts",
    "src/repositories/task-repository.ts",
    "src/services/task-service.ts",
    "src/server/app.ts",
  ];
  const testFiles = ["tests/unit/task-service.test.ts", "tests/integration/api.test.ts"];
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
    "scripts/build.mjs": makeStructuralBuildScript(buildRequiredFiles),
    "scripts/test.mjs": makeStructuralTestScript(testFiles),
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

  return {
    key: "enterprise",
    workspacePrefix: "Polaris_ETMS_Stress_E2E",
    packageName: "polaris-etms-stress-e2e",
    goal: "构建企业级多租户任务管理系统，要求任务可执行、可测试、可审计，且依赖链可闭合。",
    replies: [
      "",
      "补充：部署本机进程，JWT 鉴权，必须含可执行验收命令，禁止越权路径写入。",
      "补充：任务必须包含目标、作用域、执行清单、可测验收。",
    ],
    buildRequiredFiles,
    testFiles,
    files,
  };
}

export function buildGameProjectScenario(): FullChainProjectScenario {
  const buildRequiredFiles = [
    "package.json",
    "tsconfig.json",
    "src/engine/game-loop.ts",
    "src/engine/state.ts",
    "src/world/procedural-map.ts",
    "src/world/encounter-table.ts",
    "src/combat/combat-system.ts",
    "src/combat/action-queue.ts",
    "src/ai/director-ai.ts",
    "src/ai/behavior-tree.ts",
    "src/content/cards.ts",
    "src/content/relics.ts",
    "src/content/enemies.ts",
    "src/progression/campaign.ts",
    "src/progression/quest-log.ts",
    "src/economy/loot-table.ts",
    "src/economy/shop.ts",
    "src/persistence/save-system.ts",
    "src/renderer/hud.ts",
    "src/renderer/input-controller.ts",
    "src/renderer/scene-view.ts",
    "src/audio/sound-events.ts",
    "src/audio/music-state.ts",
    "src/tools/balance-report.ts",
  ];
  const testFiles = [
    "tests/unit/combat-system.test.ts",
    "tests/unit/procedural-map.test.ts",
    "tests/integration/game-session.test.ts",
    "tests/integration/save-restore.test.ts",
    "tests/e2e/gameplay-loop.test.ts",
  ];
  const files: Record<string, string> = {
    "package.json": JSON.stringify({
      name: "polaris-tactical-game-e2e",
      version: "1.0.0",
      private: true,
      scripts: {
        build: "node scripts/build.mjs",
        start: "node dist/renderer/index.js",
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
    "AGENTS.md": [
      "# Game Workspace Rules",
      "",
      "All text files must be read and written with explicit UTF-8.",
      "This workspace is a TypeScript browser tactical roguelike seed project.",
      "Do not introduce Rust, Cargo, Go, Python, Webpack, Jest, Vite, Vitest, or any new external build/test dependency.",
      "Preserve package.json script commands: build must remain `node scripts/build.mjs`, and test must remain `node scripts/test.mjs`.",
      "Replace structural-only script contents with substantive no-external-dependency verification before final QA.",
      "Use the existing Node verification script entrypoints for acceptance.",
      "If adding PRNG tests, assert same-seed reproducibility, range, and distribution invariants only; do not assert unverified magic-number outputs.",
    ].join("\n"),
    "index.html": [
      "<!doctype html>",
      "<html lang=\"en\">",
      "  <head>",
      "    <meta charset=\"UTF-8\" />",
      "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />",
      "    <title>Polaris Tactical Roguelike</title>",
      "  </head>",
      "  <body>",
      "    <main id=\"app\"></main>",
      "    <script type=\"module\" src=\"/src/main.ts\"></script>",
      "  </body>",
      "</html>",
    ].join("\n"),
    ".env.example": "GAME_SEED=polaris-audit\nSAVE_SLOT=local\nLEADERBOARD_URL=http://127.0.0.1:4179",
    "docker-compose.yml": "version: \"3.9\"\nservices:\n  leaderboard:\n    image: redis:7\n    ports:\n      - \"6379:6379\"",
    "scripts/build.mjs": makeStructuralBuildScript(buildRequiredFiles),
    "scripts/test.mjs": makeStructuralTestScript(testFiles),
    "src/engine/game-loop.ts": makeLargeTsModule("game-loop", 38),
    "src/engine/state.ts": makeLargeTsModule("game-state", 34),
    "src/world/procedural-map.ts": makeLargeTsModule("procedural-map", 36),
    "src/world/encounter-table.ts": makeLargeTsModule("encounter-table", 26),
    "src/combat/combat-system.ts": makeLargeTsModule("combat-system", 40),
    "src/combat/action-queue.ts": makeLargeTsModule("action-queue", 30),
    "src/ai/director-ai.ts": makeLargeTsModule("enemy-director-ai", 34),
    "src/ai/behavior-tree.ts": makeLargeTsModule("behavior-tree", 32),
    "src/content/cards.ts": makeLargeTsModule("card-content", 32),
    "src/content/relics.ts": makeLargeTsModule("relic-content", 24),
    "src/content/enemies.ts": makeLargeTsModule("enemy-content", 28),
    "src/progression/campaign.ts": makeLargeTsModule("campaign-progression", 34),
    "src/progression/quest-log.ts": makeLargeTsModule("quest-log", 28),
    "src/economy/loot-table.ts": makeLargeTsModule("loot-table", 32),
    "src/economy/shop.ts": makeLargeTsModule("shop-economy", 28),
    "src/persistence/save-system.ts": makeLargeTsModule("save-system", 30),
    "src/renderer/hud.ts": makeLargeTsModule("hud-renderer", 32),
    "src/renderer/input-controller.ts": makeLargeTsModule("input-controller", 28),
    "src/renderer/scene-view.ts": makeLargeTsModule("scene-view", 30),
    "src/audio/sound-events.ts": makeLargeTsModule("sound-events", 24),
    "src/audio/music-state.ts": makeLargeTsModule("music-state", 24),
    "src/tools/balance-report.ts": makeLargeTsModule("balance-report", 26),
    "src/main.ts": [
      "export const bootMessage = \"Polaris tactical roguelike ready\";",
      "export function boot(): string {",
      "  return bootMessage;",
      "}",
    ].join("\n"),
    "tests/unit/combat-system.test.ts": makeTestModule("combat-system-unit", 18),
    "tests/unit/procedural-map.test.ts": makeTestModule("procedural-map-unit", 18),
    "tests/integration/game-session.test.ts": makeTestModule("game-session-integration", 18),
    "tests/integration/save-restore.test.ts": makeTestModule("save-restore-integration", 18),
    "tests/e2e/gameplay-loop.test.ts": makeTestModule("gameplay-loop-e2e", 18),
    "docs/README.md": "# Tactical Roguelike Game Docs\n\nInitial docs marker for Polaris full-chain game audit.",
    "README.md": "# Tactical Roguelike Game\n\nGenerated by Polaris full-chain game audit.",
  };

  return {
    key: "game",
    workspacePrefix: "Polaris_Game_Stress_E2E",
    packageName: "polaris-tactical-game-e2e",
    goal: [
      "构建一个中大型 Web 战术 Roguelike 游戏项目，要求可执行、可测试、可审计，并且必须先完成完整计划和 Chief Engineer 全量蓝图，再交给 Director 落地代码。",
      "游戏必须包含随机种子地图生成、回合制战斗、卡牌/技能系统、敌人 AI、内容表、战役进度、经济/掉落、存档恢复、音频事件、前端渲染、平衡报告工具和测试。",
      "PM 必须拆出至少 12 个可执行任务，覆盖 engine、world、combat、ai、content、progression、economy、persistence、renderer、audio、tooling、tests 等领域，每个任务都要有目标、作用域、执行清单和可测验收。",
      "项目必须落在当前 C:/Temp 工作区内，至少 8 个模块、1200+ 行代码、单元测试、集成测试和 e2e 结构化测试，并提供 npm run build / npm run test 验收命令。",
      "必须保留现有 node scripts/build.mjs 与 scripts/test.mjs 结构化验收脚本，禁止引入 Rust/Cargo、Webpack/Jest/Vite/Vitest 或任何新外部依赖。",
      "如果实现 PRNG，不允许写固定魔法数期望测试，只能测试同 seed 序列一致性、范围和分布稳定性。",
    ].join(" "),
    replies: [
      "",
      "补充：游戏要支持浏览器端 Canvas 或 DOM 渲染、回合制行动队列、随机种子地图、敌人 AI、卡牌/技能/敌人内容表、战役进度、经济掉落、音频事件、存档恢复、本地排行榜接口和平衡报告工具。只能使用当前 TypeScript 文件和内置 node 结构化验收脚本，不要更换技术栈或包管理方案。",
      "补充：请拆成至少 12 个 Director 可执行任务，覆盖 engine、world、combat、ai、content、progression、economy、persistence、renderer、audio、tooling、tests。必须先让 Chief Engineer 为全部 PM 任务生成可交付蓝图且 handoff-ready，再允许 Director 执行。测试必须验证行为不变量，禁止把未经计算核对的随机数常量写成验收期望；禁止新增 Cargo.toml、webpack.config.js、jest.config.js 等非当前 seed 所需配置。",
    ],
    buildRequiredFiles,
    testFiles,
    files,
  };
}

export function buildCard3dProjectScenario(): FullChainProjectScenario {
  const buildRequiredFiles = [
    "package.json",
    "tsconfig.json",
    "index.html",
    "src/client/three-scene.ts",
    "src/client/card-table.ts",
    "src/client/network-client.ts",
    "src/server/app.ts",
    "src/server/realtime-gateway.ts",
    "src/server/matchmaking.ts",
    "src/server/room-state.ts",
    "src/server/session-store.ts",
    "src/server/moderation.ts",
    "src/game/card-catalog.ts",
    "src/game/deck-builder.ts",
    "src/game/rules-engine.ts",
    "src/shared/protocol.ts",
  ];
  const testFiles = [
    "tests/unit/card-rules.test.ts",
    "tests/unit/deck-builder.test.ts",
    "tests/integration/multiplayer-flow.test.ts",
    "tests/integration/realtime-sync.test.ts",
    "tests/e2e/card-table-3d.test.ts",
  ];
  const files: Record<string, string> = {
    "package.json": JSON.stringify({
      name: "polaris-card3d-multiplayer-e2e",
      version: "1.0.0",
      private: true,
      type: "module",
      scripts: {
        build: "node scripts/build.mjs",
        start: "node dist/server/app.js",
        test: "node scripts/test.mjs",
      },
      dependencies: {
        three: "^0.165.0",
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
    "AGENTS.md": [
      "# Card3D Workspace Rules",
      "",
      "All text files must be read and written with explicit UTF-8.",
      "This workspace is a TypeScript multiplayer creative card game seed project.",
      "The browser client is based on Three.js / WebGL concepts, and the backend is Node.js.",
      "Do not introduce Rust, Cargo, Go, Python, Webpack, Jest, Vite, Vitest, or any new external build/test dependency.",
      "Preserve package.json script commands: build must remain `node scripts/build.mjs`, and test must remain `node scripts/test.mjs`.",
      "Preserve the existing Three.js dependency declaration; do not rewrite package.json during implementation.",
      "Replace structural-only script contents with substantive no-external-dependency verification before final QA.",
      "Use the existing Node verification script entrypoints for acceptance.",
    ].join("\n"),
    "index.html": [
      "<!doctype html>",
      "<html lang=\"en\">",
      "  <head>",
      "    <meta charset=\"UTF-8\" />",
      "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />",
      "    <title>Polaris Card3D Multiplayer</title>",
      "  </head>",
      "  <body>",
      "    <canvas id=\"card3d-stage\"></canvas>",
      "    <script type=\"module\" src=\"/src/client/three-scene.ts\"></script>",
      "  </body>",
      "</html>",
    ].join("\n"),
    ".env.example": "CARD3D_PORT=4188\nCARD3D_ROOM_LIMIT=8\nCARD3D_MATCHMAKING_SEED=polaris-card3d",
    "docker-compose.yml": "version: \"3.9\"\nservices:\n  card3d-redis:\n    image: redis:7\n    ports:\n      - \"6381:6379\"",
    "scripts/build.mjs": makeStructuralBuildScript(buildRequiredFiles),
    "scripts/test.mjs": makeStructuralTestScript(testFiles),
    "src/client/three-scene.ts": [
      "import type { PerspectiveCamera, Scene, WebGLRenderer } from \"three\";",
      "export type ThreeSceneHandles = { scene?: Scene; camera?: PerspectiveCamera; renderer?: WebGLRenderer };",
      makeLargeTsModule("three-scene", 38),
    ].join("\n\n"),
    "src/client/card-table.ts": makeLargeTsModule("card-table", 34),
    "src/client/network-client.ts": makeLargeTsModule("network-client", 32),
    "src/server/app.ts": [
      "import type { IncomingMessage, ServerResponse } from \"node:http\";",
      "export type NodeCardServerHandler = (request: IncomingMessage, response: ServerResponse) => void;",
      makeLargeTsModule("node-card-server", 34),
    ].join("\n\n"),
    "src/server/realtime-gateway.ts": makeLargeTsModule("realtime-gateway", 34),
    "src/server/matchmaking.ts": makeLargeTsModule("matchmaking-queue", 30),
    "src/server/room-state.ts": makeLargeTsModule("room-state", 32),
    "src/server/session-store.ts": makeLargeTsModule("session-store", 28),
    "src/server/moderation.ts": makeLargeTsModule("moderation-rules", 26),
    "src/game/card-catalog.ts": makeLargeTsModule("creative-card-catalog", 34),
    "src/game/deck-builder.ts": makeLargeTsModule("deck-builder", 32),
    "src/game/rules-engine.ts": makeLargeTsModule("card-rules-engine", 34),
    "src/shared/protocol.ts": makeLargeTsModule("sync-protocol", 30),
    "src/shared/player-presence.ts": makeLargeTsModule("player-presence", 24),
    "src/shared/telemetry.ts": makeLargeTsModule("client-server-telemetry", 24),
    "src/auth/session-auth.ts": makeLargeTsModule("session-auth", 24),
    "src/lobby/lobby-service.ts": makeLargeTsModule("lobby-service", 24),
    "src/assets/card-assets.ts": makeLargeTsModule("card-assets", 24),
    "src/animation/card-animations.ts": makeLargeTsModule("card-animations", 24),
    "src/physics/table-layout.ts": makeLargeTsModule("table-layout", 24),
    "src/analytics/match-analytics.ts": makeLargeTsModule("match-analytics", 24),
    "tests/unit/card-rules.test.ts": makeTestModule("card-rules-unit", 18),
    "tests/unit/deck-builder.test.ts": makeTestModule("deck-builder-unit", 18),
    "tests/integration/multiplayer-flow.test.ts": makeTestModule("multiplayer-flow-integration", 18),
    "tests/integration/realtime-sync.test.ts": makeTestModule("realtime-sync-integration", 18),
    "tests/e2e/card-table-3d.test.ts": makeTestModule("card-table-3d-e2e", 18),
    "docs/README.md": "# Card3D Multiplayer Docs\n\nInitial docs marker for Polaris full-chain card3d audit.",
    "README.md": "# Card3D Multiplayer\n\nGenerated by Polaris full-chain card3d audit.",
  };

  return {
    key: "card3d",
    workspacePrefix: "Polaris_Card3D_Multiplayer_E2E",
    packageName: "polaris-card3d-multiplayer-e2e",
    goal: [
      "构建一个中大型多人在线创意卡牌游戏项目，前端必须基于 TypeScript + Three.js / three3d 3D 牌桌，后端必须基于 Node.js。",
      "必须可执行、可测试、可审计，并且必须先完成完整 Architect 计划和 Chief Engineer 全量蓝图，再交给 Director 落地代码。",
      "项目必须包含 3D 客户端场景、交互式卡牌桌、浏览器网络客户端、Node 后端、实时网关、匹配队列、房间状态、创意卡牌目录、牌组构筑、规则引擎、共享同步协议、会话持久化、内容安全/房间治理、玩家在线状态、遥测、认证、大厅、资产、动画、桌面布局物理、对局分析和测试。",
      "PM 必须拆出至少 22 个可执行任务，覆盖 client3d、table、networking、server、realtime、matchmaking、rooms、cards、deckbuilder、rules、sync、persistence、moderation、presence、telemetry、auth、lobby、assets、animation、physics、analytics、tests。",
      "每个任务都要有目标、作用域、执行清单和可测验收；必须使用当前 C:/Temp 工作区内的 TypeScript 文件和内置 node scripts/build.mjs / scripts/test.mjs 验收，并且 tests 域必须把结构性脚本内容替换为真实的无外部依赖校验。",
      "所有 seed 文件必须被真实业务实现替换，最终源码/测试/配置中不得保留 audit-seed 或 planning scenario 标记。",
      "禁止引入 Rust/Cargo、Go、Python、Webpack、Jest、Vite、Vitest 或任何新外部构建/测试依赖；禁止重写 package.json。",
    ].join(" "),
    replies: [
      "",
      "补充：前端必须体现 Three.js/WebGL 3D 牌桌、相机/场景/渲染器概念；后端必须体现 Node.js 多人房间、实时消息、匹配和会话状态。不要把它做成普通 roguelike 或单机卡牌 demo。",
      "补充：请先完成所有计划和 Chief Engineer 蓝图，确认 22+ 个任务全部 handoff-ready 后才允许 Director 执行。Director 需要修改当前 seed 中的 TypeScript 客户端/后端/规则/测试文件，并在代码变更视图展示红绿 diff。",
    ],
    buildRequiredFiles,
    testFiles,
    files,
  };
}
