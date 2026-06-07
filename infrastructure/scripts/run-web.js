const { spawn, execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const net = require("net");
const os = require("os");
const path = require("path");
const { formatLogLine } = require("./log-pretty");

const repoRoot = path.join(__dirname, "..", "..");
const backendScript = path.join(repoRoot, "src", "backend", "server.py");
const backendSourcePath = path.join(repoRoot, "src", "backend");
const venvRoot = path.join(repoRoot, ".venv");
const viteConfig = path.join(repoRoot, "src", "frontend", "vite.config.ts");

const mode = process.argv.includes("--static") ? "static" : "dev";
const dryRun = process.argv.includes("--dry-run");

function isTruthyEnv(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}

function positiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeForMatch(value) {
  return String(value || "").replace(/\\/g, "/").toLowerCase();
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
      if (code === "EAFNOSUPPORT" || code === "EADDRNOTAVAIL" || code === "EINVAL") {
        resolve(true);
        return;
      }
      resolve(false);
    });
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen({ port, host, exclusive: true });
  });
}

async function isPortAvailable(port, hosts) {
  for (const host of hosts) {
    // eslint-disable-next-line no-await-in-loop
    const available = await isPortAvailableOnHost(port, host);
    if (!available) {
      return false;
    }
  }
  return true;
}

async function selectPort(startPort, hosts) {
  const maxChecks = positiveInt(process.env.KERNELONE_WEB_PORT_CHECKS, 20);
  for (let offset = 0; offset < maxChecks; offset += 1) {
    const candidate = startPort + offset;
    // eslint-disable-next-line no-await-in-loop
    if (await isPortAvailable(candidate, hosts)) {
      return candidate;
    }
  }
  throw new Error(`No free port found in range ${startPort}-${startPort + maxChecks - 1}`);
}

function resolveVenvPython() {
  const pythonPath = process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
  if (fs.existsSync(pythonPath) && checkPythonExecutable(pythonPath).ok) {
    return pythonPath;
  }
  return "";
}

function checkPythonExecutable(pythonPath) {
  if (!pythonPath || !fs.existsSync(pythonPath)) {
    return { ok: false, message: "python executable is missing" };
  }
  try {
    execFileSync(pythonPath, ["-c", "import sys; print(sys.executable)"], {
      cwd: repoRoot,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      encoding: "utf8",
      timeout: 10000,
    });
    return { ok: true, message: "" };
  } catch (error) {
    const stderr = error && error.stderr ? String(error.stderr).trim() : "";
    const stdout = error && error.stdout ? String(error.stdout).trim() : "";
    const message = stderr || stdout || (error && error.message ? error.message : String(error));
    return { ok: false, message };
  }
}

function resolvePython() {
  const configured = String(process.env.KERNELONE_PYTHON || "").trim();
  if (configured && checkPythonExecutable(configured).ok) {
    return configured;
  }
  return resolveVenvPython() || "python3";
}

function resolveVenvSitePackagesPath(pythonPath) {
  if (!pythonPath || !normalizeForMatch(pythonPath).startsWith(normalizeForMatch(venvRoot))) {
    return "";
  }
  if (process.platform === "win32") {
    const sitePackagesPath = path.join(venvRoot, "Lib", "site-packages");
    return fs.existsSync(sitePackagesPath) ? sitePackagesPath : "";
  }
  const libPath = path.join(venvRoot, "lib");
  if (!fs.existsSync(libPath)) {
    return "";
  }
  const candidates = fs.readdirSync(libPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.startsWith("python"))
    .map((entry) => path.join(libPath, entry.name, "site-packages"))
    .filter((candidate) => fs.existsSync(candidate));
  return candidates[0] || "";
}

function buildPythonPath(baseEnv, pythonPath) {
  const entries = [resolveVenvSitePackagesPath(pythonPath), backendSourcePath].filter(Boolean);
  const existing = String(baseEnv.PYTHONPATH || "").split(path.delimiter).filter(Boolean);
  const seen = new Set();
  return [...entries, ...existing].filter((entry) => {
    const key = normalizeForMatch(entry);
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  }).join(path.delimiter);
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
    for (const line of lines) {
      flushLine(line);
    }
  });
  stream.on("end", () => {
    if (!buffer) return;
    flushLine(buffer);
    buffer = "";
  });
}

function spawnProcess(command, args, env, sourceTag) {
  const child = spawn(command, args, {
    cwd: repoRoot,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  attachPrefixedOutput(child.stdout, sourceTag, process.stdout);
  attachPrefixedOutput(child.stderr, sourceTag, process.stderr);
  return child;
}

function spawnNpm(args, env, sourceTag) {
  if (process.platform === "win32") {
    return spawnProcess("cmd.exe", ["/d", "/s", "/c", `npm ${args.join(" ")}`], env, sourceTag);
  }
  return spawnProcess("npm", args, env, sourceTag);
}

function runNpmSync(args, env) {
  const command = process.platform === "win32" ? "cmd.exe" : "npm";
  const commandArgs = process.platform === "win32" ? ["/d", "/s", "/c", `npm ${args.join(" ")}`] : args;
  execFileSync(command, commandArgs, {
    cwd: repoRoot,
    env,
    stdio: "inherit",
  });
}

function openBrowser(url) {
  if (!isTruthyEnv(process.env.KERNELONE_WEB_OPEN)) {
    return;
  }
  try {
    if (process.platform === "win32") {
      spawn("cmd.exe", ["/c", "start", "", url], { detached: true, stdio: "ignore" }).unref();
    } else if (process.env.WSL_DISTRO_NAME) {
      spawn("cmd.exe", ["/c", "start", "", url], { detached: true, stdio: "ignore" }).unref();
    } else if (process.platform === "darwin") {
      spawn("open", [url], { detached: true, stdio: "ignore" }).unref();
    } else {
      spawn("xdg-open", [url], { detached: true, stdio: "ignore" }).unref();
    }
  } catch {
    // Opening a browser is best-effort only.
  }
}

function killProcess(child) {
  if (!child || child.killed) return;
  child.kill("SIGTERM");
}

function shouldEnableSelfUpgrade(workspace) {
  if (process.env.KERNELONE_WEB_SELF_UPGRADE !== undefined) {
    return isTruthyEnv(process.env.KERNELONE_WEB_SELF_UPGRADE);
  }
  return normalizeForMatch(path.resolve(workspace)) === normalizeForMatch(repoRoot);
}

async function main() {
  const backendHost = String(process.env.KERNELONE_BACKEND_HOST || "127.0.0.1").trim() || "127.0.0.1";
  const rendererHost = String(process.env.KERNELONE_WEB_HOST || process.env.KERNELONE_RENDERER_HOST || "127.0.0.1").trim() || "127.0.0.1";
  const backendPort = await selectPort(
    positiveInt(process.env.KERNELONE_BACKEND_PORT, 49977),
    [backendHost],
  );
  const rendererPort = await selectPort(
    positiveInt(process.env.KERNELONE_RENDERER_PORT, 5173),
    [rendererHost],
  );
  const token = String(process.env.KERNELONE_BACKEND_TOKEN || "").trim()
    || crypto.randomBytes(16).toString("hex");
  const workspace = path.resolve(String(process.env.KERNELONE_WORKSPACE || process.cwd()).trim() || process.cwd());
  const backendUrl = `http://${backendHost}:${backendPort}`;
  const browserHost = rendererHost === "0.0.0.0" ? "127.0.0.1" : rendererHost;
  const browserUrl = `http://${browserHost}:${rendererPort}`;
  const corsOrigins = [
    `http://localhost:${rendererPort}`,
    `http://127.0.0.1:${rendererPort}`,
    `http://${browserHost}:${rendererPort}`,
  ];
  const configuredCors = String(process.env.KERNELONE_CORS_ORIGINS || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const corsArg = Array.from(new Set([...configuredCors, ...corsOrigins])).join(",");
  const python = resolvePython();
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    PYTHONPATH: buildPythonPath(process.env, python),
    KERNELONE_BACKEND_PORT: String(backendPort),
    KERNELONE_BACKEND_TOKEN: token,
    KERNELONE_CORS_ORIGINS: corsArg,
    VITE_BACKEND_URL: backendUrl,
    VITE_BACKEND_HOST: backendHost,
    VITE_BACKEND_PORT: String(backendPort),
    VITE_BACKEND_TOKEN: token,
  };

  console.log(`[web] mode: ${mode}`);
  console.log(`[web] workspace: ${workspace}`);
  console.log(`[web] backend: ${backendUrl}`);
  console.log(`[web] frontend: ${browserUrl}`);
  console.log("[web] backend token is injected into Vite as VITE_BACKEND_TOKEN");

  if (dryRun) {
    return;
  }

  const backendArgs = [
    backendScript,
    "--host", backendHost,
    "--port", String(backendPort),
    "--token", token,
    "--workspace", workspace,
    "--cors-origins", corsArg,
  ];
  if (shouldEnableSelfUpgrade(workspace)) {
    backendArgs.push("--self-upgrade-mode");
  }

  const backend = spawnProcess(python, backendArgs, env, "backend");
  let frontend = null;
  let finished = false;

  const shutdown = (code) => {
    if (finished) return;
    finished = true;
    killProcess(frontend);
    killProcess(backend);
    setTimeout(() => process.exit(code), 200);
  };

  backend.on("exit", (code) => {
    if (finished) return;
    shutdown(code === null ? 1 : code);
  });
  backend.on("error", (error) => {
    console.error(`[web] failed to start backend: ${error.message}`);
    shutdown(1);
  });

  if (mode === "static") {
    console.log("[web] building renderer with injected backend settings...");
    runNpmSync(["run", "build:renderer"], env);
    frontend = spawnNpm(
      ["exec", "--", "vite", "preview", "--config", viteConfig, "--host", rendererHost, "--port", String(rendererPort)],
      env,
      "web:static",
    );
  } else {
    frontend = spawnNpm(
      ["run", "dev:renderer", "--", "--host", rendererHost, "--port", String(rendererPort)],
      env,
      "web:renderer",
    );
  }

  frontend.on("exit", (code) => {
    if (finished) return;
    shutdown(code === null ? 1 : code);
  });
  frontend.on("error", (error) => {
    console.error(`[web] failed to start frontend: ${error.message}`);
    shutdown(1);
  });

  openBrowser(browserUrl);
  console.log(`[web] open: ${browserUrl}`);

  process.on("SIGINT", () => shutdown(130));
  process.on("SIGTERM", () => shutdown(143));
}

main().catch((error) => {
  console.error(`[web] ${error.message}`);
  process.exit(1);
});
