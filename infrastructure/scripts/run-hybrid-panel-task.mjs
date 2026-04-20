import fs from "fs";
import path from "path";
import { spawn } from "child_process";
import { fileURLToPath } from "url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const defaultConfigPath = path.join(repoRoot, "infrastructure", "e2e", "hybrid-automation.config.json");
const defaultEvidenceRoot = path.join(repoRoot, ".polaris", "logs");

function nowIso() {
  return new Date().toISOString();
}

function toUnixPath(filePath) {
  return String(filePath || "").split(path.sep).join("/");
}

function toRelativePath(filePath) {
  return toUnixPath(path.relative(repoRoot, filePath));
}

function parseIntArg(raw, fallback, label) {
  if (raw === undefined || raw === null || raw === "") {
    return fallback;
  }
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer. Received: ${raw}`);
  }
  return parsed;
}

function parseBoolEnv(name, fallback) {
  const raw = String(process.env[name] || "").trim().toLowerCase();
  if (!raw) return fallback;
  return raw === "1" || raw === "true" || raw === "yes";
}

function safeParseUrl(raw) {
  try {
    return new URL(String(raw || "").trim());
  } catch {
    return null;
  }
}

function isLocalHttpUrl(raw) {
  const parsed = safeParseUrl(raw);
  if (!parsed) return false;
  if (!["http:", "https:"].includes(parsed.protocol)) return false;
  const host = String(parsed.hostname || "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

async function canReachHttpUrl(raw, timeoutMs = 3000) {
  const parsed = safeParseUrl(raw);
  if (!parsed) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(parsed.toString(), {
      method: "GET",
      redirect: "manual",
      signal: controller.signal,
    });
    return Number.isFinite(response.status) && response.status > 0;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function isLlmSensitiveText(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return false;
  const patterns = [
    /\bllm\b/i,
    /llm设置/i,
    /模型设置/i,
    /\bprovider\b/i,
    /\bopenai\b/i,
    /\banthropic\b/i,
    /\bkimi\b/i,
    /\bclaude\b/i,
    /\bgpt\b/i,
    /custom headers/i,
    /自定义请求头/i,
  ];
  return patterns.some((pattern) => pattern.test(text));
}

function ensureDirectory(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function readJsonUtf8(filePath) {
  const raw = fs.readFileSync(filePath, "utf-8").replace(/^\uFEFF/, "");
  return JSON.parse(raw);
}

function writeJsonUtf8(filePath, payload) {
  ensureDirectory(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf-8" });
}

function parseArgs(argv) {
  const options = {
    prompt: "",
    taskFile: "",
    dictionaryPath: "",
    dryRun: false,
    untilPass: parseBoolEnv("POLARIS_HYBRID_UNTIL_PASS", true),
    maxRounds: parseIntArg(process.env.POLARIS_HYBRID_MAX_ROUNDS, 0, "POLARIS_HYBRID_MAX_ROUNDS"),
    configPath: defaultConfigPath,
    outputJson: "",
    allowProviderFallback: false,
    allowFieldFallback: false,
    computerUseCommand: "",
    semanticCommand: "",
    omniparserCommand: "",
    airtestCommand: "",
    sikulixCommand: "",
    playwrightTimeoutMs: parseIntArg(process.env.POLARIS_HYBRID_PLAYWRIGHT_TIMEOUT_MS, 30 * 60 * 1000, "POLARIS_HYBRID_PLAYWRIGHT_TIMEOUT_MS"),
    semanticTimeoutMs: parseIntArg(process.env.POLARIS_HYBRID_SEMANTIC_TIMEOUT_MS, 20 * 60 * 1000, "POLARIS_HYBRID_SEMANTIC_TIMEOUT_MS"),
    computerUseTimeoutMs: parseIntArg(process.env.POLARIS_HYBRID_COMPUTER_USE_TIMEOUT_MS, 30 * 60 * 1000, "POLARIS_HYBRID_COMPUTER_USE_TIMEOUT_MS"),
    omniparserTimeoutMs: parseIntArg(process.env.POLARIS_HYBRID_OMNIPARSER_TIMEOUT_MS, 10 * 60 * 1000, "POLARIS_HYBRID_OMNIPARSER_TIMEOUT_MS"),
    visionTimeoutMs: parseIntArg(process.env.POLARIS_HYBRID_VISION_TIMEOUT_MS, 20 * 60 * 1000, "POLARIS_HYBRID_VISION_TIMEOUT_MS"),
  };

  const trailingPrompt = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (arg === "--until-pass") {
      options.untilPass = true;
      continue;
    }
    if (arg === "--no-until-pass") {
      options.untilPass = false;
      continue;
    }
    if (arg === "--prompt") {
      options.prompt = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--task-file") {
      options.taskFile = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--dictionary") {
      options.dictionaryPath = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--max-rounds") {
      options.maxRounds = parseIntArg(argv[index + 1], options.maxRounds, "--max-rounds");
      index += 1;
      continue;
    }
    if (arg.startsWith("--max-rounds=")) {
      options.maxRounds = parseIntArg(arg.slice("--max-rounds=".length), options.maxRounds, "--max-rounds");
      continue;
    }
    if (arg === "--config") {
      options.configPath = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--output-json") {
      options.outputJson = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--allow-provider-fallback") {
      options.allowProviderFallback = true;
      continue;
    }
    if (arg === "--allow-field-fallback") {
      options.allowFieldFallback = true;
      continue;
    }
    if (arg === "--computer-use-cmd") {
      options.computerUseCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--semantic-cmd") {
      options.semanticCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--omniparser-cmd") {
      options.omniparserCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--airtest-cmd") {
      options.airtestCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--sikulix-cmd") {
      options.sikulixCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    trailingPrompt.push(arg);
  }

  if (!options.prompt) {
    options.prompt = trailingPrompt.join(" ").trim();
  }
  return options;
}

function loadConfig(configPath) {
  const resolved = path.isAbsolute(configPath) ? configPath : path.resolve(repoRoot, configPath);
  if (!fs.existsSync(resolved)) {
    return { resolvedConfigPath: resolved, config: {} };
  }
  const config = readJsonUtf8(resolved);
  return { resolvedConfigPath: resolved, config };
}

function isLlmSensitiveTaskPayload(task) {
  if (!task || typeof task !== "object") return false;
  if (isLlmSensitiveText(task.prompt)) return true;
  const steps = Array.isArray(task.navigationSteps) ? task.navigationSteps : [];
  if (steps.some((step) => isLlmSensitiveText(step?.name) || isLlmSensitiveText((step?.textCandidates || []).join(" ")))) {
    return true;
  }
  const fieldAction = task.fieldAction && typeof task.fieldAction === "object" ? task.fieldAction : {};
  if (isLlmSensitiveText(fieldAction.name) || isLlmSensitiveText((fieldAction.labelCandidates || []).join(" "))) {
    return true;
  }
  return false;
}

function taskFileLooksLlmSensitive(taskFile) {
  if (!String(taskFile || "").trim()) return false;
  try {
    const resolved = path.isAbsolute(taskFile) ? taskFile : path.resolve(repoRoot, taskFile);
    if (!fs.existsSync(resolved)) return false;
    const payload = readJsonUtf8(resolved);
    return isLlmSensitiveTaskPayload(payload);
  } catch {
    return false;
  }
}

function quoteForShell(value) {
  const text = String(value || "");
  if (!text) return "\"\"";
  return `"${text.replace(/"/g, "\"\"")}"`;
}

function applyTemplate(template, context) {
  return String(template || "").replace(/\{([a-zA-Z0-9_]+)\}/g, (_, key) => {
    const raw = context[key];
    return raw === undefined || raw === null ? "" : String(raw);
  });
}

function runProcess(command, args, options = {}) {
  const { timeoutMs = 0, env = process.env, cwd = repoRoot, label = "" } = options;
  return new Promise((resolve, reject) => {
    const display = `${command} ${args.map((item) => quoteForShell(item)).join(" ")}`.trim();
    if (label) {
      console.log(`[hybrid] run ${label}: ${display}`);
    } else {
      console.log(`[hybrid] run: ${display}`);
    }

    const child = process.platform === "win32"
      ? spawn("cmd.exe", ["/d", "/s", "/c", command, ...args], { cwd, env, stdio: "inherit" })
      : spawn(command, args, { cwd, env, stdio: "inherit" });

    let timedOut = false;
    let timer = null;
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGTERM");
      }, timeoutMs);
    }

    child.on("error", (error) => {
      if (timer) clearTimeout(timer);
      reject(error);
    });

    child.on("exit", (code) => {
      if (timer) clearTimeout(timer);
      resolve({
        exitCode: code ?? 1,
        timedOut,
      });
    });
  });
}

function runShell(commandText, options = {}) {
  const { timeoutMs = 0, env = process.env, cwd = repoRoot, label = "" } = options;
  if (!String(commandText || "").trim()) {
    return Promise.resolve({ exitCode: 1, timedOut: false, skipped: true });
  }
  return new Promise((resolve, reject) => {
    if (label) {
      console.log(`[hybrid] run ${label}: ${commandText}`);
    } else {
      console.log(`[hybrid] run shell: ${commandText}`);
    }

    const child = process.platform === "win32"
      ? spawn("cmd.exe", ["/d", "/s", "/c", commandText], { cwd, env, stdio: "inherit" })
      : spawn("sh", ["-lc", commandText], { cwd, env, stdio: "inherit" });

    let timedOut = false;
    let timer = null;
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGTERM");
      }, timeoutMs);
    }

    child.on("error", (error) => {
      if (timer) clearTimeout(timer);
      reject(error);
    });

    child.on("exit", (code) => {
      if (timer) clearTimeout(timer);
      resolve({
        exitCode: code ?? 1,
        timedOut,
      });
    });
  });
}

function collectFilesRecursively(rootPath, depthLimit, collector) {
  if (!fs.existsSync(rootPath)) return;
  const stack = [{ current: rootPath, depth: 0 }];
  while (stack.length > 0) {
    const item = stack.pop();
    if (!item) continue;
    const { current, depth } = item;

    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (depth < depthLimit) {
          stack.push({ current: fullPath, depth: depth + 1 });
        }
        continue;
      }
      collector(fullPath);
    }
  }
}

function collectEvidencePaths(config) {
  const evidenceConfig = config.evidence && typeof config.evidence === "object" ? config.evidence : {};
  const includePaths = Array.isArray(evidenceConfig.include_paths)
    ? evidenceConfig.include_paths.map((item) => String(item || "").trim()).filter(Boolean)
    : ["test-results/electron", "playwright-report", ".polaris/logs"];
  const maxFiles = parseIntArg(evidenceConfig.max_files, 80, "evidence.max_files");
  const depthLimit = parseIntArg(evidenceConfig.max_depth, 6, "evidence.max_depth");

  const allowedExtensions = new Set([
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".zip",
    ".txt",
    ".md",
    ".json",
    ".jsonl",
    ".log",
    ".trace",
  ]);

  const collected = [];
  for (const relativePath of includePaths) {
    const rootPath = path.isAbsolute(relativePath) ? relativePath : path.resolve(repoRoot, relativePath);
    collectFilesRecursively(rootPath, depthLimit, (candidatePath) => {
      const ext = path.extname(candidatePath).toLowerCase();
      if (!allowedExtensions.has(ext)) return;
      try {
        const stat = fs.statSync(candidatePath);
        collected.push({
          path: candidatePath,
          mtimeMs: stat.mtimeMs,
        });
      } catch {
        // ignore unreadable paths
      }
    });
  }

  collected.sort((left, right) => right.mtimeMs - left.mtimeMs);
  return collected.slice(0, maxFiles).map((item) => toRelativePath(item.path));
}

function pickLatestImageFromEvidence(evidencePaths) {
  const imageExt = new Set([".png", ".jpg", ".jpeg", ".webp"]);
  for (const relative of evidencePaths || []) {
    const ext = path.extname(String(relative || "")).toLowerCase();
    if (!imageExt.has(ext)) continue;
    const absolute = path.resolve(repoRoot, String(relative).split("/").join(path.sep));
    if (!fs.existsSync(absolute)) continue;
    return {
      relative,
      absolute,
    };
  }
  return null;
}

function buildPlaywrightArgs(options) {
  const args = ["run", "test:e2e:task", "--"];
  if (options.taskFile) {
    args.push("--task-file", options.taskFile);
  } else {
    args.push(options.prompt);
  }
  if (options.dictionaryPath) {
    args.push("--dictionary", options.dictionaryPath);
  }
  if (options.allowProviderFallback) {
    args.push("--allow-provider-fallback");
  }
  if (options.allowFieldFallback) {
    args.push("--allow-field-fallback");
  }
  return args;
}

function createStageResult(stage, round, command, startAt, endAt, runResult, evidencePaths, extra = {}) {
  return {
    stage,
    round,
    command,
    started_at: startAt,
    ended_at: endAt,
    duration_ms: new Date(endAt).getTime() - new Date(startAt).getTime(),
    success: runResult.exitCode === 0 && !runResult.timedOut,
    exit_code: runResult.exitCode,
    timed_out: Boolean(runResult.timedOut),
    evidence_paths: evidencePaths,
    ...extra,
  };
}

function buildCommandContext(options, reportPath, round, evidencePaths, extras = {}) {
  return {
    prompt: options.prompt,
    task_file: options.taskFile,
    workspace: repoRoot,
    round,
    evidence_json: reportPath,
    evidence_paths: evidencePaths.join("\n"),
    last_screenshot: "",
    omniparser_json: "",
    ...extras,
  };
}

function buildFinalReport(base, rounds, allEvidencePaths, issuesFixed, risks) {
  const pass = rounds.some((round) => round.pass === true);
  const acceptance = rounds.map((round) => ({
    round: round.round,
    passed: round.pass,
    gates: round.stages.map((stage) => ({
      gate: stage.stage,
      passed: stage.success,
      exit_code: stage.exit_code,
      timed_out: stage.timed_out,
    })),
  }));

  return {
    status: pass ? "PASS" : "FAIL",
    workspace: repoRoot,
    rounds,
    pm_quality_history: [],
    leakage_findings: [],
    director_tool_audit: [],
    issues_fixed: issuesFixed,
    acceptance_results: acceptance,
    evidence_paths: [...new Set(allEvidencePaths)],
    next_risks: risks,
    metadata: {
      generated_at: nowIso(),
      profile: "playwright+computer_use+sikulix_airtest",
      ...base,
    },
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.taskFile && !options.prompt) {
    throw new Error(
      "Missing prompt. Usage: npm run test:e2e:hybrid -- \"<one-line task>\" " +
      "or npm run test:e2e:hybrid -- --task-file <task.json>",
    );
  }

  const allowLlmTests = parseBoolEnv("POLARIS_E2E_ALLOW_LLM_TESTS", false);
  const llmSensitive = isLlmSensitiveText(options.prompt) || taskFileLooksLlmSensitive(options.taskFile);
  if (!allowLlmTests && llmSensitive) {
    throw new Error(
      "Blocked LLM-related hybrid task by default. " +
      "Set POLARIS_E2E_ALLOW_LLM_TESTS=1 only when you intentionally need to test LLM settings.",
    );
  }

  const { resolvedConfigPath, config } = loadConfig(options.configPath);
  const playwrightTimeoutMs = parseIntArg(
    config?.playwright?.timeout_ms,
    options.playwrightTimeoutMs,
    "playwright.timeout_ms",
  );
  const semanticTimeoutMs = parseIntArg(
    config?.semantic?.timeout_ms,
    options.semanticTimeoutMs,
    "semantic.timeout_ms",
  );
  const computerUseTimeoutMs = parseIntArg(
    config?.computer_use?.timeout_ms,
    options.computerUseTimeoutMs,
    "computer_use.timeout_ms",
  );
  const omniparserTimeoutMs = parseIntArg(
    config?.vision_fallback?.omniparser?.timeout_ms,
    options.omniparserTimeoutMs,
    "vision_fallback.omniparser.timeout_ms",
  );
  const airtestTimeoutMs = parseIntArg(
    config?.vision_fallback?.airtest?.timeout_ms,
    options.visionTimeoutMs,
    "vision_fallback.airtest.timeout_ms",
  );
  const sikulixTimeoutMs = parseIntArg(
    config?.vision_fallback?.sikulix?.timeout_ms,
    options.visionTimeoutMs,
    "vision_fallback.sikulix.timeout_ms",
  );
  const runId = `hybrid_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const reportOutputPath = options.outputJson
    ? (path.isAbsolute(options.outputJson) ? options.outputJson : path.resolve(repoRoot, options.outputJson))
    : path.join(defaultEvidenceRoot, `${runId}.audit.json`);
  const evidencePayloadPath = path.join(defaultEvidenceRoot, `${runId}.evidence.json`);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const semanticCommand = options.semanticCommand
    || String(process.env.POLARIS_HYBRID_SEMANTIC_CMD || "")
    || String(config?.semantic?.command || "");
  const computerUseCommand = options.computerUseCommand
    || String(process.env.POLARIS_COMPUTER_USE_CMD || "")
    || String(config?.computer_use?.command || "");
  const omniparserCommand = options.omniparserCommand
    || String(process.env.POLARIS_HYBRID_OMNIPARSER_CMD || "")
    || String(config?.vision_fallback?.omniparser?.command || "");
  const airtestCommand = options.airtestCommand
    || String(process.env.POLARIS_AIRTEST_CMD || "")
    || String(config?.vision_fallback?.airtest?.command || "");
  const sikulixCommand = options.sikulixCommand
    || String(process.env.POLARIS_SIKULIX_CMD || "")
    || String(config?.vision_fallback?.sikulix?.command || "");
  const semanticEnabled = config?.semantic?.enabled !== false;
  const computerUseEnabled = config?.computer_use?.enabled !== false;
  const omniparserEnabled = config?.vision_fallback?.omniparser?.enabled !== false;
  const airtestEnabled = config?.vision_fallback?.airtest?.enabled !== false;
  const sikulixEnabled = config?.vision_fallback?.sikulix?.enabled !== false;
  let semanticStageEnabled = semanticEnabled;
  let computerUseStageEnabled = computerUseEnabled;
  const risks = [];

  const configSummary = {
    config_path: toRelativePath(resolvedConfigPath),
    until_pass: options.untilPass,
    max_rounds: options.maxRounds,
    dry_run: options.dryRun,
    has_semantic: Boolean(String(semanticCommand).trim()),
    has_computer_use: Boolean(String(computerUseCommand).trim()),
    has_omniparser: Boolean(String(omniparserCommand).trim()),
    has_airtest: Boolean(String(airtestCommand).trim()),
    has_sikulix: Boolean(String(sikulixCommand).trim()),
    semantic_enabled: semanticEnabled,
    computer_use_enabled: computerUseEnabled,
    omniparser_enabled: omniparserEnabled,
    airtest_enabled: airtestEnabled,
    sikulix_enabled: sikulixEnabled,
    playwright_timeout_ms: playwrightTimeoutMs,
    semantic_timeout_ms: semanticTimeoutMs,
    computer_use_timeout_ms: computerUseTimeoutMs,
    omniparser_timeout_ms: omniparserTimeoutMs,
    airtest_timeout_ms: airtestTimeoutMs,
    sikulix_timeout_ms: sikulixTimeoutMs,
  };

  console.log(`[hybrid] start run_id=${runId}`);
  console.log(`[hybrid] config=${JSON.stringify(configSummary)}`);

  if (options.dryRun) {
    const preview = {
      run_id: runId,
      mode: "dry-run",
      config: configSummary,
      playwright_command: `${npmCommand} ${buildPlaywrightArgs(options).join(" ")}`,
      semantic_command: semanticCommand || null,
      computer_use_command: computerUseCommand || null,
      omniparser_command: omniparserCommand || null,
      airtest_command: airtestCommand || null,
      sikulix_command: sikulixCommand || null,
    };
    writeJsonUtf8(reportOutputPath, preview);
    console.log(`[hybrid] dry-run report: ${toRelativePath(reportOutputPath)}`);
    return;
  }

  // Preflight: fallback stages should not block Playwright main flow.
  const openaiApiKey = String(process.env.OPENAI_API_KEY || "").trim();
  if (computerUseStageEnabled && String(computerUseCommand || "").includes("openai-computer-use-adapter.mjs")) {
    if (!openaiApiKey) {
      computerUseStageEnabled = false;
      risks.push("Computer Use stage disabled: OPENAI_API_KEY is missing.");
    } else {
      const startUrl = String(
        process.env.POLARIS_COMPUTER_USE_START_URL
        || process.env.POLARIS_DEV_SERVER_URL
        || "http://127.0.0.1:5173",
      ).trim();

      if (isLocalHttpUrl(startUrl)) {
        const reachable = await canReachHttpUrl(startUrl, 3500);
        if (!reachable) {
          computerUseStageEnabled = false;
          risks.push(
            `Computer Use stage disabled: start URL is unreachable (${startUrl}). ` +
            "Start renderer (npm run dev:renderer) or set POLARIS_COMPUTER_USE_START_URL.",
          );
        }
      }
    }
  }

  if (semanticStageEnabled && String(semanticCommand || "").includes("run-stagehand-panel-task.mjs")) {
    if (!openaiApiKey) {
      semanticStageEnabled = false;
      risks.push("Semantic stage disabled: OPENAI_API_KEY is missing for Stagehand.");
    }
  }

  if (semanticStageEnabled !== semanticEnabled || computerUseStageEnabled !== computerUseEnabled) {
    console.log(
      `[hybrid] preflight stage override=${JSON.stringify({
        semantic_enabled: semanticStageEnabled,
        computer_use_enabled: computerUseStageEnabled,
      })}`,
    );
  }

  configSummary.semantic_runtime_enabled = semanticStageEnabled;
  configSummary.computer_use_runtime_enabled = computerUseStageEnabled;

  const rounds = [];
  const allEvidencePaths = [];
  const issuesFixed = [];
  let round = 1;
  let pass = false;
  const playOnly = (!semanticStageEnabled || !String(semanticCommand || "").trim())
    && (!computerUseStageEnabled || !String(computerUseCommand || "").trim())
    && (!airtestEnabled || !String(airtestCommand || "").trim())
    && (!sikulixEnabled || !String(sikulixCommand || "").trim());

  while (!pass) {
    const roundRecord = {
      round,
      pass: false,
      stages: [],
    };

    const playwrightStart = nowIso();
    const playwrightRun = await runProcess(npmCommand, buildPlaywrightArgs(options), {
      timeoutMs: playwrightTimeoutMs,
      label: `playwright.main.r${round}`,
    });
    const playwrightEnd = nowIso();
    const playwrightEvidence = collectEvidencePaths(config);
    allEvidencePaths.push(...playwrightEvidence);
    roundRecord.stages.push(
      createStageResult(
        "playwright",
        round,
        `${npmCommand} ${buildPlaywrightArgs(options).join(" ")}`,
        playwrightStart,
        playwrightEnd,
        playwrightRun,
        playwrightEvidence,
      ),
    );

    if (playwrightRun.exitCode === 0 && !playwrightRun.timedOut) {
      roundRecord.pass = true;
      rounds.push(roundRecord);
      pass = true;
      break;
    }

    const evidencePayload = {
      run_id: runId,
      round,
      prompt: options.prompt,
      task_file: options.taskFile || null,
      evidence_paths: playwrightEvidence,
    };
    writeJsonUtf8(evidencePayloadPath, evidencePayload);

    const latestScreenshot = pickLatestImageFromEvidence(playwrightEvidence);
    const omniparserOutputPath = path.join(defaultEvidenceRoot, `${runId}.omniparser.r${String(round).padStart(2, "0")}.json`);
    let commandContext = buildCommandContext(options, evidencePayloadPath, round, playwrightEvidence, {
      last_screenshot: latestScreenshot?.absolute || "",
      omniparser_json: omniparserOutputPath,
    });

    if (semanticStageEnabled && String(semanticCommand || "").trim()) {
      const command = applyTemplate(semanticCommand, commandContext);
      const startAt = nowIso();
      const result = await runShell(command, {
        timeoutMs: semanticTimeoutMs,
        label: `semantic.fallback.r${round}`,
      });
      const endAt = nowIso();
      const evidence = collectEvidencePaths(config);
      allEvidencePaths.push(...evidence);
      roundRecord.stages.push(
        createStageResult("semantic", round, command, startAt, endAt, result, evidence),
      );

      if (result.exitCode === 0 && !result.timedOut) {
        issuesFixed.push({
          round,
          layer: "semantic",
          summary: "Playwright failed; semantic fallback completed successfully.",
        });
        roundRecord.pass = true;
        rounds.push(roundRecord);
        pass = true;
        break;
      }
    } else {
      roundRecord.stages.push({
        stage: "semantic",
        round,
        success: false,
        skipped: true,
        skipped_reason: semanticStageEnabled
          ? "No semantic fallback command configured."
          : "Semantic stage disabled by preflight or config.",
      });
    }

    if (computerUseStageEnabled && String(computerUseCommand || "").trim()) {
      const command = applyTemplate(computerUseCommand, commandContext);
      const startAt = nowIso();
      const result = await runShell(command, {
        timeoutMs: computerUseTimeoutMs,
        label: `computer_use.fallback.r${round}`,
      });
      const endAt = nowIso();
      const evidence = collectEvidencePaths(config);
      allEvidencePaths.push(...evidence);
      roundRecord.stages.push(
        createStageResult("computer_use", round, command, startAt, endAt, result, evidence),
      );

      if (result.exitCode === 0 && !result.timedOut) {
        issuesFixed.push({
          round,
          layer: "computer_use",
          summary: "Playwright failed; computer use fallback completed successfully.",
        });
        roundRecord.pass = true;
        rounds.push(roundRecord);
        pass = true;
        break;
      }
    } else {
      roundRecord.stages.push({
        stage: "computer_use",
        round,
        success: false,
        skipped: true,
        skipped_reason: computerUseStageEnabled
          ? "No computer use command configured."
          : "Computer use stage disabled by preflight or config.",
      });
    }

    if (omniparserEnabled && String(omniparserCommand || "").trim()) {
      const command = applyTemplate(omniparserCommand, commandContext);
      const startAt = nowIso();
      const result = await runShell(command, {
        timeoutMs: omniparserTimeoutMs,
        label: `omniparser.assist.r${round}`,
      });
      const endAt = nowIso();
      const evidence = collectEvidencePaths(config);
      allEvidencePaths.push(...evidence);
      roundRecord.stages.push(
        createStageResult("omniparser", round, command, startAt, endAt, result, evidence, {
          output_json: toRelativePath(omniparserOutputPath),
        }),
      );

      if (result.exitCode !== 0 || result.timedOut) {
        risks.push(`OmniParser assist failed at round ${round}; vision stages continue without structured screen map.`);
      } else {
        commandContext = {
          ...commandContext,
          omniparser_json: omniparserOutputPath,
        };
      }
    } else {
      roundRecord.stages.push({
        stage: "omniparser",
        round,
        success: false,
        skipped: true,
        skipped_reason: omniparserEnabled
          ? "No omniparser command configured."
          : "OmniParser stage disabled by config.",
      });
    }

    let visionRecovered = false;
    const visionStages = [
      { name: "airtest", command: airtestCommand, timeoutMs: airtestTimeoutMs, enabled: airtestEnabled },
      { name: "sikulix", command: sikulixCommand, timeoutMs: sikulixTimeoutMs, enabled: sikulixEnabled },
    ];

    for (const visionStage of visionStages) {
      if (!visionStage.enabled) {
        roundRecord.stages.push({
          stage: visionStage.name,
          round,
          success: false,
          skipped: true,
          skipped_reason: `${visionStage.name} stage disabled by config.`,
        });
        continue;
      }
      const template = String(visionStage.command || "").trim();
      if (!template) {
        roundRecord.stages.push({
          stage: visionStage.name,
          round,
          success: false,
          skipped: true,
          skipped_reason: `No ${visionStage.name} command configured.`,
        });
        continue;
      }

      const command = applyTemplate(template, commandContext);
      const startAt = nowIso();
      const result = await runShell(command, {
        timeoutMs: visionStage.timeoutMs,
        label: `${visionStage.name}.fallback.r${round}`,
      });
      const endAt = nowIso();
      const evidence = collectEvidencePaths(config);
      allEvidencePaths.push(...evidence);
      roundRecord.stages.push(
        createStageResult(visionStage.name, round, command, startAt, endAt, result, evidence),
      );

      if (result.exitCode === 0 && !result.timedOut) {
        issuesFixed.push({
          round,
          layer: visionStage.name,
          summary: `Recovered by ${visionStage.name} fallback after Playwright/Computer Use failure.`,
        });
        visionRecovered = true;
        break;
      }
    }

    if (visionRecovered) {
      roundRecord.pass = true;
      rounds.push(roundRecord);
      pass = true;
      break;
    }

    rounds.push(roundRecord);

    if (playOnly) {
      risks.push("Playwright failed and no fallback command was configured. Configure semantic/computer_use/vision fallback commands.");
      break;
    }

    if (!options.untilPass) {
      risks.push("Stopped after one round because --no-until-pass is enabled.");
      break;
    }

    if (options.maxRounds > 0 && round >= options.maxRounds) {
      risks.push(`Reached max rounds (${options.maxRounds}) without passing all gates.`);
      break;
    }

    round += 1;
  }

  if (pass) {
    console.log(`[hybrid] completed PASS after ${rounds.length} round(s).`);
  } else {
    console.log(`[hybrid] completed FAIL after ${rounds.length} round(s).`);
  }

  if (!String(semanticCommand || "").trim()) {
    if (semanticEnabled) {
      risks.push("Semantic fallback is not configured.");
    }
  }
  if (!String(computerUseCommand || "").trim()) {
    if (computerUseEnabled) {
      risks.push("Computer Use fallback is not configured.");
    }
  }
  if (!String(omniparserCommand || "").trim()) {
    if (omniparserEnabled) {
      risks.push("OmniParser assist stage is not configured.");
    }
  }
  if ((airtestEnabled && !String(airtestCommand || "").trim())
    && (sikulixEnabled && !String(sikulixCommand || "").trim())) {
    risks.push("Neither Airtest nor SikuliX fallback command is configured.");
  }

  const finalReport = buildFinalReport(
    {
      run_id: runId,
      profile: "playwright-main+semantic+computer_use+omniparser+vision_fallback",
      config_path: toRelativePath(resolvedConfigPath),
      prompt: options.prompt || null,
      task_file: options.taskFile || null,
    },
    rounds,
    allEvidencePaths,
    issuesFixed,
    risks,
  );

  writeJsonUtf8(reportOutputPath, finalReport);
  console.log(`[hybrid] audit report: ${toRelativePath(reportOutputPath)}`);

  if (finalReport.status !== "PASS") {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`[hybrid] fatal: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
