const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const {
  getDesktopBackendInfoPath,
  getGlobalSettingsPath,
  isPathInside,
  isTruthyEnv,
  resolvepolarisHome,
  resolvepolarisRoot,
  selectStartupWorkspaceOverride,
  shouldEnableSelfUpgradeMode,
} = require("./config-paths.cjs");

test("resolvepolarisRoot prefers APPDATA on Windows when no overrides exist", () => {
  const env = {
    APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
    USERPROFILE: "C:\\Users\\tester",
  };

  const result = resolvepolarisRoot(env, "win32");
  assert.equal(result, path.resolve("C:\\Users\\tester\\AppData\\Roaming"));
});

test("resolvepolarisRoot trims KERNELONE_HOME when it already points at .polaris", () => {
  const env = {
    KERNELONE_HOME: "C:\\Users\\tester\\AppData\\Roaming\\.polaris",
  };

  const result = resolvepolarisRoot(env, "win32");
  assert.equal(result, path.resolve("C:\\Users\\tester\\AppData\\Roaming"));
});

test("getGlobalSettingsPath treats KERNELONE_HOME as the complete home path", () => {
  const env = {
    KERNELONE_HOME: "C:\\KernelOne\\custom-home",
  };

  assert.equal(resolvepolarisHome(env, "win32"), path.resolve("C:\\KernelOne\\custom-home"));
  assert.equal(
    getGlobalSettingsPath(env, "win32"),
    path.resolve("C:\\KernelOne\\custom-home\\config\\settings.json"),
  );
});

test("getGlobalSettingsPath matches backend-style root resolution", () => {
  const env = {
    APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
    USERPROFILE: "C:\\Users\\tester",
  };

  const home = resolvepolarisHome(env, "win32");
  const settingsPath = getGlobalSettingsPath(env, "win32");

  assert.equal(home, path.resolve("C:\\Users\\tester\\AppData\\Roaming\\.polaris"));
  assert.equal(
    settingsPath,
    path.resolve("C:\\Users\\tester\\AppData\\Roaming\\.polaris\\config\\settings.json"),
  );
});

test("getDesktopBackendInfoPath stores backend bridge state under Polaris runtime", () => {
  const env = {
    APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
    USERPROFILE: "C:\\Users\\tester",
  };

  const backendInfoPath = getDesktopBackendInfoPath(env, "win32");

  assert.equal(
    backendInfoPath,
    path.resolve("C:\\Users\\tester\\AppData\\Roaming\\.polaris\\runtime\\desktop-backend.json"),
  );
});

test("selectStartupWorkspaceOverride prefers persisted workspace by default", () => {
  const result = selectStartupWorkspaceOverride({
    env: {
      KERNELONE_WORKSPACE: "C:\\Users\\dains\\Documents\\GitLab\\polaris",
    },
    persistedWorkspace: "C:\\Temp\\FileServer",
  });

  assert.equal(result.source, "persisted");
  assert.equal(result.workspace, path.resolve("C:\\Temp\\FileServer"));
});

test("selectStartupWorkspaceOverride respects forced env override", () => {
  const result = selectStartupWorkspaceOverride({
    env: {
      KERNELONE_WORKSPACE: "C:\\Users\\dains\\Documents\\GitLab\\polaris",
      KERNELONE_WORKSPACE_FORCE: "true",
    },
    persistedWorkspace: "C:\\Temp\\FileServer",
  });

  assert.equal(isTruthyEnv("true"), true);
  assert.equal(result.source, "env_forced");
  assert.equal(result.workspace, path.resolve("C:\\Users\\dains\\Documents\\GitLab\\polaris"));
});

test("isPathInside detects nested paths", () => {
  assert.equal(
    isPathInside("C:\\Repo\\Polaris", "C:\\Repo\\Polaris\\src\\backend", "win32"),
    true,
  );
  assert.equal(
    isPathInside("C:\\Repo\\Polaris", "C:\\Repo\\OtherProject", "win32"),
    false,
  );
});

test("shouldEnableSelfUpgradeMode enables in dev when workspace is inside repo root", () => {
  const result = shouldEnableSelfUpgradeMode({
    env: {},
    workspace: "C:\\Repo\\Polaris\\src\\backend",
    repoRoot: "C:\\Repo\\Polaris",
    isPackaged: false,
    platform: "win32",
  });
  assert.deepEqual(result, { enabled: true, source: "dev_workspace_inside_repo" });
});

test("shouldEnableSelfUpgradeMode does not auto-enable when packaged", () => {
  const result = shouldEnableSelfUpgradeMode({
    env: {},
    workspace: "C:\\Repo\\Polaris\\src\\backend",
    repoRoot: "C:\\Repo\\Polaris",
    isPackaged: true,
    platform: "win32",
  });
  assert.deepEqual(result, { enabled: false, source: "none" });
});

test("shouldEnableSelfUpgradeMode respects env override", () => {
  const result = shouldEnableSelfUpgradeMode({
    env: {
      KERNELONE_SELF_UPGRADE_MODE: "1",
    },
    workspace: "C:\\Temp\\ExternalProject",
    repoRoot: "C:\\Repo\\Polaris",
    isPackaged: true,
    platform: "win32",
  });
  assert.deepEqual(result, { enabled: true, source: "env" });
});
