import { existsSync, promises as fs } from "node:fs";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import {
  assertExpandedTechEvidenceMatrix,
  collectExpandedTechEvidenceMatrix,
  writeExpandedTechEvidenceMatrix,
} from "./helpers/expandedTechEvidenceMatrix";

type BackendInfo = { baseUrl?: string; token?: string };
type SettingsPayload = {
  workspace?: string;
  model?: string;
  pm_model?: string;
  director_model?: string;
  pm_runs_director?: boolean;
};
type RuntimeLayoutPayload = {
  runtime_root?: string;
  workspace?: string;
  workspace_persistent_root?: string;
  project_persistent_root?: string;
};
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
type DirectorTaskPayload = {
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
type IntegrationQaArtifact = {
  reason?: string;
  passed?: boolean | null;
  failed?: number;
  evidence_grade?: string;
  qa_path?: string;
  summary?: string;
  result_path?: string;
  runtime_result_path?: string;
};
type DirectorResultArtifact = {
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
type DirectorResultSource = "existing_artifact" | "reconciled_terminal" | "executed" | "waited_artifact";
type RuntimeArtifactRef = { runtimeRoot: string; artifactPath: string; mtimeMs: number };
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
type PmContractAudit = {
  invalidTaskCount: number;
  issues: string[];
  coveredGameDomains: string[];
  missingGameDomains: string[];
};
type RuntimeEvent = { ts_epoch?: number; event_id?: string; name?: string };
type ImageDimensions = { width: number; height: number };

type ComplexityMetrics = {
  fileCount: number;
  codeLineCount: number;
  moduleCount: number;
  configFileCount: number;
  testFileCount: number;
};
type ProjectFileSnapshot = Record<string, { sha256: string; size: number; codeLines: number }>;
type RuntimeContributionMetrics = {
  baselineFileCount: number;
  finalFileCount: number;
  addedFiles: string[];
  modifiedFiles: string[];
  deletedFiles: string[];
  addedCodeLines: number;
  removedCodeLines: number;
};
type SnapshotSummaryMetrics = {
  fileCount: number;
  codeLineCount: number;
};
type ScenarioSeedResidue = {
  filePath: string;
  marker: string;
  line: number;
  excerpt: string;
};
type PmPlanningContribution = {
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
type ComplexityContributionBreakdown = {
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

type FullChainProjectScenario = {
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
type ResumePlanningSeed = {
  generatedAt: string;
  requirementsMarkdown: string;
  planMarkdown: string;
  pipelinePayload: Record<string, unknown>;
  progressPayload: Record<string, unknown>;
  tasks: ResumePlanningTaskSeed[];
};
type ResumePlanningWriteResult = {
  writtenPaths: string[];
  runtimeRequirementsPath: string;
  runtimePlanPath: string;
  pipelinePath: string;
  progressPath: string;
};

type ToolAuditPayload = {
  total_calls: number;
  policy_evidence_count: number;
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

function readJpegDimensions(bytes: Buffer, filePath: string): ImageDimensions {
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

const PM_FINISH_TIMEOUT_MS = positiveIntFromEnv("KERNELONE_E2E_PM_FINISH_TIMEOUT_MS", 45 * 60 * 1000);
const GAME_PM_MIN_TASKS = 12;
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
const GAME_FORBIDDEN_RUNTIME_ARTIFACT_RE = /(^|\/)(package\.json|Cargo\.toml|go\.(?:mod|sum)|requirements\.txt|pyproject\.toml|setup\.py|webpack\.config\.[cm]?[jt]s|jest\.config\.[cm]?[jt]s|vite\.config\.[cm]?ts|vitest\.config\.[cm]?ts)$|(\.rs|\.go|\.py)$/i;
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

function normalizeCoveragePath(candidate: string, workspace: string): string {
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

function scenarioRequiredDomains(scenario: FullChainProjectScenario): readonly string[] {
  if (scenario.key === "game") return GAME_PM_REQUIRED_DOMAINS;
  if (scenario.key === "card3d") return CARD3D_PM_REQUIRED_DOMAINS;
  return [];
}

function scenarioCoveredDomains(
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

function scenarioRequiresGameLikeBatch(scenario: FullChainProjectScenario): boolean {
  return scenario.key === "game" || scenario.key === "card3d";
}

function directorTaskCoveragePaths(tasks: DirectorTaskPayload[]): string[] {
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

function auditPmContract(pmContract: PmContractPayload, workspace: string, scenario: FullChainProjectScenario): PmContractAudit {
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

function resolveProjectScenario(): FullChainProjectScenario {
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

function resolveSafeWorkspaceName(prefix: string): string {
  const override = optionalEnvValue("KERNELONE_E2E_WORKSPACE_NAME");
  const candidate = override || `${prefix}_${Date.now().toString(36)}`;
  const sanitized = candidate.replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 96);
  if (!sanitized || sanitized === "." || sanitized === "..") {
    throw new Error(`Invalid KERNELONE_E2E_WORKSPACE_NAME=${override}`);
  }
  return sanitized;
}

function resolveGeneratedWorkspaceRoot(): string {
  return optionalEnvValue("KERNELONE_E2E_GENERATED_WORKSPACE_ROOT")
    || path.join(os.tmpdir(), "Polaris", "electron-e2e-generated-workspace");
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

function buildResumePlanningTaskSeeds(scenario: FullChainProjectScenario): ResumePlanningTaskSeed[] {
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

function markdownList(items: string[]): string {
  return items.map((item) => `- ${item}`).join("\n");
}

function buildResumePlanningSeed(workspace: string, scenario: FullChainProjectScenario): ResumePlanningSeed {
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

async function writeWorkspacePlanningDocs(workspace: string, seed: ResumePlanningSeed): Promise<string[]> {
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

async function writeRuntimePlanningSeed(
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
  const reviewStats = await fs.stat(reviewJpgPath);
  expect(reviewStats.size, `${name}.review.jpg should not be empty`).toBeGreaterThan(1024);
  const dimensions = readJpegDimensions(await fs.readFile(reviewJpgPath), reviewJpgPath);
  expect(dimensions.width, `${name}.review.jpg width should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.height, `${name}.review.jpg height should stay review-sized`).toBeLessThanOrEqual(2000);
  expect(dimensions.width, `${name}.review.jpg width should be visible`).toBeGreaterThan(0);
  expect(dimensions.height, `${name}.review.jpg height should be visible`).toBeGreaterThan(0);

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
    const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
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

async function tryRuntimeArtifact(
  window: Page,
  relPath: string,
  options?: { minMtimeMs?: number },
): Promise<RuntimeArtifactRef | null> {
  const normalizedRel = relPath.split(/[\\/]+/).filter(Boolean);
  const layout = await requestJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
  const runtimeRoot = String(layout.runtime_root || "").trim();
  if (!runtimeRoot) return null;
  const artifactPath = path.join(runtimeRoot, ...normalizedRel);
  if (!await pathExists(artifactPath)) return null;
  const stat = await fs.stat(artifactPath);
  if (options?.minMtimeMs && stat.mtimeMs < options.minMtimeMs) return null;
  return { runtimeRoot, artifactPath, mtimeMs: stat.mtimeMs };
}

async function dismissEngineFailureDialog(window: Page): Promise<void> {
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

function buildEnterpriseProjectScenario(): FullChainProjectScenario {
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

function buildGameProjectScenario(): FullChainProjectScenario {
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

function buildCard3dProjectScenario(): FullChainProjectScenario {
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

async function createComplexProject(
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

async function measureComplexity(workspace: string): Promise<ComplexityMetrics> {
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

function measureScenarioDefinitionComplexity(scenario: FullChainProjectScenario): ComplexityMetrics {
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

async function findScenarioSeedResidue(workspace: string, scenario: FullChainProjectScenario): Promise<ScenarioSeedResidue[]> {
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

async function snapshotProjectFiles(workspace: string): Promise<ProjectFileSnapshot> {
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

function compareProjectSnapshots(
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

function findForbiddenRuntimeArtifacts(
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

function buildPmPlanningContribution(
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

function buildComplexityContributionBreakdown(params: {
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

async function runCourtFlow(
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

async function inspectDirectorCodeChanges(
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

async function runDirectorUntilResultArtifact(
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

function stringArrayFromUnknown(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => String(item || "").trim())
    .filter((item) => item.length > 0);
}

function summarizeDirectorArtifactMaterialization(directorResult: DirectorResultArtifact | null): {
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
    seed_metrics: ComplexityMetrics | null;
    runtime_contribution: RuntimeContributionMetrics | null;
    complexity_contribution_breakdown: ComplexityContributionBreakdown | null;
    expanded_tech_evidence_matrix: {
      report_path: string;
      candidate_count: number;
      summary: Record<string, unknown>;
      core_runtime_integrations: Record<string, unknown>;
      core_runtime_evidence_placement: {
        row_count: number;
        missing: string[];
        receipt_id: string;
        handoff_id: string;
        task_projection: Record<string, unknown>;
      } | null;
    } | null;
    qa_gate: {
      passed: boolean | null;
      reason: string;
      evidence_grade: string;
      summary: string;
      result_path: string;
      runtime_result_path: string;
    } | null;
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
    director_tool_audit: {
      total_calls: 0,
      policy_evidence_count: 0,
      unauthorized_blocked: 0,
      dangerous_commands: 0,
      findings: [],
    },
    seed_metrics: null,
    runtime_contribution: null,
    complexity_contribution_breakdown: null,
    expanded_tech_evidence_matrix: null,
    qa_gate: null,
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
  let latestQaEvidenceGrade = "";
  let latestEventsPath = "";
  let baselineSnapshot: ProjectFileSnapshot = {};
  let latestPmPlanningContribution: PmPlanningContribution | null = null;

  try {
    await setReviewViewport(window);
    await dismissEngineFailureDialog(window);
    await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

    const startPhase = resolveFullChainStartPhase();
    const scenario = resolveProjectScenario();
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
        scenario,
      }
      : await createComplexProject(resolveGeneratedWorkspaceRoot(), scenario);
    const scenarioSeedMetrics = measureScenarioDefinitionComplexity(scenario);
    audit.workspace = project.workspace;
    audit.seed_metrics = project.metrics;
    const resumePlanningSeed = startPhase !== "court"
      ? buildResumePlanningSeed(project.workspace, scenario)
      : null;
    const workspacePlanningSeedPaths = resumePlanningSeed
      ? await writeWorkspacePlanningDocs(project.workspace, resumePlanningSeed)
      : [];
    baselineSnapshot = await snapshotProjectFiles(project.workspace);
    const scenarioPath = testInfo.outputPath("project.scenario.json");
    await writeUtf8File(scenarioPath, JSON.stringify({
      key: scenario.key,
      workspacePrefix: scenario.workspacePrefix,
      packageName: scenario.packageName,
      goal: scenario.goal,
      replies: scenario.replies,
      buildRequiredFiles: scenario.buildRequiredFiles,
      testFiles: scenario.testFiles,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(scenarioPath));

    const complexityPath = testInfo.outputPath("complexity.metrics.json");
    await writeUtf8File(complexityPath, JSON.stringify(project.metrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(complexityPath));
    const scenarioSeedMetricsPath = testInfo.outputPath("scenario.seed-definition.metrics.json");
    await writeUtf8File(scenarioSeedMetricsPath, JSON.stringify(scenarioSeedMetrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(scenarioSeedMetricsPath));
    const baselineSnapshotPath = testInfo.outputPath("seed.file-snapshot.json");
    await writeUtf8File(baselineSnapshotPath, JSON.stringify(baselineSnapshot, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(baselineSnapshotPath));

    expect(project.metrics.fileCount).toBeGreaterThanOrEqual(10);
    expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(500);
    expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.configFileCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(2);
    if (scenarioRequiresGameLikeBatch(scenario)) {
      expect(project.metrics.fileCount).toBeGreaterThanOrEqual(30);
      expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(1200);
      expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(10);
      expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(5);
    }

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
    if (resumePlanningSeed) {
      const resetEvidencePath = testInfo.outputPath("resume.reset-tasks.json");
      if (startPhase === "pm" || startPhase === "chief" || startPhase === "director") {
        const preservePlanningContracts = startPhase === "chief" || startPhase === "director";
        const resetResponse = await requestJson<Record<string, unknown>>(window, "/v2/runtime/reset/tasks", {
          method: "POST",
          body: preservePlanningContracts ? { preserve_planning_contracts: true } : {},
        });
        await writeUtf8File(resetEvidencePath, JSON.stringify({
          start_phase: startPhase,
          preserve_planning_contracts: preservePlanningContracts,
          response: resetResponse,
        }, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(resetEvidencePath));
      }
      if (startPhase === "pm" || startPhase === "chief" || startPhase === "director") {
        const staleRuntimeArtifacts = [
          "results/director.result.json",
          "results/integration_qa.result.json",
        ];
        const removedArtifacts: string[] = [];
        for (const relativeArtifact of staleRuntimeArtifacts) {
          const artifactPath = path.join(runtimeRoot, relativeArtifact);
          await fs.rm(artifactPath, { force: true });
          removedArtifacts.push(toPosixPath(artifactPath));
        }
        await writeUtf8File(testInfo.outputPath("resume.reset-artifacts.json"), JSON.stringify({
          start_phase: startPhase,
          removed_artifacts: removedArtifacts,
        }, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(testInfo.outputPath("resume.reset-artifacts.json")));
      }

      const resumeSeedResult = await writeRuntimePlanningSeed(layout, project.workspace, resumePlanningSeed);
      const resumeSeedEvidencePath = testInfo.outputPath("resume.planning-seed.json");
      await writeUtf8File(resumeSeedEvidencePath, JSON.stringify({
        start_phase: startPhase,
        workspace: project.workspace,
        workspace_seed_paths: workspacePlanningSeedPaths.map(toPosixPath),
        runtime_seed_paths: resumeSeedResult.writtenPaths.map(toPosixPath),
        mandatory_tasks: resumePlanningSeed.tasks,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(resumeSeedEvidencePath));
      audit.evidence_paths.logs.push(
        toPosixPath(resumeSeedResult.runtimeRequirementsPath),
        toPosixPath(resumeSeedResult.runtimePlanPath),
        toPosixPath(resumeSeedResult.pipelinePath),
        toPosixPath(resumeSeedResult.progressPath),
      );
      audit.issues_fixed.push({
        issue: `resume_planning_contract_seeded_before_${startPhase}`,
        root_cause: "phase_reuse_context",
        fix: `seeded current scenario requirements/plan into runtime contracts and workspace docs for ${project.workspace}`,
        verified: true,
      });
    }

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
      const courtFlow = await runCourtFlow(window, scenario);
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
      const pmAudit = auditPmContract(pmContract, project.workspace, scenario);
      latestPmPlanningContribution = buildPmPlanningContribution(
        "executed_pm_round",
        round,
        pmContract,
        pmAudit,
        pmContractPath,
      );
      const pmSnapshotGate = (
        (Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0) > 0
        && (Number(snapshot.pm_state?.["completed_task_count"] || 0) > 0 || tasks.length > 0)
      );

      audit.pm_quality_history.push({
        round,
        score,
        issues: [
          summary,
          ...(critical > 0 ? [`critical_issue_count=${critical}`] : []),
          ...(pmAudit.invalidTaskCount > 0 ? [`invalid_tasks=${pmAudit.invalidTaskCount}`] : []),
          ...pmAudit.issues,
        ].filter(Boolean),
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

      if (pmAudit.issues.length > 0) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_contract_quality_strict_failed`,
          root_cause: "pm_contract_quality",
          fix: `fail-fast on PM contract path, acceptance, workspace, and domain coverage issues (evidence: ${toPosixPath(pmContractPath)})`,
          verified: false,
        });
        throw new Error(
          `PM contract strict quality gate failed: ${pmAudit.issues.join("; ")}; `
          + `contract=${toPosixPath(pmContractPath)}`,
        );
      }

      if (pmSnapshotGate && score >= 80 && critical === 0 && pmAudit.issues.length === 0) {
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
        const pmAudit = auditPmContract(pmContract, project.workspace, scenario);
        latestPmPlanningContribution = buildPmPlanningContribution(
          "resumed_existing_pm_contract",
          round,
          pmContract,
          pmAudit,
          pmContractPath,
        );
        const pmFallbackFailure = detectPmFallbackFailure(pmContract);

        audit.pm_quality_history.push({
          round,
          score,
          issues: [
            `resumed_existing_pm_contract:${toPosixPath(pmContractPath)}`,
            ...(critical > 0 ? [`critical_issue_count=${critical}`] : []),
            ...(pmAudit.invalidTaskCount > 0 ? [`invalid_tasks=${pmAudit.invalidTaskCount}`] : []),
            ...pmAudit.issues,
            ...(pmFallbackFailure ? [`fallback_failure=${pmFallbackFailure}`] : []),
          ],
        });

        if (score < 80 || critical > 0 || pmAudit.issues.length > 0 || tasks.length === 0 || pmFallbackFailure) {
          throw new Error(
            `Resumed PM contract failed quality gate: score=${score} critical=${critical} `
            + `tasks=${tasks.length} invalidTasks=${pmAudit.invalidTaskCount} fallback=${pmFallbackFailure || "none"} `
            + `strictIssues=${pmAudit.issues.join("; ") || "none"} `
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
        if (scenarioRequiresGameLikeBatch(scenario)) {
          const plannedBlueprints = Number(chiefDiagnostics.blueprints?.planned_tasks || 0);
          const coveredBlueprints = Number(chiefDiagnostics.blueprints?.covered_tasks || 0);
          const expectedBlueprints = Math.max(GAME_PM_MIN_TASKS, scenarioRequiredDomains(scenario).length);
          expect(
            plannedBlueprints,
            `Chief Engineer must produce a large batch of blueprints before Director handoff; evidence=${toPosixPath(chiefSnapshotPath)}`,
          ).toBeGreaterThanOrEqual(expectedBlueprints);
          expect(
            coveredBlueprints,
            `Chief Engineer must cover every planned blueprint before Director handoff; evidence=${toPosixPath(chiefSnapshotPath)}`,
          ).toBeGreaterThanOrEqual(plannedBlueprints);
        }
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
      let directorResultSource: DirectorResultSource | "" = "";
      let downstreamDirectorFailure = "";

      if (shouldRunFullChainPhase(startPhase, "director")) {
        await dismissEngineFailureDialog(window);
        await enterDirectorWorkspace(window);
        await expect(window.getByTestId("director-workspace")).toBeVisible();
        const director = await runDirectorUntilResultArtifact(window, { minMtimeMs: startEpochSeconds * 1000 });
        if (scenarioRequiresGameLikeBatch(scenario)) {
          const expectedDirectorTasks = Math.max(GAME_PM_MIN_TASKS, scenarioRequiredDomains(scenario).length);
          expect(
            director.linkedTaskCount,
            "Director must receive the large PM task batch only after Chief Engineer blueprints are handoff-ready",
          ).toBeGreaterThanOrEqual(expectedDirectorTasks);
          expect(
            director.uiTaskCount,
            "Director workspace must visibly expose the large task batch before execution",
          ).toBeGreaterThanOrEqual(expectedDirectorTasks);
          if (scenario.key === "card3d") {
            const directorCoveragePaths = director.coveragePaths
              .map((item) => normalizeCoveragePath(item, project.workspace))
              .filter(Boolean);
            const directorCoveredDomains = scenarioCoveredDomains(scenario, directorCoveragePaths);
            const missingDirectorDomains = scenarioRequiredDomains(scenario)
              .filter((domain) => !directorCoveredDomains.includes(domain));
            expect(
              director.coveragePaths.length,
              "Director task exposure must include scope/target paths for card3d domain audit",
            ).toBeGreaterThan(0);
            expect(
              missingDirectorDomains,
              `Director task batch must cover every card3d domain before execution; `
              + `paths=${JSON.stringify(directorCoveragePaths)}`,
            ).toEqual([]);
          }
        }
        if (director.linkedTaskCount > 0 && director.uiTaskCount > 0) {
          audit.acceptance_results.director_phase = "PASS";
        }

        const directorResultArtifact = {
          runtimeRoot: director.runtimeRoot,
          artifactPath: director.artifactPath,
          mtimeMs: director.mtimeMs,
        };
        runtimeRoot = directorResultArtifact.runtimeRoot;
        directorResultPath = directorResultArtifact.artifactPath;
        directorResultSource = director.source;
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
          expect(
            directorCodeEvidence.expanded,
            `Director code change view should allow expanding change details; result=${toPosixPath(directorResultPath)}`,
          ).toBe(true);
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

        if (directorResultSource === "existing_artifact" || directorResultSource === "reconciled_terminal") {
          audit.issues_fixed.push({
            issue: `round_${round}_director_result_reused_${directorResultSource}`,
            root_cause: "resume_strategy",
            fix: `KERNELONE_E2E_START_PHASE=${startPhase} reused fresh terminal Director evidence at ${toPosixPath(directorResultPath)} mtime=${new Date(directorResultArtifact.mtimeMs).toISOString()}`,
            verified: true,
          });
        }

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
        directorResultSource = "existing_artifact";
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

      const finalSnapshot = await snapshotProjectFiles(project.workspace);
      const finalMetrics = await measureComplexity(project.workspace);
      audit.runtime_contribution = compareProjectSnapshots(baselineSnapshot, finalSnapshot);
      const forbiddenRuntimeArtifacts = findForbiddenRuntimeArtifacts(project.scenario, audit.runtime_contribution);
      expect(
        forbiddenRuntimeArtifacts,
        `Game-like scenario Director output must preserve the seed Node/TypeScript stack and must not introduce forbidden runtime artifacts; `
        + `artifacts=${JSON.stringify(forbiddenRuntimeArtifacts)}`,
      ).toEqual([]);
      const directorArtifactMaterialization = summarizeDirectorArtifactMaterialization(directorResult);
      const contributionPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.runtime-contribution.json`);
      const seedResidue = await findScenarioSeedResidue(project.workspace, project.scenario);
      const seedResiduePath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.seed-residue.json`);
      await writeUtf8File(seedResiduePath, JSON.stringify({
        workspace: toPosixPath(project.workspace),
        scenario: project.scenario.key,
        residue_count: seedResidue.length,
        residues: seedResidue,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(seedResiduePath));
      expect(
        seedResidue,
        `Game-like scenario final source must not retain audit seed markers; evidence=${toPosixPath(seedResiduePath)}`,
      ).toEqual([]);
      audit.complexity_contribution_breakdown = buildComplexityContributionBreakdown({
        scenarioSeedMetrics,
        startPhase,
        currentRunBaselineMetrics: project.metrics,
        baselineSnapshot,
        finalMetrics,
        finalSnapshot,
        pmPlanningDelta: latestPmPlanningContribution,
        directorResultSource: directorResultSource || "unknown",
        directorContribution: audit.runtime_contribution,
        contributionEvidencePath: contributionPath,
      });
      await writeUtf8File(contributionPath, JSON.stringify({
        audit_start_epoch_seconds: startEpochSeconds,
        director_result_source: directorResultSource || "unknown",
        director_result_path: toPosixPath(directorResultPath),
        director_result_mtime_ms: directorResultPath ? (await fs.stat(directorResultPath)).mtimeMs : null,
        director_artifact_source: String(directorResult?.source || ""),
        director_artifact_materialization: directorArtifactMaterialization,
        baseline: baselineSnapshot,
        final: finalSnapshot,
        contribution: audit.runtime_contribution,
        seed_residue_path: toPosixPath(seedResiduePath),
        seed_residue_count: seedResidue.length,
        seed_residue: seedResidue,
        complexity_contribution_breakdown: audit.complexity_contribution_breakdown,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(contributionPath));
      if (directorSuccesses > 0) {
        const changedFileCount = audit.runtime_contribution.addedFiles.length
          + audit.runtime_contribution.modifiedFiles.length
          + audit.runtime_contribution.deletedFiles.length;
        if (shouldRunFullChainPhase(startPhase, "director")) {
          const canReuseMaterializedArtifact = startPhase !== "court"
            && changedFileCount === 0
            && directorArtifactMaterialization.changedFileCount > 0
            && directorArtifactMaterialization.toolEvidenceCount > 0;
          if (canReuseMaterializedArtifact) {
            audit.issues_fixed.push({
              issue: `round_${round}_director_workspace_delta_zero_but_artifact_materialized`,
              root_cause: "resume_strategy",
              fix: `resume run accepted fresh Director artifact materialization evidence instead of forcing duplicate file writes; evidence=${toPosixPath(contributionPath)}`,
              verified: true,
            });
          } else {
            expect(
              changedFileCount,
              `Director phase success must produce auditable current-run contribution or resume artifact materialization; `
              + `source=${directorResultSource || "unknown"} `
              + `artifact_files=${directorArtifactMaterialization.changedFileCount} `
              + `artifact_tools=${directorArtifactMaterialization.toolEvidenceCount} `
              + `evidence=${toPosixPath(contributionPath)}`,
            ).toBeGreaterThan(0);
          }
        } else {
          audit.issues_fixed.push({
            issue: `round_${round}_runtime_contribution_not_recomputed_for_${directorResultSource || "unknown"}`,
            root_cause: "resume_strategy",
            fix: `runtime contribution gate skipped because Director result came from ${directorResultSource || "unknown"}; evidence=${toPosixPath(contributionPath)}`,
            verified: true,
          });
        }
      }

      const qaArtifactMinMtimeMs = shouldRunFullChainPhase(startPhase, "director")
        ? startEpochSeconds * 1000
        : undefined;
      const existingQaArtifact = await tryRuntimeArtifact(
        window,
        "results/integration_qa.result.json",
        qaArtifactMinMtimeMs ? { minMtimeMs: qaArtifactMinMtimeMs } : undefined,
      );
      const existingQa = existingQaArtifact
        ? await readJsonFile<IntegrationQaArtifact>(existingQaArtifact.artifactPath)
        : null;
      if (
        String(existingQa?.reason || "").trim() !== "integration_qa_passed"
        || String(existingQa?.evidence_grade || "").trim() !== "real_command_passed"
      ) {
        await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
          method: "POST",
          body: { run_id: `full-chain-qa-${Date.now()}` },
        });
      }
      const qaArtifact = await waitForRuntimeArtifact(
        window,
        "results/integration_qa.result.json",
        120_000,
        qaArtifactMinMtimeMs ? { minMtimeMs: qaArtifactMinMtimeMs } : undefined,
      );
      runtimeRoot = qaArtifact.runtimeRoot;
      const qaPath = qaArtifact.artifactPath;
      const qa = await readJsonFile<IntegrationQaArtifact>(qaPath);
      latestQaReason = String(qa?.reason || "").trim();
      latestQaEvidenceGrade = String(qa?.evidence_grade || "").trim();
      audit.qa_gate = {
        passed: typeof qa?.passed === "boolean" ? qa.passed : null,
        reason: latestQaReason,
        evidence_grade: latestQaEvidenceGrade || "unknown",
        summary: String(qa?.summary || "").trim(),
        result_path: String(qa?.result_path || "").trim(),
        runtime_result_path: String(qa?.runtime_result_path || "").trim(),
      };
      audit.evidence_paths.logs.push(toPosixPath(qaPath));
      if (latestQaReason === "integration_qa_passed") {
        expect(
          latestQaEvidenceGrade,
          `Integration QA PASS must include strong evidence grade; qa=${toPosixPath(qaPath)} summary=${String(qa?.summary || "")}`,
        ).toBe("real_command_passed");
        audit.acceptance_results.qa_phase = "PASS";

        let qaEvidenceBadge = window.getByTestId("qa-evidence-grade");
        const qaBadgeVisible = await qaEvidenceBadge.isVisible({ timeout: 5_000 }).catch(() => false);
        const qaBadgeText = qaBadgeVisible ? String(await qaEvidenceBadge.textContent().catch(() => "") || "") : "";
        if (
          !qaBadgeVisible
          || !qaBadgeText.includes("real command passed")
          || !qaBadgeText.includes("integration_qa_passed")
        ) {
          await reloadRendererAfterWorkspaceSwitch(window);
          qaEvidenceBadge = window.getByTestId("qa-evidence-grade");
        }
        await expect(
          qaEvidenceBadge,
          `QA phase PASS must be recoverable in the desktop runtime panel; qa=${toPosixPath(qaPath)}`,
        ).toBeVisible({ timeout: 60_000 });
        await expect(qaEvidenceBadge).toContainText("real command passed");
        await expect(qaEvidenceBadge).toContainText("integration_qa_passed");
        const qaShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.qa`);
        audit.evidence_paths.screenshots.push(toPosixPath(qaShot.pngPath), toPosixPath(qaShot.reviewJpgPath));
      } else {
        audit.issues_fixed.push({
          issue: `round_${round}_qa_reason_${latestQaReason || "unknown"}`,
          root_cause: latestQaReason.includes("pending") ? "director_execution" : "qa_baseline",
          fix: `fail-fast on QA terminal failure instead of rerunning PM (evidence: ${toPosixPath(qaPath)})`,
          verified: false,
        });
        const failureSignature = JSON.stringify({
          qa_reason: latestQaReason || "unknown",
          qa_evidence_grade: latestQaEvidenceGrade || "unknown",
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
    if (audit.director_tool_audit.unauthorized_blocked > 0) {
      audit.next_risks.push(
        `Director policy blocked ${audit.director_tool_audit.unauthorized_blocked} unauthorized tool attempts; verify repeated denials do not hide task drift.`,
      );
    }
    const runtimeContributionFileChanges = audit.runtime_contribution
      ? audit.runtime_contribution.addedFiles.length
        + audit.runtime_contribution.modifiedFiles.length
        + audit.runtime_contribution.deletedFiles.length
      : 0;
    const directorPolicyEvidenceRequired = shouldRunFullChainPhase(startPhase, "director") && runtimeContributionFileChanges > 0;
    if (directorPolicyEvidenceRequired && audit.director_tool_audit.policy_evidence_count === 0) {
      audit.next_risks.push(
        `Director changed ${runtimeContributionFileChanges} files but no director_policy evidence was found in runtime tool events.`,
      );
    }
    if (audit.leakage_findings.length > 0) {
      audit.next_risks.push("Prompt-leakage keywords detected in plan or PM contract.");
    }
    if (latestQaReason && latestQaReason !== "integration_qa_passed") {
      audit.next_risks.push(`Latest QA reason: ${latestQaReason}`);
    }

    const expandedTechMatrix = await collectExpandedTechEvidenceMatrix(window, {
      requireRealChain: true,
      runtimeRootOverride: runtimeRoot,
      workspaceOverride: audit.workspace,
    });
    const expandedTechMatrixPath = await writeExpandedTechEvidenceMatrix(
      testInfo,
      expandedTechMatrix,
      "full-chain-expanded-tech-evidence-matrix.json",
    );
    audit.evidence_paths.snapshots.push(toPosixPath(expandedTechMatrixPath));
    audit.expanded_tech_evidence_matrix = {
      report_path: toPosixPath(expandedTechMatrixPath),
      candidate_count: expandedTechMatrix.expanded_candidates.length,
      summary: expandedTechMatrix.summary,
      core_runtime_integrations: expandedTechMatrix.core_runtime_integrations,
      core_runtime_evidence_placement: expandedTechMatrix.core_runtime_evidence_placement
        ? {
            row_count: expandedTechMatrix.core_runtime_evidence_placement.rows.length,
            missing: expandedTechMatrix.core_runtime_evidence_placement.missing,
            receipt_id: expandedTechMatrix.core_runtime_evidence_placement.receipt_id,
            handoff_id: expandedTechMatrix.core_runtime_evidence_placement.handoff_id,
            task_projection: expandedTechMatrix.core_runtime_evidence_placement.task_projection,
          }
        : null,
    };
    assertExpandedTechEvidenceMatrix(expandedTechMatrix);

    const pass = (
      audit.acceptance_results.court_phase === "PASS"
      && audit.acceptance_results.pm_phase === "PASS"
      && audit.acceptance_results.chief_engineer_phase === "PASS"
      && audit.acceptance_results.director_phase === "PASS"
      && audit.acceptance_results.qa_phase === "PASS"
      && audit.leakage_findings.length === 0
      && (!directorPolicyEvidenceRequired || audit.director_tool_audit.policy_evidence_count > 0)
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
