import fs from "fs";
import os from "os";
import path from "path";
import { spawn } from "child_process";
import { fileURLToPath } from "url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const logsRoot = path.join(os.homedir(), ".polaris", "logs");

function nowIso() {
  return new Date().toISOString();
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function parseBool(raw, fallback) {
  if (raw === undefined || raw === null || raw === "") return fallback;
  const token = String(raw).trim().toLowerCase();
  return token === "1" || token === "true" || token === "yes";
}

function parseIntArg(raw, fallback, label) {
  if (raw === undefined || raw === null || raw === "") return fallback;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer. Received: ${raw}`);
  }
  return parsed;
}

function readJsonUtf8(filePath) {
  const raw = fs.readFileSync(filePath, "utf-8").replace(/^\uFEFF/, "");
  return JSON.parse(raw);
}

function writeJsonUtf8(filePath, payload) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf-8" });
}

function quoteForShell(value) {
  const text = String(value || "");
  if (!text) return "\"\"";
  return `"${text.replace(/"/g, "\"\"")}"`;
}

function runShell(commandText, options = {}) {
  const { timeoutMs = 0, cwd = repoRoot, env = process.env, label = "" } = options;
  return new Promise((resolve, reject) => {
    if (label) {
      console.log(`[semantic] run ${label}: ${commandText}`);
    } else {
      console.log(`[semantic] run shell: ${commandText}`);
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

function parseArgs(argv) {
  const options = {
    prompt: "",
    taskFile: "",
    evidenceJson: "",
    outputJson: "",
    round: parseIntArg(process.env.KERNELONE_STAGEHAND_ROUND, 1, "KERNELONE_STAGEHAND_ROUND"),
    model: String(process.env.KERNELONE_STAGEHAND_MODEL || "gpt-4.1-mini").trim(),
    apiKey: String(process.env.OPENAI_API_KEY || "").trim(),
    baseUrl: String(process.env.OPENAI_BASE_URL || "").trim(),
    startUrl: String(
      process.env.KERNELONE_STAGEHAND_START_URL
      || process.env.KERNELONE_DEV_SERVER_URL
      || process.env.KERNELONE_COMPUTER_USE_START_URL
      || "http://127.0.0.1:5173",
    ).trim(),
    timeoutMs: parseIntArg(process.env.KERNELONE_STAGEHAND_TIMEOUT_MS, 12 * 60 * 1000, "KERNELONE_STAGEHAND_TIMEOUT_MS"),
    width: parseIntArg(process.env.KERNELONE_STAGEHAND_WIDTH, 1280, "KERNELONE_STAGEHAND_WIDTH"),
    height: parseIntArg(process.env.KERNELONE_STAGEHAND_HEIGHT, 800, "KERNELONE_STAGEHAND_HEIGHT"),
    headless: parseBool(process.env.KERNELONE_STAGEHAND_HEADLESS, true),
    verifyCommand: String(process.env.KERNELONE_STAGEHAND_VERIFY_CMD || "").trim(),
    dryRun: false,
  };

  const trailingPrompt = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") {
      options.dryRun = true;
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
    if (arg === "--evidence-json") {
      options.evidenceJson = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--output-json") {
      options.outputJson = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--round") {
      options.round = parseIntArg(argv[index + 1], options.round, "--round");
      index += 1;
      continue;
    }
    if (arg === "--model") {
      options.model = String(argv[index + 1] || "").trim() || options.model;
      index += 1;
      continue;
    }
    if (arg === "--start-url") {
      options.startUrl = String(argv[index + 1] || "").trim() || options.startUrl;
      index += 1;
      continue;
    }
    if (arg === "--verify-command") {
      options.verifyCommand = String(argv[index + 1] || "").trim();
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

function loadPromptFromTaskFile(taskFile) {
  if (!taskFile) return "";
  const resolved = path.isAbsolute(taskFile) ? taskFile : path.resolve(repoRoot, taskFile);
  const payload = readJsonUtf8(resolved);
  const prompt = String(payload?.prompt || "").trim();
  if (prompt) return prompt;

  const navigation = Array.isArray(payload?.navigationSteps)
    ? payload.navigationSteps.map((item) => String(item?.name || "").trim()).filter(Boolean).join(" -> ")
    : "";
  const action = String(payload?.fieldAction?.name || "").trim();
  return [navigation, action].filter(Boolean).join(" | ");
}

async function loadStagehandCtor() {
  let moduleNs = null;
  try {
    moduleNs = await import("@browserbasehq/stagehand");
  } catch (error) {
    throw new Error(
      "Stagehand package is not installed. Run: npm install @browserbasehq/stagehand\n" +
      `Original error: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  const ctor = moduleNs?.Stagehand
    || moduleNs?.V3
    || moduleNs?.default?.Stagehand
    || moduleNs?.default?.V3
    || moduleNs?.default;
  if (typeof ctor !== "function") {
    throw new Error("Failed to resolve Stagehand constructor from @browserbasehq/stagehand.");
  }
  return ctor;
}

function summarizeObserveResult(observeResult, limit = 16) {
  if (!Array.isArray(observeResult)) return [];
  return observeResult
    .slice(0, limit)
    .map((item, index) => {
      const description = String(item?.description || item?.instruction || item?.selector || "").trim();
      const method = String(item?.method || item?.action || "").trim();
      return {
        index,
        method,
        description,
      };
    });
}

function appendEvidencePath(evidenceJsonPath, targetPath) {
  if (!evidenceJsonPath) return;
  const resolvedEvidence = path.isAbsolute(evidenceJsonPath)
    ? evidenceJsonPath
    : path.resolve(repoRoot, evidenceJsonPath);
  if (!fs.existsSync(resolvedEvidence)) return;

  try {
    const payload = readJsonUtf8(resolvedEvidence);
    if (!payload || typeof payload !== "object") return;
    const items = Array.isArray(payload.evidence_paths) ? payload.evidence_paths : [];
    const relative = path.relative(repoRoot, targetPath).split(path.sep).join("/");
    if (!items.includes(relative)) {
      items.push(relative);
    }
    payload.evidence_paths = items;
    writeJsonUtf8(resolvedEvidence, payload);
  } catch {
    // Keep adapter non-fatal for evidence appending.
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const taskPrompt = options.prompt || loadPromptFromTaskFile(options.taskFile);
  if (!taskPrompt) {
    throw new Error("Missing prompt. Pass --prompt or --task-file.");
  }

  const outputPath = options.outputJson
    ? (path.isAbsolute(options.outputJson) ? options.outputJson : path.resolve(repoRoot, options.outputJson))
    : path.join(logsRoot, `stagehand_semantic_r${String(options.round).padStart(2, "0")}.json`);

  const preview = {
    mode: options.dryRun ? "dry-run" : "run",
    prompt: taskPrompt,
    task_file: options.taskFile || null,
    round: options.round,
    model: options.model,
    start_url: options.startUrl,
    output_json: outputPath,
    verify_command: options.verifyCommand || null,
  };
  if (options.dryRun) {
    writeJsonUtf8(outputPath, preview);
    console.log(`[semantic] dry-run report: ${path.relative(repoRoot, outputPath)}`);
    return;
  }

  const StagehandCtor = await loadStagehandCtor();
  if (!options.apiKey) {
    throw new Error("OPENAI_API_KEY is required for Stagehand semantic fallback.");
  }

  const report = {
    started_at: nowIso(),
    ended_at: null,
    success: false,
    round: options.round,
    prompt: taskPrompt,
    task_file: options.taskFile || null,
    model: options.model,
    start_url: options.startUrl,
    observe_before: [],
    observe_after: [],
    act_result: null,
    verify: null,
    error: null,
  };

  const modelConfig = {
    modelName: options.model,
    apiKey: options.apiKey,
    ...(options.baseUrl ? { baseURL: options.baseUrl } : {}),
  };

  const stagehand = new StagehandCtor({
    env: "LOCAL",
    disableAPI: true,
    disablePino: true,
    verbose: 0,
    model: modelConfig,
    localBrowserLaunchOptions: {
      headless: options.headless,
      viewport: {
        width: options.width,
        height: options.height,
      },
      args: process.platform === "linux" ? ["--no-sandbox"] : undefined,
    },
  });

  let timedOut = false;
  const timeoutTimer = setTimeout(() => {
    timedOut = true;
  }, options.timeoutMs);

  try {
    await stagehand.init();
    const page = stagehand.context.pages()[0] || await stagehand.context.newPage(options.startUrl);
    const currentUrl = String(page.url() || "").trim();
    if (!currentUrl || currentUrl === "about:blank") {
      await page.goto(options.startUrl, { waitUntil: "domcontentloaded" });
    }

    const observeBefore = await stagehand.observe("List interactive elements that can help complete the task.");
    report.observe_before = summarizeObserveResult(observeBefore);

    const actionPrompt = [
      "Execute this UI task in Polaris with safe, deterministic actions.",
      "Do not change global OS settings. Do not navigate to unrelated sites.",
      `Task: ${taskPrompt}`,
    ].join("\n");
    const actResult = await stagehand.act(actionPrompt);
    report.act_result = actResult;

    const observeAfter = await stagehand.observe("List visible status or progress indicators after completing the task.");
    report.observe_after = summarizeObserveResult(observeAfter);

    if (options.verifyCommand) {
      const verifyRun = await runShell(options.verifyCommand, {
        timeoutMs: options.timeoutMs,
        cwd: repoRoot,
        env: process.env,
        label: "verify",
      });
      report.verify = {
        command: options.verifyCommand,
        exit_code: verifyRun.exitCode,
        timed_out: verifyRun.timedOut,
      };
      if (verifyRun.exitCode !== 0 || verifyRun.timedOut) {
        throw new Error(`verify command failed: ${quoteForShell(options.verifyCommand)}`);
      }
    }

    if (timedOut) {
      throw new Error("semantic stage reached timeout limit");
    }
    report.success = true;
  } catch (error) {
    report.error = error instanceof Error ? error.message : String(error);
    report.success = false;
  } finally {
    clearTimeout(timeoutTimer);
    report.ended_at = nowIso();
    try {
      await stagehand.close();
    } catch {
      // Ignore close errors.
    }
  }

  writeJsonUtf8(outputPath, report);
  appendEvidencePath(options.evidenceJson, outputPath);
  console.log(`[semantic] report: ${path.relative(repoRoot, outputPath)}`);

  if (!report.success) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`[semantic] fatal: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
