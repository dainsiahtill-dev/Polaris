const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");
const os = require("os");

const TAG = "[nats-server]";

const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 4222;
const STARTUP_TIMEOUT_MS = 8000;
const STARTUP_POLL_MS = 100;

/** Resolve nats-server executable path. */
function resolveNatsServerPath() {
  const explicit = String(process.env.KERNELONE_NATS_SERVER_BIN || "").trim();
  if (explicit) {
    const resolved = path.resolve(explicit.replace(/^~/, os.homedir()));
    if (fs.existsSync(resolved)) return resolved;
  }

  // Try which / where
  try {
    const cmd = process.platform === "win32" ? "where" : "which";
    const output = execFileSync(cmd, ["nats-server"], { encoding: "utf8", timeout: 5000 }).trim();
    const first = output.split(/\r?\n/)[0].trim();
    if (first && fs.existsSync(first)) return first;
  } catch {
    // not on PATH
  }

  // WinGet packages on Windows
  if (process.platform === "win32") {
    const localAppData = String(process.env.LOCALAPPDATA || "").trim();
    if (localAppData) {
      const packagesRoot = path.join(localAppData, "Microsoft", "WinGet", "Packages");
      if (fs.existsSync(packagesRoot)) {
        try {
          const entries = fs.readdirSync(packagesRoot).filter((e) => /^NATSAuthors\.NATSServer/i.test(e)).sort();
          for (const entry of entries) {
            const dir = path.join(packagesRoot, entry);
            const subdirs = fs.readdirSync(dir, { withFileTypes: true })
              .filter((d) => d.isDirectory())
              .map((d) => path.join(dir, d.name, "nats-server.exe"));
            for (const candidate of subdirs) {
              if (fs.existsSync(candidate)) return candidate;
            }
          }
        } catch {
          // ignore
        }
      }
    }
  }

  return null;
}

/** Check if a TCP port is accepting connections. */
function canAcceptTcp(host, port, timeoutMs) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    const ms = Math.max(100, timeoutMs || 500);
    let done = false;

    socket.setTimeout(ms);
    socket.once("connect", () => {
      if (done) return;
      done = true;
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      if (done) return;
      done = true;
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => {
      if (done) return;
      done = true;
      socket.destroy();
      resolve(false);
    });

    socket.connect(port, host);
  });
}

/** Wait until a TCP port accepts connections, with timeout. */
async function waitUntilAccepts(host, port, timeoutMs) {
  const deadline = Date.now() + Math.max(500, timeoutMs || STARTUP_TIMEOUT_MS);
  while (Date.now() < deadline) {
    if (await canAcceptTcp(host, port, 500)) return true;
    await new Promise((r) => setTimeout(r, STARTUP_POLL_MS));
  }
  return false;
}

/**
 * Parse NATS URL to extract host and port.
 * Falls back to 127.0.0.1:4222.
 */
function parseNatsEndpoint(url) {
  const raw = String(url || "").trim();
  const match = raw.match(/^(?:nats:\/\/)?([^/:]+):(\d+)$/);
  if (match) {
    const host = match[1].trim().toLowerCase();
    const port = Number.parseInt(match[2], 10);
    if (Number.isFinite(port) && ["127.0.0.1", "localhost", "::1"].includes(host)) {
      return { host, port };
    }
  }
  // Default local endpoint
  return { host: DEFAULT_HOST, port: DEFAULT_PORT };
}

/**
 * Resolve the JetStream storage directory.
 * Uses KERNELONE_HOME > KERNELONE_ROOT/.polaris > APPDATA/.polaris > ~/.polaris.
 */
function resolveStorageDir() {
  const home = String(process.env.KERNELONE_HOME || "").trim();
  const root = String(process.env.KERNELONE_ROOT || "").trim();
  let base;
  if (home) {
    base = home;
  } else if (root) {
    base = path.join(root, ".polaris");
  } else if (process.platform === "win32") {
    const appdata = String(process.env.APPDATA || "").trim();
    if (appdata) {
      const legacyHome = path.join(os.homedir(), ".polaris");
      const legacySettings = path.join(legacyHome, "config", "settings.json");
      const appdataHome = path.join(appdata, ".polaris");
      const appdataSettings = path.join(appdataHome, "config", "settings.json");
      if (fs.existsSync(legacySettings) && !fs.existsSync(appdataSettings)) {
        base = legacyHome;
      } else {
        base = appdataHome;
      }
    } else {
      base = path.join(os.homedir(), ".polaris");
    }
  } else {
    base = path.join(os.homedir(), ".polaris");
  }
  return path.join(base, "runtime", "nats", "jetstream");
}

/**
 * Ensure a local NATS server is running.
 * Returns an object with a `stop()` function for cleanup, or null if no action was taken.
 *
 * @param {object} [options]
 * @param {string} [options.url] - NATS URL (default from KERNELONE_NATS_URL or nats://127.0.0.1:4222)
 * @returns {Promise<{ stop: () => void, pid: number } | null>}
 */
async function ensureLocalNatsServer(options) {
  const url = String(
    (options && options.url) || process.env.KERNELONE_NATS_URL || ""
  ).trim() || `nats://${DEFAULT_HOST}:${DEFAULT_PORT}`;

  const { host, port } = parseNatsEndpoint(url);

  // Already running?
  if (await canAcceptTcp(host, port, 500)) {
    console.log(`${TAG} nats-server already accepting on ${host}:${port}`);
    return null;
  }

  const executable = resolveNatsServerPath();
  if (!executable) {
    console.warn(`${TAG} nats-server executable not found — skipping auto-start`);
    console.warn(`${TAG} set KERNELONE_NATS_SERVER_BIN or add nats-server to PATH`);
    return null;
  }

  const storageDir = resolveStorageDir();
  fs.mkdirSync(storageDir, { recursive: true });

  const logsDir = path.dirname(storageDir);
  const stdoutLog = path.join(logsDir, "nats-server.stdout.log");
  const stderrLog = path.join(logsDir, "nats-server.stderr.log");

  const args = ["-js", "-a", host, "-p", String(port), "-sd", storageDir];
  console.log(`${TAG} starting: ${executable} ${args.join(" ")}`);

  let stdoutFd;
  let stderrFd;
  try {
    stdoutFd = fs.openSync(stdoutLog, "a");
    stderrFd = fs.openSync(stderrLog, "a");
  } catch {
    console.warn(`${TAG} failed to open log files, writing to stdout/stderr instead`);
    stdoutFd = "ignore";
    stderrFd = "ignore";
  }

  const child = spawn(executable, args, {
    stdio: ["ignore", stdoutFd, stderrFd],
    detached: false,
    windowsHide: true,
  });

  child.unref();

  const ready = await waitUntilAccepts(host, port, STARTUP_TIMEOUT_MS);

  // Close log file handles in this process (child has its own dup'd copy)
  if (typeof stdoutFd === "number") {
    try { fs.closeSync(stdoutFd); } catch { /* ignore */ }
  }
  if (typeof stderrFd === "number") {
    try { fs.closeSync(stderrFd); } catch { /* ignore */ }
  }

  if (!ready) {
    console.error(`${TAG} nats-server failed to start within ${STARTUP_TIMEOUT_MS}ms`);
    console.error(`${TAG} check logs: ${stderrLog}`);
    try { child.kill(); } catch { /* ignore */ }
    return null;
  }

  console.log(`${TAG} nats-server ready: pid=${child.pid} ${host}:${port}`);

  return {
    pid: child.pid,
    stop() {
      try {
        if (child.exitCode === null) {
          child.kill();
          console.log(`${TAG} stopped nats-server pid=${child.pid}`);
        }
      } catch {
        /* ignore */
      }
    },
  };
}

module.exports = { ensureLocalNatsServer, resolveNatsServerPath };
