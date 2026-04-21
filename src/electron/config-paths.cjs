const path = require("path");
const os = require("os");

function expandPath(rawPath) {
  const value = String(rawPath || "").trim();
  if (!value) {
    return "";
  }
  return path.resolve(path.normalize(value));
}

function isTruthyEnv(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

function resolvepolarisRoot(env = process.env, platform = process.platform) {
  const rootOverride = String(env.POLARIS_ROOT || "").trim();
  if (rootOverride) {
    return expandPath(rootOverride);
  }

  const homeOverride = String(env.POLARIS_HOME || "").trim();
  if (homeOverride) {
    const expanded = expandPath(homeOverride);
    const trimmed = expanded.replace(/[\\/]+$/, "");
    if (path.basename(trimmed).toLowerCase() === ".polaris") {
      return path.dirname(trimmed) || trimmed;
    }
    return expanded;
  }

  if (platform === "win32") {
    const appData = String(env.APPDATA || "").trim();
    if (appData) {
      return expandPath(appData);
    }
  }

  const xdg = String(env.XDG_CONFIG_HOME || "").trim();
  if (xdg) {
    return expandPath(xdg);
  }

  return expandPath(os.homedir());
}

function resolvepolarisHome(env = process.env, platform = process.platform) {
  return path.join(resolvepolarisRoot(env, platform), ".polaris");
}

function getGlobalSettingsPath(env = process.env, platform = process.platform) {
  return path.join(resolvepolarisHome(env, platform), "config", "settings.json");
}

function getDesktopBackendInfoPath(env = process.env, platform = process.platform) {
  return path.join(resolvepolarisHome(env, platform), "runtime", "desktop-backend.json");
}

function selectStartupWorkspaceOverride(options = {}) {
  const env = options.env || process.env;
  const persistedWorkspace = expandPath(options.persistedWorkspace || "");
  const envWorkspace = expandPath(env.POLARIS_WORKSPACE || "");

  if (envWorkspace && isTruthyEnv(env.POLARIS_WORKSPACE_FORCE)) {
    return { workspace: envWorkspace, source: "env_forced" };
  }
  if (persistedWorkspace) {
    return { workspace: persistedWorkspace, source: "persisted" };
  }
  if (envWorkspace) {
    return { workspace: envWorkspace, source: "env" };
  }
  return { workspace: "", source: "none" };
}

function isPathInside(basePath, candidatePath, platform = process.platform) {
  const normalizedBase = expandPath(basePath || "");
  const normalizedCandidate = expandPath(candidatePath || "");
  if (!normalizedBase || !normalizedCandidate) {
    return false;
  }

  const lowerCaseOnWin = String(platform || "").toLowerCase() === "win32";
  const base = lowerCaseOnWin ? normalizedBase.toLowerCase() : normalizedBase;
  const candidate = lowerCaseOnWin ? normalizedCandidate.toLowerCase() : normalizedCandidate;

  if (candidate === base) {
    return true;
  }
  const relative = path.relative(base, candidate);
  if (!relative) {
    return true;
  }
  return !relative.startsWith("..") && !path.isAbsolute(relative);
}

function shouldEnableSelfUpgradeMode(options = {}) {
  const env = options.env || process.env;
  const workspace = expandPath(options.workspace || "");
  const repoRoot = expandPath(options.repoRoot || "");
  const isPackaged = Boolean(options.isPackaged);
  const platform = options.platform || process.platform;

  if (isTruthyEnv(env.POLARIS_SELF_UPGRADE_MODE)) {
    return { enabled: true, source: "env" };
  }
  if (!workspace || !repoRoot) {
    return { enabled: false, source: "none" };
  }
  if (!isPackaged && isPathInside(repoRoot, workspace, platform)) {
    return { enabled: true, source: "dev_workspace_inside_repo" };
  }
  return { enabled: false, source: "none" };
}

module.exports = {
  getDesktopBackendInfoPath,
  isTruthyEnv,
  isPathInside,
  resolvepolarisRoot,
  resolvepolarisHome,
  getGlobalSettingsPath,
  selectStartupWorkspaceOverride,
  shouldEnableSelfUpgradeMode,
};
