const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const {
  getDesktopBackendInfoPath,
  getGlobalSettingsPath,
  isPathInside,
  isTruthyEnv,
  resolveHarborpilotHome,
  resolveHarborpilotRoot,
  selectStartupWorkspaceOverride,
  shouldEnableSelfUpgradeMode,
} = require("./config-paths.cjs");

test("resolveHarborpilotRoot prefers APPDATA on Windows when no overrides exist", () => {
  const env = {
    APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
  };

  const result = resolveHarborpilotRoot(env, "win32");
  assert.equal(result, path.resolve("C:\\Users\\tester\\AppData\\Roaming"));
});

test("resolveHarborpilotRoot trims POLARIS_HOME when it already points at .polaris", () => {
  const env = {
    POLARIS_HOME: "C:\\Users\\tester\\AppData\\Roaming\\.polaris",
  };

  const result = resolveHarborpilotRoot(env, "win32");
  assert.equal(result, path.resolve("C:\\Users\\tester\\AppData\\Roaming"));
});

test("getGlobalSettingsPath matches backend-style root resolution", () => {
  const env = {
    APPDATA: "C:\\Users\\tester\\AppData\\Roaming",
  };

  const home = resolveHarborpilotHome(env, "win32");
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
      POLARIS_WORKSPACE: "C:\\Users\\dains\\Documents\\GitLab\\polaris",
    },
    persistedWorkspace: "C:\\Temp\\FileServer",
  });

  assert.equal(result.source, "persisted");
  assert.equal(result.workspace, path.resolve("C:\\Temp\\FileServer"));
});

test("selectStartupWorkspaceOverride respects forced env override", () => {
  const result = selectStartupWorkspaceOverride({
    env: {
      POLARIS_WORKSPACE: "C:\\Users\\dains\\Documents\\GitLab\\polaris",
      POLARIS_WORKSPACE_FORCE: "true",
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
      POLARIS_SELF_UPGRADE_MODE: "1",
    },
    workspace: "C:\\Temp\\ExternalProject",
    repoRoot: "C:\\Repo\\Polaris",
    isPackaged: true,
    platform: "win32",
  });
  assert.deepEqual(result, { enabled: true, source: "env" });
});
