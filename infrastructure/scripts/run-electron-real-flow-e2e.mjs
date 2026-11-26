import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  resolveE2ERealSettingsBootstrap,
  sanitizeRuntimeArtifacts,
} from "./lib/e2e-real-settings-bootstrap.mjs";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");

const realFlowSpecs = [
  "src/backend/polaris/tests/electron/full-chain-audit.spec.ts",
  "src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts",
];

function parseArgs(argv) {
  return {
    dryRun:
      argv.includes("--dry-run") ||
      String(process.env.KERNELONE_E2E_REAL_FLOW_DRY_RUN || "").trim() === "1",
  };
}

function buildPlaywrightArgs() {
  return [
    "playwright",
    "test",
    "-c",
    "playwright.electron.config.ts",
    ...realFlowSpecs,
  ];
}

function buildSpawnCommand(playwrightArgs) {
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

const options = parseArgs(process.argv.slice(2));
let bootstrap;
try {
  bootstrap = resolveE2ERealSettingsBootstrap({ repoRoot });
} catch (error) {
  console.error(
    `[e2e:real-flow] invalid real-flow seed: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(2);
}

const { settingsBootstrap, llmReadinessSeedValidation, envPatch, dryRunSummary } = bootstrap;
const playwrightArgs = buildPlaywrightArgs();
const { command, args } = buildSpawnCommand(playwrightArgs);
const childEnv = {
  ...process.env,
  ...envPatch,
  KERNELONE_E2E_USE_REAL_SETTINGS: "1",
  KERNELONE_DIRECTOR_RUNTIME_CODEGEN: process.env.KERNELONE_DIRECTOR_RUNTIME_CODEGEN || "1",
};

if (options.dryRun) {
  process.stdout.write(
    `${JSON.stringify(
      {
        status: "DRY_RUN",
        ...dryRunSummary,
        spawn_command: command,
        spawn_args: args,
        runtime_root: childEnv.KERNELONE_RUNTIME_ROOT || "",
        state_to_ramdisk: childEnv.KERNELONE_STATE_TO_RAMDISK || "",
        specs: realFlowSpecs,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(0);
}

if (!settingsBootstrap) {
  console.error(
    "[e2e:real-flow] real LLM settings are required. Provide " +
      "KERNELONE_E2E_SETTINGS_JSON_BASE64, KERNELONE_E2E_SETTINGS_JSON, " +
      "KERNELONE_HOME with config/settings.json, or explicitly set " +
      "KERNELONE_E2E_ALLOW_HOST_SETTINGS=1.",
  );
  process.exit(2);
}

if (!llmReadinessSeedValidation.ok) {
  console.error(`[e2e:real-flow] invalid LLM readiness seed: ${llmReadinessSeedValidation.message}`);
  process.exit(2);
}

const child = spawn(command, args, {
  cwd: repoRoot,
  env: childEnv,
  stdio: "inherit",
  windowsHide: true,
});

child.on("error", (error) => {
  console.error(`[e2e:real-flow] failed to start playwright: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (childEnv.KERNELONE_RUNTIME_ROOT) {
    try {
      sanitizeRuntimeArtifacts(repoRoot, childEnv.KERNELONE_RUNTIME_ROOT, "electron-real-flow");
    } catch (error) {
      console.error(
        `[e2e:real-flow] failed to sanitize runtime artifacts: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  if (signal) {
    console.error(`[e2e:real-flow] playwright exited via signal ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 1);
});
