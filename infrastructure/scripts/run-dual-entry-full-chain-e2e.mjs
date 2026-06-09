import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  resolveE2ERealSettingsBootstrap,
  sanitizeRuntimeArtifacts,
} from "./lib/e2e-real-settings-bootstrap.mjs";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");

const dualEntryFullChainSpecs = [
  "src/backend/polaris/tests/electron/full-chain-audit.spec.ts",
  "src/backend/polaris/tests/electron/pm-director-real-flow.web.spec.ts",
];
const dualEntryMatrixArtifacts = {
  desktop: "full-chain-expanded-tech-evidence-matrix.json",
  web: "web-full-chain-expanded-tech-evidence-matrix.json",
};
const expectedSinks = ["audit", "receipt", "handoff", "task_projection"];

function parseArgs(argv) {
  const valueAfter = (flag, fallback = "") => {
    const index = argv.indexOf(flag);
    if (index < 0 || index + 1 >= argv.length) {
      return fallback;
    }
    return argv[index + 1];
  };
  return {
    dryRun:
      argv.includes("--dry-run") ||
      String(process.env.KERNELONE_E2E_DUAL_FULL_CHAIN_DRY_RUN || "").trim() === "1",
    summarizeExisting: argv.includes("--summarize-existing"),
    requireAllCandidateRuntime:
      argv.includes("--require-all-candidate-runtime") ||
      String(process.env.KERNELONE_E2E_REQUIRE_ALL_CANDIDATE_RUNTIME || "").trim() === "1",
    summaryRoot: valueAfter(
      "--summary-root",
      process.env.KERNELONE_E2E_DUAL_FULL_CHAIN_SUMMARY_ROOT ||
        path.join(repoRoot, "test-results", "electron"),
    ),
    summaryOutput: valueAfter(
      "--summary-output",
      process.env.KERNELONE_E2E_DUAL_FULL_CHAIN_SUMMARY_OUTPUT ||
        path.join(repoRoot, "test-results", "electron-dual-full-chain", "dual-entry-full-chain-summary.json"),
    ),
    summaryMinMtimeMs: Number(
      valueAfter(
        "--summary-min-mtime-ms",
        process.env.KERNELONE_E2E_DUAL_FULL_CHAIN_SUMMARY_MIN_MTIME_MS || "0",
      ) || 0,
    ),
  };
}

function realSettingsEnabled() {
  return String(process.env.KERNELONE_E2E_USE_REAL_SETTINGS || "").trim() === "1";
}

function buildPlaywrightArgs() {
  return [
    "playwright",
    "test",
    "-c",
    "playwright.electron.config.ts",
    ...dualEntryFullChainSpecs,
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

function walkFiles(root) {
  if (!fs.existsSync(root)) {
    return [];
  }
  const files = [];
  const pending = [path.resolve(root)];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(fullPath);
      } else if (entry.isFile()) {
        files.push(fullPath);
      }
    }
  }
  return files;
}

function newestFileByName(root, basename) {
  const candidates = walkFiles(root)
    .filter((filePath) => path.basename(filePath) === basename)
    .map((filePath) => ({ filePath, mtimeMs: fs.statSync(filePath).mtimeMs }))
    .sort((left, right) => right.mtimeMs - left.mtimeMs);
  return candidates[0]?.filePath || "";
}

function readJsonFile(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

function stringArray(value) {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function positiveNumber(value) {
  const numericValue = Number(value || 0);
  return Number.isFinite(numericValue) && numericValue > 0;
}

function candidateCoverageRows(candidateCoverage) {
  return Array.isArray(candidateCoverage?.rows) ? candidateCoverage.rows : [];
}

function candidateIdsByRuntimeStatus(candidateCoverage, expectedStatus) {
  return candidateCoverageRows(candidateCoverage)
    .filter((row) => String(row?.candidate_id || "").trim())
    .filter((row) => String(row?.coverage_status || "").trim() === expectedStatus)
    .map((row) => String(row.candidate_id).trim());
}

function expectedCandidateIds(candidateCoverage) {
  return candidateCoverageRows(candidateCoverage)
    .map((row) => String(row?.candidate_id || "").trim())
    .filter(Boolean);
}

function buildCandidateRuntimeCoverageUnion(matrices) {
  const expectedIds = new Set();
  const runtimeProvedIds = new Set();
  for (const matrix of Object.values(matrices)) {
    const candidateCoverage = matrix?.candidate_runtime_coverage;
    if (!candidateCoverage) {
      continue;
    }
    for (const candidateId of candidateCoverage.expected_ids || []) {
      expectedIds.add(candidateId);
    }
    for (const candidateId of candidateCoverage.runtime_proved_ids || []) {
      runtimeProvedIds.add(candidateId);
    }
  }
  const expected = [...expectedIds].sort();
  const runtimeProved = expected.filter((candidateId) => runtimeProvedIds.has(candidateId));
  const missing = expected.filter((candidateId) => !runtimeProvedIds.has(candidateId));
  return {
    expected_count: expected.length,
    runtime_proved_count: runtimeProved.length,
    missing_runtime_ids: missing,
  };
}

function validateMatrix(entrypoint, filePath, minMtimeMs = 0, requireAllCandidateRuntime = false) {
  const payload = readJsonFile(filePath);
  const stat = fs.statSync(filePath);
  const placement = payload?.core_runtime_evidence_placement;
  const core = payload?.core_runtime_integrations || {};
  const candidateCoverage = payload?.candidate_runtime_coverage;
  const missingPlacement = stringArray(placement?.missing);
  const missingCore = stringArray(core?.missing_ids);
  const missingCandidateRuntime = stringArray(candidateCoverage?.missing_runtime_ids);
  const notRuntimeProvedCandidates = stringArray(candidateCoverage?.not_runtime_proved_ids);
  const candidateRuntimeProvedIds = candidateIdsByRuntimeStatus(candidateCoverage, "runtime_proved");
  const candidateExpectedIds = expectedCandidateIds(candidateCoverage);
  const actualSinks = stringArray(placement?.expected_sinks);
  const sinkMismatch =
    actualSinks.length !== expectedSinks.length ||
    expectedSinks.some((sink) => !actualSinks.includes(sink));
  const findings = [];

  if (minMtimeMs > 0 && stat.mtimeMs < minMtimeMs) {
    findings.push(
      `${entrypoint} matrix artifact for ${entrypoint} is older than required minimum mtime: ` +
        `mtime=${Math.floor(stat.mtimeMs)} min=${Math.floor(minMtimeMs)}`,
    );
  }
  if (payload?.schema !== "polaris.e2e.expanded_tech_evidence_matrix.v1") {
    findings.push(`${entrypoint} matrix schema mismatch`);
  }
  if (payload?.require_real_chain !== true) {
    findings.push(`${entrypoint} matrix was not collected with require_real_chain=true`);
  }
  if (Number(core?.expected_count || 0) !== 16 || Number(core?.actual_count || 0) !== 16 || missingCore.length > 0) {
    findings.push(`${entrypoint} core runtime integrations incomplete: missing=${missingCore.join(",") || "(none)"}`);
  }
  if (!placement || !Array.isArray(placement.rows) || placement.rows.length !== 16 || missingPlacement.length > 0) {
    findings.push(
      `${entrypoint} core runtime evidence placement incomplete: rows=${Array.isArray(placement?.rows) ? placement.rows.length : 0}/16 ` +
        `missing=${missingPlacement.join(",") || "(none)"}`,
    );
  }
  if (sinkMismatch) {
    findings.push(`${entrypoint} placement sinks mismatch: ${actualSinks.join(",") || "(none)"}`);
  }
  if (!String(placement?.receipt_id || "").trim()) {
    findings.push(`${entrypoint} placement receipt_id is missing`);
  }
  if (!String(placement?.handoff_id || "").trim()) {
    findings.push(`${entrypoint} placement handoff_id is missing`);
  }
  const taskProjection = placement?.task_projection && typeof placement.task_projection === "object" ? placement.task_projection : {};
  for (const fieldName of ["task_count", "linked_pm_task_count", "projection_source_count"]) {
    if (!positiveNumber(taskProjection?.[fieldName])) {
      findings.push(`${entrypoint} task_projection ${fieldName} must be > 0`);
    }
  }
  if (Array.isArray(placement?.rows)) {
    for (const row of placement.rows) {
      const techId = String(row?.tech_id || "");
      const sinks = row?.sinks && typeof row.sinks === "object" ? row.sinks : {};
      for (const sinkName of expectedSinks) {
        if (sinks?.[sinkName]?.present !== true) {
          findings.push(`${entrypoint} row ${techId || "(missing-tech-id)"} ${sinkName} sink not present`);
        }
      }
    }
  }
  if (Number(payload?.summary?.required_fail || 0) !== 0) {
    findings.push(`${entrypoint} matrix has required_fail=${Number(payload?.summary?.required_fail || 0)}`);
  }
  if (requireAllCandidateRuntime) {
    if (!candidateCoverage || typeof candidateCoverage !== "object") {
      findings.push(`${entrypoint} candidate runtime coverage is missing`);
    } else if (
      Number(candidateCoverage?.expected_count || 0) <= 0 ||
      Number(candidateCoverage?.runtime_required_count || 0) <= 0 ||
      candidateExpectedIds.length <= 0
    ) {
      findings.push(
        `${entrypoint} candidate runtime coverage malformed: `
          + `runtime_proved=${Number(candidateCoverage?.runtime_proved_count || 0)}/`
          + `${Number(candidateCoverage?.expected_count || 0)} `
          + `rows=${candidateExpectedIds.length}`,
      );
    }
  }

  return {
    file: filePath,
    mtime_ms: stat.mtimeMs,
    generated_at: String(payload?.generated_at || ""),
    workspace: String(payload?.workspace || ""),
    runtime_root: String(payload?.runtime_root || ""),
    require_real_chain: Boolean(payload?.require_real_chain),
    core_actual_count: Number(core?.actual_count || 0),
    core_missing_ids: missingCore,
    placement_rows: Array.isArray(placement?.rows) ? placement.rows.length : 0,
    placement_missing: missingPlacement,
    placement_sinks: actualSinks,
    receipt_id: String(placement?.receipt_id || ""),
    handoff_id: String(placement?.handoff_id || ""),
    task_projection: placement?.task_projection || null,
    candidate_runtime_coverage: candidateCoverage
      ? {
          expected_count: Number(candidateCoverage?.expected_count || 0),
          runtime_proved_count: Number(candidateCoverage?.runtime_proved_count || 0),
          runtime_required_count: Number(candidateCoverage?.runtime_required_count || 0),
          missing_runtime_ids: missingCandidateRuntime,
          not_runtime_proved_count: notRuntimeProvedCandidates.length,
          expected_ids: candidateExpectedIds,
          runtime_proved_ids: candidateRuntimeProvedIds,
        }
      : null,
    findings,
  };
}

function summarizeDualEntryMatrices(summaryRoot, minMtimeMs = 0, requireAllCandidateRuntime = false) {
  const root = path.resolve(summaryRoot);
  const matrices = {};
  const findings = [];

  for (const [entrypoint, filename] of Object.entries(dualEntryMatrixArtifacts)) {
    const filePath = newestFileByName(root, filename);
    if (!filePath) {
      findings.push(`missing matrix artifact for ${entrypoint}: ${filename}`);
      continue;
    }
    const matrix = validateMatrix(entrypoint, filePath, minMtimeMs, requireAllCandidateRuntime);
    matrices[entrypoint] = matrix;
    findings.push(...matrix.findings);
  }

  const candidateRuntimeCoverageUnion = requireAllCandidateRuntime
    ? buildCandidateRuntimeCoverageUnion(matrices)
    : null;
  if (requireAllCandidateRuntime) {
    if (
      !candidateRuntimeCoverageUnion ||
      candidateRuntimeCoverageUnion.expected_count <= 0 ||
      candidateRuntimeCoverageUnion.missing_runtime_ids.length > 0
    ) {
      findings.push(
        `dual candidate runtime coverage incomplete: `
          + `runtime_proved=${candidateRuntimeCoverageUnion?.runtime_proved_count || 0}/`
          + `${candidateRuntimeCoverageUnion?.expected_count || 0} `
          + `missing=${candidateRuntimeCoverageUnion?.missing_runtime_ids.join(",") || "(none)"}`,
      );
    }
  }

  return {
    schema: "polaris.e2e.dual_entry_full_chain_summary.v1",
    generated_at: new Date().toISOString(),
    status: findings.length === 0 ? "PASS" : "FAIL",
    entrypoints: ["desktop", "web"],
    summary_root: root,
    summary_min_mtime_ms: minMtimeMs,
    require_all_candidate_runtime: requireAllCandidateRuntime,
    expected_matrix_artifacts: dualEntryMatrixArtifacts,
    candidate_runtime_coverage_union: candidateRuntimeCoverageUnion,
    matrices,
    findings,
  };
}

function writeSummary(summary, summaryOutput) {
  const outputPath = path.resolve(summaryOutput);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(summary, null, 2)}\n`, "utf-8");
  return outputPath;
}

function runSummaryGate(summaryRoot, summaryOutput, minMtimeMs = 0, requireAllCandidateRuntime = false) {
  const summary = summarizeDualEntryMatrices(summaryRoot, minMtimeMs, requireAllCandidateRuntime);
  writeSummary(summary, summaryOutput);
  if (summary.status !== "PASS") {
    throw new Error(summary.findings.join("; "));
  }
  return summary;
}

const options = parseArgs(process.argv.slice(2));
const runnerStartMtimeMs = Date.now();
const effectiveRunSummaryMinMtimeMs = options.summaryMinMtimeMs || runnerStartMtimeMs;

if (options.summarizeExisting) {
  try {
    const summary = runSummaryGate(
      options.summaryRoot,
      options.summaryOutput,
      options.summaryMinMtimeMs,
      options.requireAllCandidateRuntime,
    );
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
    process.exit(0);
  } catch (error) {
    console.error(`[e2e:dual-full-chain] summary failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  }
}

let realSettingsBootstrap = null;

try {
  realSettingsBootstrap = resolveE2ERealSettingsBootstrap({
    repoRoot,
    homePrefix: "e2e-dual-full-chain-home",
    runtimePrefix: "e2e-dual-full-chain",
  });
} catch (error) {
  console.error(
    `[e2e:dual-full-chain] invalid real settings seed: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(2);
}

if (!realSettingsEnabled() && !realSettingsBootstrap.settingsBootstrap) {
  console.error(
    "[e2e:dual-full-chain] KERNELONE_E2E_USE_REAL_SETTINGS=1 is required. " +
      "Alternatively provide KERNELONE_E2E_SETTINGS_JSON_BASE64 or KERNELONE_E2E_SETTINGS_JSON. " +
      "Dual-entry full-chain E2E is not allowed to pass by skipping real PM/Director/QA flows.",
  );
  process.exit(2);
}

const playwrightArgs = buildPlaywrightArgs();
const { command, args } = buildSpawnCommand(playwrightArgs);
const childEnv = {
  ...process.env,
  ...realSettingsBootstrap.envPatch,
  KERNELONE_E2E_USE_REAL_SETTINGS: "1",
  KERNELONE_NATS_ENABLED: process.env.KERNELONE_NATS_ENABLED || "1",
  KERNELONE_NATS_REQUIRED: process.env.KERNELONE_NATS_REQUIRED || "1",
  KERNELONE_DIRECTOR_RUNTIME_CODEGEN: process.env.KERNELONE_DIRECTOR_RUNTIME_CODEGEN || "1",
};
const childEnvSummary = {
  KERNELONE_E2E_USE_REAL_SETTINGS: childEnv.KERNELONE_E2E_USE_REAL_SETTINGS,
  KERNELONE_NATS_ENABLED: childEnv.KERNELONE_NATS_ENABLED,
  KERNELONE_NATS_REQUIRED: childEnv.KERNELONE_NATS_REQUIRED,
  KERNELONE_DIRECTOR_RUNTIME_CODEGEN: childEnv.KERNELONE_DIRECTOR_RUNTIME_CODEGEN,
};

if (options.dryRun) {
  process.stdout.write(
    `${JSON.stringify(
      {
        status: "DRY_RUN",
        entrypoints: ["desktop", "web"],
        ...realSettingsBootstrap.dryRunSummary,
        summary_root: path.resolve(options.summaryRoot),
        summary_output: path.resolve(options.summaryOutput),
        summary_min_mtime_ms: effectiveRunSummaryMinMtimeMs,
        require_all_candidate_runtime: options.requireAllCandidateRuntime,
        spawn_command: command,
        spawn_args: args,
        child_env: childEnvSummary,
        specs: dualEntryFullChainSpecs,
      },
      null,
      2,
    )}\n`,
  );
  process.exit(0);
}

if (!realSettingsBootstrap.settingsBootstrap) {
  console.error(
    "[e2e:dual-full-chain] real LLM settings are required. Provide " +
      "KERNELONE_E2E_SETTINGS_JSON_BASE64, KERNELONE_E2E_SETTINGS_JSON, " +
      "KERNELONE_HOME with config/settings.json, or explicitly set " +
      "KERNELONE_E2E_ALLOW_HOST_SETTINGS=1.",
  );
  process.exit(2);
}

if (!realSettingsBootstrap.llmReadinessSeedValidation.ok) {
  console.error(
    `[e2e:dual-full-chain] invalid LLM readiness seed: ${realSettingsBootstrap.llmReadinessSeedValidation.message}`,
  );
  process.exit(2);
}

const child = spawn(command, args, {
  cwd: repoRoot,
  env: childEnv,
  stdio: "inherit",
  windowsHide: true,
});

child.on("error", (error) => {
  console.error(
    `[e2e:dual-full-chain] failed to start playwright: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(1);
});

child.on("exit", (code, signal) => {
  let exitCode = code ?? 1;
  if (childEnv.KERNELONE_RUNTIME_ROOT) {
    try {
      sanitizeRuntimeArtifacts(repoRoot, childEnv.KERNELONE_RUNTIME_ROOT, "electron-dual-full-chain");
    } catch (error) {
      console.error(
        `[e2e:dual-full-chain] failed to sanitize runtime artifacts: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }
  if (signal) {
    console.error(`[e2e:dual-full-chain] playwright exited via signal ${signal}`);
    process.exit(1);
  }
  if (exitCode === 0) {
    try {
      const summary = runSummaryGate(
        options.summaryRoot,
        options.summaryOutput,
        effectiveRunSummaryMinMtimeMs,
        options.requireAllCandidateRuntime,
      );
      process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
    } catch (error) {
      console.error(`[e2e:dual-full-chain] summary failed: ${error instanceof Error ? error.message : String(error)}`);
      exitCode = 1;
    }
  }
  process.exit(exitCode);
});
