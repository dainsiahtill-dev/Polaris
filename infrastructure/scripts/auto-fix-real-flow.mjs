import fs from "fs";
import path from "path";
import { spawn } from "child_process";
import { fileURLToPath } from "url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const logsDir = path.join(repoRoot, ".polaris", "logs");
const promptContractPath = path.join(repoRoot, "docs", "prompt", "元设计师-自动化测试v5.1.md");
const realFlowSpecRelativePath = path.join("tests", "electron", "pm-director-real-flow.spec.ts");
const realFlowSpecPath = path.join(repoRoot, realFlowSpecRelativePath);

function nowIso() {
  return new Date().toISOString();
}

function toUnixPath(filePath) {
  return String(filePath || "").split(path.sep).join("/");
}

function toRelative(filePath) {
  return toUnixPath(path.relative(repoRoot, filePath));
}

function ensureDirectory(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function parseBooleanEnv(name, fallback) {
  const raw = String(process.env[name] || "").trim().toLowerCase();
  if (!raw) {
    return fallback;
  }
  return raw === "1" || raw === "true" || raw === "yes";
}

function parseNonNegativeInt(raw, fallback, label) {
  if (raw === undefined || raw === null || raw === "") {
    return fallback;
  }
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer. Received: ${raw}`);
  }
  return parsed;
}

function writeUtf8(filePath, content) {
  ensureDirectory(path.dirname(filePath));
  fs.writeFileSync(filePath, String(content), { encoding: "utf-8" });
}

function readUtf8(filePath) {
  return fs.readFileSync(filePath, "utf-8").replace(/^\uFEFF/, "");
}

function quoteForDisplay(value) {
  const text = String(value ?? "");
  if (!text) {
    return "\"\"";
  }
  if (/[\s"]/u.test(text)) {
    return `"${text.replace(/"/g, "\\\"")}"`;
  }
  return text;
}

function parseArgs(argv) {
  const options = {
    dryRun: false,
    skipBuild: parseBooleanEnv("KERNELONE_REAL_FLOW_AUTOFIX_SKIP_BUILD", false),
    maxFixAttempts: parseNonNegativeInt(
      process.env.KERNELONE_REAL_FLOW_AUTOFIX_MAX_ATTEMPTS,
      2,
      "KERNELONE_REAL_FLOW_AUTOFIX_MAX_ATTEMPTS",
    ),
    claudeModel: String(process.env.KERNELONE_CLAUDE_MODEL || "").trim() || undefined,
    permissionMode: String(process.env.KERNELONE_CLAUDE_PERMISSION_MODE || "").trim() || "bypassPermissions",
    agentName: String(process.env.KERNELONE_CLAUDE_AGENT || "").trim() || undefined,
    allowedTools: String(process.env.KERNELONE_CLAUDE_ALLOWED_TOOLS || "").trim() || undefined,
    noSessionPersistence: parseBooleanEnv("KERNELONE_CLAUDE_NO_SESSION_PERSISTENCE", true),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    if (arg === "--skip-build") {
      options.skipBuild = true;
      continue;
    }
    if (arg === "--max-attempts") {
      const raw = argv[index + 1];
      if (raw === undefined) {
        throw new Error("Missing value for --max-attempts");
      }
      options.maxFixAttempts = parseNonNegativeInt(raw, options.maxFixAttempts, "--max-attempts");
      index += 1;
      continue;
    }
    if (arg.startsWith("--max-attempts=")) {
      options.maxFixAttempts = parseNonNegativeInt(
        arg.slice("--max-attempts=".length),
        options.maxFixAttempts,
        "--max-attempts",
      );
      continue;
    }
    if (arg === "--model") {
      const raw = String(argv[index + 1] || "").trim();
      if (!raw) {
        throw new Error("Missing value for --model");
      }
      options.claudeModel = raw;
      index += 1;
      continue;
    }
    if (arg.startsWith("--model=")) {
      const raw = String(arg.slice("--model=".length)).trim();
      if (!raw) {
        throw new Error("Missing value for --model");
      }
      options.claudeModel = raw;
      continue;
    }
    if (arg === "--permission-mode") {
      const raw = String(argv[index + 1] || "").trim();
      if (!raw) {
        throw new Error("Missing value for --permission-mode");
      }
      options.permissionMode = raw;
      index += 1;
      continue;
    }
    if (arg.startsWith("--permission-mode=")) {
      const raw = String(arg.slice("--permission-mode=".length)).trim();
      if (!raw) {
        throw new Error("Missing value for --permission-mode");
      }
      options.permissionMode = raw;
      continue;
    }
    if (arg === "--agent") {
      const raw = String(argv[index + 1] || "").trim();
      if (!raw) {
        throw new Error("Missing value for --agent");
      }
      options.agentName = raw;
      index += 1;
      continue;
    }
    if (arg.startsWith("--agent=")) {
      const raw = String(arg.slice("--agent=".length)).trim();
      if (!raw) {
        throw new Error("Missing value for --agent");
      }
      options.agentName = raw;
      continue;
    }
    if (arg === "--allowed-tools") {
      const raw = String(argv[index + 1] || "").trim();
      if (!raw) {
        throw new Error("Missing value for --allowed-tools");
      }
      options.allowedTools = raw;
      index += 1;
      continue;
    }
    if (arg.startsWith("--allowed-tools=")) {
      const raw = String(arg.slice("--allowed-tools=".length)).trim();
      if (!raw) {
        throw new Error("Missing value for --allowed-tools");
      }
      options.allowedTools = raw;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }

  return options;
}

function createRunLog() {
  const nonce = `autofix_real_flow_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const logPath = path.join(logsDir, `${nonce}.log`);
  const lines = [];

  function record(message) {
    const line = `${nowIso()} ${message}`;
    lines.push(line);
    console.log(message);
  }

  function flush() {
    writeUtf8(logPath, `${lines.join("\n")}\n`);
  }

  return { nonce, logPath, record, flush };
}

function collectFiles(rootPath, maxDepth, collector) {
  if (!fs.existsSync(rootPath)) {
    return;
  }

  const stack = [{ current: rootPath, depth: 0 }];
  while (stack.length > 0) {
    const item = stack.pop();
    if (!item) {
      continue;
    }

    let entries = [];
    try {
      entries = fs.readdirSync(item.current, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      const fullPath = path.join(item.current, entry.name);
      if (entry.isDirectory()) {
        if (item.depth < maxDepth) {
          stack.push({ current: fullPath, depth: item.depth + 1 });
        }
        continue;
      }
      collector(fullPath);
    }
  }
}

function listFailureArtifacts() {
  const roots = [
    path.join(repoRoot, "test-results", "electron"),
    path.join(repoRoot, "playwright-report"),
    logsDir,
  ];
  const allowedExtensions = new Set([".png", ".jpg", ".jpeg", ".webp", ".zip", ".json", ".jsonl", ".md", ".log", ".txt", ".webm"]);
  const collected = [];

  for (const rootPath of roots) {
    collectFiles(rootPath, 6, (candidatePath) => {
      const name = path.basename(candidatePath).toLowerCase();
      const ext = path.extname(candidatePath).toLowerCase();
      if (
        name !== "error-context.md"
        && name !== "trace.zip"
        && name !== "renderer-errors.txt"
        && !allowedExtensions.has(ext)
      ) {
        return;
      }
      try {
        const stat = fs.statSync(candidatePath);
        collected.push({ filePath: candidatePath, mtimeMs: stat.mtimeMs });
      } catch {
        // Ignore unreadable files.
      }
    });
  }

  collected.sort((left, right) => right.mtimeMs - left.mtimeMs);
  return collected;
}

function loadPromptContract() {
  if (!fs.existsSync(promptContractPath)) {
    return {
      reference: "",
      content: "",
    };
  }
  return {
    reference: toRelative(promptContractPath),
    content: readUtf8(promptContractPath).trim(),
  };
}

function buildClaudePrompt(round, maxFixAttempts, artifactPaths) {
  const contract = loadPromptContract();
  const artifactLines = artifactPaths.length > 0
    ? artifactPaths.map((item) => `- ${item}`).join("\n")
    : "- test-results/electron/** (no specific file was detected yet)";
  const realFlowCommand = `set KERNELONE_E2E_USE_REAL_SETTINGS=1 && npm run test:e2e -- ${toUnixPath(realFlowSpecRelativePath)}`;
  const lines = [
    "You are running a supervised Polaris repair round for the real PM/Director E2E flow.",
    `Round: ${round}/${maxFixAttempts}`,
    "",
    "Mandatory operating contract:",
    "- Use UTF-8 for every text file you read or write.",
    "- Only modify Polaris source code inside this repository.",
    "- Do not modify generated target-project code under C:/Temp; treat it as test evidence only.",
    "- Fix root causes, not superficial patches.",
    "- After every code change, reproduce and re-test.",
    "",
    "Current failing gate:",
    `- ${realFlowCommand}`,
    "",
    "Execution requirements:",
    "1. Read the newest failure evidence first (error-context, renderer-errors, trace, screenshots, logs).",
    "2. Diagnose whether the failure is caused by configuration, prompt leakage, PM task quality, tool authorization, or runtime implementation.",
    "3. Apply the smallest sufficient fix in Polaris.",
    "4. Re-run `npm run build` unless it is already passing and unchanged.",
    `5. Re-run \`${realFlowCommand}\` until this round is green or you hit a concrete blocker.`,
    "6. If you still fail, stop with a precise root-cause summary and the next most likely fix.",
    "",
    "Known real-flow assertions in the Playwright spec:",
    "- PM workspace opens and `pm-workspace-run-once` starts `/v2/pm/status`.",
    "- `/state/snapshot` must contain tasks, completed_task_count, and last_director_status.",
    "- `integration_qa.result.json` must have a recognized reason.",
    "- Director workspace must enter RUNNING and create tasks linked by `metadata.pm_task_id`.",
    "",
    "Failure evidence (newest first):",
    artifactLines,
    "",
    "When you finish, print a compact JSON object with this shape:",
    "{\"status\":\"PASS|FAIL\",\"round\":1,\"issues_fixed\":[],\"tests_run\":[],\"evidence_paths\":[],\"next_risks\":[]}",
  ];

  if (contract.content) {
    lines.splice(
      2,
      0,
      `Base contract source: ${contract.reference}`,
      "",
      contract.content,
      "",
    );
  } else {
    lines.splice(
      2,
      0,
      "Base contract source: (missing)",
      "The prompt contract file is missing; follow the instructions in this prompt as the source of truth.",
      "",
    );
  }

  return lines.join("\n");
}

function buildClaudeArgs(options) {
  const args = [
    "-p",
    "--input-format",
    "text",
    "--output-format",
    "json",
    "--add-dir",
    repoRoot,
  ];

  if (options.noSessionPersistence) {
    args.push("--no-session-persistence");
  }
  if (options.permissionMode) {
    args.push("--permission-mode", options.permissionMode);
    if (options.permissionMode === "bypassPermissions") {
      args.push("--allow-dangerously-skip-permissions");
    }
  }
  if (options.agentName) {
    args.push("--agent", options.agentName);
  }
  if (options.claudeModel) {
    args.push("--model", options.claudeModel);
  }
  if (options.allowedTools) {
    args.push("--allowedTools", options.allowedTools);
  }
  return args;
}

function runProcess(command, args, options = {}) {
  const {
    env = process.env,
    cwd = repoRoot,
    timeoutMs = 0,
    captureOutput = false,
    stdinText = "",
    log,
    label,
  } = options;

  return new Promise((resolve, reject) => {
    const displayCommand = `${command} ${args.map((item) => quoteForDisplay(item)).join(" ")}`.trim();
    if (log && label) {
      log.record(`[auto-fix-real-flow] run ${label}: ${displayCommand}`);
    }

    const spawnCommand = process.platform === "win32" ? "cmd.exe" : command;
    const spawnArgs = process.platform === "win32"
      ? ["/d", "/s", "/c", command, ...args]
      : args;
    const usePipedInput = captureOutput || String(stdinText).length > 0;
    const child = spawn(spawnCommand, spawnArgs, {
      cwd,
      env,
      windowsHide: true,
      stdio: captureOutput ? ["pipe", "pipe", "pipe"] : (usePipedInput ? ["pipe", "inherit", "inherit"] : "inherit"),
    });

    let timedOut = false;
    let timer = null;
    let stdout = "";
    let stderr = "";

    if (captureOutput) {
      child.stdout?.setEncoding("utf8");
      child.stderr?.setEncoding("utf8");
      child.stdout?.on("data", (chunk) => {
        stdout += String(chunk);
      });
      child.stderr?.on("data", (chunk) => {
        stderr += String(chunk);
      });
    }

    if (String(stdinText).length > 0 && child.stdin) {
      child.stdin.setDefaultEncoding("utf8");
      child.stdin.write(String(stdinText));
      child.stdin.end();
    }

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        timedOut = true;
        child.kill();
      }, timeoutMs);
    }

    child.on("error", (error) => {
      if (timer) {
        clearTimeout(timer);
      }
      reject(error);
    });

    child.on("exit", (code) => {
      if (timer) {
        clearTimeout(timer);
      }
      resolve({
        exitCode: code ?? 1,
        timedOut,
        stdout,
        stderr,
      });
    });
  });
}

function createAttemptRecord(attempt, totalAttempts) {
  return {
    attempt,
    total_attempts: totalAttempts,
    build: null,
    test: null,
    agent: null,
    artifact_paths: [],
    prompt_path: null,
    agent_output_path: null,
  };
}

async function main() {
  if (!fs.existsSync(realFlowSpecPath)) {
    throw new Error(`Real-flow Playwright spec not found: ${toRelative(realFlowSpecPath)}`);
  }

  const options = parseArgs(process.argv.slice(2));
  const runLog = createRunLog();
  const auditPath = path.join(logsDir, `${runLog.nonce}.audit.json`);
  const totalAttempts = options.maxFixAttempts + 1;
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const claudeCommand = process.platform === "win32" ? "claude.exe" : "claude";
  const testArgs = ["run", "test:e2e", "--", toUnixPath(realFlowSpecRelativePath)];
  const buildArgs = ["run", "build"];
  const testEnv = {
    ...process.env,
    KERNELONE_E2E_USE_REAL_SETTINGS: "1",
  };
  const auditPayload = {
    status: "FAIL",
    workspace: repoRoot,
    rounds: [],
    settings: {
      skip_build: options.skipBuild,
      max_fix_attempts: options.maxFixAttempts,
      claude_model: options.claudeModel || null,
      permission_mode: options.permissionMode,
      claude_agent: options.agentName || null,
      allowed_tools: options.allowedTools || null,
      no_session_persistence: options.noSessionPersistence,
      prompt_contract: loadPromptContract().reference || null,
      real_flow_spec: toUnixPath(realFlowSpecRelativePath),
    },
    evidence_paths: [],
    run_log_path: toRelative(runLog.logPath),
    audit_path: toRelative(auditPath),
    generated_at: nowIso(),
  };

  runLog.record(`[auto-fix-real-flow] start nonce=${runLog.nonce}`);
  runLog.record(`[auto-fix-real-flow] total_attempts=${totalAttempts} skipBuild=${options.skipBuild ? "1" : "0"}`);
  runLog.record(`[auto-fix-real-flow] permissionMode=${options.permissionMode} model=${options.claudeModel || "(default)"}`);

  try {
    if (options.dryRun) {
      const preview = {
        ...auditPayload,
        status: "DRY_RUN",
        preview: {
          build_command: `${npmCommand} ${buildArgs.join(" ")}`,
          test_command: `set KERNELONE_E2E_USE_REAL_SETTINGS=1 && ${npmCommand} ${testArgs.join(" ")}`,
          claude_command: `${claudeCommand} ${buildClaudeArgs(options).map((item) => quoteForDisplay(item)).join(" ")} < stdin:prompt`,
        },
      };
      writeUtf8(auditPath, `${JSON.stringify(preview, null, 2)}\n`);
      runLog.record(`[auto-fix-real-flow] dry-run audit: ${toRelative(auditPath)}`);
      return;
    }

    let passed = false;

    for (let attempt = 1; attempt <= totalAttempts; attempt += 1) {
      const attemptRecord = createAttemptRecord(attempt, totalAttempts);
      auditPayload.rounds.push(attemptRecord);
      runLog.record(`[auto-fix-real-flow] attempt ${attempt}/${totalAttempts}`);

      let buildPassed = true;
      if (!options.skipBuild) {
        const buildStartAt = nowIso();
        const buildResult = await runProcess(npmCommand, buildArgs, {
          env: process.env,
          log: runLog,
          label: "build",
        });
        const buildEndAt = nowIso();
        buildPassed = buildResult.exitCode === 0 && !buildResult.timedOut;
        attemptRecord.build = {
          started_at: buildStartAt,
          ended_at: buildEndAt,
          exit_code: buildResult.exitCode,
          timed_out: buildResult.timedOut,
          success: buildPassed,
        };
      } else {
        attemptRecord.build = {
          skipped: true,
          success: true,
        };
      }

      let testPassed = false;
      if (buildPassed) {
        const testStartAt = nowIso();
        const testResult = await runProcess(npmCommand, testArgs, {
          env: testEnv,
          log: runLog,
          label: "real-flow-test",
        });
        const testEndAt = nowIso();
        testPassed = testResult.exitCode === 0 && !testResult.timedOut;
        attemptRecord.test = {
          started_at: testStartAt,
          ended_at: testEndAt,
          exit_code: testResult.exitCode,
          timed_out: testResult.timedOut,
          success: testPassed,
        };
      } else {
        attemptRecord.test = {
          skipped: true,
          success: false,
          skipped_reason: "build_failed",
        };
      }

      const artifacts = listFailureArtifacts().slice(0, 16).map((item) => toRelative(item.filePath));
      attemptRecord.artifact_paths = artifacts;
      auditPayload.evidence_paths.push(...artifacts);

      if (buildPassed && testPassed) {
        passed = true;
        auditPayload.status = "PASS";
        runLog.record(`[auto-fix-real-flow] flow passed on attempt ${attempt}`);
        break;
      }

      if (attempt >= totalAttempts) {
        runLog.record(`[auto-fix-real-flow] attempts exhausted after failing attempt ${attempt}`);
        break;
      }

      const promptText = buildClaudePrompt(attempt, options.maxFixAttempts, artifacts);
      const promptPath = path.join(logsDir, `${runLog.nonce}.prompt.r${String(attempt).padStart(2, "0")}.md`);
      const agentOutputPath = path.join(logsDir, `${runLog.nonce}.claude.r${String(attempt).padStart(2, "0")}.json`);
      writeUtf8(promptPath, `${promptText}\n`);
      attemptRecord.prompt_path = toRelative(promptPath);
      attemptRecord.agent_output_path = toRelative(agentOutputPath);

      const claudeArgs = buildClaudeArgs(options);
      const agentStartAt = nowIso();
      const agentResult = await runProcess(claudeCommand, claudeArgs, {
        env: process.env,
        captureOutput: true,
        stdinText: promptText,
        timeoutMs: 60 * 60 * 1000,
        log: runLog,
        label: `claude-repair-r${attempt}`,
      });
      const agentEndAt = nowIso();

      writeUtf8(agentOutputPath, agentResult.stdout || agentResult.stderr || "");
      attemptRecord.agent = {
        started_at: agentStartAt,
        ended_at: agentEndAt,
        exit_code: agentResult.exitCode,
        timed_out: agentResult.timedOut,
        success: agentResult.exitCode === 0 && !agentResult.timedOut,
        output_path: toRelative(agentOutputPath),
      };

      if (agentResult.exitCode !== 0 || agentResult.timedOut) {
        runLog.record(
          `[auto-fix-real-flow] claude repair failed at attempt ${attempt} ` +
          `exit=${agentResult.exitCode} timedOut=${agentResult.timedOut ? "1" : "0"}`,
        );
        break;
      }
    }

    auditPayload.evidence_paths = [...new Set(auditPayload.evidence_paths)];
    writeUtf8(auditPath, `${JSON.stringify(auditPayload, null, 2)}\n`);

    if (!passed) {
      process.exitCode = 1;
    }
  } finally {
    runLog.flush();
    console.log(`[auto-fix-real-flow] run log: ${toRelative(runLog.logPath)}`);
    console.log(`[auto-fix-real-flow] audit: ${toRelative(auditPath)}`);
  }
}

main().catch((error) => {
  console.error(`[auto-fix-real-flow] fatal: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
