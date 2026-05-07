import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");

const realFlowSpecs = [
  "src/backend/polaris/tests/electron/full-chain-audit.spec.ts",
  "src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts",
];

function parseArgs(argv) {
  return {
    dryRun:
      argv.includes("--dry-run") ||
      String(process.env.KERNELONE_E2E_REAL_FLOW_DRY_RUN || "").trim() === "1",
  };
}

function isCiEnvironment() {
  return (
    String(process.env.CI || "").trim().toLowerCase() === "true" ||
    String(process.env.GITHUB_ACTIONS || "").trim().toLowerCase() === "true"
  );
}

function readJsonSeed(base64EnvName, jsonEnvName) {
  const base64 = String(process.env[base64EnvName] || "").trim();
  if (base64) {
    return {
      source: `env:${base64EnvName}`,
      raw: Buffer.from(base64, "base64").toString("utf-8"),
    };
  }

  const json = String(process.env[jsonEnvName] || "").trim();
  if (json) {
    return {
      source: `env:${jsonEnvName}`,
      raw: json,
    };
  }

  return null;
}

function readSettingsSeed() {
  return readJsonSeed("KERNELONE_E2E_SETTINGS_JSON_BASE64", "KERNELONE_E2E_SETTINGS_JSON");
}

function readLlmConfigSeed() {
  return readJsonSeed("KERNELONE_E2E_LLM_CONFIG_JSON_BASE64", "KERNELONE_E2E_LLM_CONFIG_JSON");
}

function readLlmTestIndexSeed() {
  return readJsonSeed("KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64", "KERNELONE_E2E_LLM_TEST_INDEX_JSON");
}

function isSeededSettingsBootstrap(settingsBootstrap) {
  return String(settingsBootstrap?.source || "").startsWith("env:KERNELONE_E2E_SETTINGS_JSON");
}

function resolveSeededHome() {
  return path.resolve(
    process.env.KERNELONE_E2E_HOME ||
      process.env.KERNELONE_HOME ||
      path.join(repoRoot, ".polaris", "e2e-real-home"),
  );
}

function writeSeededSettings(seed) {
  const parsed = JSON.parse(seed.raw);
  const home = resolveSeededHome();
  const configDir = path.join(home, "config");
  const settingsPath = path.join(configDir, "settings.json");
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(settingsPath, `${JSON.stringify(parsed, null, 2)}\n`, "utf-8");
  return {
    source: seed.source,
    home,
    settingsPath,
  };
}

function writeSeededLlmConfig(home, seed) {
  if (!seed) {
    return null;
  }

  const parsed = JSON.parse(seed.raw);
  const requiredReadyRoles = extractRequiredReadyRoles(parsed);
  const configDir = path.join(home, "config", "llm");
  const configPath = path.join(configDir, "llm_config.json");
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(configPath, `${JSON.stringify(parsed, null, 2)}\n`, "utf-8");
  return {
    source: seed.source,
    configPath,
    requiredReadyRoles,
  };
}

function writeSeededLlmTestIndex(home, seed) {
  if (!seed) {
    return null;
  }

  const parsed = JSON.parse(seed.raw);
  const configDir = path.join(home, "config", "llm");
  const indexPath = path.join(configDir, "llm_test_index.json");
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(indexPath, `${JSON.stringify(parsed, null, 2)}\n`, "utf-8");
  return {
    source: seed.source,
    indexPath,
    missingReadyRoles: rolesMissingReadiness(parsed),
  };
}

function readJsonFileSeed(filePath, source) {
  const resolved = path.resolve(filePath);
  if (!fs.existsSync(resolved)) {
    return null;
  }
  if (!fs.statSync(resolved).isFile()) {
    return null;
  }
  return {
    source,
    raw: fs.readFileSync(resolved, "utf-8"),
  };
}

function readExistingLlmTestIndexSeed(seededHome) {
  const explicitPath = String(process.env.KERNELONE_E2E_LLM_TEST_INDEX_PATH || "").trim();
  if (explicitPath) {
    return readJsonFileSeed(explicitPath, "env:KERNELONE_E2E_LLM_TEST_INDEX_PATH");
  }

  const candidates = [];
  if (seededHome) {
    candidates.push({
      source: "seeded-home-existing",
      path: path.join(path.resolve(seededHome), "config", "llm", "llm_test_index.json"),
    });
  }

  const kerneloneHome = String(process.env.KERNELONE_HOME || "").trim();
  if (kerneloneHome) {
    candidates.push({
      source: "env:KERNELONE_HOME",
      path: path.join(path.resolve(kerneloneHome), "config", "llm", "llm_test_index.json"),
    });
  }

  if (String(process.env.KERNELONE_E2E_LLM_TEST_INDEX_HOST_FALLBACK || "1").trim() !== "0") {
    candidates.push({
      source: "host-home",
      path: path.join(os.homedir(), ".polaris", "config", "llm", "llm_test_index.json"),
    });
  }

  const seen = new Set();
  for (const candidate of candidates) {
    const resolved = path.resolve(candidate.path);
    if (seen.has(resolved)) {
      continue;
    }
    seen.add(resolved);
    const seed = readJsonFileSeed(resolved, candidate.source);
    if (seed) {
      return seed;
    }
  }

  return null;
}

function extractRequiredReadyRoles(config) {
  const policies = config?.policies;
  const required = policies && typeof policies === "object" ? policies.required_ready_roles : [];
  if (!Array.isArray(required)) {
    return [];
  }
  return Array.from(
    new Set(
      required
        .map((role) => String(role || "").trim().toLowerCase())
        .filter(Boolean),
    ),
  );
}

function rolesMissingReadiness(indexPayload, requiredRoles = null) {
  const roles = indexPayload?.roles && typeof indexPayload.roles === "object" ? indexPayload.roles : {};
  const targetRoles = requiredRoles || Object.keys(roles);
  return targetRoles.filter((role) => {
    const info = roles[role];
    if (!info || typeof info !== "object") {
      return true;
    }
    return info.ready !== true;
  });
}

function validateSeededLlmReadiness(llmConfigBootstrap, llmTestIndexBootstrap) {
  const requiredRoles = llmConfigBootstrap?.requiredReadyRoles || [];
  if (requiredRoles.length === 0) {
    return {
      ok: true,
      requiredRoles,
      missingReadyRoles: [],
      message: "",
    };
  }

  if (!llmTestIndexBootstrap?.indexPath) {
    return {
      ok: false,
      requiredRoles,
      missingReadyRoles: requiredRoles,
      message:
        "Seeded real-flow LLM config declares required ready roles but no llm_test_index seed was available. " +
        "Provide KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64, KERNELONE_E2E_LLM_TEST_INDEX_JSON, " +
        "KERNELONE_E2E_LLM_TEST_INDEX_PATH, or a host/global llm_test_index.json.",
    };
  }

  let indexPayload = null;
  try {
    indexPayload = JSON.parse(fs.readFileSync(llmTestIndexBootstrap.indexPath, "utf-8"));
  } catch (error) {
    return {
      ok: false,
      requiredRoles,
      missingReadyRoles: requiredRoles,
      message: `Failed to parse seeded llm_test_index.json: ${error instanceof Error ? error.message : String(error)}`,
    };
  }

  const missingReadyRoles = rolesMissingReadiness(indexPayload, requiredRoles);
  return {
    ok: missingReadyRoles.length === 0,
    requiredRoles,
    missingReadyRoles,
    message:
      missingReadyRoles.length > 0
        ? `Seeded llm_test_index.json is missing ready=true for required roles: ${missingReadyRoles.join(", ")}.`
        : "",
  };
}

function existingKerneloneHomeSettings() {
  for (const envName of ["KERNELONE_E2E_HOME", "KERNELONE_HOME"]) {
    const home = String(process.env[envName] || "").trim();
    if (!home) {
      continue;
    }
    const settingsPath = path.join(path.resolve(home), "config", "settings.json");
    if (!fs.existsSync(settingsPath)) {
      continue;
    }
    return {
      source: `env:${envName}`,
      home: path.resolve(home),
      settingsPath,
    };
  }
  return null;
}

function hostSettingsAllowed() {
  return String(process.env.KERNELONE_E2E_ALLOW_HOST_SETTINGS || "").trim() === "1";
}

function resolveSettingsBootstrap() {
  const seed = readSettingsSeed();
  if (seed) {
    return writeSeededSettings(seed);
  }

  if (isCiEnvironment()) {
    throw new Error(
      "CI real-flow requires KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON; host settings fallback is not allowed.",
    );
  }

  const existing = existingKerneloneHomeSettings();
  if (existing) {
    return existing;
  }

  if (hostSettingsAllowed()) {
    return {
      source: "host-settings",
      home: "",
      settingsPath: "",
    };
  }

  return null;
}

function redactSensitiveText(text) {
  let sanitized = text;
  sanitized = sanitized.replace(
    /("(?:api[_-]?key|token|authorization|password|secret)"\s*:\s*")([^"]*)(")/gi,
    "$1[REDACTED]$3",
  );
  sanitized = sanitized.replace(
    /((?:api[_-]?key|token|authorization|password|secret)\s*[=:]\s*)([^\s,;]+)/gi,
    "$1[REDACTED]",
  );
  sanitized = sanitized.replace(/[A-Za-z]:[\\/][^\s"',;]+/g, "[ABSOLUTE_PATH]");
  sanitized = sanitized.replace(/\/(?:Users|home|tmp|var|private)\/[^\s"',;]+/g, "[ABSOLUTE_PATH]");
  return sanitized;
}

function sanitizeRuntimeArtifacts(runtimeRoot) {
  const sourceRoot = path.resolve(runtimeRoot);
  if (!fs.existsSync(sourceRoot)) {
    return null;
  }

  const targetRoot = path.join(repoRoot, "test-results", "electron-real-flow", "runtime-sanitized");
  fs.rmSync(targetRoot, { recursive: true, force: true });
  fs.mkdirSync(targetRoot, { recursive: true });

  const allowedExtensions = new Set([".json", ".jsonl", ".log", ".txt", ".md"]);
  const maxFileBytes = 2 * 1024 * 1024;
  const pending = [sourceRoot];

  while (pending.length > 0) {
    const current = pending.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const sourcePath = path.join(current, entry.name);
      const relativePath = path.relative(sourceRoot, sourcePath);
      const targetPath = path.join(targetRoot, relativePath);
      if (entry.isDirectory()) {
        pending.push(sourcePath);
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }

      const extension = path.extname(entry.name).toLowerCase();
      const stat = fs.statSync(sourcePath);
      if (!allowedExtensions.has(extension) || stat.size > maxFileBytes) {
        continue;
      }

      fs.mkdirSync(path.dirname(targetPath), { recursive: true });
      const text = fs.readFileSync(sourcePath, "utf-8");
      fs.writeFileSync(targetPath, redactSensitiveText(text), "utf-8");
    }
  }

  return targetRoot;
}

function buildPlaywrightArgs() {
  return [
    "playwright",
    "test",
    "-c",
    "playwright.electron.config.ts",
    ...realFlowSpecs,
  ];
}

function buildSpawnCommand(playwrightArgs) {
  if (process.platform === "win32") {
    return {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", "npx.cmd", ...playwrightArgs],
    };
  }
  return {
    command: "npx",
    args: playwrightArgs,
  };
}

const options = parseArgs(process.argv.slice(2));
let settingsBootstrap = null;
let llmConfigBootstrap = null;
let llmTestIndexBootstrap = null;
let llmReadinessSeedValidation = {
  ok: true,
  requiredRoles: [],
  missingReadyRoles: [],
  message: "",
};

try {
  settingsBootstrap = resolveSettingsBootstrap();
  const llmConfigSeed = readLlmConfigSeed();
  if (llmConfigSeed && isSeededSettingsBootstrap(settingsBootstrap)) {
    llmConfigBootstrap = writeSeededLlmConfig(settingsBootstrap.home, llmConfigSeed);
  } else if (llmConfigSeed) {
    throw new Error("LLM config seed requires KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON.");
  }

  const explicitLlmTestIndexSeed = readLlmTestIndexSeed();
  if (explicitLlmTestIndexSeed && isSeededSettingsBootstrap(settingsBootstrap)) {
    llmTestIndexBootstrap = writeSeededLlmTestIndex(settingsBootstrap.home, explicitLlmTestIndexSeed);
  } else if (explicitLlmTestIndexSeed) {
    throw new Error("LLM test index seed requires KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON.");
  } else if (isSeededSettingsBootstrap(settingsBootstrap)) {
    const existingSeed = readExistingLlmTestIndexSeed(settingsBootstrap.home);
    if (existingSeed) {
      llmTestIndexBootstrap = writeSeededLlmTestIndex(settingsBootstrap.home, existingSeed);
    }
  }

  llmReadinessSeedValidation = validateSeededLlmReadiness(llmConfigBootstrap, llmTestIndexBootstrap);
} catch (error) {
  console.error(
    `[e2e:real-flow] invalid real-flow seed: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(2);
}

const playwrightArgs = buildPlaywrightArgs();
const { command, args } = buildSpawnCommand(playwrightArgs);
const childEnv = {
  ...process.env,
  KERNELONE_E2E_USE_REAL_SETTINGS: "1",
};

if (settingsBootstrap?.home) {
  childEnv.KERNELONE_HOME = settingsBootstrap.home;
  childEnv.KERNELONE_RUNTIME_ROOT = path.join(settingsBootstrap.home, "runtime-cache");
  childEnv.KERNELONE_STATE_TO_RAMDISK = "0";
}

if (options.dryRun) {
  process.stdout.write(
    `${JSON.stringify(
      {
        status: "DRY_RUN",
        settings_source: settingsBootstrap?.source || "missing",
        settings_seeded: isSeededSettingsBootstrap(settingsBootstrap),
        llm_config_source: llmConfigBootstrap?.source || "missing",
        llm_config_seeded: Boolean(llmConfigBootstrap?.configPath),
        llm_test_index_source: llmTestIndexBootstrap?.source || "missing",
        llm_test_index_seeded: Boolean(llmTestIndexBootstrap?.indexPath),
        llm_required_ready_roles: llmReadinessSeedValidation.requiredRoles,
        llm_readiness_seed_ok: llmReadinessSeedValidation.ok,
        llm_readiness_missing_roles: llmReadinessSeedValidation.missingReadyRoles,
        ci_host_fallback_allowed: !isCiEnvironment(),
        spawn_command: command,
        spawn_args: args,
        runtime_root: childEnv.KERNELONE_RUNTIME_ROOT || "",
        state_to_ramdisk: childEnv.KERNELONE_STATE_TO_RAMDISK || "",
        specs: realFlowSpecs,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(0);
}

if (!settingsBootstrap) {
  console.error(
    "[e2e:real-flow] real LLM settings are required. Provide " +
      "KERNELONE_E2E_SETTINGS_JSON_BASE64, KERNELONE_E2E_SETTINGS_JSON, " +
      "KERNELONE_HOME with config/settings.json, or explicitly set " +
      "KERNELONE_E2E_ALLOW_HOST_SETTINGS=1.",
  );
  process.exit(2);
}

if (!llmReadinessSeedValidation.ok) {
  console.error(`[e2e:real-flow] invalid LLM readiness seed: ${llmReadinessSeedValidation.message}`);
  process.exit(2);
}

const child = spawn(command, args, {
  cwd: repoRoot,
  env: childEnv,
  stdio: "inherit",
  windowsHide: true,
});

child.on("error", (error) => {
  console.error(`[e2e:real-flow] failed to start playwright: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (childEnv.KERNELONE_RUNTIME_ROOT) {
    try {
      sanitizeRuntimeArtifacts(childEnv.KERNELONE_RUNTIME_ROOT);
    } catch (error) {
      console.error(
        `[e2e:real-flow] failed to sanitize runtime artifacts: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  if (signal) {
    console.error(`[e2e:real-flow] playwright exited via signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
