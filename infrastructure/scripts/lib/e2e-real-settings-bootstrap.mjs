import fs from "node:fs";
import os from "node:os";
import path from "node:path";

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

function stripUtf8Bom(raw) {
  const text = String(raw || "");
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
}

function parseJsonText(raw, label) {
  try {
    return JSON.parse(stripUtf8Bom(raw));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${label}: ${message}`);
  }
}

function readJsonFile(filePath, label) {
  return parseJsonText(fs.readFileSync(filePath, "utf-8"), label);
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

function isPathInside(basePath, candidatePath) {
  const base = path.resolve(basePath);
  const candidate = path.resolve(candidatePath);
  const normalizedBase = process.platform === "win32" ? base.toLowerCase() : base;
  const normalizedCandidate = process.platform === "win32" ? candidate.toLowerCase() : candidate;
  if (normalizedCandidate === normalizedBase) {
    return true;
  }
  const relative = path.relative(normalizedBase, normalizedCandidate);
  return Boolean(relative) && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function assertOutsideRepo(repoRoot, candidatePath, label) {
  const resolved = path.resolve(candidatePath);
  if (isPathInside(repoRoot, resolved)) {
    throw new Error(`${label} must not be inside the Polaris meta-project repository.`);
  }
  return resolved;
}

function defaultE2EHome(homePrefix) {
  return path.join(os.tmpdir(), "Polaris", homePrefix, `seeded-${process.pid}`);
}

function resolveSeededHome(repoRoot, homePrefix) {
  return assertOutsideRepo(
    repoRoot,
    process.env.KERNELONE_E2E_HOME || process.env.KERNELONE_HOME || defaultE2EHome(homePrefix),
    "KERNELONE_E2E_HOME",
  );
}

function defaultRuntimeRoot(runtimePrefix) {
  if (process.platform === "win32") {
    const xDrive = "X:\\";
    if (fs.existsSync(xDrive)) {
      return path.join(xDrive, "Polaris", "runtime", `${runtimePrefix}-${process.pid}`);
    }
  }
  return path.join(os.tmpdir(), "Polaris", "runtime", `${runtimePrefix}-${process.pid}`);
}

function resolveRuntimeRoot(repoRoot, runtimePrefix) {
  return assertOutsideRepo(
    repoRoot,
    process.env.KERNELONE_E2E_RUNTIME_ROOT || process.env.KERNELONE_RUNTIME_ROOT || defaultRuntimeRoot(runtimePrefix),
    "KERNELONE_RUNTIME_ROOT",
  );
}

function writeSeededSettings(repoRoot, homePrefix, seed) {
  const parsed = parseJsonText(seed.raw, "Invalid seeded settings JSON");
  const home = resolveSeededHome(repoRoot, homePrefix);
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

  const parsed = parseJsonText(seed.raw, "Invalid seeded LLM config JSON");
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

  const parsed = parseJsonText(seed.raw, "Invalid seeded LLM test index JSON");
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
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    return null;
  }
  return {
    source,
    raw: stripUtf8Bom(fs.readFileSync(resolved, "utf-8")),
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

function readExistingLlmConfigBootstrap(settingsBootstrap) {
  const home = String(settingsBootstrap?.home || "").trim();
  if (!home) {
    return null;
  }
  const configPath = path.join(path.resolve(home), "config", "llm", "llm_config.json");
  if (!fs.existsSync(configPath) || !fs.statSync(configPath).isFile()) {
    return null;
  }
  const parsed = readJsonFile(configPath, "Failed to parse existing llm_config.json");
  return {
    source: `${settingsBootstrap.source}:llm_config`,
    configPath,
    requiredReadyRoles: extractRequiredReadyRoles(parsed),
  };
}

function readExistingLlmTestIndexBootstrap(settingsBootstrap) {
  const home = String(settingsBootstrap?.home || "").trim();
  if (!home) {
    return null;
  }
  const indexPath = path.join(path.resolve(home), "config", "llm", "llm_test_index.json");
  if (!fs.existsSync(indexPath) || !fs.statSync(indexPath).isFile()) {
    return null;
  }
  const parsed = readJsonFile(indexPath, "Failed to parse existing llm_test_index.json");
  return {
    source: `${settingsBootstrap.source}:llm_test_index`,
    indexPath,
    missingReadyRoles: rolesMissingReadiness(parsed),
  };
}

function extractRequiredReadyRoles(config) {
  const policies = config?.policies;
  const required = policies && typeof policies === "object" ? policies.required_ready_roles : [];
  if (!Array.isArray(required)) {
    return [];
  }
  return Array.from(new Set(required.map((role) => String(role || "").trim().toLowerCase()).filter(Boolean)));
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

function modelIdentityKey(model) {
  return String(model || "")
    .trim()
    .toLowerCase()
    .replace(/[/:]+/g, "-")
    .replace(/[\s_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function modelIdentityAliases(model) {
  const token = String(model || "").trim();
  if (!token) {
    return new Set();
  }

  const aliases = new Set([modelIdentityKey(token)]);
  const parts = token.split(/[/:]+/).filter((part) => part.trim());
  if (parts.length > 0) {
    aliases.add(modelIdentityKey(parts[parts.length - 1]));
  }
  aliases.delete("");
  return aliases;
}

function modelIdentityEqual(left, right) {
  const leftAliases = modelIdentityAliases(left);
  const rightAliases = modelIdentityAliases(right);
  for (const alias of leftAliases) {
    if (rightAliases.has(alias)) {
      return true;
    }
  }
  return false;
}

function roleReadinessBindingIssues(configPayload, indexPayload, requiredRoles) {
  const rolesCfg = configPayload?.roles && typeof configPayload.roles === "object" ? configPayload.roles : {};
  const rolesIndex = indexPayload?.roles && typeof indexPayload.roles === "object" ? indexPayload.roles : {};
  const providerIndex =
    indexPayload?.providers && typeof indexPayload.providers === "object" ? indexPayload.providers : {};
  const issues = [];

  for (const role of requiredRoles) {
    const roleCfg = rolesCfg[role] && typeof rolesCfg[role] === "object" ? rolesCfg[role] : {};
    const roleInfo = rolesIndex[role] && typeof rolesIndex[role] === "object" ? rolesIndex[role] : {};
    const providerId = String(roleCfg.provider_id || "").trim();
    const model = String(roleCfg.model || "").trim();
    const providerInfo =
      providerId && providerIndex[providerId] && typeof providerIndex[providerId] === "object"
        ? providerIndex[providerId]
        : {};
    const testedProviderId = String(roleInfo.provider_id || (providerInfo.model ? providerId : "") || "").trim();
    const testedModel = String(roleInfo.model || providerInfo.model || "").trim();

    let reason = "";
    if (!providerId || !model) {
      reason = "role_binding_missing";
    } else if (testedProviderId && testedProviderId !== providerId) {
      reason = "provider_mismatch";
    } else if (!testedModel) {
      reason = "tested_model_missing";
    } else if (!modelIdentityEqual(testedModel, model)) {
      reason = "model_mismatch";
    }

    if (reason) {
      issues.push({
        role,
        reason,
        provider_id: providerId,
        model,
        tested_provider_id: testedProviderId,
        tested_model: testedModel,
      });
    }
  }

  return issues;
}

function validateSeededLlmReadiness(llmConfigBootstrap, llmTestIndexBootstrap) {
  const requiredRoles = llmConfigBootstrap?.requiredReadyRoles || [];
  if (requiredRoles.length === 0) {
    return {
      ok: true,
      requiredRoles,
      missingReadyRoles: [],
      bindingIssues: [],
      message: "",
    };
  }

  if (!llmTestIndexBootstrap?.indexPath) {
    return {
      ok: false,
      requiredRoles,
      missingReadyRoles: requiredRoles,
      bindingIssues: [],
      message:
        "Seeded real-flow LLM config declares required ready roles but no llm_test_index seed was available. " +
        "Provide KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64, KERNELONE_E2E_LLM_TEST_INDEX_JSON, " +
        "KERNELONE_E2E_LLM_TEST_INDEX_PATH, or a host/global llm_test_index.json.",
    };
  }

  let indexPayload = null;
  let configPayload = null;
  try {
    indexPayload = readJsonFile(llmTestIndexBootstrap.indexPath, "Failed to parse seeded llm_test_index.json");
    configPayload = readJsonFile(llmConfigBootstrap.configPath, "Failed to parse seeded llm_config.json");
  } catch (error) {
    return {
      ok: false,
      requiredRoles,
      missingReadyRoles: requiredRoles,
      bindingIssues: [],
      message: `Failed to parse seeded llm_test_index.json: ${error instanceof Error ? error.message : String(error)}`,
    };
  }

  const missingReadyRoles = rolesMissingReadiness(indexPayload, requiredRoles);
  const bindingIssues = roleReadinessBindingIssues(configPayload, indexPayload, requiredRoles);
  const issueSummary = bindingIssues.map((issue) => `${issue.role}:${issue.reason}`).join(", ");
  return {
    ok: missingReadyRoles.length === 0 && bindingIssues.length === 0,
    requiredRoles,
    missingReadyRoles,
    bindingIssues,
    message:
      missingReadyRoles.length > 0
        ? `Seeded llm_test_index.json is missing ready=true for required roles: ${missingReadyRoles.join(", ")}.`
        : bindingIssues.length > 0
          ? `Seeded llm_test_index.json has stale provider/model readiness bindings: ${issueSummary}.`
          : "",
  };
}

function existingKerneloneHomeSettings(repoRoot) {
  for (const envName of ["KERNELONE_E2E_HOME", "KERNELONE_HOME"]) {
    const home = String(process.env[envName] || "").trim();
    if (!home) {
      continue;
    }
    const resolvedHome = assertOutsideRepo(repoRoot, home, envName);
    const settingsPath = path.join(resolvedHome, "config", "settings.json");
    if (!fs.existsSync(settingsPath)) {
      continue;
    }
    return {
      source: `env:${envName}`,
      home: resolvedHome,
      settingsPath,
    };
  }
  return null;
}

function hostSettingsAllowed() {
  return String(process.env.KERNELONE_E2E_ALLOW_HOST_SETTINGS || "").trim() === "1";
}

function resolveSettingsBootstrap(repoRoot, homePrefix) {
  const seed = readSettingsSeed();
  if (seed) {
    return writeSeededSettings(repoRoot, homePrefix, seed);
  }

  if (isCiEnvironment()) {
    throw new Error(
      "CI real-flow requires KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON; host settings fallback is not allowed.",
    );
  }

  const existing = existingKerneloneHomeSettings(repoRoot);
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

export function resolveE2ERealSettingsBootstrap({
  repoRoot,
  homePrefix = "e2e-real-flow-home",
  runtimePrefix = "e2e-real-flow",
} = {}) {
  if (!repoRoot) {
    throw new Error("repoRoot is required for E2E real settings bootstrap.");
  }

  const settingsBootstrap = resolveSettingsBootstrap(repoRoot, homePrefix);
  let llmConfigBootstrap = null;
  let llmTestIndexBootstrap = null;
  let llmReadinessSeedValidation = {
    ok: true,
    requiredRoles: [],
    missingReadyRoles: [],
    bindingIssues: [],
    message: "",
  };

  const llmConfigSeed = readLlmConfigSeed();
  if (llmConfigSeed && isSeededSettingsBootstrap(settingsBootstrap)) {
    llmConfigBootstrap = writeSeededLlmConfig(settingsBootstrap.home, llmConfigSeed);
  } else if (llmConfigSeed) {
    throw new Error("LLM config seed requires KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON.");
  } else {
    llmConfigBootstrap = readExistingLlmConfigBootstrap(settingsBootstrap);
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
  } else {
    llmTestIndexBootstrap = readExistingLlmTestIndexBootstrap(settingsBootstrap);
  }

  llmReadinessSeedValidation = validateSeededLlmReadiness(llmConfigBootstrap, llmTestIndexBootstrap);
  const envPatch = {};
  if (settingsBootstrap?.home) {
    envPatch.KERNELONE_HOME = settingsBootstrap.home;
    envPatch.KERNELONE_RUNTIME_ROOT = resolveRuntimeRoot(repoRoot, runtimePrefix);
    envPatch.KERNELONE_STATE_TO_RAMDISK = "0";
  }

  return {
    settingsBootstrap,
    llmConfigBootstrap,
    llmTestIndexBootstrap,
    llmReadinessSeedValidation,
    envPatch,
    dryRunSummary: {
      settings_source: settingsBootstrap?.source || "missing",
      settings_seeded: isSeededSettingsBootstrap(settingsBootstrap),
      llm_config_source: llmConfigBootstrap?.source || "missing",
      llm_config_seeded: Boolean(llmConfigBootstrap?.configPath),
      llm_test_index_source: llmTestIndexBootstrap?.source || "missing",
      llm_test_index_seeded: Boolean(llmTestIndexBootstrap?.indexPath),
      llm_required_ready_roles: llmReadinessSeedValidation.requiredRoles,
      llm_readiness_seed_ok: llmReadinessSeedValidation.ok,
      llm_readiness_missing_roles: llmReadinessSeedValidation.missingReadyRoles,
      llm_readiness_binding_issues: llmReadinessSeedValidation.bindingIssues,
      ci_host_fallback_allowed: !isCiEnvironment(),
      runtime_root: envPatch.KERNELONE_RUNTIME_ROOT || "",
      state_to_ramdisk: envPatch.KERNELONE_STATE_TO_RAMDISK || "",
    },
  };
}

export function redactSensitiveText(text) {
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

export function sanitizeRuntimeArtifacts(repoRoot, runtimeRoot, outputSubdir) {
  const sourceRoot = path.resolve(runtimeRoot);
  if (!fs.existsSync(sourceRoot)) {
    return null;
  }

  const targetRoot = path.join(repoRoot, "test-results", outputSubdir, "runtime-sanitized");
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
