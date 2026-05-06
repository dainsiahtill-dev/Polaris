import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { resolvePanelTaskFromPrompt } from "./resolve-panel-task.mjs";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const panelTaskSpecRelativePath = path.join("src", "backend", "polaris", "tests", "electron", "panel-task.spec.ts");
const panelTaskSpecPath = path.join(repoRoot, panelTaskSpecRelativePath);

function parseBool(raw, fallback) {
  if (raw === undefined || raw === null || raw === "") {
    return fallback;
  }
  const token = String(raw).trim().toLowerCase();
  return token === "1" || token === "true" || token === "yes";
}

function parseArgs(argv) {
  const args = [...argv];
  let dryRun = false;
  let taskFile = "";
  let dictionaryPath = "";
  let allowProviderFallback = false;
  let allowFieldFallback = false;
  let semanticFallback = parseBool(process.env.KERNELONE_PANEL_SEMANTIC_FALLBACK, true);
  let semanticCommand = String(process.env.KERNELONE_PANEL_SEMANTIC_CMD || "").trim();
  const promptParts = [];

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--dry-run") {
      dryRun = true;
      continue;
    }
    if (arg === "--task-file") {
      taskFile = String(args[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--dictionary") {
      dictionaryPath = String(args[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--allow-provider-fallback") {
      allowProviderFallback = true;
      continue;
    }
    if (arg === "--allow-field-fallback") {
      allowFieldFallback = true;
      continue;
    }
    if (arg === "--semantic-fallback") {
      semanticFallback = true;
      continue;
    }
    if (arg === "--no-semantic-fallback") {
      semanticFallback = false;
      continue;
    }
    if (arg === "--semantic-cmd") {
      semanticCommand = String(args[index + 1] || "").trim();
      index += 1;
      continue;
    }
    promptParts.push(arg);
  }

  return {
    dryRun,
    taskFile: taskFile || String(process.env.E2E_PANEL_TASK_FILE || "").trim(),
    dictionaryPath: dictionaryPath || String(process.env.E2E_PANEL_TASK_DICTIONARY || "").trim(),
    allowProviderFallback,
    allowFieldFallback,
    semanticFallback,
    semanticCommand,
    prompt: promptParts.join(" ").trim() || String(process.env.E2E_PANEL_TASK_PROMPT || "").trim(),
  };
}

function isTruthyEnv(name) {
  const raw = String(process.env[name] || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes";
}

function toUnixPath(filePath) {
  return String(filePath || "").split(path.sep).join("/");
}

function toRelative(filePath) {
  return toUnixPath(path.relative(repoRoot, filePath));
}

function assertFileExists(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${label} not found: ${toRelative(filePath)}`);
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

function isLlmSensitiveTask(task) {
  if (!task || typeof task !== "object") return false;
  const prompt = String(task.prompt || "");
  if (isLlmSensitiveText(prompt)) return true;
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

function normalizeTaskObject(task, sourceLabel = "inline") {
  if (!task || typeof task !== "object") {
    throw new Error(`Invalid task payload from ${sourceLabel}: expected JSON object.`);
  }
  if (!Array.isArray(task.navigationSteps)) {
    throw new Error(`Invalid task payload from ${sourceLabel}: navigationSteps must be an array.`);
  }
  if (!task.fieldAction || typeof task.fieldAction !== "object") {
    throw new Error(`Invalid task payload from ${sourceLabel}: fieldAction is required.`);
  }
  if (typeof task.fieldAction.name !== "string" || !task.fieldAction.name.trim()) {
    throw new Error(`Invalid task payload from ${sourceLabel}: fieldAction.name is required.`);
  }

  const prompt = typeof task.prompt === "string" && task.prompt.trim()
    ? task.prompt
    : `[task-file:${sourceLabel}]`;
  return {
    ...task,
    prompt,
  };
}

function loadTaskFromFile(taskFile) {
  const resolvedPath = path.isAbsolute(taskFile)
    ? taskFile
    : path.resolve(process.cwd(), taskFile);
  const raw = fs.readFileSync(resolvedPath, "utf-8").replace(/^\uFEFF/, "");
  const parsed = JSON.parse(raw);
  return {
    task: normalizeTaskObject(parsed, resolvedPath),
    resolvedPath,
  };
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

function runShell(commandText, options = {}) {
  const { cwd = repoRoot, env = process.env, label = "" } = options;
  return new Promise((resolve, reject) => {
    if (label) {
      console.log(`[task-e2e] run ${label}: ${commandText}`);
    } else {
      console.log(`[task-e2e] run shell: ${commandText}`);
    }
    const child = process.platform === "win32"
      ? spawn("cmd.exe", ["/d", "/s", "/c", commandText], { cwd, env, stdio: "inherit" })
      : spawn("sh", ["-lc", commandText], { cwd, env, stdio: "inherit" });

    child.on("error", reject);
    child.on("exit", (code) => {
      resolve({
        exitCode: code ?? 1,
      });
    });
  });
}

function runPlaywrightTask(task) {
  assertFileExists(panelTaskSpecPath, "Panel-task Playwright spec");
  const taskBase64 = Buffer.from(JSON.stringify(task), "utf-8").toString("base64");
  const env = {
    ...process.env,
    E2E_PANEL_TASK_JSON_BASE64: taskBase64,
    E2E_PANEL_STRICT_ERRORS: process.env.E2E_PANEL_STRICT_ERRORS || "1",
    E2E_PANEL_STRICT_TERMINAL_ERRORS: process.env.E2E_PANEL_STRICT_TERMINAL_ERRORS || "1",
  };

  const command = `npx playwright test -c playwright.electron.config.ts ${toUnixPath(panelTaskSpecRelativePath)}`;
  return runShell(command, {
    cwd: repoRoot,
    env,
    label: "playwright.main",
  });
}

async function maybeRunSemanticFallback(options, task) {
  if (!options.semanticFallback) {
    return {
      attempted: false,
      success: false,
      skippedReason: "semantic fallback disabled (--no-semantic-fallback).",
    };
  }

  const template = String(options.semanticCommand || "").trim();
  if (!template) {
    return {
      attempted: false,
      success: false,
      skippedReason: "no semantic fallback command configured.",
    };
  }

  const command = applyTemplate(template, {
    prompt: task.prompt,
    task_file: options.taskFile || "",
    workspace: repoRoot,
  });
  const result = await runShell(command, {
    cwd: repoRoot,
    env: process.env,
    label: "semantic.fallback",
  });
  return {
    attempted: true,
    success: result.exitCode === 0,
    command,
    exitCode: result.exitCode,
  };
}

async function main() {
  try {
    const {
      dryRun,
      taskFile,
      dictionaryPath,
      allowProviderFallback,
      allowFieldFallback,
      semanticFallback,
      semanticCommand,
      prompt,
    } = parseArgs(process.argv.slice(2));

    const allowLlmTests = isTruthyEnv("KERNELONE_E2E_ALLOW_LLM_TESTS");
    const promptLooksLlm = isLlmSensitiveText(prompt);
    let taskFileLooksLlm = false;

    let task;
    if (taskFile) {
      const loaded = loadTaskFromFile(taskFile);
      task = loaded.task;
      taskFileLooksLlm = isLlmSensitiveTask(task);
      console.log(`[task-e2e] task file: ${loaded.resolvedPath}`);
    } else {
      if (!prompt) {
        console.error(
          "[task-e2e] Missing prompt. Usage: npm run test:e2e:task -- \"<one-line task>\" " +
          "or npm run test:e2e:task -- --task-file <task.json>",
        );
        process.exit(1);
      }
      task = resolvePanelTaskFromPrompt(prompt, {
        dictionaryPath: dictionaryPath || undefined,
        requireProviderMatch: !allowProviderFallback,
        requireFieldMatch: !allowFieldFallback,
      });
    }
    task = normalizeTaskObject(task, taskFile || "prompt");

    if (!allowLlmTests && (promptLooksLlm || taskFileLooksLlm)) {
      console.error(
        "[task-e2e] Blocked LLM-related task by default. " +
        "Set KERNELONE_E2E_ALLOW_LLM_TESTS=1 only when you intentionally need to test LLM settings.",
      );
      process.exit(2);
    }

    const navigationSteps = Array.isArray(task.navigationSteps) ? task.navigationSteps : [];
    const warnings = Array.isArray(task?.resolved?.warnings) ? task.resolved.warnings : [];
    const fieldActionName = typeof task?.fieldAction?.name === "string" ? task.fieldAction.name : "(unknown)";

    console.log(`[task-e2e] prompt: ${task.prompt}`);
    console.log(`[task-e2e] navigation: ${navigationSteps.map((step) => step.name).join(" -> ")}`);
    console.log(`[task-e2e] field action: ${fieldActionName}`);
    if (warnings.length > 0) {
      console.log(`[task-e2e] warnings: ${warnings.join(" | ")}`);
    }

    if (dryRun) {
      assertFileExists(panelTaskSpecPath, "Panel-task Playwright spec");
      const preview = {
        task,
        panel_task_spec: toUnixPath(panelTaskSpecRelativePath),
        playwright_command: `npx playwright test -c playwright.electron.config.ts ${toUnixPath(panelTaskSpecRelativePath)}`,
        semantic_fallback: {
          enabled: semanticFallback,
          command: semanticCommand || null,
          rendered_command: semanticCommand
            ? applyTemplate(semanticCommand, {
              prompt: task.prompt,
              task_file: taskFile || "",
              workspace: repoRoot,
            })
            : null,
        },
      };
      process.stdout.write(`${JSON.stringify(preview, null, 2)}\n`);
      return;
    }

    const playwrightResult = await runPlaywrightTask(task);
    if (playwrightResult.exitCode === 0) {
      process.exit(0);
      return;
    }

    const semanticResult = await maybeRunSemanticFallback(
      { semanticFallback, semanticCommand, taskFile },
      task,
    );
    if (!semanticResult.attempted) {
      console.log(`[task-e2e] semantic fallback skipped: ${semanticResult.skippedReason}`);
      process.exit(playwrightResult.exitCode);
      return;
    }

    console.log(
      `[task-e2e] semantic fallback command exit=${semanticResult.exitCode}: ${quoteForShell(semanticResult.command || "")}`,
    );
    process.exit(semanticResult.success ? 0 : playwrightResult.exitCode);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[task-e2e] ${message}`);
    process.exit(1);
  }
}

await main();
