const { app, BrowserWindow, ipcMain, dialog, shell, safeStorage, Tray, Menu, nativeImage, Notification } = require("electron");
const { spawn, spawnSync } = require("child_process");
const { randomBytes } = require("crypto");

// 完全禁用 util._extend 的弃用警告
// const util = require("util");
// const originalExtend = util._extend;
// if (originalExtend) {
//   util._extend = function(target, source) {
//     // 直接使用 Object.assign 替代，不产生警告
//     return Object.assign(target, source);
//   };
//   // 保持原有属性
//   Object.setPrototypeOf(util._extend, Object.getPrototypeOf(originalExtend));
//   Object.getOwnPropertyNames(originalExtend).forEach(name => {
//     if (name !== 'length' && name !== 'name' && name !== 'prototype') {
//       Object.defineProperty(util._extend, name, Object.getOwnPropertyDescriptor(originalExtend, name));
//     }
//   });
// }

const pty = require("node-pty");
const net = require("net");
const path = require("path");
const fs = require("fs");
const os = require("os");
const {
  getDesktopBackendInfoPath,
  getGlobalSettingsPath,
  isDirectoryPath,
  isTruthyEnv,
  selectStartupWorkspaceOverride,
  shouldEnableSelfUpgradeMode,
} = require("./config-paths.cjs");

// Guard against Electron being forced into Node mode by an inherited env var.
if (process.env.ELECTRON_RUN_AS_NODE) {
  delete process.env.ELECTRON_RUN_AS_NODE;
}

const repoRoot = path.join(__dirname, "..", "..");
const backendScript = path.join(__dirname, "..", "backend", "server.py");
const frontendDist = path.join(__dirname, "..", "frontend", "dist", "index.html");
const globalSettingsPath = getGlobalSettingsPath(process.env, process.platform);
const desktopBackendInfoPath = getDesktopBackendInfoPath(process.env, process.platform);
const BACKEND_START_TIMEOUT_MS = 30000;
const BACKEND_HEALTH_POLL_MS = 350;
const BACKEND_MAX_RESTARTS = 3;
const BACKEND_SHUTDOWN_TIMEOUT_MS = 6000;

// Polaris 测试阶段：默认开启所有调试功能
// 可以通过设置 KERNELONE_DEBUG_BACKEND_START_TRACE=false 来禁用
const BACKEND_START_TRACE_ENABLED =
  process.env.KERNELONE_DEBUG_BACKEND_START_TRACE === undefined
    ? true
    : isTruthyEnv(process.env.KERNELONE_DEBUG_BACKEND_START_TRACE);

// Window state management
function getConfigPath() {
  return path.join(app.getPath('userData'), 'window-config.json');
}

function loadWindowState() {
  try {
    const configPath = getConfigPath();
    console.log('Loading window state from:', configPath);

    if (fs.existsSync(configPath)) {
      const data = fs.readFileSync(configPath, 'utf8');
      const state = JSON.parse(data);
      console.log('Loaded window state:', state);
      return state;
    } else {
      console.log('Window config file does not exist, using defaults');
    }
  } catch (error) {
    console.warn('Failed to load window config:', error);
  }
  return { maximized: false, bounds: null };
}

function saveWindowState(win) {
  try {
    const configPath = getConfigPath();
    const configDir = path.dirname(configPath);

    // Ensure config directory exists
    if (!fs.existsSync(configDir)) {
      fs.mkdirSync(configDir, { recursive: true });
      console.log('Created config directory:', configDir);
    }

    const state = {
      maximized: win.isMaximized(),
      bounds: win.isMaximized() ? null : win.getBounds()
    };

    fs.writeFileSync(configPath, JSON.stringify(state, null, 2));
    console.log('Window state saved to:', configPath);
    console.log('Saved state:', state);
  } catch (error) {
    console.warn('Failed to save window config:', error);
  }
}

function readPersistedWorkspace() {
  try {
    if (!fs.existsSync(globalSettingsPath)) {
      console.log(`[workspace] Settings file not found: ${globalSettingsPath}`);
      return "";
    }
    const raw = fs.readFileSync(globalSettingsPath, "utf8");
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      console.log(`[workspace] Invalid settings format in: ${globalSettingsPath}`);
      return "";
    }
    const workspace = typeof parsed.workspace === "string" ? parsed.workspace.trim() : "";
    console.log(`[workspace] Read persisted workspace: ${workspace || "(empty)"} from ${globalSettingsPath}`);
    return workspace;
  } catch (err) {
    console.error(`[workspace] Error reading settings: ${err.message}`);
    return "";
  }
}

function resolveStartupWorkspaceOverride() {
  const persistedWorkspace = readPersistedWorkspace();
  const envWorkspace = String(process.env.KERNELONE_WORKSPACE || "").trim();
  const selection = selectStartupWorkspaceOverride({
    env: process.env,
    persistedWorkspace,
    validateDirectory: true,
  });

  console.log(`[workspace] Resolving startup workspace:`);
  console.log(`[workspace]   KERNELONE_WORKSPACE: ${envWorkspace || "(not set)"}`);
  console.log(`[workspace]   Persisted workspace: ${persistedWorkspace || "(not set)"}`);
  console.log(`[workspace]   Selected source: ${selection.source}`);
  if (selection.invalidWorkspace) {
    console.warn(
      `[workspace] Ignoring invalid ${String(selection.source).replace(/_missing$/, "")} workspace: ${selection.invalidWorkspace}`,
    );
  }

  if (selection.source === "env_forced") {
    console.log("[workspace] Applying FORCED workspace override from KERNELONE_WORKSPACE_FORCE");
  } else if (selection.source === "persisted") {
    console.log(`[workspace] Using persisted workspace: ${selection.workspace}`);
  } else if (selection.source === "env") {
    console.log(`[workspace] Using env workspace: ${selection.workspace}`);
  } else if (!selection.workspace && envWorkspace && !isDirectoryPath(envWorkspace)) {
    console.warn(`[workspace] Ignoring invalid env workspace: ${envWorkspace}`);
  }

  return selection.workspace;
}

let backendProcess = null;
let backendInfo = {
  port: null,
  token: null,
  baseUrl: null,
  pid: null,
};
let backendStatus = {
  state: "stopped", // stopped | starting | running | restarting | errored
  ready: false,
  restarts: 0,
  lastError: "",
  lastExitCode: null,
};
let backendRestartTimer = null;
let isAppQuitting = false;
let quitSequenceStarted = false;
let quitSequenceDone = false;

// Backend startup lock to prevent concurrent start attempts
let backendStartPromise = null;

const ptySessions = new Map();
const PTY_MAX_ARGS = 128;
const PTY_MAX_ARG_LENGTH = 2048;
const PTY_MAX_WRITE_CHARS = 200000;

// Tray management
let tray = null;
let mainWindowRef = null;

function getTrayIconPath() {
  // Prefer 16x16 icon for tray, fallback to any available icon
  const possiblePaths = [
    path.join(__dirname, 'assets', 'icons', '16x16.png'),
    path.join(__dirname, 'assets', 'icons', '32x32.png'),
    path.join(__dirname, 'assets', 'icon.png'),
    path.join(__dirname, 'assets', 'Polaris-icon.png'),
  ];
  for (const iconPath of possiblePaths) {
    if (fs.existsSync(iconPath)) {
      return iconPath;
    }
  }
  return null;
}

function createTray() {
  const iconPath = getTrayIconPath();
  let icon;

  if (iconPath) {
    try {
      icon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
    } catch {
      icon = nativeImage.createEmpty();
    }
  } else {
    // Create a simple colored icon as fallback
    icon = nativeImage.createEmpty();
    console.warn('[tray] No icon found, using empty icon');
  }

  tray = new Tray(icon);
  tray.setToolTip('Polaris');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示 Polaris',
      click: () => {
        mainWindowRef?.show();
        mainWindowRef?.focus();
      },
    },
    {
      label: '隐藏到托盘',
      click: () => mainWindowRef?.hide(),
    },
    { type: 'separator' },
    {
      label: '新建任务',
      click: () => {
        mainWindowRef?.show();
        mainWindowRef?.webContents.send('hp:action', { type: 'new-task' });
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        isAppQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setContextMenu(contextMenu);

  tray.on('click', () => {
    if (mainWindowRef?.isVisible()) {
      mainWindowRef.hide();
    } else {
      mainWindowRef?.show();
      mainWindowRef?.focus();
    }
  });

  tray.on('double-click', () => {
    mainWindowRef?.show();
    mainWindowRef?.focus();
  });

  console.log('[tray] System tray created');
}

function destroyTray() {
  if (tray) {
    tray.destroy();
    tray = null;
    console.log('[tray] System tray destroyed');
  }
}

function showDesktopNotification(title, body, options = {}) {
  if (!Notification.isSupported()) {
    console.warn('[notification] Notifications not supported on this system');
    return { ok: false, error: 'Notifications not supported' };
  }

  try {
    const notification = new Notification({
      title: String(title || 'Polaris'),
      body: String(body || ''),
      silent: Boolean(options.silent ?? false),
    });

    notification.on('click', () => {
      mainWindowRef?.show();
      mainWindowRef?.focus();
    });

    notification.on('close', () => {
      // Notification closed
    });

    notification.show();
    return { ok: true };
  } catch (error) {
    console.error('[notification] Failed to show notification:', error);
    return { ok: false, error: String(error) };
  }
}

function resetBackendInfo() {
  backendInfo = {
    port: null,
    token: null,
    baseUrl: null,
    pid: null,
  };
}

function getDesktopBackendInfoPayload() {
  const normalizedToken = typeof backendInfo?.token === "string" && backendInfo.token.trim()
    ? backendInfo.token.trim()
    : null;
  const normalizedBaseUrl = typeof backendInfo?.baseUrl === "string" && backendInfo.baseUrl.trim()
    ? backendInfo.baseUrl.trim()
    : null;
  return {
    schema_version: 1,
    source: "electron_main",
    updated_at: new Date().toISOString(),
    state: String(backendStatus.state || "stopped"),
    ready: Boolean(backendStatus.ready),
    restarts: Number.isInteger(backendStatus.restarts) ? backendStatus.restarts : 0,
    lastError: String(backendStatus.lastError || ""),
    lastExitCode: Number.isInteger(backendStatus.lastExitCode) ? backendStatus.lastExitCode : null,
    backend: {
      port: Number.isInteger(backendInfo?.port) ? backendInfo.port : null,
      token: normalizedToken,
      baseUrl: normalizedBaseUrl,
      pid: Number.isInteger(backendInfo?.pid) ? backendInfo.pid : null,
    },
  };
}

function writeJsonFileAtomic(targetPath, payload) {
  const directory = path.dirname(targetPath);
  const tempPath = path.join(
    directory,
    `.${path.basename(targetPath)}.${process.pid}.${Date.now()}.tmp`,
  );
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(tempPath, JSON.stringify(payload, null, 2), "utf-8");
  fs.renameSync(tempPath, targetPath);
}

function publishDesktopBackendInfo() {
  try {
    writeJsonFileAtomic(desktopBackendInfoPath, getDesktopBackendInfoPayload());
  } catch (error) {
    console.warn(`[backend] failed to write desktop backend info: ${String(error?.message || error)}`);
  }
}

publishDesktopBackendInfo();

function asObject(value, fieldName) {
  if (value === undefined || value === null) {
    return {};
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${fieldName} must be an object`);
  }
  return value;
}

function asTrimmedString(value, fieldName, { maxLength = 4096, allowEmpty = false } = {}) {
  if (typeof value !== "string") {
    throw new TypeError(`${fieldName} must be a string`);
  }
  const text = value.trim();
  if (!allowEmpty && text.length === 0) {
    throw new TypeError(`${fieldName} cannot be empty`);
  }
  if (text.length > maxLength) {
    throw new TypeError(`${fieldName} exceeds maxLength=${maxLength}`);
  }
  return text;
}

function asInteger(value, fieldName, { min = 1, max = 10000 } = {}) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) {
    throw new TypeError(`${fieldName} must be an integer`);
  }
  if (parsed < min || parsed > max) {
    throw new TypeError(`${fieldName} must be in range [${min}, ${max}]`);
  }
  return parsed;
}

function allocatePtySessionId() {
  for (let index = 0; index < 10; index += 1) {
    const candidate = randomBytes(12).toString("hex");
    if (!ptySessions.has(candidate)) {
      return candidate;
    }
  }
  throw new Error("failed to allocate unique PTY session id");
}

function ensurePtyOwner(event, session) {
  const senderId = event?.sender?.id;
  if (!session || typeof senderId !== "number") {
    return false;
  }
  return session.senderId === senderId;
}

function clearBackendRestartTimer() {
  if (backendRestartTimer !== null) {
    clearTimeout(backendRestartTimer);
    backendRestartTimer = null;
  }
}

function closeAllPtySessions() {
  if (ptySessions.size <= 0) {
    return;
  }
  for (const session of ptySessions.values()) {
    try {
      session.term.kill();
    } catch {
      // ignore
    }
  }
  ptySessions.clear();
}

function killBackendProcessHard() {
  if (!backendProcess) {
    return;
  }
  try {
    // On Windows, use taskkill /T to kill the entire process tree (venv creates child processes)
    if (process.platform === "win32" && backendProcess.pid) {
      try {
        const { execSync } = require("child_process");
        execSync(`taskkill /PID ${backendProcess.pid} /T /F 2>nul`, { timeout: 5000 });
      } catch {
        // Fallback to normal kill
        backendProcess.kill();
      }
    } else {
      backendProcess.kill();
    }
  } catch {
    // ignore
  }
  backendProcess = null;
}

async function requestBackendShutdown(timeoutMs = BACKEND_SHUTDOWN_TIMEOUT_MS) {
  if (!backendInfo || !backendInfo.baseUrl || !backendInfo.token) {
    return { ok: false, reason: "backend_info_missing" };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => {
    try {
      controller.abort();
    } catch {
      // ignore
    }
  }, Math.max(timeoutMs, 1000));
  try {
    const response = await fetch(`${backendInfo.baseUrl}/app/shutdown`, {
      method: "POST",
      headers: { authorization: `Bearer ${backendInfo.token}` },
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, reason: `http_${response.status}` };
    }
    return { ok: true, reason: "ok" };
  } catch (error) {
    return { ok: false, reason: String(error?.message || error || "shutdown_request_failed") };
  } finally {
    clearTimeout(timer);
  }
}

async function waitForBackendHealth(baseUrl, token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/health`, {
        headers: token ? { authorization: `Bearer ${token}` } : {},
      });
      if (response.ok) {
        return;
      }
    } catch {
      // ignore and retry
    }
    await new Promise((resolve) => setTimeout(resolve, BACKEND_HEALTH_POLL_MS));
  }
  throw new Error("backend health check timeout");
}

function parseBackendStdoutForReady(chunk, markReady) {
  const text = String(chunk ?? "");
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) {
      continue;
    }
    try {
      const payload = JSON.parse(trimmed);
      if (payload && payload.event === "backend_started") {
        markReady();
        return;
      }
    } catch {
      // ignore malformed json line
    }
  }
}

function scheduleBackendRestart() {
  if (isAppQuitting) return;
  if (backendStatus.restarts >= BACKEND_MAX_RESTARTS) {
    backendStatus.state = "errored";
    backendStatus.lastError = `Backend crashed too many times (max=${BACKEND_MAX_RESTARTS}).`;
    publishDesktopBackendInfo();
    dialog.showMessageBox({
      type: "error",
      title: "Polaris",
      message: "Backend repeatedly crashed",
      detail: backendStatus.lastError,
    }).catch(() => { });
    return;
  }
  clearBackendRestartTimer();
  const nextAttempt = backendStatus.restarts + 1;
  const delayMs = Math.min(1000 * (2 ** (nextAttempt - 1)), 8000);
  backendStatus.state = "restarting";
  publishDesktopBackendInfo();
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null;
    startBackend({ restartAttempt: nextAttempt }).catch((error) => {
      backendStatus.state = "errored";
      backendStatus.lastError = String(error?.message || error || "unknown backend restart error");
      publishDesktopBackendInfo();
      scheduleBackendRestart();
    });
  }, delayMs);
}

function buildPtyEnv(env) {
  const base = { ...process.env };
  if (env && typeof env === "object") {
    for (const [key, value] of Object.entries(env)) {
      if (value === undefined || value === null) continue;
      base[String(key)] = String(value);
    }
  }
  if (!base.TERM) {
    base.TERM = "xterm-256color";
  }
  return base;
}

function secretsPath() {
  return path.join(app.getPath("userData"), "secrets.json");
}

function loadSecrets() {
  const target = secretsPath();
  try {
    if (!fs.existsSync(target)) {
      return {};
    }
    const raw = fs.readFileSync(target, "utf-8");
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

function saveSecrets(payload) {
  const target = secretsPath();
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(payload, null, 2), "utf-8");
  } catch {
    // ignore
  }
}

function setSecret(key, value) {
  if (!safeStorage.isEncryptionAvailable()) {
    return { ok: false, error: "safeStorage unavailable" };
  }
  if (!key) {
    return { ok: false, error: "key required" };
  }
  const data = loadSecrets();
  const encrypted = safeStorage.encryptString(String(value));
  data[key] = encrypted.toString("base64");
  saveSecrets(data);
  return { ok: true };
}

function getSecret(key) {
  if (!safeStorage.isEncryptionAvailable()) {
    return { ok: false, error: "safeStorage unavailable" };
  }
  if (!key) {
    return { ok: false, error: "key required" };
  }
  const data = loadSecrets();
  const encoded = data[key];
  if (!encoded) {
    return { ok: false, value: null };
  }
  try {
    const decrypted = safeStorage.decryptString(Buffer.from(encoded, "base64"));
    return { ok: true, value: decrypted };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function deleteSecret(key) {
  if (!key) {
    return { ok: false, error: "key required" };
  }
  const data = loadSecrets();
  if (data && Object.prototype.hasOwnProperty.call(data, key)) {
    delete data[key];
    saveSecrets(data);
  }
  return { ok: true };
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function resolveVenvPython() {
  const venvRoot = path.join(repoRoot, ".venv");
  const venvPython = process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
  if (fs.existsSync(venvPython)) {
    return { exists: true, pythonPath: venvPython };
  }
  return { exists: false, pythonPath: "" };
}

function checkVenvDependencies(pythonPath) {
  if (!pythonPath) {
    return { ok: false, message: "No venv python configured." };
  }
  try {
    const result = spawnSync(pythonPath, ["-m", "pip", "check"], {
      cwd: repoRoot,
      encoding: "utf-8",
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    if (result.status === 0) {
      return { ok: true, message: "" };
    }
    return {
      ok: false,
      message: (result.stdout || result.stderr || "").trim(),
    };
  } catch (err) {
    return { ok: false, message: String(err) };
  }
}

function ensureVenvNotice() {
  const venv = resolveVenvPython();
  if (!venv.exists) {
    const text = [
      "Python 虚拟环境未检测到。",
      "请运行 npm run setup:dev，或执行 infrastructure/setup/setup_venv.bat（Windows）/ infrastructure/setup/setup_venv.sh（macOS/Linux）。",
    ].join("\n");
    console.warn(text);
    dialog.showMessageBoxSync({
      type: "warning",
      title: "Polaris",
      message: "缺少 Python 虚拟环境（.venv）",
      detail: text,
    });
    return { pythonPath: "" };
  }
  return { pythonPath: venv.pythonPath };
}

// Global counter to track startBackend calls
let startBackendCallCount = 0;

async function startBackend(options = {}) {
  startBackendCallCount++;
  const restartAttempt = Number(options.restartAttempt || 0);

  console.log(`[backend] startBackend called #${startBackendCallCount} (restartAttempt=${restartAttempt})`);
  if (BACKEND_START_TRACE_ENABLED) {
    const stackLines = String(new Error().stack || "")
      .split(/\r?\n/)
      .slice(1)
      .map((line) => line.trim())
      .filter(Boolean);
    if (stackLines.length > 0) {
      console.log(`[backend] startBackend stack:\n${stackLines.join("\n")}`);
    }
  }

  // Prevent concurrent start attempts - wait for existing startup to complete
  if (backendStartPromise !== null) {
    console.log("[backend] startup already in progress, waiting for it to complete...");
    try {
      await backendStartPromise;
      // If we're here, the previous startup succeeded - check current state
      if (backendStatus.state === "running" && backendStatus.ready) {
        console.log("[backend] previous startup succeeded, using existing backend");
        return;
      }
      // If it failed or is in error state, we'll proceed with a new start attempt
    } catch (error) {
      // Previous startup failed, proceed with new attempt
      console.log("[backend] previous startup failed, proceeding with new attempt:", error.message);
    }
  }

  // Check if backend is already running - prevent duplicate processes
  if (backendProcess !== null && backendStatus.state === "running" && backendStatus.ready) {
    console.log("[backend] backend already running, skipping duplicate start");
    return;
  }

  // Kill any existing backend process before starting
  if (backendProcess !== null) {
    console.log("[backend] killing existing backend process before restart");
    try {
      // On Windows, use taskkill /T to kill the entire process tree (venv creates child processes)
      if (process.platform === "win32" && backendProcess.pid) {
        try {
          const { execSync } = require("child_process");
          execSync(`taskkill /PID ${backendProcess.pid} /T /F 2>nul`, { timeout: 5000 });
          console.log(`[backend] killed process tree for PID ${backendProcess.pid}`);
        } catch (taskkillErr) {
          // Fallback to normal kill
          backendProcess.kill();
        }
      } else {
        backendProcess.kill();
      }
    } catch (e) {
      // ignore
    }
    backendProcess = null;
  }

  console.log(`[backend] startBackend starting: backendProcess=${backendProcess !== null}, state=${backendStatus.state}, ready=${backendStatus.ready}, restartAttempt=${restartAttempt}`);

  // Create the startup promise to prevent concurrent calls.
  // Attach a noop catch so internal lock rejection never becomes an
  // unhandled promise rejection when there are no concurrent waiters.
  let resolveStart;
  let rejectStart;
  backendStartPromise = new Promise((resolve, reject) => {
    resolveStart = resolve;
    rejectStart = reject;
  });
  backendStartPromise.catch(() => { });

  const port = await getFreePort();
  const token = randomBytes(16).toString("hex");
  backendStatus.state = restartAttempt > 0 ? "restarting" : "starting";
  backendStatus.ready = false;
  backendStatus.restarts = restartAttempt;
  backendStatus.lastError = "";
  backendStatus.lastExitCode = null;
  const venv = ensureVenvNotice();
  if (!process.env.KERNELONE_PYTHON && venv.pythonPath) {
    process.env.KERNELONE_PYTHON = venv.pythonPath;
  }
  const python = process.env.KERNELONE_PYTHON || "python";
  if (venv.pythonPath) {
    const depCheck = checkVenvDependencies(venv.pythonPath);
    if (!depCheck.ok) {
      const detail = depCheck.message || "依赖检测失败，请重新运行 npm run setup:dev。";
      console.warn(detail);
      dialog.showMessageBoxSync({
        type: "warning",
        title: "Polaris",
        message: "Python 依赖可能不完整",
        detail,
      });
    }
  }
  const args = [backendScript, "--host", "127.0.0.1", "--port", String(port), "--token", token];

  const workspaceOverride = resolveStartupWorkspaceOverride();
  if (workspaceOverride) {
    args.push("--workspace", workspaceOverride);
  }
  const selfUpgradeMode = shouldEnableSelfUpgradeMode({
    env: process.env,
    workspace: workspaceOverride || repoRoot,
    repoRoot,
    isPackaged: app.isPackaged,
    platform: process.platform,
  });
  if (selfUpgradeMode.enabled) {
    console.log(`[workspace] Enabling backend self-upgrade mode (${selfUpgradeMode.source})`);
    args.push("--self-upgrade-mode");
  }

  const backendEnv = { ...process.env, PYTHONUNBUFFERED: "1" };
  if (backendEnv.KERNELONE_E2E_USE_REAL_SETTINGS === "1" && backendEnv.KERNELONE_HOME) {
    backendEnv.KERNELONE_RUNTIME_ROOT = backendEnv.KERNELONE_RUNTIME_ROOT || path.join(backendEnv.KERNELONE_HOME, "runtime-cache");
    backendEnv.KERNELONE_STATE_TO_RAMDISK = "0";
  }

  console.log(`[backend] spawn #${startBackendCallCount}: ${python} ${args.join(" ")}`);
  if (backendEnv.KERNELONE_E2E === "1") {
    console.log(
      `[backend] e2e env: home=${backendEnv.KERNELONE_HOME || ""} runtime_root=${backendEnv.KERNELONE_RUNTIME_ROOT || ""} state_to_ramdisk=${backendEnv.KERNELONE_STATE_TO_RAMDISK || ""}`,
    );
  }

  const spawned = spawn(python, args, {
    cwd: repoRoot,
    env: backendEnv,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendProcess = spawned;

  backendInfo = {
    port,
    token,
    baseUrl: `http://127.0.0.1:${port}`,
    pid: backendProcess.pid,
  };
  publishDesktopBackendInfo();

  let backendStartedEventSeen = false;
  const markReadyFromJson = () => {
    backendStartedEventSeen = true;
  };

  spawned.stdout.on("data", (data) => {
    process.stdout.write(`[backend] ${data}`);
    parseBackendStdoutForReady(data, markReadyFromJson);
  });

  spawned.stderr.on("data", (data) => {
    process.stderr.write(`[backend] STDERR: ${data}`);
  });

  spawned.on("exit", (code) => {
    console.log(`[backend] exited with code ${code}, isAppQuitting=${isAppQuitting}`);
    backendStatus.ready = false;
    backendStatus.lastExitCode = code ?? null;
    // Clear startup lock on unexpected exit
    if (backendStartPromise !== null) {
      rejectStart(new Error(`backend exited before ready (code=${String(code ?? "unknown")})`));
      backendStartPromise = null;
    }
    if (!isAppQuitting) {
      backendStatus.state = "errored";
      backendStatus.lastError = `Backend exited unexpectedly with code ${String(code ?? "unknown")}`;
      scheduleBackendRestart();
    } else {
      backendStatus.state = "stopped";
    }
    backendProcess = null;
    resetBackendInfo();
    publishDesktopBackendInfo();
  });

  spawned.on("error", (err) => {
    console.error(`[backend] spawn failed: ${err.message}`);
    backendStatus.state = "errored";
    backendStatus.ready = false;
    backendStatus.lastError = err.message;
    // Clear startup lock on spawn error
    if (backendStartPromise !== null) {
      rejectStart(err);
      backendStartPromise = null;
    }
    resetBackendInfo();
    publishDesktopBackendInfo();
  });

  // Only HTTP health proves the backend is ready for renderer fetches.  The
  // backend_started stdout event can arrive before Uvicorn starts listening.
  const timeoutPromise = new Promise((_, reject) => {
    setTimeout(() => reject(new Error("backend start timeout")), BACKEND_START_TIMEOUT_MS);
  });
  try {
    await Promise.race([
      waitForBackendHealth(backendInfo.baseUrl, backendInfo.token, BACKEND_START_TIMEOUT_MS),
      timeoutPromise,
    ]);
    if (backendStartedEventSeen) {
      console.log("[backend] backend_started event observed before health readiness");
    }
    backendStatus.state = "running";
    backendStatus.ready = true;
    publishDesktopBackendInfo();
    // Clear startup lock on success
    if (resolveStart) resolveStart();
    backendStartPromise = null;
  } catch (error) {
    backendStatus.state = "errored";
    backendStatus.ready = false;
    backendStatus.lastError = String(error?.message || error || "unknown backend startup error");
    // Clear startup lock on failure
    if (rejectStart) rejectStart(error);
    backendStartPromise = null;
    if (spawned && spawned.pid && spawned.exitCode === null) {
      spawned.kill();
    }
    resetBackendInfo();
    publishDesktopBackendInfo();
    throw error;
  }
}

async function createWindow() {
  const savedState = loadWindowState();
  console.log('Creating window with saved state:', savedState);
  const forcedDevUrl = String(process.env.KERNELONE_DEV_SERVER_URL || "").trim();
  const isE2E = process.env.KERNELONE_E2E === "1";
  const allowVisibleE2EWindow = ["1", "true", "yes", "on"].includes(
    String(process.env.KERNELONE_E2E_SHOW_WINDOW || "").trim().toLowerCase(),
  );
  const shouldShowWindow = !isE2E || allowVisibleE2EWindow;

  const win = new BrowserWindow({
    width: savedState.bounds?.width || 1200,
    height: savedState.bounds?.height || 900,
    x: savedState.bounds?.x || undefined,
    y: savedState.bounds?.y || undefined,
    show: shouldShowWindow,
    frame: false, // Custom frame
    backgroundColor: '#000000', // Avoid white flash
    icon: path.join(__dirname, 'assets', 'icons', 'icon.png'), // 应用图标
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      preload: path.join(__dirname, "preload.cjs"),
    },
  });

  // Restore maximized state after window is ready
  if (savedState.maximized) {
    console.log('Window will be maximized after ready');
    // Wait for window to be fully loaded and ready
    win.once('ready-to-show', () => {
      console.log('Window ready to show, maximizing now');
      win.maximize();
    });
  }

  // Save state on window events
  win.on('maximize', () => {
    console.log('Window maximized, saving state');
    saveWindowState(win);
  });
  win.on('unmaximize', () => {
    console.log('Window unmaximized, saving state');
    saveWindowState(win);
  });
  win.on('resized', () => saveWindowState(win));
  win.on('moved', () => saveWindowState(win));

  // Handle close button - hide to tray instead of quitting
  win.on('close', (event) => {
    if (!isAppQuitting && !quitSequenceStarted) {
      event.preventDefault();
      win.hide();
      console.log('[window] Hidden to tray instead of closing');
      return;
    }
    // Allow actual close during quit sequence
  });

  if (forcedDevUrl) {
    await win.loadURL(forcedDevUrl);
    if (!app.isPackaged && !isE2E) {
      win.webContents.openDevTools({ mode: "detach" });
    }
  } else if (!app.isPackaged) {
    await win.loadURL("http://localhost:5173");
    if (!isE2E) {
      win.webContents.openDevTools({ mode: "detach" });
    }
  } else {
    await win.loadFile(frontendDist);
  }

  // Alternative: maximize after URL is loaded as backup
  if (savedState.maximized) {
    console.log('Window loaded, checking if maximization needed');
    setTimeout(() => {
      if (!win.isMaximized()) {
        console.log('Window not maximized yet, forcing maximize now');
        win.maximize();
      }
    }, 100);
  }

  return win;
}

const allowE2EMultiInstance =
  process.env.KERNELONE_E2E === "1" &&
  process.env.KERNELONE_E2E_ALLOW_MULTI_INSTANCE === "1";

const hasSingleInstanceLock = allowE2EMultiInstance ? true : app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  console.warn("[app] Polaris is already running. Exiting duplicate instance.");
  app.quit();
}

const SECOND_INSTANCE_NOTICE_COOLDOWN_MS = 3000;
let lastSecondInstanceNoticeTs = 0;

function maybeShowSecondInstanceNotice(win) {
  const now = Date.now();
  if (now - lastSecondInstanceNoticeTs < SECOND_INSTANCE_NOTICE_COOLDOWN_MS) {
    return;
  }
  lastSecondInstanceNoticeTs = now;

  const options = {
    type: "info",
    title: "Polaris",
    message: "Polaris 已在运行",
    detail: "已切换到当前运行实例。",
    buttons: ["确定"],
    defaultId: 0,
    noLink: true,
  };

  if (win && !win.isDestroyed()) {
    dialog.showMessageBox(win, options).catch(() => { });
    return;
  }
  dialog.showMessageBox(options).catch(() => { });
}

if (hasSingleInstanceLock) {
  if (!allowE2EMultiInstance) {
    app.on("second-instance", () => {
      const windows = BrowserWindow.getAllWindows();
      if (windows.length === 0) {
        if (app.isReady()) {
          createWindow().catch((error) => {
            console.error(`[app] failed to restore main window: ${String(error)}`);
          });
        }
        maybeShowSecondInstanceNotice(null);
        return;
      }
      const win = windows[0];
      if (win.isMinimized()) {
        win.restore();
      }
      if (!win.isVisible()) {
        win.show();
      }
      win.focus();
      maybeShowSecondInstanceNotice(win);
    });
  }

  // Diagnostic logging at startup
  console.log(`[main] Electron starting: PID=${process.pid}, PPID=${process.ppid}, timestamp=${Date.now()}`);

  app.whenReady().then(async () => {
    console.log(`[main] app.whenReady fired: PID=${process.pid}, timestamp=${Date.now()}`);
    try {
      await startBackend();
    } catch (error) {
      const detail = String(error?.message || error || "unknown backend startup error");
      backendStatus.state = "errored";
      backendStatus.ready = false;
      backendStatus.lastError = detail;
      dialog.showMessageBoxSync({
        type: "error",
        title: "Polaris",
        message: "Backend 启动失败",
        detail,
      });
    }

    // Backend IPC - Register BEFORE creating window so renderer can call them immediately
    ipcMain.handle("hp:get-backend", async () => backendInfo);
    ipcMain.handle("hp:backend-status", async () => ({
      ...backendStatus,
      info: backendInfo,
    }));
    ipcMain.handle("hp:pick-workspace", async (_event, options = {}) => {
      const request = asObject(options, "options");
      const defaultPath = request.defaultPath
        ? asTrimmedString(request.defaultPath, "options.defaultPath", { maxLength: 2048 })
        : undefined;
      const result = await dialog.showOpenDialog({
        properties: ["openDirectory"],
        defaultPath,
      });
      if (result.canceled || !result.filePaths.length) {
        return null;
      }
      return result.filePaths[0];
    });
    ipcMain.handle("hp:open-path", async (_event, targetPath) => {
      if (typeof targetPath !== "string" || !targetPath.trim()) {
        return { ok: false, error: "path is required" };
      }
      if (targetPath.length > 2048) {
        return { ok: false, error: "path too long" };
      }
      const error = await shell.openPath(targetPath.trim());
      if (error) {
        return { ok: false, error };
      }
      return { ok: true, error: null };
    });
    ipcMain.handle("hp:secrets-available", async () => {
      return { ok: true, available: safeStorage.isEncryptionAvailable() };
    });
    ipcMain.handle("hp:secrets-set", async (_event, payload) => {
      let key;
      let value;
      try {
        const request = asObject(payload, "payload");
        key = asTrimmedString(request.key, "payload.key", { maxLength: 128 });
        value = asTrimmedString(request.value, "payload.value", { maxLength: 8192, allowEmpty: true });
      } catch (error) {
        return { ok: false, error: String(error) };
      }
      return setSecret(key, value);
    });
    ipcMain.handle("hp:secrets-get", async (_event, key) => {
      try {
        const normalizedKey = asTrimmedString(key, "key", { maxLength: 128 });
        return getSecret(normalizedKey);
      } catch (error) {
        return { ok: false, error: String(error) };
      }
    });
    ipcMain.handle("hp:secrets-delete", async (_event, key) => {
      try {
        const normalizedKey = asTrimmedString(key, "key", { maxLength: 128 });
        return deleteSecret(normalizedKey);
      } catch (error) {
        return { ok: false, error: String(error) };
      }
    });
    ipcMain.handle("hp:pty-start", async (event, payload = {}) => {
      try {
        const request = asObject(payload, "payload");
        let command = request.command;
        if (!command) {
          command = process.platform === "win32" ? "powershell.exe" : "bash";
        }
        const normalizedCommand = asTrimmedString(command, "payload.command", { maxLength: 512 });
        if (/[;&|`]/.test(normalizedCommand) || normalizedCommand.includes("$(")) {
          return { ok: false, error: "forbidden command tokens" };
        }
        const rawArgs = Array.isArray(request.args) ? request.args.map((arg) => String(arg)) : [];
        if (rawArgs.length > PTY_MAX_ARGS) {
          return { ok: false, error: `too many args (max ${PTY_MAX_ARGS})` };
        }
        for (let index = 0; index < rawArgs.length; index += 1) {
          const arg = rawArgs[index];
          if (arg.length > PTY_MAX_ARG_LENGTH) {
            return { ok: false, error: `arg[${index}] exceeds max length` };
          }
        }

        let spawnCommand = normalizedCommand;
        let spawnArgs = rawArgs;
        if (process.platform === "win32") {
          const preferredExts = [".exe", ".cmd", ".bat", ".ps1"];
          const resolveCandidate = (candidate) => {
            if (!candidate) return null;
            const ext = path.extname(candidate).toLowerCase();
            if (ext) return candidate;
            if (path.isAbsolute(candidate) && fs.existsSync(candidate)) {
              for (const extOpt of preferredExts) {
                const withExt = `${candidate}${extOpt}`;
                if (fs.existsSync(withExt)) return withExt;
              }
            }
            return null;
          };
          const resolveFromWhere = (value) => {
            try {
              const result = spawnSync("where", [value], { encoding: "utf-8" });
              if (result.status !== 0 || typeof result.stdout !== "string") return null;
              const lines = result.stdout
                .split(/\r?\n/)
                .map((line) => line.trim())
                .filter(Boolean);
              if (lines.length === 0) return null;
              for (const extOpt of preferredExts) {
                const match = lines.find((line) => path.extname(line).toLowerCase() === extOpt);
                if (match) return match;
              }
              return null;
            } catch {
              return null;
            }
          };

          let resolved = resolveCandidate(spawnCommand);
          if (!resolved && !path.isAbsolute(spawnCommand)) {
            resolved = resolveFromWhere(spawnCommand);
            if (!resolved) {
              for (const extOpt of preferredExts) {
                resolved = resolveFromWhere(`${spawnCommand}${extOpt}`);
                if (resolved) break;
              }
            }
          }

          const target = resolved || spawnCommand;
          const targetExt = path.extname(target).toLowerCase();
          if (targetExt === ".cmd" || targetExt === ".bat") {
            spawnCommand = "cmd.exe";
            spawnArgs = ["/c", target, ...rawArgs];
          } else if (targetExt === ".ps1") {
            spawnCommand = "powershell.exe";
            spawnArgs = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", target, ...rawArgs];
          } else if (resolved) {
            spawnCommand = resolved;
            spawnArgs = rawArgs;
          }
        }

        const cols = request.cols === undefined ? 120 : asInteger(request.cols, "payload.cols", { min: 20, max: 800 });
        const rows = request.rows === undefined ? 32 : asInteger(request.rows, "payload.rows", { min: 5, max: 400 });
        const cwd = request.cwd ? asTrimmedString(request.cwd, "payload.cwd", { maxLength: 2048 }) : repoRoot;
        const resolvedCwd = path.resolve(cwd);
        if (!fs.existsSync(resolvedCwd) || !fs.statSync(resolvedCwd).isDirectory()) {
          return { ok: false, error: "cwd not found" };
        }
        const env = buildPtyEnv(asObject(request.env || {}, "payload.env"));
        const useConpty = typeof request.use_conpty === "boolean" ? request.use_conpty : false;

        let term;
        try {
          term = pty.spawn(spawnCommand, spawnArgs, {
            name: "xterm-256color",
            cols,
            rows,
            cwd: resolvedCwd,
            env,
            useConpty,
          });
        } catch (err) {
          if (useConpty) {
            term = pty.spawn(spawnCommand, spawnArgs, {
              name: "xterm-256color",
              cols,
              rows,
              cwd: resolvedCwd,
              env,
              useConpty: false,
            });
          } else {
            throw err;
          }
        }

        const id = allocatePtySessionId();
        const sender = event.sender;
        term.onData((data) => {
          if (sender.isDestroyed()) return;
          sender.send("hp:pty-data", { id, data });
        });
        term.onExit(({ exitCode, signal }) => {
          if (!sender.isDestroyed()) {
            sender.send("hp:pty-exit", { id, exitCode, signal });
          }
          ptySessions.delete(id);
        });
        ptySessions.set(id, { term, senderId: sender.id });
        return { ok: true, id };
      } catch (err) {
        return { ok: false, error: String(err) };
      }
    });
    ipcMain.handle("hp:pty-write", async (event, payload = {}) => {
      try {
        const request = asObject(payload, "payload");
        const id = asTrimmedString(request.id, "payload.id", { maxLength: 128 });
        const session = ptySessions.get(id);
        if (!session) {
          return { ok: false, error: "session not found" };
        }
        if (!ensurePtyOwner(event, session)) {
          return { ok: false, error: "session ownership mismatch" };
        }
        const data = String(request.data ?? "");
        if (data.length > PTY_MAX_WRITE_CHARS) {
          return { ok: false, error: "payload too large" };
        }
        session.term.write(data);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: String(err) };
      }
    });
    ipcMain.handle("hp:pty-resize", async (event, payload = {}) => {
      try {
        const request = asObject(payload, "payload");
        const id = asTrimmedString(request.id, "payload.id", { maxLength: 128 });
        const cols = asInteger(request.cols, "payload.cols", { min: 20, max: 800 });
        const rows = asInteger(request.rows, "payload.rows", { min: 5, max: 400 });
        const session = ptySessions.get(id);
        if (!session) {
          return { ok: false, error: "session not found" };
        }
        if (!ensurePtyOwner(event, session)) {
          return { ok: false, error: "session ownership mismatch" };
        }
        session.term.resize(cols, rows);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: String(err) };
      }
    });
    ipcMain.handle("hp:pty-close", async (event, payload = {}) => {
      try {
        const request = asObject(payload, "payload");
        const id = asTrimmedString(request.id, "payload.id", { maxLength: 128 });
        const session = ptySessions.get(id);
        if (!session) {
          return { ok: true };
        }
        if (!ensurePtyOwner(event, session)) {
          return { ok: false, error: "session ownership mismatch" };
        }
        try {
          session.term.kill();
        } catch {
          // ignore
        }
        ptySessions.delete(id);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: String(err) };
      }
    });

    // Window Control IPC
    ipcMain.handle("hp:window-minimize", (event) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      win?.minimize();
    });
    ipcMain.handle("hp:window-maximize", (event) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      if (win?.isMaximized()) {
        win.unmaximize();
      } else {
        win?.maximize();
      }
      // State is automatically saved by event listeners
      return win?.isMaximized() || false;
    });
    ipcMain.handle("hp:window-close", (event) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      win?.close();
    });
    ipcMain.handle("hp:window-get-state", (event) => {
      const win = BrowserWindow.fromWebContents(event.sender);
      return {
        maximized: win?.isMaximized() || false,
        bounds: win?.getBounds() || null
      };
    });

    // Notification IPC
    ipcMain.handle("hp:notification-show", async (_event, payload) => {
      try {
        const request = asObject(payload, "payload");
        const title = request.title !== undefined ? asTrimmedString(request.title, "payload.title", { maxLength: 256 }) : 'Polaris';
        const body = request.body !== undefined ? asTrimmedString(request.body, "payload.body", { maxLength: 1024 }) : '';
        const silent = typeof request.silent === 'boolean' ? request.silent : false;
        return showDesktopNotification(title, body, { silent });
      } catch (error) {
        return { ok: false, error: String(error) };
      }
    });

    // Window reference for tray
    mainWindowRef = null;

    const win = await createWindow();
    mainWindowRef = win;

    // Create system tray after window is ready
    createTray();

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", (event) => {
  if (quitSequenceDone) {
    return;
  }
  if (quitSequenceStarted) {
    event.preventDefault();
    return;
  }
  quitSequenceStarted = true;
  event.preventDefault();
  isAppQuitting = true;
  clearBackendRestartTimer();

  // Destroy tray before quit
  destroyTray();

  const finalizeQuit = () => {
    killBackendProcessHard();
    backendStatus.state = "stopped";
    backendStatus.ready = false;
    resetBackendInfo();
    publishDesktopBackendInfo();
    quitSequenceDone = true;
    app.quit();
  };

  closeAllPtySessions();
  requestBackendShutdown()
    .catch(() => ({ ok: false, reason: "shutdown_request_failed" }))
    .finally(() => {
      finalizeQuit();
    });
});
