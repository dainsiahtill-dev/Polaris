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

function readJsonUtf8(filePath) {
  const raw = fs.readFileSync(filePath, "utf-8").replace(/^\uFEFF/, "");
  return JSON.parse(raw);
}

function writeJsonUtf8(filePath, payload) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf-8" });
}

function parseIntArg(raw, fallback, label) {
  if (raw === undefined || raw === null || raw === "") return fallback;
  const parsed = Number.parseInt(String(raw), 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${label} must be a non-negative integer. Received: ${raw}`);
  }
  return parsed;
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
  const { timeoutMs = 0, cwd = repoRoot, env = process.env, label = "" } = options;
  return new Promise((resolve, reject) => {
    if (label) {
      console.log(`[omniparser] run ${label}: ${commandText}`);
    } else {
      console.log(`[omniparser] run shell: ${commandText}`);
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
    imagePath: "",
    outputJson: "",
    round: parseIntArg(process.env.KERNELONE_OMNIPARSER_ROUND, 1, "KERNELONE_OMNIPARSER_ROUND"),
    timeoutMs: parseIntArg(process.env.KERNELONE_OMNIPARSER_TIMEOUT_MS, 10 * 60 * 1000, "KERNELONE_OMNIPARSER_TIMEOUT_MS"),
    engineCommand: String(process.env.KERNELONE_OMNIPARSER_ENGINE_CMD || "").trim(),
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
    if (arg === "--image-path") {
      options.imagePath = String(argv[index + 1] || "").trim();
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
    if (arg === "--engine-cmd") {
      options.engineCommand = String(argv[index + 1] || "").trim();
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

function toAbsolutePath(candidate) {
  if (!candidate) return "";
  return path.isAbsolute(candidate) ? candidate : path.resolve(repoRoot, candidate);
}

function isImageFile(filePath) {
  const ext = path.extname(String(filePath || "")).toLowerCase();
  return ext === ".png" || ext === ".jpg" || ext === ".jpeg" || ext === ".webp";
}

function collectImageCandidatesFromEvidence(evidenceJsonPath) {
  if (!evidenceJsonPath) return [];
  const resolved = toAbsolutePath(evidenceJsonPath);
  if (!fs.existsSync(resolved)) return [];

  try {
    const payload = readJsonUtf8(resolved);
    const evidencePaths = Array.isArray(payload?.evidence_paths) ? payload.evidence_paths : [];
    return evidencePaths
      .map((item) => toAbsolutePath(String(item || "").trim()))
      .filter((item) => item && fs.existsSync(item) && isImageFile(item));
  } catch {
    return [];
  }
}

function collectLatestImageFromDirectory(rootDir) {
  const resolvedRoot = toAbsolutePath(rootDir);
  if (!fs.existsSync(resolvedRoot)) return "";
  const stack = [resolvedRoot];
  let best = { path: "", mtimeMs: 0 };

  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) continue;

    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }
      if (!isImageFile(fullPath)) continue;
      try {
        const stat = fs.statSync(fullPath);
        if (stat.mtimeMs > best.mtimeMs) {
          best = { path: fullPath, mtimeMs: stat.mtimeMs };
        }
      } catch {
        // ignore unreadable entry
      }
    }
  }
  return best.path;
}

function resolveSourceImage(options) {
  const explicit = toAbsolutePath(options.imagePath);
  if (explicit && fs.existsSync(explicit) && isImageFile(explicit)) {
    return explicit;
  }

  const fromEvidence = collectImageCandidatesFromEvidence(options.evidenceJson);
  if (fromEvidence.length > 0) {
    const latest = fromEvidence
      .map((filePath) => {
        try {
          return { filePath, mtimeMs: fs.statSync(filePath).mtimeMs };
        } catch {
          return { filePath, mtimeMs: 0 };
        }
      })
      .sort((left, right) => right.mtimeMs - left.mtimeMs)[0];
    return latest?.filePath || "";
  }

  return collectLatestImageFromDirectory(path.join(repoRoot, "test-results", "electron"));
}

function parsePngSize(buffer) {
  if (buffer.length < 24) return null;
  const signature = buffer.subarray(0, 8).toString("hex");
  if (signature !== "89504e470d0a1a0a") return null;
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return null;
  }
  return { width, height };
}

function parseJpegSize(buffer) {
  if (buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) return null;
  let offset = 2;
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = buffer[offset + 1];
    const length = buffer.readUInt16BE(offset + 2);
    if (length < 2) return null;
    const isSof = marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
    if (isSof && offset + 8 < buffer.length) {
      const height = buffer.readUInt16BE(offset + 5);
      const width = buffer.readUInt16BE(offset + 7);
      if (width > 0 && height > 0) {
        return { width, height };
      }
      return null;
    }
    offset += 2 + length;
  }
  return null;
}

function readImageSize(filePath) {
  const buffer = fs.readFileSync(filePath);
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".png") {
    return parsePngSize(buffer);
  }
  if (ext === ".jpg" || ext === ".jpeg") {
    return parseJpegSize(buffer);
  }
  return null;
}

function buildGridElements(width, height) {
  const cols = 3;
  const rows = 3;
  const cellWidth = Math.max(1, Math.floor(width / cols));
  const cellHeight = Math.max(1, Math.floor(height / rows));
  const elements = [];
  let index = 1;
  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = col * cellWidth;
      const y = row * cellHeight;
      const w = col === cols - 1 ? (width - x) : cellWidth;
      const h = row === rows - 1 ? (height - y) : cellHeight;
      elements.push({
        id: `grid-${index}`,
        label: `grid_cell_${index}`,
        confidence: 0.15,
        bbox: {
          x,
          y,
          width: w,
          height: h,
        },
      });
      index += 1;
    }
  }
  return elements;
}

async function maybeRunExternalEngine(options, sourceImage, outputPath) {
  const engine = String(options.engineCommand || "").trim();
  if (!engine) {
    return {
      attempted: false,
      success: false,
      command: "",
      exitCode: 0,
      timedOut: false,
    };
  }
  const command = applyTemplate(engine, {
    prompt: options.prompt,
    task_file: options.taskFile || "",
    evidence_json: options.evidenceJson || "",
    image_path: sourceImage,
    output_json: outputPath,
    round: options.round,
    workspace: repoRoot,
  });
  const result = await runShell(command, {
    timeoutMs: options.timeoutMs,
    cwd: repoRoot,
    env: process.env,
    label: "omniparser.engine",
  });
  return {
    attempted: true,
    success: result.exitCode === 0 && !result.timedOut,
    command,
    exitCode: result.exitCode,
    timedOut: result.timedOut,
  };
}

function appendEvidencePath(evidenceJsonPath, targetPath) {
  if (!evidenceJsonPath) return;
  const resolvedEvidence = toAbsolutePath(evidenceJsonPath);
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
  const outputPath = options.outputJson
    ? toAbsolutePath(options.outputJson)
    : path.join(logsRoot, `omniparser_r${String(options.round).padStart(2, "0")}.json`);

  if (options.dryRun) {
    const sourceImage = resolveSourceImage(options);
    const preview = {
      mode: "dry-run",
      round: options.round,
      source_image: sourceImage || null,
      output_json: outputPath,
      engine_command: options.engineCommand || null,
      note: sourceImage ? null : "No screenshot found yet. Provide --image-path or run Playwright once before real OmniParser execution.",
    };
    writeJsonUtf8(outputPath, preview);
    console.log(`[omniparser] dry-run report: ${path.relative(repoRoot, outputPath)}`);
    return;
  }

  const sourceImage = resolveSourceImage(options);
  if (!sourceImage) {
    throw new Error("No screenshot evidence found for OmniParser stage.");
  }

  const report = {
    started_at: nowIso(),
    ended_at: null,
    success: false,
    round: options.round,
    prompt: options.prompt || null,
    task_file: options.taskFile || null,
    source_image: sourceImage,
    parser_mode: "fallback_grid",
    image_size: null,
    elements: [],
    engine: {
      attempted: false,
      success: false,
      command: null,
      exit_code: 0,
      timed_out: false,
    },
    error: null,
  };

  try {
    const size = readImageSize(sourceImage);
    if (!size) {
      throw new Error(`Unsupported or unreadable image format: ${sourceImage}`);
    }
    report.image_size = size;

    const engineRun = await maybeRunExternalEngine(options, sourceImage, outputPath);
    report.engine = {
      attempted: engineRun.attempted,
      success: engineRun.success,
      command: engineRun.command || null,
      exit_code: engineRun.exitCode,
      timed_out: engineRun.timedOut,
    };

    if (engineRun.success && fs.existsSync(outputPath)) {
      const existing = readJsonUtf8(outputPath);
      report.parser_mode = "external_engine";
      report.elements = Array.isArray(existing?.elements) ? existing.elements : [];
      report.success = true;
    } else {
      report.parser_mode = "fallback_grid";
      report.elements = buildGridElements(size.width, size.height);
      report.success = true;
    }
  } catch (error) {
    report.error = error instanceof Error ? error.message : String(error);
    report.success = false;
  } finally {
    report.ended_at = nowIso();
  }

  writeJsonUtf8(outputPath, report);
  appendEvidencePath(options.evidenceJson, outputPath);
  console.log(`[omniparser] report: ${path.relative(repoRoot, outputPath)}`);

  if (!report.success) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`[omniparser] fatal: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
