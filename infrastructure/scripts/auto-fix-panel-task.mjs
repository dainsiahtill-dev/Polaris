import fs from "fs";
import os from "os";
import path from "path";
import { spawn } from "child_process";
import { fileURLToPath } from "url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const logsDir = path.join(os.homedir(), ".polaris", "logs");

function nowIso() {
  return new Date().toISOString();
}

function toUnixPath(filePath) {
  return filePath.split(path.sep).join("/");
}

function toRelative(filePath) {
  return toUnixPath(path.relative(repoRoot, filePath));
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

function parseArgs(argv) {
  const options = {
    dryRun: false,
    skipBuild: parseBooleanEnv("E2E_PANEL_AUTOFIX_SKIP_BUILD", false),
    maxFixAttempts: parseNonNegativeInt(
      process.env.E2E_PANEL_AUTOFIX_MAX_ATTEMPTS,
      2,
      "E2E_PANEL_AUTOFIX_MAX_ATTEMPTS",
    ),
    codexModel: String(process.env.E2E_PANEL_CODEX_MODEL || "").trim() || undefined,
    codexDangerousBypass: parseBooleanEnv("E2E_PANEL_CODEX_DANGEROUS", false),
    prompt: "",
  };

  const promptParts = [];
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
    if (arg === "--dangerous-codex") {
      options.codexDangerousBypass = true;
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
      const raw = arg.slice("--max-attempts=".length);
      options.maxFixAttempts = parseNonNegativeInt(raw, options.maxFixAttempts, "--max-attempts");
      continue;
    }
    if (arg === "--model") {
      const raw = String(argv[index + 1] || "").trim();
      if (!raw) {
        throw new Error("Missing value for --model");
      }
      options.codexModel = raw;
      index += 1;
      continue;
    }
    if (arg.startsWith("--model=")) {
      const raw = String(arg.slice("--model=".length)).trim();
      if (!raw) {
        throw new Error("Missing value for --model");
      }
      options.codexModel = raw;
      continue;
    }
    promptParts.push(arg);
  }

  options.prompt = promptParts.join(" ").trim() || String(process.env.E2E_PANEL_TASK_PROMPT || "").trim();
  return options;
}

function createRunLog() {
  const nonce = `autofix_panel_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const logPath = path.join(logsDir, `${nonce}.log`);
  const lines = [];

  function record(message) {
    const line = `${nowIso()} ${message}`;
    lines.push(line);
    console.log(message);
  }

  function flush() {
    fs.mkdirSync(logsDir, { recursive: true });
    fs.writeFileSync(logPath, `${lines.join("\n")}\n`, { encoding: "utf-8" });
  }

  return { nonce, logPath, record, flush };
}

function quoteForCmd(value) {
  const text = String(value);
  if (text.length === 0) {
    return "\"\"";
  }
  return `"${text.replace(/"/g, "\"\"")}"`;
}

function runProcess(command, args, log, label) {
  return new Promise((resolve, reject) => {
    log.record(`[auto-fix] run ${label}: ${command} ${args.map((item) => quoteForCmd(item)).join(" ")}`);
    const child = process.platform === "win32"
      ? spawn(
        "cmd.exe",
        ["/d", "/s", "/c", command, ...args],
        {
          cwd: repoRoot,
          env: process.env,
          stdio: "inherit",
        },
      )
      : spawn(command, args, {
        cwd: repoRoot,
        env: process.env,
        stdio: "inherit",
      });

    child.on("error", (error) => {
      reject(error);
    });
    child.on("exit", (code) => {
      resolve(code ?? 1);
    });
  });
}

function listFailureArtifacts() {
  const rootDir = path.join(repoRoot, "test-results", "electron");
  if (!fs.existsSync(rootDir)) {
    return [];
  }

  const collected = [];
  const stack = [rootDir];

  while (stack.length > 0) {
    const currentDir = stack.pop();
    if (!currentDir) {
      continue;
    }
    const entries = fs.readdirSync(currentDir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(currentDir, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }
      const name = entry.name.toLowerCase();
      if (
        name === "error-context.md" ||
        name === "trace.zip" ||
        name.endsWith(".png") ||
        name === "renderer-errors.txt"
      ) {
        const stat = fs.statSync(fullPath);
        collected.push({ filePath: fullPath, mtimeMs: stat.mtimeMs });
      }
    }
  }

  collected.sort((left, right) => right.mtimeMs - left.mtimeMs);
  return collected;
}

function buildCodexPrompt(prompt, fixRound, maxFixAttempts, artifactPaths) {
  const artifactLines = artifactPaths.length > 0
    ? artifactPaths.map((item) => `- ${item}`).join("\n")
    : "- test-results/electron/** (未找到明确文件，请先检索失败目录)";

  return [
    "你在 Polaris 仓库中执行自动修复任务。",
    `用户一句话任务：${prompt}`,
    "",
    "当前失败命令：",
    `npm run test:e2e:task -- "${prompt}"`,
    "",
    `自动修复轮次：${fixRound}/${maxFixAttempts}`,
    "",
    "要求：",
    "1. 先读取最新失败证据（优先 error-context.md、trace.zip、失败截图）。",
    "2. 仅做最小必要改动，禁止顺手重构。",
    "3. 修改后必须执行并确保通过：",
    "   - npm run build",
    `   - npm run test:e2e:task -- "${prompt}"`,
    "4. 如果仍失败，明确下一步最可能修复点。",
    "",
    "可用失败证据：",
    artifactLines,
  ].join("\n");
}

function printDryRun(options, logPath) {
  console.log(`[auto-fix] dry-run enabled`);
  console.log(`[auto-fix] prompt: ${options.prompt}`);
  console.log(`[auto-fix] maxFixAttempts: ${options.maxFixAttempts}`);
  console.log(`[auto-fix] skipBuild: ${options.skipBuild ? "1" : "0"}`);
  console.log(`[auto-fix] codexModel: ${options.codexModel || "(default)"}`);
  console.log(`[auto-fix] codexDangerousBypass: ${options.codexDangerousBypass ? "1" : "0"}`);
  console.log(`[auto-fix] run log path: ${toRelative(logPath)}`);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.prompt) {
    throw new Error("Missing prompt. Usage: npm run auto:fix:panel -- \"<one-line task>\" [--max-attempts 2]");
  }

  const runLog = createRunLog();
  runLog.record(`[auto-fix] start nonce=${runLog.nonce}`);
  runLog.record(`[auto-fix] prompt=${options.prompt}`);
  runLog.record(`[auto-fix] maxFixAttempts=${options.maxFixAttempts} skipBuild=${options.skipBuild ? "1" : "0"}`);
  runLog.record(`[auto-fix] codexModel=${options.codexModel || "(default)"} codexDangerousBypass=${options.codexDangerousBypass ? "1" : "0"}`);

  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const codexCommand = process.platform === "win32" ? "codex.cmd" : "codex";

  let passed = false;
  try {
    if (options.dryRun) {
      printDryRun(options, runLog.logPath);
      runLog.record(`[auto-fix] dry-run completed`);
      return;
    }

    const totalTestAttempts = options.maxFixAttempts + 1;
    for (let testAttempt = 1; testAttempt <= totalTestAttempts; testAttempt += 1) {
      runLog.record(`[auto-fix] test attempt ${testAttempt}/${totalTestAttempts}`);

      if (!options.skipBuild) {
        const buildExit = await runProcess(npmCommand, ["run", "build"], runLog, "build");
        if (buildExit !== 0) {
          runLog.record(`[auto-fix] build failed with exit code ${buildExit}`);
          throw new Error(`Build failed before test attempt ${testAttempt}.`);
        }
      }

      const taskExit = await runProcess(
        npmCommand,
        ["run", "test:e2e:task", "--", options.prompt],
        runLog,
        "test:e2e:task",
      );
      if (taskExit === 0) {
        passed = true;
        runLog.record(`[auto-fix] task passed on attempt ${testAttempt}`);
        break;
      }

      runLog.record(`[auto-fix] task failed on attempt ${testAttempt} with exit code ${taskExit}`);
      if (testAttempt >= totalTestAttempts) {
        break;
      }

      const artifacts = listFailureArtifacts().slice(0, 8).map((item) => toRelative(item.filePath));
      const codexPrompt = buildCodexPrompt(options.prompt, testAttempt, options.maxFixAttempts, artifacts);
      const codexArgs = ["exec", "--cd", repoRoot, "--full-auto"];
      if (options.codexDangerousBypass) {
        codexArgs.push("--dangerously-bypass-approvals-and-sandbox");
      }
      if (options.codexModel) {
        codexArgs.push("--model", options.codexModel);
      }
      codexArgs.push(codexPrompt);

      runLog.record(`[auto-fix] invoking codex repair round ${testAttempt}/${options.maxFixAttempts}`);
      const codexExit = await runProcess(codexCommand, codexArgs, runLog, "codex exec");
      if (codexExit !== 0) {
        runLog.record(`[auto-fix] codex exec failed with exit code ${codexExit}`);
        throw new Error(`Codex repair failed at round ${testAttempt}.`);
      }
    }

    runLog.record(`[auto-fix] completed status=${passed ? "passed" : "failed"}`);
    if (!passed) {
      const latestArtifacts = listFailureArtifacts().slice(0, 8).map((item) => toRelative(item.filePath));
      if (latestArtifacts.length > 0) {
        console.error(`[auto-fix] latest failure artifacts:\n${latestArtifacts.map((item) => `- ${item}`).join("\n")}`);
      }
      process.exitCode = 1;
    }
  } finally {
    runLog.flush();
    console.log(`[auto-fix] run log: ${toRelative(runLog.logPath)}`);
  }
}

main().catch((error) => {
  console.error(`[auto-fix] fatal: ${error.message}`);
  process.exit(1);
});
