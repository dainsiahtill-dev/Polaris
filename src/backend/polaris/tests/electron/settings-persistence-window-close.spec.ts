import { _electron as electron, type ElectronApplication, type Page, type TestInfo } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "./fixtures";
import type { TestEnvironment } from "./fixtures";

type BackendInfo = { baseUrl?: string; token?: string };

type LlmConfigPayload = {
  schema_version?: number;
  providers?: Record<string, Record<string, unknown>>;
  roles?: Record<string, { provider_id?: string; model?: string; profile?: string }>;
  policies?: Record<string, unknown>;
  visual_layout?: Record<string, unknown>;
  visual_node_states?: Record<string, unknown>;
};

type SettingsPayload = {
  close_to_tray?: boolean;
};

type WindowStateSnapshot = Array<{
  visible: boolean;
  destroyed: boolean;
}>;

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const info = await window.evaluate(async () => {
    const api = (window as Window & {
      polaris?: { getBackendInfo?: () => Promise<BackendInfo> };
    }).polaris;
    if (!api?.getBackendInfo) {
      throw new Error("polaris.getBackendInfo missing");
    }
    return await api.getBackendInfo();
  });
  if (!info?.baseUrl || !info?.token) {
    throw new Error("backend info missing");
  }
  return { baseUrl: info.baseUrl, token: info.token };
}

async function requestJson<T>(
  window: Page,
  endpoint: string,
  options?: { method?: "GET" | "POST"; body?: Record<string, unknown> },
): Promise<T> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token, apiPath, method, body }) => {
      const response = await fetch(`${baseUrl}${apiPath}`, {
        method,
        cache: "no-store",
        headers: {
          authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!response.ok) {
        const detail = await response.text().catch(() => "");
        throw new Error(`fetch ${apiPath} failed: ${response.status} ${detail}`);
      }
      return (await response.json()) as unknown;
    },
    {
      baseUrl: backend.baseUrl,
      token: backend.token,
      apiPath: endpoint,
      method: options?.method || "GET",
      body: options?.body,
    },
  ) as Promise<T>;
}

async function openSettings(window: Page): Promise<void> {
  if (await window.getByTestId("settings-modal").isVisible().catch(() => false)) {
    return;
  }
  const candidates = [
    window.getByTestId("control-panel-open-settings"),
    window.getByRole("button", { name: /^Settings$/ }),
    window.locator('button[title="Settings"]'),
    window.locator('button[aria-label="Settings"]'),
  ];
  let lastError: unknown = null;
  for (const locator of candidates) {
    const candidate = locator.first();
    try {
      await candidate.click({ timeout: 15_000 });
      await expect(window.getByTestId("settings-modal")).toBeVisible({ timeout: 30_000 });
      return;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`No visible enabled settings button found: ${String(lastError)}`);
}

async function attachJsonEvidence(testInfo: TestInfo, name: string, payload: unknown): Promise<void> {
  await testInfo.attach(name, {
    body: JSON.stringify(payload, null, 2),
    contentType: "application/json",
  });
}

async function readJsonFile<T>(filePath: string): Promise<T> {
  return JSON.parse(await fs.promises.readFile(filePath, "utf-8")) as T;
}

function resolveRepoRoot(): string {
  return path.resolve(process.cwd());
}

function resolveElectronMain(): string {
  const mainPath = path.join(resolveRepoRoot(), "src", "electron", "main.cjs");
  if (!fs.existsSync(mainPath)) {
    throw new Error(`Electron main entry missing: ${mainPath}`);
  }
  return mainPath;
}

function resolveVenvPython(): string {
  const pythonPath = process.platform === "win32"
    ? path.join(resolveRepoRoot(), ".venv", "Scripts", "python.exe")
    : path.join(resolveRepoRoot(), ".venv", "bin", "python");
  return fs.existsSync(pythonPath) ? pythonPath : "";
}

function mergeCorsOrigins(existing: string | undefined, origin: string): string {
  const values = new Set(
    (existing || "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
  values.add(origin);
  return Array.from(values).join(",");
}

async function launchRestartedElectronApp(
  testEnv: TestEnvironment,
  devServerUrl: string,
): Promise<ElectronApplication> {
  const restartRuntimeRoot = await fs.promises.mkdtemp(`${testEnv.isolatedRuntimeRoot}-restart-`);
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    KERNELONE_E2E: "1",
    KERNELONE_E2E_ALLOW_MULTI_INSTANCE: "1",
    KERNELONE_RATE_LIMIT_EXEMPT_LOOPBACK: process.env.KERNELONE_RATE_LIMIT_EXEMPT_LOOPBACK || "1",
    KERNELONE_HOME: testEnv.settingsHome,
    KERNELONE_RUNTIME_ROOT: restartRuntimeRoot,
    KERNELONE_STATE_TO_RAMDISK: "0",
    KERNELONE_WORKSPACE: testEnv.isolatedWorkspace,
    KERNELONE_DEV_SERVER_URL: devServerUrl,
  };
  delete env.ELECTRON_RUN_AS_NODE;
  try {
    env.KERNELONE_CORS_ORIGINS = mergeCorsOrigins(env.KERNELONE_CORS_ORIGINS, new URL(devServerUrl).origin);
  } catch {
    // Keep parity with the shared fixture: invalid dev URLs simply skip CORS augmentation.
  }

  if (testEnv.useRealSettings) {
    env.KERNELONE_E2E_PROTECT_GLOBAL_SETTINGS = "1";
  }
  const pythonPath = resolveVenvPython();
  if (pythonPath && !env.KERNELONE_PYTHON) {
    env.KERNELONE_PYTHON = pythonPath;
  }

  return electron.launch({
    args: [resolveElectronMain()],
    env,
  });
}

async function captureLlmConfigPostBodies(window: Page): Promise<unknown[]> {
  const bodies: unknown[] = [];
  await window.route("**/v2/llm/config", async (route) => {
    const request = route.request();
    if (request.method().toUpperCase() === "POST") {
      bodies.push(request.postDataJSON());
    }
    await route.continue();
  });
  return bodies;
}

async function captureSettingsPostBodies(window: Page): Promise<unknown[]> {
  const bodies: unknown[] = [];
  await window.route("**/settings", async (route) => {
    const request = route.request();
    if (request.method().toUpperCase() === "POST") {
      bodies.push(request.postDataJSON());
    }
    await route.continue();
  });
  return bodies;
}

async function saveSettings(window: Page): Promise<void> {
  await window.getByTestId("settings-save").click();
  await expect(window.getByTestId("settings-modal")).toBeHidden({ timeout: 30_000 });
}

async function openLlmSettings(window: Page): Promise<void> {
  await openSettings(window);
  await window.getByTestId("settings-tab-llm").click();
  await expect(window.getByTestId("llm-readiness-summary")).toBeVisible({ timeout: 30_000 });
}

function providerCard(window: Page, providerId: string) {
  return window.locator(`[data-provider-id="${providerId}"]`);
}

function providerDeleteButton(window: Page, providerId: string) {
  return providerCard(window, providerId).locator('[data-provider-action="delete"]');
}

function asLlmConfigPayload(value: unknown): LlmConfigPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const payload = value as LlmConfigPayload;
  if (!payload.providers || typeof payload.providers !== "object" || Array.isArray(payload.providers)) {
    return null;
  }
  return payload;
}

function latestLlmConfigPostBody(postBodies: unknown[]): LlmConfigPayload {
  for (let index = postBodies.length - 1; index >= 0; index -= 1) {
    const payload = asLlmConfigPayload(postBodies[index]);
    if (payload) {
      return payload;
    }
  }
  throw new Error(`No LLM config POST body captured; bodies=${JSON.stringify(postBodies)}`);
}

function expectProviderAbsentFromConfig(config: LlmConfigPayload, providerId: string): void {
  expect(config.providers?.[providerId]).toBeUndefined();
  for (const [roleId, roleConfig] of Object.entries(config.roles || {})) {
    expect(roleConfig?.provider_id, `role ${roleId} should not reference deleted provider`).not.toBe(providerId);
  }
}

function latestSettingsPostBody(postBodies: unknown[]): SettingsPayload {
  for (let index = postBodies.length - 1; index >= 0; index -= 1) {
    const payload = postBodies[index];
    if (payload && typeof payload === "object" && !Array.isArray(payload)) {
      return payload as SettingsPayload;
    }
  }
  throw new Error(`No settings POST body captured; bodies=${JSON.stringify(postBodies)}`);
}

async function expectCloseToTrayCheckboxState(window: Page, enabled: boolean): Promise<void> {
  await openSettings(window);
  await window.getByTestId("settings-tab-general").click();
  const checkbox = window.getByTestId("settings-close-to-tray-checkbox");
  await expect(checkbox).toBeVisible({ timeout: 30_000 });
  if (enabled) {
    await expect(checkbox).toBeChecked();
  } else {
    await expect(checkbox).not.toBeChecked();
  }
}

async function closeSettingsIfOpen(window: Page): Promise<void> {
  if (!(await window.getByTestId("settings-modal").isVisible().catch(() => false))) {
    return;
  }
  await window.getByTestId("settings-modal-close").click();
  await expect(window.getByTestId("settings-modal")).toBeHidden({ timeout: 30_000 });
}

async function setCloseToTrayThroughSettings(window: Page, enabled: boolean): Promise<void> {
  await openSettings(window);
  await window.getByTestId("settings-tab-general").click();
  const checkbox = window.getByTestId("settings-close-to-tray-checkbox");
  await expect(checkbox).toBeVisible({ timeout: 30_000 });
  if ((await checkbox.isChecked()) !== enabled) {
    await checkbox.click();
  }
  await saveSettings(window);
  const settings = await requestJson<SettingsPayload>(window, "/settings");
  expect(settings.close_to_tray).toBe(enabled);
  await expectCloseToTrayCheckboxState(window, enabled);
  await closeSettingsIfOpen(window);
}

async function closeWindowFromRenderer(window: Page): Promise<void> {
  await window.evaluate(() => {
    const api = (window as Window & {
      polaris?: { windowControl?: { close?: () => Promise<void> } };
    }).polaris;
    void api?.windowControl?.close?.();
    return "close-requested";
  });
}

async function windowStates(electronApp: ElectronApplication): Promise<WindowStateSnapshot> {
  return electronApp.evaluate(({ BrowserWindow }) =>
    BrowserWindow.getAllWindows().map((win) => ({
      visible: win.isVisible(),
      destroyed: win.isDestroyed(),
    })),
  );
}

test("LLM provider deletion persists after save and settings reopen", async ({ window }, testInfo) => {
  const llmConfigPostBodies = await captureLlmConfigPostBodies(window);
  const initialConfig: LlmConfigPayload = {
    schema_version: 2,
    providers: {
      persist_keep: {
        type: "openai_compat",
        name: "Persist Keep Provider",
        base_url: "http://127.0.0.1:9/v1",
        model: "persist-keep-model",
      },
      persist_delete: {
        type: "openai_compat",
        name: "Persist Delete Provider",
        base_url: "http://127.0.0.1:9/v1",
        model: "persist-delete-model",
      },
    },
    roles: {
      pm: { provider_id: "persist_delete", model: "persist-delete-model", profile: "pm-default" },
      chief_engineer: {
        provider_id: "persist_keep",
        model: "persist-keep-model",
        profile: "chief-engineer-blueprint",
      },
      director: { provider_id: "persist_keep", model: "persist-keep-model", profile: "director-default" },
      qa: { provider_id: "persist_keep", model: "persist-keep-model", profile: "qa-strict" },
      architect: { provider_id: "persist_keep", model: "persist-keep-model", profile: "architect-writer" },
    },
    policies: { required_ready_roles: ["pm", "director"] },
    visual_layout: {},
    visual_node_states: {},
  };

  try {
    await requestJson<LlmConfigPayload>(window, "/v2/llm/config", {
      method: "POST",
      body: initialConfig as Record<string, unknown>,
    });

    await openLlmSettings(window);
    await expect(providerCard(window, "persist_keep")).toBeVisible({ timeout: 30_000 });
    await expect(providerCard(window, "persist_delete")).toBeVisible({ timeout: 30_000 });

    await providerDeleteButton(window, "persist_delete").click();
    await expect(providerCard(window, "persist_delete")).toHaveCount(0);
    await saveSettings(window);

    const latestPostBody = latestLlmConfigPostBody(llmConfigPostBodies);
    expectProviderAbsentFromConfig(latestPostBody, "persist_delete");
    expect(latestPostBody.providers?.persist_keep).toBeTruthy();
    expect(latestPostBody.roles?.pm?.provider_id).toBeUndefined();
    expect(latestPostBody.roles?.pm?.model).toBeUndefined();

    const savedConfig = await requestJson<LlmConfigPayload>(window, "/v2/llm/config");
    expect(savedConfig.providers?.persist_keep).toBeTruthy();
    expectProviderAbsentFromConfig(savedConfig, "persist_delete");
    expect(savedConfig.roles?.pm?.provider_id).toBeUndefined();
    expect(savedConfig.roles?.pm?.model).toBeUndefined();

    await openLlmSettings(window);
    await expect(providerCard(window, "persist_keep")).toBeVisible({ timeout: 30_000 });
    await expect(providerCard(window, "persist_delete")).toHaveCount(0);

    await attachJsonEvidence(testInfo, "llm-config-final-save-body", latestPostBody);
    await attachJsonEvidence(testInfo, "llm-config-saved-readback", savedConfig);
  } finally {
    await attachJsonEvidence(testInfo, "llm-config-post-bodies", llmConfigPostBodies);
  }
});

test("window close hides to tray when close_to_tray is enabled", async ({ electronApp, window }, testInfo) => {
  const settingsPostBodies = await captureSettingsPostBodies(window);
  await setCloseToTrayThroughSettings(window, true);
  expect(latestSettingsPostBody(settingsPostBodies).close_to_tray).toBe(true);
  await attachJsonEvidence(testInfo, "settings-post-bodies-close-to-tray-enabled", settingsPostBodies);
  await closeWindowFromRenderer(window);

  await expect.poll(async () => windowStates(electronApp), { timeout: 15_000 }).toEqual([
    { visible: false, destroyed: false },
  ]);
});

test("window close exits the app when close_to_tray is disabled", async ({ electronApp, window }, testInfo) => {
  const settingsPostBodies = await captureSettingsPostBodies(window);
  await setCloseToTrayThroughSettings(window, false);
  expect(latestSettingsPostBody(settingsPostBodies).close_to_tray).toBe(false);
  await attachJsonEvidence(testInfo, "settings-post-bodies-close-to-tray-disabled", settingsPostBodies);

  const closeEvent = electronApp.waitForEvent("close", { timeout: 20_000 });
  await closeWindowFromRenderer(window);
  await closeEvent;
});

test("LLM provider deletion and close behavior survive Electron restart", async ({ window, testEnv }, testInfo) => {
  test.skip(testEnv.useRealSettings, "restart persistence test requires an isolated settings home");

  const llmConfigPath = path.join(testEnv.settingsHome, "config", "llm", "llm_config.json");
  const settingsPath = path.join(testEnv.settingsHome, "config", "settings.json");
  const initialConfig: LlmConfigPayload = {
    schema_version: 2,
    providers: {
      restart_keep: {
        type: "openai_compat",
        name: "Restart Keep Provider",
        base_url: "http://127.0.0.1:9/v1",
        model: "restart-keep-model",
      },
      restart_delete: {
        type: "openai_compat",
        name: "Restart Delete Provider",
        base_url: "http://127.0.0.1:9/v1",
        model: "restart-delete-model",
      },
    },
    roles: {
      pm: { provider_id: "restart_delete", model: "restart-delete-model", profile: "pm-default" },
      director: { provider_id: "restart_keep", model: "restart-keep-model", profile: "director-default" },
      chief_engineer: {
        provider_id: "restart_keep",
        model: "restart-keep-model",
        profile: "chief-engineer-blueprint",
      },
      qa: { provider_id: "restart_keep", model: "restart-keep-model", profile: "qa-strict" },
      architect: { provider_id: "restart_keep", model: "restart-keep-model", profile: "architect-writer" },
    },
    policies: { required_ready_roles: ["pm", "director"] },
    visual_layout: {},
    visual_node_states: {},
  };

  await requestJson<LlmConfigPayload>(window, "/v2/llm/config", {
    method: "POST",
    body: initialConfig as Record<string, unknown>,
  });

  await openLlmSettings(window);
  await expect(providerCard(window, "restart_delete")).toBeVisible({ timeout: 30_000 });
  await providerDeleteButton(window, "restart_delete").click();
  await expect(providerCard(window, "restart_delete")).toHaveCount(0);
  await saveSettings(window);
  await setCloseToTrayThroughSettings(window, false);

  const savedFileConfig = await readJsonFile<LlmConfigPayload>(llmConfigPath);
  const savedSettings = await readJsonFile<SettingsPayload>(settingsPath);
  expectProviderAbsentFromConfig(savedFileConfig, "restart_delete");
  expect(savedFileConfig.roles?.pm?.provider_id).toBeUndefined();
  expect(savedFileConfig.roles?.pm?.model).toBeUndefined();
  expect(savedSettings.close_to_tray).toBe(false);
  await attachJsonEvidence(testInfo, "restart-before-llm-config-file", savedFileConfig);
  await attachJsonEvidence(testInfo, "restart-before-settings-file", savedSettings);

  let restartedApp: ElectronApplication | null = await launchRestartedElectronApp(testEnv, window.url());
  try {
    const restartedWindow = await restartedApp.firstWindow();
    await expect(restartedWindow.locator("#root")).toHaveCount(1, { timeout: 60_000 });

    const restartedConfig = await requestJson<LlmConfigPayload>(restartedWindow, "/v2/llm/config");
    const restartedSettings = await requestJson<SettingsPayload>(restartedWindow, "/settings");
    expectProviderAbsentFromConfig(restartedConfig, "restart_delete");
    expect(restartedConfig.providers?.restart_keep).toBeTruthy();
    expect(restartedConfig.roles?.pm?.provider_id).toBeUndefined();
    expect(restartedConfig.roles?.pm?.model).toBeUndefined();
    expect(restartedSettings.close_to_tray).toBe(false);

    await openLlmSettings(restartedWindow);
    await expect(providerCard(restartedWindow, "restart_keep")).toBeVisible({ timeout: 30_000 });
    await expect(providerCard(restartedWindow, "restart_delete")).toHaveCount(0);
    await closeSettingsIfOpen(restartedWindow);
    await expectCloseToTrayCheckboxState(restartedWindow, false);
    await closeSettingsIfOpen(restartedWindow);

    await attachJsonEvidence(testInfo, "restart-after-llm-config-api", restartedConfig);
    await attachJsonEvidence(testInfo, "restart-after-settings-api", restartedSettings);

    const closeEvent = restartedApp.waitForEvent("close", { timeout: 20_000 });
    await closeWindowFromRenderer(restartedWindow);
    await closeEvent;
    restartedApp = null;
  } finally {
    if (restartedApp) {
      await restartedApp.close();
    }
  }
});
