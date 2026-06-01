const fs = require("fs");
const path = require("path");
const os = require("os");

function expandPath(rawPath) {
  const value = String(rawPath || "").trim();
  if (!value) {
    return "";
  }
  return path.resolve(path.normalize(value));
}

function isDirectoryPath(rawPath) {
  const expanded = expandPath(rawPath);
  if (!expanded) {
    return false;
  }
  try {
    return fs.statSync(expanded).isDirectory();
  } catch {
    return false;
  }
}

function resolveUserHome(env = process.env, platform = process.platform) {
  if (platform === "win32") {
    const userProfile = String(env.USERPROFILE || "").trim();
    if (userProfile) {
      return expandPath(userProfile);
    }
    const homeDrive = String(env.HOMEDRIVE || "").trim();
    const homePath = String(env.HOMEPATH || "").trim();
    if (homeDrive && homePath) {
      return expandPath(path.join(homeDrive, homePath));
    }
  }

  const home = String(env.HOME || "").trim();
  if (home) {
    return expandPath(home);
  }
  return expandPath(os.homedir());
}

function isTruthyEnv(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

function stripUtf8Bom(text) {
  const value = String(text || "");
  return value.charCodeAt(0) === 0xfeff ? value.slice(1) : value;
}

function resolvepolarisRoot(env = process.env, platform = process.platform) {
  const rootOverride = String(env.KERNELONE_ROOT || "").trim();
  if (rootOverride) {
    return expandPath(rootOverride);
  }

  const homeOverride = String(env.KERNELONE_HOME || "").trim();
  if (homeOverride) {
    const expanded = expandPath(homeOverride);
    return path.dirname(expanded.replace(/[\\/]+$/, "")) || expanded;
  }

  const xdg = String(env.XDG_CONFIG_HOME || "").trim();
  if (platform !== "win32" && xdg) {
    return expandPath(xdg);
  }

  return resolveUserHome(env, platform);
}

function resolvepolarisHome(env = process.env, platform = process.platform) {
  const homeOverride = String(env.KERNELONE_HOME || "").trim();
  if (homeOverride) {
    return expandPath(homeOverride);
  }
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
  const validateDirectory = Boolean(options.validateDirectory);
  const persistedWorkspace = expandPath(options.persistedWorkspace || "");
  const envWorkspace = expandPath(env.KERNELONE_WORKSPACE || "");

  function candidate(workspace, source) {
    if (!workspace) {
      return null;
    }
    if (!validateDirectory || isDirectoryPath(workspace)) {
      return { workspace, source };
    }
    return {
      workspace: "",
      source: `${source}_missing`,
      invalidWorkspace: workspace,
    };
  }

  if (envWorkspace && isTruthyEnv(env.KERNELONE_WORKSPACE_FORCE)) {
    return candidate(envWorkspace, "env_forced");
  }
  if (envWorkspace) {
    return candidate(envWorkspace, "env");
  }
  if (persistedWorkspace) {
    return candidate(persistedWorkspace, "persisted");
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

  if (isTruthyEnv(env.KERNELONE_SELF_UPGRADE_MODE)) {
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
  isDirectoryPath,
  isTruthyEnv,
  isPathInside,
  resolvepolarisRoot,
  resolvepolarisHome,
  getGlobalSettingsPath,
  selectStartupWorkspaceOverride,
  shouldEnableSelfUpgradeMode,
  stripUtf8Bom,
};
