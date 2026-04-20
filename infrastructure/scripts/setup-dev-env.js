const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const repoRoot = path.join(__dirname, "..", "..");
const args = new Set(process.argv.slice(2));
const predevMode = args.has("--predev");
const pythonOnly = args.has("--python-only");
const nodeOnly = args.has("--node-only");

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
