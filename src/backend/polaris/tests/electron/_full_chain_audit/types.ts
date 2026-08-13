import os from "node:os";
import path from "node:path";
import { expect, test } from "../fixtures";
import { buildCard3dProjectScenario, buildEnterpriseProjectScenario, buildGameProjectScenario } from "./helpers_a";

export type BackendInfo = { baseUrl?: string; token?: string };
export type SettingsPayload = {
  workspace?: string;
  model?: string;
  pm_model?: string;
  director_model?: string;
  pm_runs_director?: boolean;
};
export type RuntimeLayoutPayload = {
  runtime_root?: string;
  workspace?: string;
  workspace_persistent_root?: string;
  project_persistent_root?: string;
};
export type PmStatusPayload = {
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
export type SnapshotPayload = { tasks?: unknown[]; pm_state?: Record<string, unknown> | null };
export type DirectorStatusPayload = { state?: string };
export type DirectorTaskPayload = {
  id?: string;
  task_id?: string;
  status?: string;
  scope_paths?: unknown;
  target_files?: unknown;
  metadata?: {
    pm_task_id?: string;
    source_task_id?: string;
    external_task_id?: string;
    scope_paths?: unknown;
    target_files?: unknown;
  };
};
export type DirectorDiagnosticsPayload = {
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
export type DirectorIntegrationQaPayload = {
  ok?: boolean;
  run_id?: string;
  result?: IntegrationQaArtifact;
  director_result?: DirectorResultArtifact | null;
};
export type IntegrationQaArtifact = {
  reason?: string;
  passed?: boolean | null;
  failed?: number;
  evidence_grade?: string;
  qa_path?: string;
  summary?: string;
  result_path?: string;
  runtime_result_path?: string;
};
export type DirectorResultArtifact = {
  status?: string;
  successes?: number;
  total?: number;
  failures?: number;
  blocked?: number;
  error?: string;
  source?: string;
  task_results?: Array<{
    task_id?: string;
    changed_files?: unknown;
    tools_executed?: unknown;
    adapter_result?: {
      changed_files?: unknown;
      new_files?: unknown;
      modified_files?: unknown;
      tools_executed?: unknown;
      write_tool_evidence?: unknown;
      materialization_mode?: unknown;
    };
  }>;
};
export type DirectorResultSource = "existing_artifact" | "reconciled_terminal" | "executed" | "waited_artifact";
export type RuntimeArtifactRef = { runtimeRoot: string; artifactPath: string; mtimeMs: number };
export type ChiefEngineerDiagnosticsPayload = {
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
export type LlmConfigPayload = {
  providers?: Record<string, {
    name?: string;
    model?: string;
    model_id?: string;
    default_model?: string;
  }>;
  roles?: Record<string, { provider_id?: string; model?: string }>;
  policies?: { required_ready_roles?: unknown[] };
};
export type LlmStatusPayload = {
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
export type PmContractPayload = {
  workspace?: string;
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
export type PmContractAudit = {
  invalidTaskCount: number;
  issues: string[];
  coveredGameDomains: string[];
  missingGameDomains: string[];
};
export type RuntimeEvent = { ts_epoch?: number; event_id?: string; name?: string };
type ImageDimensions = { width: number; height: number };

export type ComplexityMetrics = {
  fileCount: number;
  codeLineCount: number;
  moduleCount: number;
  configFileCount: number;
  testFileCount: number;
};
export type ProjectFileSnapshot = Record<string, { sha256: string; size: number; codeLines: number }>;
export type RuntimeContributionMetrics = {
  baselineFileCount: number;
  finalFileCount: number;
  addedFiles: string[];
  modifiedFiles: string[];
  deletedFiles: string[];
  addedCodeLines: number;
  removedCodeLines: number;
};
export type SnapshotSummaryMetrics = {
  fileCount: number;
  codeLineCount: number;
};
export type ScenarioSeedResidue = {
  filePath: string;
  marker: string;
  line: number;
  excerpt: string;
};
export type PmPlanningContribution = {
  source: "executed_pm_round" | "resumed_existing_pm_contract";
  round: number;
  taskCount: number;
  qualityScore: number;
  criticalIssueCount: number;
  invalidTaskCount: number;
  autofixTaskCount: number;
  coveredGameDomains: string[];
  missingGameDomains: string[];
  evidencePath: string;
};
export type ComplexityContributionBreakdown = {
  scenario_seed_definition: ComplexityMetrics;
  current_run_baseline: {
    all_files: ComplexityMetrics;
    contribution_scope: SnapshotSummaryMetrics;
    includes_previous_run_contributions: boolean;
  };
  pm_planning_delta: PmPlanningContribution | null;
  director_runtime_delta: RuntimeContributionMetrics & {
    source: string;
    evidencePath: string;
    changedFileCount: number;
  };
  final_total: {
    all_files: ComplexityMetrics;
    contribution_scope: SnapshotSummaryMetrics;
  };
  ratios: {
    baseline_contribution_file_share_of_final: number;
    baseline_contribution_code_line_share_of_final: number;
    director_changed_file_share_of_final: number;
    director_added_code_line_share_of_final: number;
    scenario_seed_file_share_of_final_all_files: number;
    scenario_seed_code_line_share_of_final_all_files: number;
  };
  notes: string[];
};

export type FullChainProjectScenario = {
  key: "enterprise" | "game" | "card3d";
  workspacePrefix: string;
  packageName: string;
  goal: string;
  replies: string[];
  buildRequiredFiles: string[];
  testFiles: string[];
  files: Record<string, string>;
};
type ResumePlanningTaskSeed = {
  id: string;
  domain: string;
  title: string;
  scopePaths: string[];
  acceptance: string[];
};
export type ResumePlanningSeed = {
  generatedAt: string;
  requirementsMarkdown: string;
  planMarkdown: string;
  pipelinePayload: Record<string, unknown>;
  progressPayload: Record<string, unknown>;
  tasks: ResumePlanningTaskSeed[];
};
export type ResumePlanningWriteResult = {
  writtenPaths: string[];
  runtimeRequirementsPath: string;
  runtimePlanPath: string;
  pipelinePath: string;
  progressPath: string;
};

export type ToolAuditPayload = {
  total_calls: number;
  policy_evidence_count: number;
  unauthorized_blocked: number;
  dangerous_commands: number;
  findings: Array<{ type: string; evidence: string }>;
};

export const LEAKAGE_KEYWORDS = [
  "you are",
  "role",
  "system prompt",
  "no yapping",
  "提示词",
  "角色设定",
  "<thinking>",
  "<tool_call>",
];
export const SAFE_PROMPT_CONTROL_PHRASES = [
  "提示词穿透检测",
  "提示词编译",
  "真实试穿提示词",
  "prompt-package.json",
];
export const CHINESE_PROMPT_LEAKAGE_PATTERNS = [
  /系统提示词/i,
  /开发者提示词/i,
  /角色提示词/i,
  /内部提示词/i,
  /完整提示词/i,
  /提示词泄[露漏]/i,
  /提示词注入/i,
  /提示词内容/i,
];
export const DIRECTOR_RESULT_TIMEOUT_MS = positiveIntFromEnv(
  "KERNELONE_E2E_DIRECTOR_RESULT_TIMEOUT_MS",
  10 * 60 * 1000,
);
export const REVIEW_SCREENSHOT_WIDTH = 1920;
export const REVIEW_SCREENSHOT_HEIGHT = 1080;

export function readJpegDimensions(bytes: Buffer, filePath: string): ImageDimensions {
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

function positiveIntFromEnv(name: string, fallback: number): number {
  const raw = String(process.env[name] || "").trim();
  if (!raw) {
    return fallback;
  }

  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const PM_FINISH_TIMEOUT_MS = positiveIntFromEnv("KERNELONE_E2E_PM_FINISH_TIMEOUT_MS", 45 * 60 * 1000);
export const GAME_PM_MIN_TASKS = 12;
const GAME_PM_REQUIRED_DOMAINS = [
  "engine",
  "world",
  "combat",
  "ai",
  "content",
  "progression",
  "economy",
  "persistence",
  "renderer",
  "audio",
  "tooling",
  "tests",
] as const;
type GameDomain = (typeof GAME_PM_REQUIRED_DOMAINS)[number];
const CARD3D_PM_REQUIRED_DOMAINS = [
  "client3d",
  "table",
  "networking",
  "server",
  "realtime",
  "matchmaking",
  "rooms",
  "cards",
  "deckbuilder",
  "rules",
  "sync",
  "persistence",
  "moderation",
  "presence",
  "telemetry",
  "auth",
  "lobby",
  "assets",
  "animation",
  "physics",
  "analytics",
  "tests",
] as const;
type Card3dDomain = (typeof CARD3D_PM_REQUIRED_DOMAINS)[number];
const GAME_PM_DOMAIN_ROOTS: Record<GameDomain, readonly string[]> = {
  engine: ["src/engine"],
  world: ["src/world"],
  combat: ["src/combat"],
  ai: ["src/ai"],
  content: ["src/content"],
  progression: ["src/progression"],
  economy: ["src/economy"],
  persistence: ["src/persistence"],
  renderer: ["src/renderer"],
  audio: ["src/audio"],
  tooling: ["src/tools"],
  tests: ["tests"],
};
const CARD3D_PM_DOMAIN_ROOTS: Record<Card3dDomain, readonly string[]> = {
  client3d: ["src/client/three-scene.ts", "src/client/scene.ts", "src/client/app.tsx", "src/client/main.ts"],
  table: ["src/client/card-table.ts", "src/client/table.ts", "src/client/tabletop.ts"],
  networking: ["src/client/network-client.ts", "src/client/network.ts", "src/client/realtime-client.ts"],
  server: ["src/server/app.ts", "src/server/index.ts", "src/server/server.ts"],
  realtime: ["src/server/realtime-gateway.ts", "src/server/websocket.ts", "src/server/ws-gateway.ts"],
  matchmaking: ["src/server/matchmaking.ts", "src/server/matchmaker.ts"],
  rooms: ["src/server/room-state.ts", "src/server/rooms.ts"],
  cards: ["src/game/card-catalog.ts", "src/game/cards.ts"],
  deckbuilder: ["src/game/deck-builder.ts", "src/game/deckbuilder.ts"],
  rules: ["src/game/rules-engine.ts", "src/game/rules.ts"],
  sync: ["src/shared/protocol.ts", "src/shared/sync-protocol.ts"],
  persistence: ["src/server/session-store.ts", "src/server/persistence.ts"],
  moderation: ["src/server/moderation.ts", "src/server/safety.ts"],
  presence: ["src/shared/player-presence.ts", "src/shared/presence.ts"],
  telemetry: ["src/shared/telemetry.ts", "src/shared/events.ts"],
  auth: ["src/auth/session-auth.ts", "src/auth/auth.ts"],
  lobby: ["src/lobby/lobby-service.ts", "src/lobby/index.ts"],
  assets: ["src/assets/card-assets.ts", "src/assets/assets.ts"],
  animation: ["src/animation/card-animations.ts", "src/animation/animations.ts"],
  physics: ["src/physics/table-layout.ts", "src/physics/layout.ts"],
  analytics: ["src/analytics/match-analytics.ts", "src/analytics/analytics.ts"],
  tests: ["tests", "tests/integration/multiplayer-flow.test.ts"],
};
const CARD3D_PM_DIRECTORY_SCOPE_DOMAINS: Record<string, readonly Card3dDomain[]> = {
  "src/client": ["client3d", "table", "networking"],
  "src/server": ["server", "realtime", "matchmaking", "rooms", "persistence", "moderation"],
  "src/game": ["cards", "deckbuilder", "rules"],
  "src/shared": ["sync", "presence", "telemetry"],
  "src/auth": ["auth"],
  "src/lobby": ["lobby"],
  "src/assets": ["assets"],
  "src/animation": ["animation"],
  "src/physics": ["physics"],
  "src/analytics": ["analytics"],
  tests: ["tests"],
};
const GAME_PM_FRAGILE_ACCEPTANCE_RE = /(参考序列|逐位一致|卡方|固定序列|魔法数字|快照序列|硬编码.*预期值|magic[- ]?number|golden[- ]?sequence|chi[- ]?square|snapshot[- ]?sequence|hard[- ]?coded.*expected)/i;
export const GAME_FORBIDDEN_RUNTIME_ARTIFACT_RE = /(^|\/)(package\.json|Cargo\.toml|go\.(?:mod|sum)|requirements\.txt|pyproject\.toml|setup\.py|webpack\.config\.[cm]?[jt]s|jest\.config\.[cm]?[jt]s|vite\.config\.[cm]?ts|vitest\.config\.[cm]?ts)$|(\.rs|\.go|\.py)$/i;
const FULL_CHAIN_START_PHASES = ["court", "pm", "chief", "director", "qa"] as const;
export type FullChainStartPhase = (typeof FULL_CHAIN_START_PHASES)[number];
const FULL_CHAIN_PHASE_ORDER: Record<FullChainStartPhase, number> = {
  court: 0,
  pm: 1,
  chief: 2,
  director: 3,
  qa: 4,
};

export function toPosixPath(filePath: string): string {
  return String(filePath || "").split(path.sep).join("/");
}

export function optionalEnvValue(name: string): string {
  return String(process.env[name] || "").trim();
}

function contractStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function unknownStringList(value: unknown): string[] {
  if (typeof value === "string") {
    const token = value.trim();
    return token ? [token] : [];
  }
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function workspacePathPrefix(workspace: string): string {
  return path.resolve(workspace).toLowerCase().replace(/[\\/]+$/, "");
}

function isWorkspaceBoundPath(candidate: string, workspace: string): boolean {
  const raw = String(candidate || "").trim();
  if (!raw) return false;
  const normalizedToken = raw.replace(/\\/g, "/");
  if (normalizedToken.split("/").some((part) => part === "..")) return false;
  const workspacePrefix = workspacePathPrefix(workspace);
  const resolved = path.resolve(path.isAbsolute(raw) ? raw : path.join(workspace, raw)).toLowerCase();
  return resolved === workspacePrefix || resolved.startsWith(`${workspacePrefix}${path.sep.toLowerCase()}`);
}

function hasExecutableOrFileAcceptance(acceptanceItems: string[]): boolean {
  const commandPattern = /\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b/i;
  const fileEvidencePattern = /\b(verify|assert|expect|should|must|exists?|contains?|校验|验证|断言|存在|包含)\b.*(?:[A-Za-z]:[\\/]|[\w.-]+[\\/][\w./\\-]+\.[A-Za-z0-9]+)/i;
  return acceptanceItems.some((item) => {
    const text = String(item || "").trim();
    if (!text) return false;
    return commandPattern.test(text) || fileEvidencePattern.test(text);
  });
}

export function normalizeCoveragePath(candidate: string, workspace: string): string {
  const raw = String(candidate || "").trim();
  if (!raw) return "";
  const workspacePrefix = workspacePathPrefix(workspace).replace(/\\/g, "/");
  const normalized = raw.replace(/\\/g, "/").toLowerCase();
  if (path.isAbsolute(raw)) {
    const resolved = path.resolve(raw).replace(/\\/g, "/").toLowerCase();
    if (resolved === workspacePrefix) return "";
    if (resolved.startsWith(`${workspacePrefix}/`)) {
      return resolved.slice(workspacePrefix.length + 1);
    }
    return "";
  }
  return normalized.replace(/^\.\//, "");
}

function pathMatchesGameDomain(candidate: string, domain: GameDomain): boolean {
  const normalized = String(candidate || "").replace(/\\/g, "/").toLowerCase().replace(/^\.\//, "").replace(/\/+$/, "");
  if (!normalized) return false;
  return GAME_PM_DOMAIN_ROOTS[domain].some((root) => normalized === root || normalized.startsWith(`${root}/`));
}

function pathMatchesCard3dDomain(candidate: string, domain: Card3dDomain): boolean {
  const normalized = String(candidate || "").replace(/\\/g, "/").toLowerCase().replace(/^\.\//, "").replace(/\/+$/, "");
  if (!normalized) return false;
  const directoryDomains = CARD3D_PM_DIRECTORY_SCOPE_DOMAINS[normalized];
  if (directoryDomains) return directoryDomains.includes(domain);
  return CARD3D_PM_DOMAIN_ROOTS[domain].some((root) => normalized === root || normalized.startsWith(`${root}/`));
}

function scenarioMinTaskCount(scenario: FullChainProjectScenario): number {
  return scenario.key === "game" || scenario.key === "card3d" ? GAME_PM_MIN_TASKS : 0;
}

export function scenarioRequiredDomains(scenario: FullChainProjectScenario): readonly string[] {
  if (scenario.key === "game") return GAME_PM_REQUIRED_DOMAINS;
  if (scenario.key === "card3d") return CARD3D_PM_REQUIRED_DOMAINS;
  return [];
}

export function scenarioCoveredDomains(
  scenario: FullChainProjectScenario,
  coveragePaths: string[],
): string[] {
  if (scenario.key === "game") {
    return GAME_PM_REQUIRED_DOMAINS.filter((domain) => coveragePaths.some((item) => pathMatchesGameDomain(item, domain)));
  }
  if (scenario.key === "card3d") {
    return CARD3D_PM_REQUIRED_DOMAINS.filter((domain) => coveragePaths.some((item) => pathMatchesCard3dDomain(item, domain)));
  }
  return [];
}

export function scenarioRequiresGameLikeBatch(scenario: FullChainProjectScenario): boolean {
  return scenario.key === "game" || scenario.key === "card3d";
}

export function directorTaskCoveragePaths(tasks: DirectorTaskPayload[]): string[] {
  const paths: string[] = [];
  for (const task of tasks) {
    paths.push(
      ...unknownStringList(task.scope_paths),
      ...unknownStringList(task.target_files),
      ...unknownStringList(task.metadata?.scope_paths),
      ...unknownStringList(task.metadata?.target_files),
    );
  }
  return Array.from(new Set(paths.map((item) => item.trim()).filter(Boolean)));
}

export function auditPmContract(pmContract: PmContractPayload, workspace: string, scenario: FullChainProjectScenario): PmContractAudit {
  const issues: string[] = [];
  const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
  const contractWorkspace = String(pmContract?.workspace || "").trim();
  if (workspacePathPrefix(contractWorkspace) !== workspacePathPrefix(workspace)) {
    issues.push(`pm_contract_workspace_mismatch:${contractWorkspace || "(missing)"}`);
  }
  const minTaskCount = scenarioMinTaskCount(scenario);
  if (minTaskCount > 0 && tasks.length < minTaskCount) {
    issues.push(`${scenario.key}_pm_task_count_too_low:${tasks.length}<${minTaskCount}`);
  }

  const coveragePaths: string[] = [];
  let invalidTaskCount = 0;
  tasks.forEach((task, index) => {
    const taskId = String(task.id || task.task_id || `task_${index + 1}`).trim();
    const hasGoal = String(task.goal || "").trim().length > 0;
    const scopePaths = contractStringList(task.scope_paths);
    const targetFiles = contractStringList(task.target_files);
    const hasScope = scopePaths.length > 0 || targetFiles.length > 0;
    const hasSteps = contractStringList(task.execution_checklist).length > 0;
    const acceptance = contractStringList(Array.isArray(task.acceptance_criteria) ? task.acceptance_criteria : task.acceptance);
    const hasAcceptance = acceptance.length > 0;
    const hasExecutableAcceptance = hasAcceptance && hasExecutableOrFileAcceptance(acceptance);
    const hasFragileGameAcceptance = scenarioRequiresGameLikeBatch(scenario)
      && acceptance.some((item) => GAME_PM_FRAGILE_ACCEPTANCE_RE.test(item));
    const pathFields = [...scopePaths, ...targetFiles];
    const unsafePaths = pathFields.filter((item) => !isWorkspaceBoundPath(item, workspace));
    coveragePaths.push(...pathFields.map((item) => normalizeCoveragePath(item, workspace)).filter(Boolean));

    const taskIssues = [
      ...(hasGoal ? [] : ["missing_goal"]),
      ...(hasScope ? [] : ["missing_scope"]),
      ...(hasSteps ? [] : ["missing_execution_checklist"]),
      ...(hasAcceptance ? [] : ["missing_acceptance"]),
      ...(hasExecutableAcceptance ? [] : ["acceptance_without_command_or_file_evidence"]),
      ...(hasFragileGameAcceptance ? ["fragile_random_acceptance"] : []),
      ...unsafePaths.map((item) => `path_not_workspace_bound:${item}`),
    ];
    if (taskIssues.length > 0) {
      invalidTaskCount += 1;
      issues.push(`${taskId}:${taskIssues.join(",")}`);
    }
  });

  const coveredGameDomains = scenarioCoveredDomains(scenario, coveragePaths);
  const missingGameDomains = scenarioRequiredDomains(scenario).filter((domain) => !coveredGameDomains.includes(domain));
  if (missingGameDomains.length > 0) {
    issues.push(`${scenario.key}_pm_missing_domains:${missingGameDomains.join(",")}`);
  }

  return { invalidTaskCount, issues, coveredGameDomains, missingGameDomains };
}

function withGoalOverride(scenario: FullChainProjectScenario): FullChainProjectScenario {
  const goalOverride = optionalEnvValue("KERNELONE_E2E_PROJECT_GOAL");
  return goalOverride ? { ...scenario, goal: goalOverride } : scenario;
}

export function resolveProjectScenario(): FullChainProjectScenario {
  const raw = (
    optionalEnvValue("KERNELONE_E2E_PROJECT_SCENARIO")
    || optionalEnvValue("KERNELONE_E2E_PROJECT_TYPE")
  ).toLowerCase();
  if (!raw || raw === "enterprise" || raw === "etms") {
    return withGoalOverride(buildEnterpriseProjectScenario());
  }
  if (raw === "game" || raw === "tactical-game") {
    return withGoalOverride(buildGameProjectScenario());
  }
  if (raw === "card3d" || raw === "multiplayer-card" || raw === "three-card") {
    return withGoalOverride(buildCard3dProjectScenario());
  }
  throw new Error(
    `Unsupported KERNELONE_E2E_PROJECT_SCENARIO=${raw}; supported=enterprise, game, card3d`,
  );
}

export function resolveSafeWorkspaceName(prefix: string): string {
  const override = optionalEnvValue("KERNELONE_E2E_WORKSPACE_NAME");
  const candidate = override || `${prefix}_${Date.now().toString(36)}`;
  const sanitized = candidate.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 96);
  if (!sanitized || sanitized === "." || sanitized === "..") {
    throw new Error(`Invalid KERNELONE_E2E_WORKSPACE_NAME=${override}`);
  }
  return sanitized;
}

export function resolveGeneratedWorkspaceRoot(): string {
  return optionalEnvValue("KERNELONE_E2E_GENERATED_WORKSPACE_ROOT")
    || path.join(os.tmpdir(), "Polaris", "electron-e2e-generated-workspace");
}

export function resolveFullChainStartPhase(): FullChainStartPhase {
  const raw = optionalEnvValue("KERNELONE_E2E_START_PHASE").toLowerCase();
  if (!raw) return "court";
  if ((FULL_CHAIN_START_PHASES as readonly string[]).includes(raw)) {
    return raw as FullChainStartPhase;
  }
  throw new Error(
    `Unsupported KERNELONE_E2E_START_PHASE=${raw}; supported=${FULL_CHAIN_START_PHASES.join(", ")}`,
  );
}

export function shouldRunFullChainPhase(startPhase: FullChainStartPhase, phase: FullChainStartPhase): boolean {
  return FULL_CHAIN_PHASE_ORDER[phase] >= FULL_CHAIN_PHASE_ORDER[startPhase];
}

export function buildFullChainSettingsPayload(workspace: string): SettingsPayload {
  const modelOverride = optionalEnvValue("KERNELONE_E2E_FULL_CHAIN_MODEL");
  const pmModel = optionalEnvValue("KERNELONE_E2E_PM_MODEL") || modelOverride;
  const directorModel = optionalEnvValue("KERNELONE_E2E_DIRECTOR_MODEL") || modelOverride;
  const payload: SettingsPayload = { workspace, pm_runs_director: true };

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

export function buildResumePlanningTaskSeeds(scenario: FullChainProjectScenario): ResumePlanningTaskSeed[] {
  if (scenario.key === "card3d") {
    return [
      {
        id: "CARD3D-CLIENT3D",
        domain: "client3d",
        title: "Extend TypeScript Three.js scene runtime",
        scopePaths: ["src/client/three-scene.ts"],
        acceptance: [
          "Run `npm run build` and verify src/client/three-scene.ts exists.",
          "Run `npm run test` and verify client scene contracts remain covered.",
        ],
      },
      {
        id: "CARD3D-TABLE",
        domain: "table",
        title: "Extend interactive 3D card table",
        scopePaths: ["src/client/card-table.ts"],
        acceptance: [
          "Run `npm run build` and verify src/client/card-table.ts exists.",
          "Run `npm run test` and verify card table interaction contracts remain covered.",
        ],
      },
      {
        id: "CARD3D-NETWORKING",
        domain: "networking",
        title: "Extend browser networking client",
        scopePaths: ["src/client/network-client.ts"],
        acceptance: [
          "Run `npm run build` and verify src/client/network-client.ts exists.",
          "Run `npm run test` and verify networking protocol coverage remains present.",
        ],
      },
      {
        id: "CARD3D-SERVER",
        domain: "server",
        title: "Extend Node.js backend entrypoint",
        scopePaths: ["src/server/app.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/app.ts exists.",
          "Run `npm run test` and verify backend route coverage remains present.",
        ],
      },
      {
        id: "CARD3D-REALTIME",
        domain: "realtime",
        title: "Extend realtime gateway",
        scopePaths: ["src/server/realtime-gateway.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/realtime-gateway.ts exists.",
          "Run `npm run test` and verify realtime message coverage remains present.",
        ],
      },
      {
        id: "CARD3D-MATCHMAKING",
        domain: "matchmaking",
        title: "Extend matchmaking queue",
        scopePaths: ["src/server/matchmaking.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/matchmaking.ts exists.",
          "Run `npm run test` and verify matchmaking coverage remains present.",
        ],
      },
      {
        id: "CARD3D-ROOMS",
        domain: "rooms",
        title: "Extend authoritative room state",
        scopePaths: ["src/server/room-state.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/room-state.ts exists.",
          "Run `npm run test` and verify room state coverage remains present.",
        ],
      },
      {
        id: "CARD3D-CARDS",
        domain: "cards",
        title: "Extend creative card catalog",
        scopePaths: ["src/game/card-catalog.ts"],
        acceptance: [
          "Run `npm run build` and verify src/game/card-catalog.ts exists.",
          "Run `npm run test` and verify card catalog coverage remains present.",
        ],
      },
      {
        id: "CARD3D-DECKBUILDER",
        domain: "deckbuilder",
        title: "Extend deck builder rules",
        scopePaths: ["src/game/deck-builder.ts"],
        acceptance: [
          "Run `npm run build` and verify src/game/deck-builder.ts exists.",
          "Run `npm run test` and verify deck builder coverage remains present.",
        ],
      },
      {
        id: "CARD3D-RULES",
        domain: "rules",
        title: "Extend card rules engine",
        scopePaths: ["src/game/rules-engine.ts"],
        acceptance: [
          "Run `npm run build` and verify src/game/rules-engine.ts exists.",
          "Run `npm run test` and verify rules engine coverage remains present.",
        ],
      },
      {
        id: "CARD3D-SYNC",
        domain: "sync",
        title: "Extend shared sync protocol",
        scopePaths: ["src/shared/protocol.ts"],
        acceptance: [
          "Run `npm run build` and verify src/shared/protocol.ts exists.",
          "Run `npm run test` and verify sync protocol coverage remains present.",
        ],
      },
      {
        id: "CARD3D-PERSISTENCE",
        domain: "persistence",
        title: "Extend multiplayer session persistence",
        scopePaths: ["src/server/session-store.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/session-store.ts exists.",
          "Run `npm run test` and verify persistence coverage remains present.",
        ],
      },
      {
        id: "CARD3D-MODERATION",
        domain: "moderation",
        title: "Extend room safety and moderation",
        scopePaths: ["src/server/moderation.ts"],
        acceptance: [
          "Run `npm run build` and verify src/server/moderation.ts exists.",
          "Run `npm run test` and verify moderation coverage remains present.",
        ],
      },
      {
        id: "CARD3D-PRESENCE",
        domain: "presence",
        title: "Extend player presence tracking",
        scopePaths: ["src/shared/player-presence.ts"],
        acceptance: [
          "Run `npm run build` and verify src/shared/player-presence.ts exists.",
          "Run `npm run test` and verify presence state coverage remains present.",
        ],
      },
      {
        id: "CARD3D-TELEMETRY",
        domain: "telemetry",
        title: "Extend client/server telemetry events",
        scopePaths: ["src/shared/telemetry.ts"],
        acceptance: [
          "Run `npm run build` and verify src/shared/telemetry.ts exists.",
          "Run `npm run test` and verify telemetry event coverage remains present.",
        ],
      },
      {
        id: "CARD3D-AUTH",
        domain: "auth",
        title: "Extend multiplayer session authentication",
        scopePaths: ["src/auth/session-auth.ts"],
        acceptance: [
          "Run `npm run build` and verify src/auth/session-auth.ts exists.",
          "Run `npm run test` and verify session authentication coverage remains present.",
        ],
      },
      {
        id: "CARD3D-LOBBY",
        domain: "lobby",
        title: "Extend lobby and room discovery service",
        scopePaths: ["src/lobby/lobby-service.ts"],
        acceptance: [
          "Run `npm run build` and verify src/lobby/lobby-service.ts exists.",
          "Run `npm run test` and verify lobby flow coverage remains present.",
        ],
      },
      {
        id: "CARD3D-ASSETS",
        domain: "assets",
        title: "Extend card asset manifest and loading contracts",
        scopePaths: ["src/assets/card-assets.ts"],
        acceptance: [
          "Run `npm run build` and verify src/assets/card-assets.ts exists.",
          "Run `npm run test` and verify card asset coverage remains present.",
        ],
      },
      {
        id: "CARD3D-ANIMATION",
        domain: "animation",
        title: "Extend card dealing and table animation contracts",
        scopePaths: ["src/animation/card-animations.ts"],
        acceptance: [
          "Run `npm run build` and verify src/animation/card-animations.ts exists.",
          "Run `npm run test` and verify card animation coverage remains present.",
        ],
      },
      {
        id: "CARD3D-PHYSICS",
        domain: "physics",
        title: "Extend table layout and card collision physics",
        scopePaths: ["src/physics/table-layout.ts"],
        acceptance: [
          "Run `npm run build` and verify src/physics/table-layout.ts exists.",
          "Run `npm run test` and verify table physics coverage remains present.",
        ],
      },
      {
        id: "CARD3D-ANALYTICS",
        domain: "analytics",
        title: "Extend match analytics and audit events",
        scopePaths: ["src/analytics/match-analytics.ts"],
        acceptance: [
          "Run `npm run build` and verify src/analytics/match-analytics.ts exists.",
          "Run `npm run test` and verify match analytics coverage remains present.",
        ],
      },
      {
        id: "CARD3D-TESTS",
        domain: "tests",
        title: "Strengthen multiplayer card integration tests",
        scopePaths: [
          "scripts/build.mjs",
          "scripts/test.mjs",
          "tests/unit/card-rules.test.ts",
          "tests/unit/deck-builder.test.ts",
          "tests/integration/multiplayer-flow.test.ts",
          "tests/integration/realtime-sync.test.ts",
          "tests/e2e/card-table-3d.test.ts",
        ],
        acceptance: [
          "Replace structural-only build/test scripts with substantive no-external-dependency verification.",
          "Run `npm run test` and verify all multiplayer card tests import and exercise src modules.",
          "Run `npm run build` and verify scripts no longer only check file existence or string markers.",
        ],
      },
    ];
  }
  if (scenario.key === "game") {
    return [
      {
        id: "GAME-ENGINE",
        domain: "engine",
        title: "Extend deterministic game loop and state transitions",
        scopePaths: ["src/engine/game-loop.ts", "src/engine/state.ts"],
        acceptance: [
          "Run `npm run build` and verify src/engine/game-loop.ts and src/engine/state.ts remain non-empty.",
          "Run `npm run test` and verify turn/state invariants are still covered.",
        ],
      },
      {
        id: "GAME-WORLD",
        domain: "world",
        title: "Extend seed-driven map and encounter generation",
        scopePaths: ["src/world/procedural-map.ts", "src/world/encounter-table.ts"],
        acceptance: [
          "Run `npm run build` and verify src/world/procedural-map.ts and src/world/encounter-table.ts exist.",
          "Run `npm run test` and verify generated-world behavior is covered without magic-number PRNG assertions.",
        ],
      },
      {
        id: "GAME-COMBAT",
        domain: "combat",
        title: "Extend turn-based combat and action queue behavior",
        scopePaths: ["src/combat/combat-system.ts", "src/combat/action-queue.ts"],
        acceptance: [
          "Run `npm run build` and verify src/combat/combat-system.ts and src/combat/action-queue.ts exist.",
          "Run `npm run test` and verify combat behavior invariants are covered.",
        ],
      },
      {
        id: "GAME-AI",
        domain: "ai",
        title: "Extend enemy director AI and tactical decision behavior",
        scopePaths: ["src/ai/director-ai.ts", "src/ai/behavior-tree.ts"],
        acceptance: [
          "Run `npm run build` and verify src/ai/director-ai.ts and src/ai/behavior-tree.ts exist.",
          "Run `npm run test` and verify AI behavior is exercised by the existing test suite.",
        ],
      },
      {
        id: "GAME-CONTENT",
        domain: "content",
        title: "Extend cards, relics, enemies, and encounter content tables",
        scopePaths: ["src/content/cards.ts", "src/content/relics.ts", "src/content/enemies.ts"],
        acceptance: [
          "Run `npm run build` and verify content tables exist under src/content.",
          "Run `npm run test` and verify content references remain structurally valid.",
        ],
      },
      {
        id: "GAME-PROGRESSION",
        domain: "progression",
        title: "Extend campaign progression, quests, and unlock state",
        scopePaths: ["src/progression/campaign.ts", "src/progression/quest-log.ts"],
        acceptance: [
          "Run `npm run build` and verify src/progression/campaign.ts and src/progression/quest-log.ts exist.",
          "Run `npm run test` and verify progression behavior is represented in integration coverage.",
        ],
      },
      {
        id: "GAME-ECONOMY",
        domain: "economy",
        title: "Extend loot, rewards, and shop economy rules",
        scopePaths: ["src/economy/loot-table.ts", "src/economy/shop.ts"],
        acceptance: [
          "Run `npm run build` and verify economy modules exist.",
          "Run `npm run test` and verify loot/reward behavior is covered without brittle random constants.",
        ],
      },
      {
        id: "GAME-PERSISTENCE",
        domain: "persistence",
        title: "Extend save/load and progress persistence",
        scopePaths: ["src/persistence/save-system.ts"],
        acceptance: [
          "Run `npm run build` and verify src/persistence/save-system.ts exists.",
          "Run `npm run test` and verify save/load behavior is represented in integration coverage.",
        ],
      },
      {
        id: "GAME-RENDERER",
        domain: "renderer",
        title: "Extend browser-facing HUD and input rendering",
        scopePaths: ["src/renderer/hud.ts", "src/renderer/input-controller.ts", "src/renderer/scene-view.ts", "src/main.ts", "index.html"],
        acceptance: [
          "Run `npm run build` and verify renderer modules, src/main.ts, and index.html exist.",
          "Run `npm run test` and verify renderer-facing integration behavior remains covered.",
        ],
      },
      {
        id: "GAME-AUDIO",
        domain: "audio",
        title: "Extend audio event routing and feedback cues",
        scopePaths: ["src/audio/sound-events.ts", "src/audio/music-state.ts"],
        acceptance: [
          "Run `npm run build` and verify audio modules exist.",
          "Run `npm run test` and verify audio state contracts are represented structurally.",
        ],
      },
      {
        id: "GAME-TOOLING",
        domain: "tooling",
        title: "Extend local balance-report tooling without changing package scripts",
        scopePaths: ["src/tools/balance-report.ts", "scripts/build.mjs", "scripts/test.mjs", "package.json"],
        acceptance: [
          "Run `npm run build` and verify src/tools/balance-report.ts exists.",
          "Run `npm run test` and verify package scripts remain node scripts/build.mjs and node scripts/test.mjs.",
        ],
      },
      {
        id: "GAME-TESTS",
        domain: "tests",
        title: "Strengthen unit and integration tests for current-run game changes",
        scopePaths: [
          "tests/unit/combat-system.test.ts",
          "tests/unit/procedural-map.test.ts",
          "tests/integration/game-session.test.ts",
          "tests/integration/save-restore.test.ts",
          "tests/e2e/gameplay-loop.test.ts",
        ],
        acceptance: [
          "Run `npm run test` and verify all unit, integration, and e2e test files contain describe/expect coverage.",
          "Run `npm run build` and verify test additions do not require external dependencies.",
        ],
      },
    ];
  }

  return [
    {
      id: "ENT-MODEL",
      domain: "models",
      title: "Extend task domain model and validation contracts",
      scopePaths: ["src/models/task.ts", "src/utils/validation.ts"],
      acceptance: ["Run `npm run build` and verify src/models/task.ts and src/utils/validation.ts exist."],
    },
    {
      id: "ENT-REPOSITORY",
      domain: "repository",
      title: "Extend repository persistence behavior",
      scopePaths: ["src/repositories/task-repository.ts"],
      acceptance: ["Run `npm run build` and verify src/repositories/task-repository.ts exists."],
    },
    {
      id: "ENT-SERVICE",
      domain: "service",
      title: "Extend task orchestration service behavior",
      scopePaths: ["src/services/task-service.ts"],
      acceptance: ["Run `npm run build` and verify src/services/task-service.ts exists."],
    },
    {
      id: "ENT-API",
      domain: "api",
      title: "Extend server API and auth boundary",
      scopePaths: ["src/server/app.ts", "src/middleware/auth.ts"],
      acceptance: ["Run `npm run build` and verify src/server/app.ts and src/middleware/auth.ts exist."],
    },
    {
      id: "ENT-TESTS",
      domain: "tests",
      title: "Strengthen unit and integration tests",
      scopePaths: ["tests/unit/task-service.test.ts", "tests/integration/api.test.ts"],
      acceptance: ["Run `npm run test` and verify both task-service and API tests contain describe/expect coverage."],
    },
    {
      id: "ENT-VERIFY",
      domain: "verification",
      title: "Preserve structural build and test verification",
      scopePaths: ["scripts/build.mjs", "scripts/test.mjs", "package.json"],
      acceptance: ["Run `npm run build` and `npm run test` using the existing package scripts."],
    },
  ];
}
