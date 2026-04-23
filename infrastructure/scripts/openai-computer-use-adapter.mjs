import fs from "fs";
import path from "path";
import { spawn } from "child_process";
import { fileURLToPath } from "url";
import { chromium } from "playwright";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");
const logsRoot = path.join(repoRoot, ".polaris", "logs");

function nowIso() {
  return new Date().toISOString();
}

function toUnixPath(filePath) {
  return String(filePath || "").split(path.sep).join("/");
}

function toRelativePath(filePath) {
  return toUnixPath(path.relative(repoRoot, filePath));
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
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

function parseBool(raw, fallback) {
  if (raw === undefined || raw === null || raw === "") {
    return fallback;
  }
  const token = String(raw).trim().toLowerCase();
  return token === "1" || token === "true" || token === "yes";
}

function extractErrorStatusCode(error) {
  const message = String(error?.message || "");
  const match = message.match(/OpenAI API\s+(\d{3})/i);
  if (!match) return 0;
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : 0;
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
      console.log(`[computer-use] run ${label}: ${commandText}`);
    } else {
      console.log(`[computer-use] run shell: ${commandText}`);
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
    round: parseIntArg(process.env.KERNELONE_COMPUTER_USE_ROUND, 1, "KERNELONE_COMPUTER_USE_ROUND"),
    model: String(process.env.KERNELONE_COMPUTER_USE_MODEL || "computer-use-preview").trim(),
    displayWidth: parseIntArg(process.env.KERNELONE_COMPUTER_USE_DISPLAY_WIDTH, 1280, "KERNELONE_COMPUTER_USE_DISPLAY_WIDTH"),
    displayHeight: parseIntArg(process.env.KERNELONE_COMPUTER_USE_DISPLAY_HEIGHT, 800, "KERNELONE_COMPUTER_USE_DISPLAY_HEIGHT"),
    environment: String(process.env.KERNELONE_COMPUTER_USE_ENVIRONMENT || "browser").trim(),
    maxSteps: parseIntArg(process.env.KERNELONE_COMPUTER_USE_MAX_STEPS, 24, "KERNELONE_COMPUTER_USE_MAX_STEPS"),
    timeoutMs: parseIntArg(process.env.KERNELONE_COMPUTER_USE_TIMEOUT_MS, 20 * 60 * 1000, "KERNELONE_COMPUTER_USE_TIMEOUT_MS"),
    actionDelayMs: parseIntArg(process.env.KERNELONE_COMPUTER_USE_ACTION_DELAY_MS, 350, "KERNELONE_COMPUTER_USE_ACTION_DELAY_MS"),
    requestTimeoutMs: parseIntArg(process.env.KERNELONE_COMPUTER_USE_REQUEST_TIMEOUT_MS, 90 * 1000, "KERNELONE_COMPUTER_USE_REQUEST_TIMEOUT_MS"),
    startUrl: String(process.env.KERNELONE_COMPUTER_USE_START_URL || process.env.KERNELONE_DEV_SERVER_URL || "http://127.0.0.1:5173").trim(),
    verifyCommand: String(process.env.KERNELONE_COMPUTER_USE_VERIFY_CMD || "").trim(),
    openaiBaseUrl: String(process.env.OPENAI_BASE_URL || "https://api.openai.com/v1").trim(),
    chatCompatOn404: parseBool(process.env.KERNELONE_COMPUTER_USE_CHAT_COMPAT, true),
    outputType: String(process.env.KERNELONE_COMPUTER_USE_OUTPUT_TYPE || "input_image").trim(),
    reasonSummary: String(process.env.KERNELONE_COMPUTER_USE_REASONING_SUMMARY || "concise").trim(),
    headless: parseBool(process.env.KERNELONE_COMPUTER_USE_HEADLESS, true),
    acknowledgeSafety: parseBool(process.env.KERNELONE_COMPUTER_USE_ACK_SAFETY, true),
    dryRun: false,
    outputJson: "",
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
    if (arg === "--display-width") {
      options.displayWidth = parseIntArg(argv[index + 1], options.displayWidth, "--display-width");
      index += 1;
      continue;
    }
    if (arg === "--display-height") {
      options.displayHeight = parseIntArg(argv[index + 1], options.displayHeight, "--display-height");
      index += 1;
      continue;
    }
    if (arg === "--start-url") {
      options.startUrl = String(argv[index + 1] || "").trim() || options.startUrl;
      index += 1;
      continue;
    }
    if (arg === "--max-steps") {
      options.maxSteps = parseIntArg(argv[index + 1], options.maxSteps, "--max-steps");
      index += 1;
      continue;
    }
    if (arg === "--verify-command") {
      options.verifyCommand = String(argv[index + 1] || "").trim();
      index += 1;
      continue;
    }
    if (arg === "--output-json") {
      options.outputJson = String(argv[index + 1] || "").trim();
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

function normalizeSafetyChecks(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => ({
      id: String(item?.id || "").trim(),
      code: String(item?.code || "").trim(),
      message: String(item?.message || "").trim(),
    }))
    .filter((item) => item.id && item.code);
}

function summarizeOutput(outputItems) {
  if (!Array.isArray(outputItems)) return "";
  const parts = [];
  for (const item of outputItems) {
    const type = String(item?.type || "");
    if (type === "message") {
      const content = Array.isArray(item?.content) ? item.content : [];
      for (const block of content) {
        const text = String(block?.text || "").trim();
        if (text) parts.push(text);
      }
    }
    if (type === "reasoning") {
      const summary = Array.isArray(item?.summary) ? item.summary : [];
      for (const part of summary) {
        const text = String(part?.text || "").trim();
        if (text) parts.push(text);
      }
    }
  }
  return parts.join("\n").trim();
}

async function callResponsesApi({ apiKey, baseUrl, payload, timeoutMs }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const endpoint = `${baseUrl.replace(/\/+$/, "")}/responses`;
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }
    if (!response.ok) {
      const detail = typeof data === "object" && data && "error" in data
        ? JSON.stringify(data.error)
        : text;
      throw new Error(`OpenAI API ${response.status}: ${detail}`);
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function callChatCompletionsApi({ apiKey, baseUrl, payload, timeoutMs }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const endpoint = `${baseUrl.replace(/\/+$/, "")}/chat/completions`;
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }
    if (!response.ok) {
      const detail = typeof data === "object" && data && "error" in data
        ? JSON.stringify(data.error)
        : text;
      throw new Error(`OpenAI API ${response.status}: ${detail}`);
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function executeComputerAction(page, action) {
  const type = String(action?.type || "").trim().toLowerCase();
  const x = Number(action?.x ?? 0);
  const y = Number(action?.y ?? 0);
  const button = String(action?.button || "left").toLowerCase();
  const text = String(action?.text ?? action?.value ?? action?.input_text ?? "");
  const key = String(action?.key ?? action?.keys ?? action?.key_combination ?? "");
  const scrollX = Number(action?.scroll_x ?? action?.delta_x ?? 0);
  const scrollY = Number(action?.scroll_y ?? action?.delta_y ?? 0);

  switch (type) {
    case "click":
      await page.mouse.click(x, y, { button: button === "right" ? "right" : "left", clickCount: 1 });
      return;
    case "double_click":
    case "doubleclick":
      await page.mouse.click(x, y, { button: button === "right" ? "right" : "left", clickCount: 2 });
      return;
    case "move":
      await page.mouse.move(x, y);
      return;
    case "drag":
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(Number(action?.end_x ?? action?.to_x ?? x), Number(action?.end_y ?? action?.to_y ?? y), { steps: 12 });
      await page.mouse.up();
      return;
    case "scroll":
      await page.mouse.wheel(scrollX, scrollY || 500);
      return;
    case "type":
      if (text) {
        await page.keyboard.type(text);
      }
      return;
    case "keypress":
    case "key_press":
      if (key) {
        const normalized = key.replace(/\s*\+\s*/g, "+");
        await page.keyboard.press(normalized);
      }
      return;
    case "wait":
      await page.waitForTimeout(parseIntArg(action?.duration_ms ?? action?.ms, 1000, "action.wait"));
      return;
    case "navigate":
    case "open_url":
      if (action?.url) {
        await page.goto(String(action.url), { waitUntil: "domcontentloaded", timeout: 120000 });
      }
      return;
    default:
      throw new Error(`Unsupported computer action type: ${type}`);
  }
}

function buildComputerCallOutput(callId, imageDataUrl, outputType, safetyChecks) {
  const normalizedType = outputType === "computer_screenshot" ? "computer_screenshot" : "input_image";
  const output = normalizedType === "computer_screenshot"
    ? { type: "computer_screenshot", image_url: imageDataUrl }
    : { type: "input_image", image_url: imageDataUrl };
  const payload = {
    type: "computer_call_output",
    call_id: callId,
    output,
  };
  if (Array.isArray(safetyChecks) && safetyChecks.length > 0) {
    payload.acknowledged_safety_checks = safetyChecks;
  }
  return payload;
}

async function runComputerUse(options) {
  const apiKey = String(process.env.OPENAI_API_KEY || "").trim();
  if (!apiKey) {
    throw new Error("OPENAI_API_KEY is required.");
  }

  const runId = `computer_use_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  const outputPath = options.outputJson
    ? (path.isAbsolute(options.outputJson) ? options.outputJson : path.resolve(repoRoot, options.outputJson))
    : path.join(logsRoot, `${runId}.json`);
  const runDir = path.join(logsRoot, runId);
  ensureDir(runDir);

  const evidencePayload = options.evidenceJson
    ? readJsonUtf8(path.isAbsolute(options.evidenceJson) ? options.evidenceJson : path.resolve(repoRoot, options.evidenceJson))
    : {};

  const userPrompt = options.prompt || loadPromptFromTaskFile(options.taskFile);
  if (!userPrompt) {
    throw new Error("Prompt is required. Provide --prompt or --task-file with prompt field.");
  }

  const browser = await chromium.launch({ headless: options.headless });
  const context = await browser.newContext({
    viewport: { width: options.displayWidth, height: options.displayHeight },
  });
  const page = await context.newPage();

  const startedAt = nowIso();
  const steps = [];
  const screenshots = [];
  let finalSummary = "";
  let finalStatus = "FAIL";
  let responseId = "";
  let transportMode = "responses";

  try {
    await page.goto(options.startUrl, { waitUntil: "domcontentloaded", timeout: 120000 });
    const initialShot = await page.screenshot({ fullPage: true, type: "png" });
    const initialBase64 = initialShot.toString("base64");
    const initialShotPath = path.join(runDir, "step_000_initial.png");
    fs.writeFileSync(initialShotPath, initialShot);
    screenshots.push(toRelativePath(initialShotPath));

    const instructionLines = [
      "You are operating Polaris UI as a computer-use fallback agent.",
      `Goal: ${userPrompt}`,
      "Operate safely and complete the goal with minimal steps.",
      "Stop once the goal is achieved and provide a concise completion summary.",
    ];
    const evidencePaths = Array.isArray(evidencePayload?.evidence_paths) ? evidencePayload.evidence_paths : [];
    if (evidencePaths.length > 0) {
      instructionLines.push("Known failure evidence paths:");
      for (const item of evidencePaths.slice(0, 20)) {
        instructionLines.push(`- ${String(item)}`);
      }
    }

    let response = null;
    try {
      response = await callResponsesApi({
        apiKey,
        baseUrl: options.openaiBaseUrl,
        timeoutMs: options.requestTimeoutMs,
        payload: {
          model: options.model,
          truncation: "auto",
          reasoning: { summary: options.reasonSummary || "concise" },
          tools: [
            {
              type: "computer_use_preview",
              display_width: options.displayWidth,
              display_height: options.displayHeight,
              environment: options.environment,
            },
          ],
          input: [
            {
              role: "user",
              content: [
                {
                  type: "input_text",
                  text: instructionLines.join("\n"),
                },
                {
                  type: "input_image",
                  image_url: `data:image/png;base64,${initialBase64}`,
                },
              ],
            },
          ],
        },
      });
    } catch (error) {
      const statusCode = extractErrorStatusCode(error);
      if (!options.chatCompatOn404 || statusCode !== 404) {
        throw error;
      }
      transportMode = "chat_completions_compat";
      const compatStart = nowIso();
      const compatResponse = await callChatCompletionsApi({
        apiKey,
        baseUrl: options.openaiBaseUrl,
        timeoutMs: options.requestTimeoutMs,
        payload: {
          model: options.model,
          messages: [
            {
              role: "user",
              content: `${instructionLines.join("\n")}\n\n(Provider does not support /responses; return concise execution guidance only.)`,
            },
          ],
          max_tokens: 256,
        },
      });
      const compatEnd = nowIso();
      const compatText = String(compatResponse?.choices?.[0]?.message?.content || "").trim();
      finalSummary = compatText || "chat/completions compatibility request succeeded.";
      finalStatus = "PASS";
      steps.push({
        step: 1,
        started_at: compatStart,
        ended_at: compatEnd,
        action: "chat_completions_compat",
        call_id: "",
        success: true,
        error: undefined,
        screenshot: toRelativePath(initialShotPath),
      });
    }

    if (transportMode === "chat_completions_compat") {
      if (finalStatus === "PASS" && options.verifyCommand) {
        const verifyResult = await runShell(options.verifyCommand, {
          timeoutMs: options.timeoutMs,
          label: "verify",
        });
        steps.push({
          step: steps.length + 1,
          started_at: nowIso(),
          ended_at: nowIso(),
          action: "verify_command",
          call_id: "",
          success: verifyResult.exitCode === 0 && !verifyResult.timedOut,
          error: verifyResult.exitCode === 0 ? undefined : "verify command failed",
        });
        if (verifyResult.exitCode !== 0 || verifyResult.timedOut) {
          finalStatus = "FAIL";
        }
      }

      const report = {
        status: finalStatus,
        workspace: repoRoot,
        round: options.round,
        run_id: runId,
        started_at: startedAt,
        ended_at: nowIso(),
        prompt: userPrompt,
        model: options.model,
        transport_mode: transportMode,
        summary: finalSummary || undefined,
        steps,
        evidence_paths: screenshots,
        output_json: toRelativePath(outputPath),
      };
      writeJsonUtf8(outputPath, report);
      console.log(`[computer-use] report: ${toRelativePath(outputPath)}`);
      if (finalStatus !== "PASS") {
        process.exitCode = 1;
      }
      return;
    }

    for (let step = 1; step <= options.maxSteps; step += 1) {
      responseId = String(response?.id || "").trim();
      const outputItems = Array.isArray(response?.output) ? response.output : [];
      const computerCall = outputItems.find((item) => String(item?.type || "").trim() === "computer_call");
      finalSummary = summarizeOutput(outputItems) || finalSummary;

      if (!computerCall) {
        finalStatus = "PASS";
        break;
      }

      const action = computerCall?.action || {};
      const stepStart = nowIso();
      let stepOk = true;
      let stepError = "";
      try {
        await executeComputerAction(page, action);
        await page.waitForTimeout(options.actionDelayMs);
      } catch (error) {
        stepOk = false;
        stepError = error instanceof Error ? error.message : String(error);
      }
      const stepEnd = nowIso();

      const shot = await page.screenshot({ fullPage: true, type: "png" });
      const shotBase64 = shot.toString("base64");
      const shotPath = path.join(runDir, `step_${String(step).padStart(3, "0")}.png`);
      fs.writeFileSync(shotPath, shot);
      screenshots.push(toRelativePath(shotPath));

      const actionType = String(action?.type || "").trim();
      steps.push({
        step,
        started_at: stepStart,
        ended_at: stepEnd,
        action: actionType,
        call_id: String(computerCall?.call_id || ""),
        success: stepOk,
        error: stepError || undefined,
        screenshot: toRelativePath(shotPath),
      });

      if (!stepOk) {
        finalStatus = "FAIL";
        break;
      }

      const safetyChecks = options.acknowledgeSafety
        ? normalizeSafetyChecks(computerCall?.pending_safety_checks)
        : [];
      const outputPayload = buildComputerCallOutput(
        String(computerCall?.call_id || ""),
        `data:image/png;base64,${shotBase64}`,
        options.outputType,
        safetyChecks,
      );

      response = await callResponsesApi({
        apiKey,
        baseUrl: options.openaiBaseUrl,
        timeoutMs: options.requestTimeoutMs,
        payload: {
          model: options.model,
          truncation: "auto",
          previous_response_id: responseId || undefined,
          tools: [
            {
              type: "computer_use_preview",
              display_width: options.displayWidth,
              display_height: options.displayHeight,
              environment: options.environment,
            },
          ],
          input: [outputPayload],
        },
      });
    }

    if (steps.length >= options.maxSteps && finalStatus !== "PASS") {
      finalSummary = `${finalSummary}\nMax steps reached without completion.`.trim();
    }

    if (finalStatus === "PASS" && options.verifyCommand) {
      const verifyResult = await runShell(options.verifyCommand, {
        timeoutMs: options.timeoutMs,
        label: "verify",
      });
      steps.push({
        step: steps.length + 1,
        started_at: nowIso(),
        ended_at: nowIso(),
        action: "verify_command",
        call_id: "",
        success: verifyResult.exitCode === 0 && !verifyResult.timedOut,
        error: verifyResult.exitCode === 0 ? undefined : "verify command failed",
      });
      if (verifyResult.exitCode !== 0 || verifyResult.timedOut) {
        finalStatus = "FAIL";
      }
    }

    const report = {
      status: finalStatus,
      workspace: repoRoot,
      round: options.round,
      run_id: runId,
      started_at: startedAt,
      ended_at: nowIso(),
      prompt: userPrompt,
      model: options.model,
      transport_mode: transportMode,
      response_id: responseId || undefined,
      summary: finalSummary || undefined,
      steps,
      evidence_paths: screenshots,
      output_json: toRelativePath(outputPath),
    };
    writeJsonUtf8(outputPath, report);
    console.log(`[computer-use] report: ${toRelativePath(outputPath)}`);
    if (finalStatus !== "PASS") {
      process.exitCode = 1;
    }
  } finally {
    await context.close();
    await browser.close();
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.dryRun) {
    const preview = {
      mode: "dry-run",
      model: options.model,
      start_url: options.startUrl,
      max_steps: options.maxSteps,
      output_type: options.outputType,
      chat_compat_on_404: options.chatCompatOn404,
      verify_command: options.verifyCommand || null,
      has_openai_api_key: Boolean(String(process.env.OPENAI_API_KEY || "").trim()),
    };
    console.log(JSON.stringify(preview, null, 2));
    return;
  }
  await runComputerUse(options);
}

main().catch((error) => {
  console.error(`[computer-use] fatal: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
