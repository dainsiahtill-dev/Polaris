const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const repoRoot = path.join(__dirname, "..", "..");
const args = new Set(process.argv.slice(2));
const predevMode = args.has("--predev");
const pythonOnly = args.has("--python-only");
const nodeOnly = args.has("--node-only");
const backendSourcePath = path.join(repoRoot, "src", "backend");
const pythonRuntimeProbe = [
  "import importlib",
  "import pathlib",
  "import sys",
  "backend = pathlib.Path.cwd() / 'src' / 'backend'",
  "if backend.exists():",
  "    sys.path.insert(0, str(backend))",
  "required = ['fastapi', 'uvicorn', 'pydantic', 'polaris.delivery.server']",
  "missing = []",
  "for name in required:",
  "    try:",
  "        importlib.import_module(name)",
  "    except Exception as exc:",
  "        missing.append(f'{name}: {exc.__class__.__name__}: {exc}')",
  "if missing:",
  "    raise SystemExit('missing runtime dependencies: ' + '; '.join(missing))",
  "print(sys.executable)",
].join("\n");

if (pythonOnly && nodeOnly) {
  console.error("[setup:dev] --python-only and --node-only cannot be used together.");
  process.exit(1);
}

function runCommand(command, commandArgs, label) {
  const joined = [command, ...commandArgs].join(" ");
  console.log(`[setup:dev] ${label}: ${joined}`);
  const result = spawnSync(command, commandArgs, {
    cwd: repoRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${String(result.status)}`);
  }
}

function runWindowsBatchScript(scriptPath, label) {
  console.log(`[setup:dev] ${label}: ${scriptPath}`);
  const result = spawnSync(scriptPath, [], {
    cwd: repoRoot,
    env: process.env,
    stdio: "inherit",
    shell: true,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${label} failed with exit code ${String(result.status)}`);
  }
}

function resolveVenvPythonPath() {
  const venvRoot = path.join(repoRoot, ".venv");
  const pythonPath = process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
  return fs.existsSync(pythonPath) ? pythonPath : "";
}

function resolveVenvSitePackagesPath() {
  const venvRoot = path.join(repoRoot, ".venv");
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

function withPrependedPythonPath(baseEnv, entries) {
  const normalizedEntries = entries.filter(Boolean);
  if (normalizedEntries.length === 0) {
    return baseEnv;
  }
  const existing = String(baseEnv.PYTHONPATH || "")
    .split(path.delimiter)
    .filter(Boolean);
  const seen = new Set();
  const merged = [...normalizedEntries, ...existing].filter((entry) => {
    const key = entry.toLowerCase();
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
  return { ...baseEnv, PYTHONPATH: merged.join(path.delimiter) };
}

function buildPythonRuntimeEnv() {
  return withPrependedPythonPath(
    { ...process.env, PYTHONUNBUFFERED: "1" },
    [resolveVenvSitePackagesPath(), backendSourcePath],
  );
}

function checkPythonExecutable(pythonPath) {
  if (!pythonPath || !fs.existsSync(pythonPath)) {
    return { ok: false, message: "python executable is missing" };
  }
  const result = spawnSync(pythonPath, ["-c", "import sys; print(sys.executable)"], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    encoding: "utf8",
  });
  if (result.error) {
    return { ok: false, message: result.error.message || String(result.error) };
  }
  if (result.status !== 0) {
    const detail = `${result.stderr || result.stdout || ""}`.trim();
    return { ok: false, message: detail || `python exited with code ${String(result.status)}` };
  }
  return { ok: true, message: "" };
}

function checkPythonRuntime(pythonPath) {
  const executableCheck = checkPythonExecutable(pythonPath);
  if (!executableCheck.ok) {
    return executableCheck;
  }
  const result = spawnSync(pythonPath, ["-c", pythonRuntimeProbe], {
    cwd: repoRoot,
    env: buildPythonRuntimeEnv(),
    encoding: "utf8",
  });
  if (result.error) {
    return { ok: false, message: result.error.message || String(result.error) };
  }
  if (result.status !== 0) {
    const detail = `${result.stderr || result.stdout || ""}`.trim();
    return { ok: false, message: detail || `python runtime probe exited with code ${String(result.status)}` };
  }
  return { ok: true, message: "" };
}

function hasNodeDependency(moduleEntry) {
  try {
    require.resolve(moduleEntry, { paths: [repoRoot] });
    return true;
  } catch {
    return false;
  }
}

function runNpmInstall() {
  if (process.platform === "win32") {
    runCommand("cmd.exe", ["/d", "/s", "/c", "npm install"], "npm install");
    return;
  }
  runCommand("npm", ["install"], "npm install");
}

function ensureNodeDependencies() {
  const requiredEntries = [
    "wait-on/package.json",
    "vite/package.json",
    "electron/package.json",
  ];
  const missing = requiredEntries.filter((entry) => !hasNodeDependency(entry));
  if (missing.length === 0) {
    console.log("[setup:dev] Node dependencies already available.");
    return;
  }
  console.log(`[setup:dev] Missing Node dependencies detected: ${missing.join(", ")}`);
  runNpmInstall();
}

function runSetupVenvScript() {
  const setupBat = path.join(repoRoot, "infrastructure", "setup", "setup_venv.bat");
  const setupSh = path.join(repoRoot, "infrastructure", "setup", "setup_venv.sh");

  if (process.platform === "win32") {
    if (!fs.existsSync(setupBat)) {
      throw new Error(`Missing setup script: ${setupBat}`);
    }
    runWindowsBatchScript(setupBat, "setup_venv.bat");
    return;
  }

  if (!fs.existsSync(setupSh)) {
    throw new Error(`Missing setup script: ${setupSh}`);
  }
  runCommand("bash", [setupSh], "setup_venv.sh");
}

function ensurePythonEnvironment() {
  const venvPythonPath = resolveVenvPythonPath();
  const shouldRunFullSetup = !predevMode;

  if (!venvPythonPath) {
    console.log("[setup:dev] Python virtual environment is missing, bootstrapping .venv.");
    runSetupVenvScript();
    return;
  }

  const pythonCheck = checkPythonExecutable(venvPythonPath);
  if (!pythonCheck.ok) {
    console.warn(`[setup:dev] Python virtual environment is invalid: ${pythonCheck.message}`);
    console.warn("[setup:dev] Rebuilding .venv.");
    runSetupVenvScript();
    return;
  }

  const runtimeCheck = checkPythonRuntime(venvPythonPath);
  if (!runtimeCheck.ok) {
    console.warn(`[setup:dev] Python virtual environment runtime check failed: ${runtimeCheck.message}`);
    console.warn("[setup:dev] Refreshing Python dependencies in .venv.");
    runSetupVenvScript();
    return;
  }

  if (!shouldRunFullSetup) {
    console.log(`[setup:dev] Python virtual environment detected: ${venvPythonPath}`);
    return;
  }

  console.log("[setup:dev] Refreshing Python dependencies in .venv.");
  runSetupVenvScript();
}

function main() {
  if (!pythonOnly) {
    ensureNodeDependencies();
  }
  if (!nodeOnly) {
    ensurePythonEnvironment();
  }
  console.log("[setup:dev] Environment is ready.");
}

try {
  main();
} catch (error) {
  const message = error && error.message ? error.message : String(error);
  console.error(`[setup:dev] ERROR: ${message}`);
  process.exit(1);
}
