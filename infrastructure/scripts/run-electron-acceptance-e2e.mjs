import { spawn } from "node:child_process";

const acceptanceSpecs = [
  "src/backend/polaris/tests/electron/full-chain-audit.spec.ts",
  "src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts",
];
const requiredRealSettings = String(process.env.KERNELONE_E2E_USE_REAL_SETTINGS || "").trim() === "1";
const dryRun =
  process.argv.slice(2).includes("--dry-run") ||
  String(process.env.KERNELONE_E2E_ACCEPTANCE_DRY_RUN || "").trim() === "1";

if (!requiredRealSettings) {
  console.error(
    "[e2e:acceptance] KERNELONE_E2E_USE_REAL_SETTINGS=1 is required. " +
      "Acceptance is not allowed to pass by skipping real PM/Director flows.",
  );
  process.exit(2);
}

const playwrightArgs = [
  "playwright",
  "test",
  "-c",
  "playwright.electron.config.ts",
  ...acceptanceSpecs,
];

function buildSpawnCommand() {
  if (process.platform === "win32") {
    return {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", "npx.cmd", ...playwrightArgs],
    };
  }
  return {
    command: "npx",
    args: playwrightArgs,
  };
}

const { command, args } = buildSpawnCommand();
const childEnv = {
  ...process.env,
  KERNELONE_E2E_USE_REAL_SETTINGS: "1",
};

if (dryRun) {
  process.stdout.write(
    `${JSON.stringify(
      {
        status: "DRY_RUN",
        spawn_command: command,
        spawn_args: args,
        specs: acceptanceSpecs,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(0);
}

const child = spawn(command, args, {
  cwd: process.cwd(),
  env: childEnv,
  stdio: "inherit",
  windowsHide: true,
});

child.on("error", (error) => {
  console.error(`[e2e:acceptance] failed to start playwright: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    console.error(`[e2e:acceptance] playwright exited via signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
