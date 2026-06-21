#!/usr/bin/env node
/**
 * Polaris 一键端到端启动与联通核验
 *
 * 验证 backend + nat-jetstream + vite 三层的联通性:
 *   1. Vite Renderer (端口 5173)
 *   2. Backend API (端口 49977)
 *   3. NATS/JetStream (通过 runtime.v2 WebSocket)
 *
 * 用法:
 *   node infrastructure/scripts/run-connectivity-verification.mjs
 *
 * 环境变量:
 *   KERNELONE_E2E_HOME        — settings 目录（默认隔离临时目录）
 *   KERNELONE_E2E_ALLOW_REAL  — 设为 "1" 使用真实 workspace
 *   CI                        — CI 模式（retry=1）
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const specPath = "src/backend/polaris/tests/electron/connectivity-verification.spec.ts";
const playwrightArgs = ["playwright", "test", "-c", "playwright.electron.config.ts", specPath];

function buildSpawnCommand() {
  if (process.platform === "win32") {
    return { command: "cmd.exe", args: ["/d", "/s", "/c", "npx.cmd", ...playwrightArgs] };
  }
  return { command: "npx", args: playwrightArgs };
}

async function main() {
  console.log("══════════════════════════════════════════════════════════════");
  console.log("  Polaris 端到端启动与联通核验");
  console.log("  backend + nat-jetstream + vite");
  console.log("══════════════════════════════════════════════════════════════\n");

  const env = {
    ...process.env,
    KERNELONE_E2E: "1",
    KERNELONE_NATS_ENABLED: "1",
    KERNELONE_NATS_REQUIRED: "1",
    KERNELONE_RATE_LIMIT_EXEMPT_LOOPBACK: "1",
    KERNELONE_STATE_TO_RAMDISK: "0",
  };

  if (process.env.KERNELONE_E2E_ALLOW_REAL === "1") {
    env.KERNELONE_E2E_USE_REAL_SETTINGS = "1";
  }

  const dryRun = process.argv.includes("--dry-run");

  if (dryRun) {
    console.log(`[dry-run] Would run: ${buildSpawnCommand().command} ${buildSpawnCommand().args.join(" ")}`);
    console.log(`[dry-run] Spec:       ${specPath}`);
    console.log(`[dry-run] CWD:        ${repoRoot}`);
    process.exit(0);
  }

  const { command, args } = buildSpawnCommand();
  console.log(`[runner] command: ${command} ${args.join(" ")}\n`);

  const child = spawn(command, args, {
    cwd: repoRoot,
    stdio: "inherit",
    env,
  });

  child.on("exit", (code) => {
    if (code === 0) {
      console.log("\n✅ 端到端联通核验通过");
    } else {
      console.log(`\n❌ 端到端联通核验失败 (exit code: ${code})`);
    }
    process.exit(code ?? 1);
  });

  child.on("error", (err) => {
    console.error(`\n❌ 启动失败: ${err.message}`);
    process.exit(1);
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
