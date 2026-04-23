const { spawn, execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const repoRoot = path.join(__dirname, "..", "..");
const electronMain = path.join(repoRoot, "src", "electron", "main.cjs");
const backendScript = path.join(repoRoot, "src", "backend", "server.py");

function normalizeForMatch(value) {
  return String(value || "").replace(/\\/g, "/").toLowerCase();
}

const normalizedElectronMain = normalizeForMatch(electronMain);
const normalizedBackendScript = normalizeForMatch(backendScript);

function parseProcessJson(raw) {
  const text = String(raw || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return [];
  }
}

function listWindowsProcessesByName(imageName) {
  try {
    const script = [
      `$items = Get-CimInstance Win32_Process -Filter "name = '${String(imageName || "").replace(/'/g, "''")}'" | Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine`,
      "$items | ConvertTo-Json -Compress",
    ].join("; ");
    const output = execFileSync("powershell.exe", ["-NoProfile", "-Command", script], {
      encoding: "utf8",
      timeout: 5000,
    });
    return parseProcessJson(output);
  } catch {
    return [];
  }
}

function isPolarisBackendProcess(proc) {
  return normalizeForMatch(proc && proc.CommandLine).includes(normalizedBackendScript);
}

function isPolarisElectronProcess(proc) {
  return normalizeForMatch(proc && proc.CommandLine).includes(normalizedElectronMain);
}

function killProcessTree(pid) {
  try {
    execFileSync("taskkill.exe", ["/PID", String(pid), "/T", "/F"], { timeout: 5000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// Kill any existing Polaris backend processes before starting
function killExistingBackend() {
  console.log("[run-electron] killExistingBackend starting...");
  try {
    if (process.platform === "win32") {
      let killedAny = false;

      const backendPids = listWindowsProcessesByName("python.exe")
        .filter(isPolarisBackendProcess)
        .map((proc) => Number(proc.ProcessId))
        .filter((pid) => Number.isFinite(pid) && pid > 0);

      for (const pid of backendPids) {
        if (killProcessTree(pid)) {
          console.log(`[run-electron] killed Polaris backend tree: ${pid}`);
          killedAny = true;
        }
      }

      const electronPids = listWindowsProcessesByName("electron.exe")
        .filter(isPolarisElectronProcess)
        .map((proc) => Number(proc.ProcessId))
        .filter((pid) => Number.isFinite(pid) && pid > 0);

      for (const pid of electronPids) {
        if (killProcessTree(pid)) {
          console.log(`[run-electron] killed Polaris electron tree: ${pid}`);
          killedAny = true;
        }
      }

      if (!killedAny) {
        console.log("[run-electron] no existing Polaris backend/electron processes found");
      }
    } else {
      // Unix-like systems
      try {
        execFileSync("pkill", ["-f", backendScript], { timeout: 5000, stdio: "ignore" });
        console.log("[run-electron] killed Polaris backend processes");
      } catch {
        // ignore
      }
    }
  } catch (e) {
    console.log("[run-electron] killExistingBackend error: " + e.message);
  }
  console.log("[run-electron] killExistingBackend completed");
}

console.log("[run-electron] starting, PID=" + process.pid);
killExistingBackend();
console.log("[run-electron] killExistingBackend completed");

function resolveVenvPython() {
  const venvRoot = path.join(repoRoot, ".venv");
  const pythonPath = process.platform === "win32"
    ? path.join(venvRoot, "Scripts", "python.exe")
    : path.join(venvRoot, "bin", "python");
  if (fs.existsSync(pythonPath)) {
    return pythonPath;
  }
  return "";
}

const env = { ...process.env };
if (env.ELECTRON_RUN_AS_NODE) {
  delete env.ELECTRON_RUN_AS_NODE;
}
const venvPython = resolveVenvPython();
const configuredPython = (env.KERNELONE_PYTHON || "").trim();

if (venvPython) {
  if (!configuredPython) {
    env.KERNELONE_PYTHON = venvPython;
  } else if (!fs.existsSync(configuredPython)) {
    console.warn(`[polaris] KERNELONE_PYTHON not found: ${configuredPython}`);
    env.KERNELONE_PYTHON = venvPython;
  }
}

console.log(`[polaris] python: ${env.KERNELONE_PYTHON || "python"}`);

const electronBinary = require("electron");
const child = spawn(electronBinary, [electronMain], {
  stdio: "inherit",
  env,
});

child.on("exit", (code) => {
  process.exit(code ?? 0);
});
