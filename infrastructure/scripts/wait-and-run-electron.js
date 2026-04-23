const { spawn } = require("child_process");
const path = require("path");
const waitOn = require("wait-on");

const repoRoot = path.join(__dirname, "..", "..");
const runElectronScript = path.join(__dirname, "run-electron.js");
const defaultPortRaw = Number(process.env.KERNELONE_RENDERER_PORT || "5173");
const defaultPort = Number.isFinite(defaultPortRaw) && defaultPortRaw > 0 ? defaultPortRaw : 5173;
const rendererUrl = process.env.KERNELONE_DEV_SERVER_URL || `http://localhost:${defaultPort}`;
const dryRun = process.argv.includes("--dry-run");

async function main() {
  console.log(`[dev:electron] waiting for renderer: ${rendererUrl}`);
  await waitOn({
    resources: [rendererUrl],
    timeout: 120000,
    interval: 200,
    tcpTimeout: 1000,
    validateStatus: (status) => status >= 200 && status < 500,
  });

  if (dryRun) {
    console.log(`[dev:electron] renderer is ready: ${rendererUrl}`);
    return;
  }

  const child = spawn(process.execPath, [runElectronScript], {
    cwd: repoRoot,
    stdio: "inherit",
    env: process.env,
  });

  child.on("exit", (code) => {
    process.exit(code ?? 0);
  });

  child.on("error", (error) => {
    console.error(`[dev:electron] failed to launch electron: ${error.message}`);
    process.exit(1);
  });
}

main().catch((error) => {
  console.error(`[dev:electron] ${error.message}`);
  process.exit(1);
});
