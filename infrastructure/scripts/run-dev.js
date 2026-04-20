const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const net = require("net");
const path = require("path");
const { formatLogLine } = require("./log-pretty");

const repoRoot = path.join(__dirname, "..", "..");
const runtimeDir = path.join(repoRoot, ".polaris", "runtime");
const devStatePath = path.join(runtimeDir, "dev-runner-state.json");
const preferredPortRaw = Number(process.env.POLARIS_RENDERER_PORT || "5173");
const preferredPort = Number.isFinite(preferredPortRaw) && preferredPortRaw > 0 ? preferredPortRaw : 5173;
const maxPortChecks = 20;
const probeHosts = ["127.0.0.1", "::1"];
const portCleanupTimeoutMs = 3000;
const portCleanupPollMs = 100;

function isTruthyEnv(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parsePort(address) {
  const match = String(address || "").trim().match(/:(\d+)$/);
  if (!match) return null;
  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function readDevState() {
  try {
    const raw = fs.readFileSync(devStatePath, { encoding: "utf8" });
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeDevState(payload) {
  try {
    fs.mkdirSync(runtimeDir, { recursive: true });
    const tempPath = `${devStatePath}.${process.pid}.${Date.now()}.tmp`;
    fs.writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8" });
    fs.renameSync(tempPath, devStatePath);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    console.warn(`[dev-runner] failed to persist state: ${message}`);
  }
}

function getListeningPidsByPort(port) {
  if (!Number.isFinite(port) || port <= 0) {
    return [];
  }
  try {
    if (process.platform === "win32") {
      const output = execFileSync("netstat", ["-ano", "-p", "tcp"], { encoding: "utf8" });
      const pids = new Set();
      for (const line of output.split(/\r?\n/)) {
        if (!/\bLISTENING\b/.test(line)) continue;
        const parts = line.trim().split(/\s+/);
        if (parts.length < 5) continue;
        const localPort = parsePort(parts[1]);
        if (localPort !== port) continue;
        const pid = Number.parseInt(parts[4], 10);
        if (Number.isFinite(pid) && pid > 0) {
          pids.add(pid);
        }
      }
      return Array.from(pids);
    }

    const output = execFileSync("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"], { encoding: "utf8" });
    return output
      .split(/\r?\n/)
      .map((entry) => Number.parseInt(entry.trim(), 10))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  } catch {
    return [];
  }
}

function getProcessName(pid) {
  try {
    if (process.platform === "win32") {
      const output = execFileSync("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], { encoding: "utf8" }).trim();
      if (!output || output.startsWith("INFO:")) return "";
      const match = output.match(/^"([^"]+)"/);
      return match ? match[1].toLowerCase() : "";
    }
    return execFileSync("ps", ["-p", String(pid), "-o", "comm="], { encoding: "utf8" }).trim().toLowerCase();
  } catch {
    return "";
  }
}

function isAutoKillCandidate(pid) {
  const processName = getProcessName(pid);
  if (!processName) return false;
  const allowList = process.platform === "win32"
    ? new Set(["node.exe", "cmd.exe", "npm.exe", "npm.cmd", "electron.exe"])
    : new Set(["node", "npm", "electron"]);
  return allowList.has(processName);
}

async function cleanupRememberedPort() {
  if (String(process.env.POLARIS_AUTOKILL_LAST_PORT || "1") === "0") {
    return;
  }

  const remembered = readDevState();
  const rememberedPort = Number.parseInt(String(remembered && remembered.rendererPort ? remembered.rendererPort : ""), 10);
  if (!Number.isFinite(rememberedPort) || rememberedPort <= 0) {
    return;
  }

  const occupied = !(await isPortAvailable(rememberedPort));
  if (!occupied) {
    return;
  }

  const listeningPids = getListeningPidsByPort(rememberedPort);
  if (listeningPids.length === 0) {
    console.warn(`[dev-runner] remembered port ${rememberedPort} is occupied, but PID lookup failed`);
    return;
  }

  let killedAny = false;
  for (const pid of listeningPids) {
    if (pid === process.pid) continue;
    if (!isAutoKillCandidate(pid)) {
      console.warn(`[dev-runner] skip PID ${pid} on remembered port ${rememberedPort} (not a known dev process)`);
      continue;
    }
    try {
      process.kill(pid);
      killedAny = true;
      console.log(`[dev-runner] killed stale PID ${pid} on remembered port ${rememberedPort}`);
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      console.warn(`[dev-runner] failed to kill PID ${pid}: ${message}`);
    }
  }

  if (!killedAny) {
    return;
  }

  const deadline = Date.now() + portCleanupTimeoutMs;
  while (Date.now() < deadline) {
    // eslint-disable-next-line no-await-in-loop
    const available = await isPortAvailable(rememberedPort);
    if (available) return;
    // eslint-disable-next-line no-await-in-loop
    await sleep(portCleanupPollMs);
  }

  console.warn(`[dev-runner] remembered port ${rememberedPort} is still occupied after cleanup`);
}

function isPortAvailableOnHost(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", (error) => {
      const code = error && error.code ? String(error.code) : "";
      if (code === "EADDRINUSE" || code === "EACCES") {
        resolve(false);
        return;
      }

      // Ignore hosts not supported in the current runtime/network stack.
      if (code === "EAFNOSUPPORT" || code === "EADDRNOTAVAIL" || code === "EINVAL") {
        resolve(true);
        return;
      }

      resolve(false);
    });
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen({
      port,
      host,
      exclusive: true,
    });
  });
}

async function isPortAvailable(port) {
  for (const host of probeHosts) {
    // eslint-disable-next-line no-await-in-loop
    const availableOnHost = await isPortAvailableOnHost(port, host);
    if (!availableOnHost) {
      return false;
    }
  }
  return true;
}

async function selectRendererPort(startPort, checks) {
  for (let offset = 0; offset < checks; offset += 1) {
    const candidate = startPort + offset;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortAvailable(candidate)) {
      return candidate;
    }
  }
  throw new Error(`No free renderer port found in range ${startPort}-${startPort + checks - 1}`);
}

function attachPrefixedOutput(stream, sourceTag, target) {
  if (!stream) return;
  let buffer = "";

  const flushLine = (line) => {
    const formatted = formatLogLine(sourceTag, line, { tty: Boolean(target && target.isTTY) });
    if (formatted === null) return;
    target.write(`${formatted}\n`);
  };

  stream.on("data", (chunk) => {
    buffer += String(chunk || "");
    const lines = buffer.split(/\r\n|\n|\r/);
    buffer = lines.pop() || "";
    for (const line of lines) flushLine(line);
  });

  stream.on("end", () => {
    if (!buffer) return;
    flushLine(buffer);
    buffer = "";
  });
}

function runNpmScript(scriptName, env) {
  if (process.platform === "win32") {
    return spawn("cmd.exe", ["/d", "/s", "/c", `npm run ${scriptName}`], {
      cwd: repoRoot,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
  }

  return spawn("npm", ["run", scriptName], {
    cwd: repoRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function killProcess(child) {
  if (!child || child.killed) return;
  child.kill("SIGTERM");
}

function buildCorsOrigins(rendererPort) {
  const dynamicOrigins = [
    `http://localhost:${rendererPort}`,
    `http://127.0.0.1:${rendererPort}`,
  ];
  const configuredOrigins = String(process.env.POLARIS_CORS_ORIGINS || "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  const deduped = new Set([...configuredOrigins, ...dynamicOrigins]);
  return Array.from(deduped).join(",");
}

async function main() {
  const dryRun = process.argv.includes("--dry-run");
  await cleanupRememberedPort();
  const rendererPort = await selectRendererPort(preferredPort, maxPortChecks);
  const rendererUrl = `http://localhost:${rendererPort}`;
  const corsOrigins = buildCorsOrigins(rendererPort);

  console.log(`[dev-runner] renderer port: ${rendererPort}`);
  console.log(`[dev-runner] renderer url: ${rendererUrl}`);

  writeDevState({
    updatedAt: new Date().toISOString(),
    rendererPort,
    rendererUrl,
    rendererPid: null,
    electronPid: null,
  });

  if (dryRun) {
    return;
  }

  const env = {
    ...process.env,
    POLARIS_RENDERER_PORT: String(rendererPort),
    POLARIS_DEV_SERVER_URL: rendererUrl,
    POLARIS_CORS_ORIGINS: corsOrigins,
  };
  const skipElectron = isTruthyEnv(env.POLARIS_DEV_SKIP_ELECTRON);

  const renderer = runNpmScript("dev:renderer", env);
  const electron = skipElectron ? null : runNpmScript("dev:electron", env);
  if (skipElectron) {
    console.log("[dev-runner] skipping dev:electron because POLARIS_DEV_SKIP_ELECTRON=1");
  }

  writeDevState({
    updatedAt: new Date().toISOString(),
    rendererPort,
    rendererUrl,
    rendererPid: renderer && Number.isFinite(renderer.pid) ? renderer.pid : null,
    electronPid: electron && Number.isFinite(electron.pid) ? electron.pid : null,
  });

  attachPrefixedOutput(renderer.stdout, "dev:renderer", process.stdout);
  attachPrefixedOutput(renderer.stderr, "dev:renderer", process.stderr);
  if (electron) {
    attachPrefixedOutput(electron.stdout, "dev:electron", process.stdout);
    attachPrefixedOutput(electron.stderr, "dev:electron", process.stderr);
  }

  let finished = false;
  let exitCode = 0;

  const shutdown = (code) => {
    if (finished) return;
    finished = true;
    exitCode = code;
    killProcess(renderer);
    killProcess(electron);
    setTimeout(() => process.exit(exitCode), 200);
  };

  renderer.on("exit", (code) => {
    if (finished) return;
    const normalized = code === null ? 1 : code;
    if (normalized !== 0) {
      console.error(`[dev-runner] dev:renderer exited with code ${normalized}`);
      shutdown(normalized);
      return;
    }
    shutdown(0);
  });

  if (electron) {
    electron.on("exit", (code) => {
      if (finished) return;
      const normalized = code === null ? 1 : code;
      if (normalized !== 0) {
        console.error(`[dev-runner] dev:electron exited with code ${normalized}`);
        shutdown(normalized);
        return;
      }
      shutdown(0);
    });
  }

  renderer.on("error", (error) => {
    if (finished) return;
    console.error(`[dev-runner] failed to start dev:renderer: ${error.message}`);
    shutdown(1);
  });

  if (electron) {
    electron.on("error", (error) => {
      if (finished) return;
      console.error(`[dev-runner] failed to start dev:electron: ${error.message}`);
      shutdown(1);
    });
  }

  process.on("SIGINT", () => shutdown(130));
  process.on("SIGTERM", () => shutdown(143));
}

main().catch((error) => {
  console.error(`[dev-runner] ${error.message}`);
  process.exit(1);
});
