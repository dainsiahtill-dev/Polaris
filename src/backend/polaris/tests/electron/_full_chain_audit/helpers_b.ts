import { existsSync, promises as fs } from "node:fs";
import { createHash } from "node:crypto";
import path from "node:path";
import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "../fixtures";
import { listFilesRecursive, pathExists, readJsonFile, requestJson, tryRuntimeArtifact, waitForRuntimeArtifact, writeUtf8File } from "./helpers_a";
import { CHINESE_PROMPT_LEAKAGE_PATTERNS, DIRECTOR_RESULT_TIMEOUT_MS, GAME_FORBIDDEN_RUNTIME_ARTIFACT_RE, LEAKAGE_KEYWORDS, PM_FINISH_TIMEOUT_MS, SAFE_PROMPT_CONTROL_PHRASES, directorTaskCoveragePaths, resolveSafeWorkspaceName, scenarioRequiresGameLikeBatch, toPosixPath, type ChiefEngineerDiagnosticsPayload, type ComplexityContributionBreakdown, type ComplexityMetrics, type DirectorDiagnosticsPayload, type DirectorIntegrationQaPayload, type DirectorResultArtifact, type DirectorResultSource, type DirectorStatusPayload, type DirectorTaskPayload, type FullChainProjectScenario, type FullChainStartPhase, type PmContractAudit, type PmContractPayload, type PmPlanningContribution, type PmStatusPayload, type ProjectFileSnapshot, type RuntimeArtifactRef, type RuntimeContributionMetrics, type RuntimeEvent, type ScenarioSeedResidue, type SnapshotSummaryMetrics, type ToolAuditPayload } from "./types";

export async function createComplexProject(
  baseRoot: string,
  scenario: FullChainProjectScenario,
): Promise<{ workspace: string; metrics: ComplexityMetrics; scenario: FullChainProjectScenario }> {
  const workspace = path.join(baseRoot, resolveSafeWorkspaceName(scenario.workspacePrefix));
  const resolvedBase = path.resolve(baseRoot);
  const resolvedWorkspace = path.resolve(workspace);
  if (resolvedWorkspace !== resolvedBase && !resolvedWorkspace.startsWith(`${resolvedBase}${path.sep}`)) {
    throw new Error(`Refusing to create workspace outside ${resolvedBase}: ${resolvedWorkspace}`);
  }
  await fs.rm(workspace, { recursive: true, force: true });
  await fs.mkdir(workspace, { recursive: true });

  await Promise.all(
    Object.entries(scenario.files).map(async ([relativePath, content]) => {
      await writeUtf8File(path.join(workspace, relativePath), content);
    }),
  );

  const metrics = await measureComplexity(workspace);
  return { workspace, metrics, scenario };
}

function isAuditableCodeFile(filePath: string): boolean {
  return /\.(ts|tsx|js|jsx|mjs|cjs|py|css|html)$/i.test(filePath);
}

function isAuditableTestFile(filePath: string): boolean {
  const normalized = toPosixPath(filePath).toLowerCase();
  return (
    /(?:^|\/)test_[^/]+\.py$/i.test(normalized)
    || /\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$/i.test(normalized)
  );
}

export async function measureComplexity(workspace: string): Promise<ComplexityMetrics> {
  const allFiles = await listFilesRecursive(workspace);
  const codeFiles = allFiles.filter(isAuditableCodeFile);
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
    testFileCount: allFiles.filter(isAuditableTestFile).length,
  };
}

export function measureScenarioDefinitionComplexity(scenario: FullChainProjectScenario): ComplexityMetrics {
  const entries = Object.entries(scenario.files).map(([relativePath, content]) => ({
    relativePath: toPosixPath(relativePath),
    content,
  }));
  const relativePaths = entries.map((entry) => entry.relativePath);
  const codeLineCount = entries.reduce((total, entry) => {
    if (!isAuditableCodeFile(entry.relativePath)) {
      return total;
    }
    return total + String(entry.content || "").split(/\r?\n/).length;
  }, 0);
  const modules = new Set<string>();
  for (const filePath of relativePaths) {
    const parts = filePath.split("/");
    if (parts[0] === "src" && parts[1]) {
      modules.add(parts[1]);
    }
  }
  const normalized = new Set(relativePaths.map((filePath) => filePath.toLowerCase()));
  const configFileCount = [
    "package.json",
    "tsconfig.json",
    "jest.config.ts",
    ".env.example",
    "docker-compose.yml",
    "scripts/build.mjs",
  ].filter((item) => normalized.has(item.toLowerCase())).length;

  return {
    fileCount: relativePaths.length,
    codeLineCount,
    moduleCount: modules.size,
    configFileCount,
    testFileCount: relativePaths.filter(isAuditableTestFile).length,
  };
}

export async function findLatestEventsPath(runtimeRoot: string): Promise<string | null> {
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

function shouldIncludeContributionFile(relativePath: string): boolean {
  const normalized = toPosixPath(relativePath).toLowerCase();
  if (!normalized || normalized.endsWith("/")) return false;
  const parts = normalized.split("/");
  const excludedRoots = new Set([".git", ".polaris", "runtime", "node_modules", "dist", "build", "coverage"]);
  return !parts.some((part) => excludedRoots.has(part));
}

function isCodeContributionFile(relativePath: string): boolean {
  return isAuditableCodeFile(relativePath);
}

function isScenarioSeedResidueFile(relativePath: string): boolean {
  const normalized = toPosixPath(relativePath);
  if (!shouldIncludeContributionFile(normalized)) return false;
  return /\.(ts|tsx|js|jsx|mjs|cjs|json|md|html|css|ya?ml)$/i.test(normalized);
}

function isScenarioSeedResidueAllowedReference(line: string): boolean {
  const normalized = line.toLowerCase();
  if (!/\b(?:audit-seed|planning scenario)\b/i.test(line)) return false;
  return (
    normalized.includes("must not retain")
    || normalized.includes("contain no")
    || normalized.includes("do not retain")
    || normalized.includes("不得保留")
    || normalized.includes("不能保留")
    || normalized.includes("禁止保留")
  );
}

export async function findScenarioSeedResidue(workspace: string, scenario: FullChainProjectScenario): Promise<ScenarioSeedResidue[]> {
  if (!scenarioRequiresGameLikeBatch(scenario)) {
    return [];
  }
  const files = await listFilesRecursive(workspace);
  const residues: ScenarioSeedResidue[] = [];
  const markerPatterns: Array<{ marker: string; pattern: RegExp }> = [
    { marker: "audit-seed", pattern: /\baudit-seed\b/i },
    { marker: "planning scenario", pattern: /\bplanning scenario\b/i },
    { marker: "build verification completed", pattern: /\bbuild verification completed\b/i },
    { marker: "test verification completed", pattern: /\btest verification completed\b/i },
    { marker: "structural build passed", pattern: /\bstructural build passed\b/i },
    { marker: "structural tests passed", pattern: /\bstructural tests passed\b/i },
  ];

  for (const filePath of files) {
    const relativePath = toPosixPath(path.relative(workspace, filePath));
    if (!isScenarioSeedResidueFile(relativePath)) continue;

    let text = "";
    try {
      text = await fs.readFile(filePath, "utf-8");
    } catch {
      continue;
    }

    const lines = text.split(/\r?\n/);
    for (const [index, line] of lines.entries()) {
      const marker = markerPatterns.find((item) => item.pattern.test(line));
      if (!marker) continue;
      if (isScenarioSeedResidueAllowedReference(line)) continue;
      residues.push({
        filePath: relativePath,
        marker: marker.marker,
        line: index + 1,
        excerpt: line.trim().slice(0, 180),
      });
      break;
    }
  }

  return residues.sort((left, right) => left.filePath.localeCompare(right.filePath));
}

export async function snapshotProjectFiles(workspace: string): Promise<ProjectFileSnapshot> {
  const files = await listFilesRecursive(workspace);
  const snapshot: ProjectFileSnapshot = {};
  for (const filePath of files) {
    const relativePath = toPosixPath(path.relative(workspace, filePath));
    if (!shouldIncludeContributionFile(relativePath)) continue;
    const bytes = await fs.readFile(filePath);
    let codeLines = 0;
    if (isCodeContributionFile(relativePath)) {
      codeLines = (await fs.readFile(filePath, "utf-8")).split(/\r?\n/).length;
    }
    snapshot[relativePath] = {
      sha256: createHash("sha256").update(bytes).digest("hex"),
      size: bytes.length,
      codeLines,
    };
  }
  return snapshot;
}

function summarizeProjectSnapshot(snapshot: ProjectFileSnapshot): SnapshotSummaryMetrics {
  return {
    fileCount: Object.keys(snapshot).length,
    codeLineCount: Object.values(snapshot).reduce((total, item) => total + item.codeLines, 0),
  };
}

export function compareProjectSnapshots(
  baseline: ProjectFileSnapshot,
  finalSnapshot: ProjectFileSnapshot,
): RuntimeContributionMetrics {
  const addedFiles = Object.keys(finalSnapshot).filter((filePath) => !baseline[filePath]).sort();
  const deletedFiles = Object.keys(baseline).filter((filePath) => !finalSnapshot[filePath]).sort();
  const modifiedFiles = Object.keys(finalSnapshot)
    .filter((filePath) => Boolean(baseline[filePath]) && baseline[filePath].sha256 !== finalSnapshot[filePath].sha256)
    .sort();
  const addedCodeLines = addedFiles.reduce((total, filePath) => total + finalSnapshot[filePath].codeLines, 0);
  const removedCodeLines = deletedFiles.reduce((total, filePath) => total + baseline[filePath].codeLines, 0);
  return {
    baselineFileCount: Object.keys(baseline).length,
    finalFileCount: Object.keys(finalSnapshot).length,
    addedFiles,
    modifiedFiles,
    deletedFiles,
    addedCodeLines,
    removedCodeLines,
  };
}

export function findForbiddenRuntimeArtifacts(
  scenario: FullChainProjectScenario,
  contribution: RuntimeContributionMetrics,
): string[] {
  if (!scenarioRequiresGameLikeBatch(scenario)) {
    return [];
  }
  const changedFiles = [
    ...contribution.addedFiles.map((filePath) => `added:${filePath}`),
    ...contribution.modifiedFiles.map((filePath) => `modified:${filePath}`),
  ];
  return changedFiles.filter((entry) => {
    const filePath = entry.replace(/^(?:added|modified):/, "");
    return GAME_FORBIDDEN_RUNTIME_ARTIFACT_RE.test(toPosixPath(filePath));
  });
}

function countPmAutofixTasks(tasks: NonNullable<PmContractPayload["tasks"]>): number {
  return tasks.filter((task) => {
    const record = task as Record<string, unknown>;
    const metadata = record.metadata && typeof record.metadata === "object"
      ? record.metadata as Record<string, unknown>
      : {};
    return Boolean(record.autofix || metadata.autofix || record.autofix_reason || metadata.autofix_reason);
  }).length;
}

export function buildPmPlanningContribution(
  source: PmPlanningContribution["source"],
  round: number,
  pmContract: PmContractPayload,
  pmAudit: PmContractAudit,
  evidencePath: string,
): PmPlanningContribution {
  const tasks = Array.isArray(pmContract.tasks) ? pmContract.tasks : [];
  return {
    source,
    round,
    taskCount: tasks.length,
    qualityScore: Number(pmContract?.quality_gate?.score || 0),
    criticalIssueCount: Number(pmContract?.quality_gate?.critical_issue_count || 0),
    invalidTaskCount: pmAudit.invalidTaskCount,
    autofixTaskCount: countPmAutofixTasks(tasks),
    coveredGameDomains: pmAudit.coveredGameDomains,
    missingGameDomains: pmAudit.missingGameDomains,
    evidencePath: toPosixPath(evidencePath),
  };
}

function contributionRatio(numerator: number, denominator: number): number {
  if (denominator <= 0) {
    return 0;
  }
  return Number((numerator / denominator).toFixed(6));
}

export function buildComplexityContributionBreakdown(params: {
  scenarioSeedMetrics: ComplexityMetrics;
  startPhase: FullChainStartPhase;
  currentRunBaselineMetrics: ComplexityMetrics;
  baselineSnapshot: ProjectFileSnapshot;
  finalMetrics: ComplexityMetrics;
  finalSnapshot: ProjectFileSnapshot;
  pmPlanningDelta: PmPlanningContribution | null;
  directorResultSource: string;
  directorContribution: RuntimeContributionMetrics;
  contributionEvidencePath: string;
}): ComplexityContributionBreakdown {
  const baselineSummary = summarizeProjectSnapshot(params.baselineSnapshot);
  const finalSummary = summarizeProjectSnapshot(params.finalSnapshot);
  const changedFileCount = params.directorContribution.addedFiles.length
    + params.directorContribution.modifiedFiles.length
    + params.directorContribution.deletedFiles.length;

  return {
    scenario_seed_definition: params.scenarioSeedMetrics,
    current_run_baseline: {
      all_files: params.currentRunBaselineMetrics,
      contribution_scope: baselineSummary,
      includes_previous_run_contributions: params.startPhase !== "court",
    },
    pm_planning_delta: params.pmPlanningDelta,
    director_runtime_delta: {
      ...params.directorContribution,
      source: params.directorResultSource || "unknown",
      evidencePath: toPosixPath(params.contributionEvidencePath),
      changedFileCount,
    },
    final_total: {
      all_files: params.finalMetrics,
      contribution_scope: finalSummary,
    },
    ratios: {
      baseline_contribution_file_share_of_final: contributionRatio(baselineSummary.fileCount, finalSummary.fileCount),
      baseline_contribution_code_line_share_of_final: contributionRatio(
        baselineSummary.codeLineCount,
        finalSummary.codeLineCount,
      ),
      director_changed_file_share_of_final: contributionRatio(changedFileCount, finalSummary.fileCount),
      director_added_code_line_share_of_final: contributionRatio(
        params.directorContribution.addedCodeLines,
        finalSummary.codeLineCount,
      ),
      scenario_seed_file_share_of_final_all_files: contributionRatio(
        params.scenarioSeedMetrics.fileCount,
        params.finalMetrics.fileCount,
      ),
      scenario_seed_code_line_share_of_final_all_files: contributionRatio(
        params.scenarioSeedMetrics.codeLineCount,
        params.finalMetrics.codeLineCount,
      ),
    },
    notes: [
      "PM planning contribution is contract/task coverage, not file contribution.",
      "Director runtime contribution is computed from current-run baseline to final workspace snapshot.",
      params.startPhase !== "court"
        ? "Resume runs may include previous run contributions in current_run_baseline."
        : "Cold runs use the generated scenario seed as current_run_baseline.",
    ],
  };
}

export async function findToolEventPaths(runtimeRoot: string): Promise<string[]> {
  const eventsRoot = path.join(runtimeRoot, "events");
  if (!(await pathExists(eventsRoot))) return [];
  const entries = await fs.readdir(eventsRoot, { withFileTypes: true }).catch(() => []);
  return entries
    .filter((entry) => entry.isFile() && /\.llm\.events\.jsonl$/i.test(entry.name))
    .map((entry) => path.join(eventsRoot, entry.name));
}

export function detectPromptLeakage(text: string, evidencePath: string): Array<{ type: string; evidence: string; fixed: boolean }> {
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

export function analyzeToolAudit(events: RuntimeEvent[], startEpochSeconds: number): ToolAuditPayload {
  const audit: ToolAuditPayload = {
    total_calls: 0,
    policy_evidence_count: 0,
    unauthorized_blocked: 0,
    dangerous_commands: 0,
    findings: [],
  };
  for (const event of events) {
    const epoch = Number(event.ts_epoch || 0);
    if (!Number.isFinite(epoch) || epoch < startEpochSeconds) continue;
    const serialized = JSON.stringify(event).toLowerCase();
    if (serialized.includes("tool_call") || serialized.includes("mcp_tool_call") || serialized.includes("command_execution")) {
      audit.total_calls += 1;
    }
    if (serialized.includes("director_policy")) {
      audit.policy_evidence_count += 1;
    }
    if (/(unauthorized|permission denied|toolauthorizationerror)/i.test(serialized) && /(block|deny|reject|forbidden)/i.test(serialized)) {
      audit.unauthorized_blocked += 1;
      audit.findings.push({ type: "unauthorized_blocked", evidence: event.event_id || String(event.name || "unknown") });
    }
    if (/director_write_policy_denied|director_policy_denials/i.test(serialized)) {
      audit.unauthorized_blocked += 1;
      audit.findings.push({ type: "director_policy_denied", evidence: event.event_id || String(event.name || "unknown") });
    }
    if (/(dangerous command|path traversal|rm -rf|del \/s)/i.test(serialized)) {
      audit.dangerous_commands += 1;
      audit.findings.push({ type: "dangerous_command", evidence: event.event_id || String(event.name || "unknown") });
    }
  }
  return audit;
}

export async function resolveVisibleLocator(
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

export async function runCourtFlow(
  window: Page,
  scenario: FullChainProjectScenario,
): Promise<{ dialogueReady: boolean; fallbackUsed: boolean }> {
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
  await goalInput.fill(scenario.goal);

  let dialogueReady = false;
  let fallbackUsed = false;
  for (let index = 0; index < scenario.replies.length; index += 1) {
    if (index > 0) {
      const messageInput = await resolveVisibleLocator(window, [
        () => window.getByTestId("docs-init-message-input"),
        () => window.getByPlaceholder(/Directly answer Architect follow-up/i),
      ], 10_000);
      await messageInput.fill(scenario.replies[index]);
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

export async function enterPmWorkspace(window: Page): Promise<void> {
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

export async function enterDirectorWorkspace(window: Page): Promise<void> {
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

export async function inspectDirectorCodeChanges(
  window: Page,
): Promise<{ eventCount: number; empty: boolean; expanded: boolean; detailKind: "diff" | "summary" | "none" }> {
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

  if (eventCount === 0) {
    return { eventCount, empty, expanded: false, detailKind: "none" };
  }

  const firstEvent = eventList.locator(":scope > div").first();
  await firstEvent.scrollIntoViewIfNeeded();
  await firstEvent.click();
  let detailKind: "diff" | "summary" | "none" = "none";
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (await window.getByTestId("real-time-file-diff").first().isVisible().catch(() => false)) {
      detailKind = "diff";
      break;
    }
    if (await window.getByTestId("director-file-edit-summary").first().isVisible().catch(() => false)) {
      detailKind = "summary";
      break;
    }
    await window.waitForTimeout(250);
  }
  expect(detailKind, "Director code panel should expand a file-change detail view").not.toBe("none");

  return { eventCount, empty, expanded: true, detailKind };
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

export async function runPmRound(window: Page): Promise<PmStatusPayload> {
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

export function chiefEngineerHandoffReady(payload: ChiefEngineerDiagnosticsPayload | null): boolean {
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

export async function verifyChiefEngineerPhase(
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

async function runDirectorFromWorkspace(
  window: Page,
  options?: { minMtimeMs?: number },
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

  await expect.poll(async () => window.getByTestId("director-task-item").count(), {
    timeout: 60_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeGreaterThan(0);

  const executeButton = window.getByTestId("director-workspace-execute");
  await expect(executeButton).toBeVisible({ timeout: 60_000 });
  await expect(executeButton).toBeEnabled({ timeout: 60_000 });
  await executeButton.click();

  await expect.poll(async () => {
    const artifact = await tryRuntimeArtifact(window, "results/director.result.json", options);
    if (artifact) return true;
    const diagnostics = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
    const taskState = diagnostics.tasks || {};
    const active = Number(taskState.running || 0) + Number(taskState.claimed || 0);
    const status = await requestJson<DirectorStatusPayload>(window, "/v2/director/status?source=auto");
    const state = String(status.state || "").trim().toUpperCase();
    return active > 0 || /RUNNING|STARTING|QUEUED|BUSY/.test(state) || directorDiagnosticsTerminal(taskState);
  }, {
    timeout: 120_000,
    intervals: [500, 1000, 2000, 3000],
  }).toBeTruthy();

  await expect.poll(async () => {
    const artifact = await tryRuntimeArtifact(window, "results/director.result.json", options);
    if (artifact) return 0;
    const diagnostics = await requestJson<DirectorDiagnosticsPayload>(window, "/v2/director/diagnostics");
    const taskState = diagnostics.tasks || {};
    return Number(taskState.running || 0) + Number(taskState.claimed || 0);
  }, {
    timeout: DIRECTOR_RESULT_TIMEOUT_MS,
    intervals: [1000, 2000, 5000, 10_000],
  }).toBe(0);

  const executed = await tryRuntimeArtifact(window, "results/director.result.json", options);
  return await collectDirectorTaskExposure(window, executed || undefined);
}

async function readDirectorResultTaskCount(artifact: RuntimeArtifactRef | null | undefined): Promise<number> {
  if (!artifact?.artifactPath) return 0;
  const payload = await readJsonFile<DirectorResultArtifact>(artifact.artifactPath);
  const taskResults = Array.isArray(payload?.task_results) ? payload.task_results.length : 0;
  const total = Number(payload?.total || 0);
  const terminal = Number(payload?.successes || 0) + Number(payload?.failures || 0) + Number(payload?.blocked || 0);
  return Math.max(taskResults, total, terminal);
}

async function collectDirectorTaskExposure(
  window: Page,
  artifact?: RuntimeArtifactRef | null,
): Promise<{ linkedTaskCount: number; uiTaskCount: number; state: string; coveragePaths: string[] }> {
  const tasks = await requestJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto").catch(() => []);
  const linkedByApi = Array.isArray(tasks)
    ? tasks.filter((item) => {
      const metadata = item?.metadata || {};
      return [
        metadata.pm_task_id,
        metadata.source_task_id,
        metadata.external_task_id,
        item?.id,
        item?.task_id,
      ].some((value) => String(value || "").trim().length > 0);
    }).length
    : 0;
  const coveragePaths = Array.isArray(tasks) ? directorTaskCoveragePaths(tasks) : [];
  const resultTaskCount = await readDirectorResultTaskCount(artifact);
  const linkedTaskCount = Math.max(linkedByApi, resultTaskCount);
  const uiTaskCount = await window.getByTestId("director-task-item").count().catch(() => 0);
  const status = await requestJson<DirectorStatusPayload>(window, "/v2/director/status?source=auto").catch(() => ({}));
  return { linkedTaskCount, uiTaskCount, state: String(status.state || "").trim().toUpperCase(), coveragePaths };
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

export async function runDirectorUntilResultArtifact(
  window: Page,
  options?: { minMtimeMs?: number },
): Promise<{
  linkedTaskCount: number;
  uiTaskCount: number;
  state: string;
  coveragePaths: string[];
  artifactPath: string;
  runtimeRoot: string;
  mtimeMs: number;
  source: DirectorResultSource;
}> {
  let latestRun = { linkedTaskCount: 0, uiTaskCount: 0, state: "", coveragePaths: [] as string[] };
  let dispatchAttempts = 0;
  while (dispatchAttempts < 32) {
    const existing = await tryRuntimeArtifact(window, "results/director.result.json", options);
    if (existing) {
      latestRun = await collectDirectorTaskExposure(window, existing);
      return { ...latestRun, ...existing, source: "existing_artifact" };
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
      const reconciled = await tryRuntimeArtifact(window, "results/director.result.json", options);
      if (reconciled) {
        latestRun = await collectDirectorTaskExposure(window, reconciled);
        return { ...latestRun, ...reconciled, source: "reconciled_terminal" };
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

    dispatchAttempts += 1;
    latestRun = await runDirectorFromWorkspace(window, options);
    const executed = await tryRuntimeArtifact(window, "results/director.result.json", options);
    if (executed) {
      latestRun = await collectDirectorTaskExposure(window, executed);
      return { ...latestRun, ...executed, source: "executed" };
    }
  }

  const artifact = await waitForRuntimeArtifact(
    window,
    "results/director.result.json",
    DIRECTOR_RESULT_TIMEOUT_MS,
    options,
  );
  return { ...latestRun, ...artifact, source: "waited_artifact" };
}

export function detectPmFallbackFailure(pmContract: PmContractPayload | null): string {
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

export function directorFailureReason(directorResult: DirectorResultArtifact | null): string {
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

function stringArrayFromUnknown(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item || "").trim())
    .filter((item) => item.length > 0);
}

export function summarizeDirectorArtifactMaterialization(directorResult: DirectorResultArtifact | null): {
  changedFileCount: number;
  toolEvidenceCount: number;
  materializedTaskCount: number;
} {
  const changedFiles = new Set<string>();
  let toolEvidenceCount = 0;
  let materializedTaskCount = 0;
  for (const task of directorResult?.task_results || []) {
    const adapter = task.adapter_result || {};
    const taskChangedFiles = [
      ...stringArrayFromUnknown(task.changed_files),
      ...stringArrayFromUnknown(adapter.changed_files),
      ...stringArrayFromUnknown(adapter.new_files),
      ...stringArrayFromUnknown(adapter.modified_files),
    ];
    for (const filePath of taskChangedFiles) changedFiles.add(filePath);
    const taskTools = Number(task.tools_executed || 0) + Number(adapter.tools_executed || 0);
    if (taskTools > 0 || adapter.write_tool_evidence === true) {
      toolEvidenceCount += 1;
    }
    if (taskChangedFiles.length > 0 || taskTools > 0 || adapter.write_tool_evidence === true) {
      materializedTaskCount += 1;
    }
  }
  return {
    changedFileCount: changedFiles.size,
    toolEvidenceCount,
    materializedTaskCount,
  };
}

test.setTimeout(70 * 60 * 1000);
