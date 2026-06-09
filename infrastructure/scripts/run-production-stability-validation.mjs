import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const currentFile = fileURLToPath(import.meta.url);
const scriptDir = path.dirname(currentFile);
const repoRoot = path.resolve(scriptDir, "..", "..");

const DEFAULT_OUTPUT = path.join(
  repoRoot,
  "test-results",
  "production-stability",
  "production-stability-audit.json",
);

const SCHEMA = "polaris.e2e.production_stability_validation.v1";
const OUTPUT_TAIL_LIMIT = 20000;

function collectFlagValues(argv, flag) {
  const values = [];
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === flag) {
      if (index + 1 < argv.length) {
        values.push(argv[index + 1]);
        index += 1;
      }
      continue;
    }
    if (arg.startsWith(`${flag}=`)) {
      values.push(arg.slice(flag.length + 1));
    }
  }
  return values;
}

function splitCsv(values) {
  return values.flatMap((value) =>
    String(value)
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
}

function parsePositiveInteger(value, label) {
  const raw = String(value ?? "").trim();
  if (!/^[1-9]\d*$/.test(raw)) {
    throw new Error(`${label} must be a positive integer`);
  }
  return Number(raw);
}

function parseMaxFailed(value) {
  // maxFailed=0 means disabled (no early termination)
  const raw = String(value ?? "").trim();
  if (raw === "0" || raw === "") {
    return 0;
  }
  if (!/^[1-9]\d*$/.test(raw)) {
    throw new Error("--max-failed must be a non-negative integer");
  }
  return Number(raw);
}

function parseArgs(argv) {
  const valueAfter = (flag, fallback = "") => {
    const index = argv.indexOf(flag);
    if (index < 0 || index + 1 >= argv.length) {
      return fallback;
    }
    return argv[index + 1];
  };

  return {
    dryRun: argv.includes("--dry-run"),
    output: path.resolve(valueAfter("--output", process.env.KERNELONE_PRODUCTION_STABILITY_OUTPUT || DEFAULT_OUTPUT)),
    onlyGateIds: splitCsv(collectFlagValues(argv, "--only-gate")),
    repeatCount: parsePositiveInteger(
      collectFlagValues(argv, "--repeat").at(-1) || process.env.KERNELONE_PRODUCTION_STABILITY_REPEAT || "1",
      "--repeat",
    ),
    maxFailed: parseMaxFailed(
      collectFlagValues(argv, "--max-failed").at(-1) || process.env.KERNELONE_PRODUCTION_STABILITY_MAX_FAILED || "0",
    ),
    skipRealChain: argv.includes("--skip-real-chain"),
  };
}

function envPatch(patch) {
  return {
    ...patch,
    LC_ALL: "C.UTF-8",
    LANG: "C.UTF-8",
  };
}

function commandLabel(command) {
  return command.join(" ");
}

function buildGates(options) {
  const allGates = [
    {
      id: "full_chain",
      title: "Dual-entry full-chain PM/Chief Engineer/Director/QA runtime validation",
      required: true,
      real_chain_required: true,
      skipped: Boolean(options.skipRealChain),
      commands: [
        [
          "npm",
          "run",
          "test:e2e:dual-full-chain",
          "--",
          "--require-all-candidate-runtime",
        ],
      ],
      env: envPatch({}),
      evidence: [
        "src/backend/polaris/tests/electron/full-chain-audit.spec.ts",
        "src/backend/polaris/tests/electron/pm-director-real-flow.web.spec.ts",
        "test-results/electron-dual-full-chain/dual-entry-full-chain-summary.json",
      ],
    },
    {
      id: "fault_injection_rollback",
      title: "Fault injection, transaction rollback, and recovery guards",
      required: true,
      real_chain_required: false,
      skipped: false,
      commands: [
        [
          "python",
          "-m",
          "pytest",
          "src/backend/polaris/tests/unit/cells/roles/kernel/internal/test_transaction_rollback_and_guards.py",
          "src/backend/polaris/cells/chief_engineer/blueprint/tests/test_director_pool_chaos.py",
          "src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard.py",
          "-q",
        ],
      ],
      env: envPatch({ PYTHONPATH: "src/backend" }),
      evidence: [
        "src/backend/polaris/tests/unit/cells/roles/kernel/internal/test_transaction_rollback_and_guards.py",
        "src/backend/polaris/cells/chief_engineer/blueprint/tests/test_director_pool_chaos.py",
        "src/backend/polaris/cells/chief_engineer/blueprint/tests/test_rollback_guard.py",
      ],
    },
    {
      id: "performance_stress",
      title: "Endpoint performance, tool performance, and stress audit package checks",
      required: true,
      real_chain_required: false,
      skipped: false,
      commands: [
        [
          "python",
          "-m",
          "pytest",
          "src/backend/polaris/tests/performance/test_v2_endpoint_performance.py",
          "src/backend/polaris/tests/performance/test_tool_performance.py",
          "src/backend/polaris/tests/agent_stress/test_runner_audit_package.py",
          "src/backend/polaris/tests/agent_stress/test_runner_post_batch_audit_flag.py",
          "-q",
        ],
      ],
      env: envPatch({ PYTHONPATH: "src/backend" }),
      evidence: [
        "src/backend/polaris/tests/performance/test_v2_endpoint_performance.py",
        "src/backend/polaris/tests/performance/test_tool_performance.py",
        "src/backend/polaris/tests/agent_stress/test_runner_audit_package.py",
        "src/backend/polaris/tests/agent_stress/test_runner_post_batch_audit_flag.py",
      ],
    },
    {
      id: "governance",
      title: "Graph and Cell governance hard-fail gate",
      required: true,
      real_chain_required: false,
      skipped: false,
      commands: [
        [
          "python",
          "src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py",
          "--workspace",
          "src/backend",
          "--mode",
          "hard-fail",
        ],
      ],
      env: envPatch({ PYTHONPATH: "src/backend" }),
      evidence: [
        "run_catalog_governance_gate.py --workspace src/backend --mode hard-fail",
        "src/backend/docs/graph/catalog/cells.yaml",
      ],
    },
    {
      id: "projection_adaptive_matrix",
      title: "ProjectionEngine adaptive ordering A/B validation (ON vs OFF)",
      required: true,
      real_chain_required: false,
      skipped: false,
      commands: [
        [
          "python",
          "-m",
          "polaris.delivery.cli.agentic_eval",
          "--suite",
          "projection_adaptive_matrix",
          "--workspace",
          "src/backend",
          "--output-format",
          "json",
        ],
      ],
      env: envPatch({ PYTHONPATH: "src/backend" }),
      evidence: [
        "src/backend/polaris/cells/llm/evaluation/internal/projection_adaptive_matrix.py",
        "runtime/llm_tests/reports/rep-*.json",
      ],
    },
  ];

  if (options.onlyGateIds.length === 0) {
    return allGates;
  }

  const selectedIds = new Set(options.onlyGateIds);
  const selectedGates = allGates.filter((gate) => selectedIds.has(gate.id));
  const knownIds = new Set(allGates.map((gate) => gate.id));
  const unknownGates = options.onlyGateIds
    .filter((gateId) => !knownIds.has(gateId))
    .map((gateId) => ({
      id: gateId,
      title: `Unknown selected production stability gate: ${gateId}`,
      required: true,
      real_chain_required: false,
      skipped: true,
      commands: [],
      evidence: [],
    }));

  return [...selectedGates, ...unknownGates];
}

function sanitizeGateForJson(gate) {
  return {
    id: gate.id,
    title: gate.title,
    required: gate.required,
    real_chain_required: gate.real_chain_required,
    skipped: gate.skipped,
    commands: gate.commands,
    evidence: gate.evidence,
  };
}

function appendOutputTail(current, chunk) {
  const text = Buffer.isBuffer(chunk) ? chunk.toString("utf-8") : String(chunk);
  const next = `${current}${text}`;
  if (next.length <= OUTPUT_TAIL_LIMIT) {
    return next;
  }
  return next.slice(next.length - OUTPUT_TAIL_LIMIT);
}

function parsePytestSummary(output) {
  const text = String(output || "");
  const summaryLine =
    text
      .split(/\r?\n/)
      .filter((line) => /={2,}/.test(line) && /\b(passed|failed|skipped|errors?|warnings?)\b/.test(line))
      .at(-1) || "";
  const collectedMatch = text.match(/collected\s+(\d+)\s+items?/);
  const summary = {};

  if (collectedMatch) {
    summary.collected = Number(collectedMatch[1]);
  }

  for (const match of summaryLine.matchAll(
    /(\d+)\s+(passed|failed|skipped|error|errors|warning|warnings|xfailed|xpassed|deselected)/g,
  )) {
    const [, rawCount, rawKey] = match;
    const key = rawKey === "errors" ? "error" : rawKey === "warnings" ? "warning" : rawKey;
    summary[key] = (summary[key] || 0) + Number(rawCount);
  }

  const durationMatch = summaryLine.match(/\bin\s+([0-9.]+)s\b/);
  if (durationMatch) {
    summary.duration_seconds = Number(durationMatch[1]);
  }

  if (summaryLine) {
    summary.summary_line = summaryLine.trim();
  }

  return Object.keys(summary).length > 0 ? summary : null;
}

function spawnCommand(command, env) {
  return new Promise((resolve) => {
    const childEnv = {
      ...process.env,
      ...env,
    };
    let stdoutTail = "";
    let stderrTail = "";
    let resolved = false;
    const resolveOnce = (result) => {
      if (resolved) {
        return;
      }
      resolved = true;
      resolve(result);
    };
    const child = spawn(command[0], command.slice(1), {
      cwd: repoRoot,
      env: childEnv,
      stdio: ["inherit", "pipe", "pipe"],
      windowsHide: true,
      shell: process.platform === "win32",
    });
    child.stdout.on("data", (chunk) => {
      stdoutTail = appendOutputTail(stdoutTail, chunk);
      process.stdout.write(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderrTail = appendOutputTail(stderrTail, chunk);
      process.stderr.write(chunk);
    });
    child.on("error", (error) => {
      resolveOnce({
        command,
        exit_code: 1,
        signal: "",
        error: error instanceof Error ? error.message : String(error),
        stdout_tail: stdoutTail,
        stderr_tail: stderrTail,
      });
    });
    child.on("exit", (code, signal) => {
      resolveOnce({
        command,
        exit_code: code ?? 1,
        signal: signal || "",
        error: "",
        stdout_tail: stdoutTail,
        stderr_tail: stderrTail,
      });
    });
  });
}

async function runGate(gate, options) {
  if (gate.skipped) {
    return {
      ...sanitizeGateForJson(gate),
      status: gate.required ? "SKIP_REQUIRED" : "SKIP",
      results: [],
      findings: gate.required ? [`required gate skipped: ${gate.id}`] : [],
    };
  }

  const results = [];
  for (let runIndex = 1; runIndex <= options.repeatCount; runIndex += 1) {
    let commandIndex = 0;
    for (const command of gate.commands) {
      commandIndex += 1;
      const result = await spawnCommand(command, gate.env);
      const commandResult = {
        run_index: runIndex,
        command_index: commandIndex,
        command: commandLabel(command),
        exit_code: result.exit_code,
        signal: result.signal || "",
        error: result.error || "",
        stdout_tail: result.stdout_tail || "",
        stderr_tail: result.stderr_tail || "",
      };
      const testSummary = parsePytestSummary(`${commandResult.stdout_tail}\n${commandResult.stderr_tail}`);
      if (testSummary) {
        commandResult.test_summary = testSummary;
      }
      results.push(commandResult);
      if (result.exit_code !== 0) {
        break;
      }
    }
  }

  const findings = results
    .filter((result) => result.exit_code !== 0)
    .map(
      (result) =>
        `${gate.id} run=${result.run_index} command=${result.command_index} failed: ${result.command} exit_code=${result.exit_code}`,
    );

  return {
    ...sanitizeGateForJson(gate),
    status: findings.length === 0 ? "PASS" : "FAIL",
    results,
    findings,
  };
}

function buildPayload(options, gates, results = []) {
  const gatePayloads = results.length > 0 ? results : gates.map(sanitizeGateForJson);
  const findings = results.flatMap((result) => result.findings || []);
  // Only FAIL on actual "FAIL" status — "SKIP" and "SKIP_REQUIRED" are not failures.
  // A required gate that is intentionally skipped (e.g., --skip-real-chain) should
  // not cause the overall run to fail; it signals "not applicable in this environment".
  const requiredFailures = results.filter((result) => result.required && result.status === "FAIL");
  const status = options.dryRun ? "DRY_RUN" : requiredFailures.length === 0 ? "PASS" : "FAIL";

  return {
    schema: SCHEMA,
    generated_at: new Date().toISOString(),
    status,
    workspace: repoRoot,
    output: options.output,
    gates: gatePayloads,
    summary: {
      gate_count: gates.length,
      required_count: gates.filter((gate) => gate.required).length,
      required_fail_count: requiredFailures.length,
      finding_count: findings.length,
      selected_gate_ids: options.onlyGateIds,
      repeat_count: options.repeatCount,
    },
    findings,
  };
}

function writePayload(outputPath, payload) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

const options = parseArgs(process.argv.slice(2));
const gates = buildGates(options);

if (options.dryRun) {
  const payload = buildPayload(options, gates);
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(0);
}

const repeatCount = options.repeatCount;
const maxFailed = options.maxFailed;
const cumulativeRounds = [];
let totalFailureCount = 0;
let earlyTermination = null;
let finalStatus = "PASS";
let firstRoundResults = null; // Round 1's full results array (preserved for multi-round main output)
let singleRoundResults = null; // Single-round results (repeatCount === 1)

// Multi-round execution with per-round subdirectories and cumulative tracking
for (let roundIndex = 1; roundIndex <= repeatCount; roundIndex += 1) {
  const roundStartMs = Date.now();
  const roundLabel = `round-${roundIndex}`;
  const roundDir = path.join(path.dirname(options.output), roundLabel);
  const roundOutput = path.join(roundDir, path.basename(options.output));

  // Run all gates for this round
  const roundResults = [];
  if (roundIndex === 1) {
    firstRoundResults = roundResults; // Preserve reference: will be filled in-place by the inner loop
  }
  for (const gate of gates) {
    // eslint-disable-next-line no-await-in-loop
    const result = await runGate(gate, options);
    roundResults.push(result);

    // Track failures for max-failed enforcement
    if (result.status === "FAIL") {
      totalFailureCount += 1;
      if (maxFailed > 0 && totalFailureCount >= maxFailed) {
        earlyTermination = {
          round: roundIndex,
          reason: `max_failed=${maxFailed} exceeded after ${totalFailureCount} gate failure(s)`,
          gates_failed: roundResults.filter((r) => r.status === "FAIL").map((r) => r.id),
        };
        break; // Stop processing more gates in this round
      }
    }
  }

  // Capture single-round results (inside loop so roundResults is in scope)
  if (roundIndex === 1 && repeatCount === 1) {
    singleRoundResults = roundResults; // Snapshot for single-round output
  }

  const roundDurationMs = Date.now() - roundStartMs;
  const roundStatus = roundResults.some((r) => r.required && r.status === "FAIL") ? "FAIL" : "PASS";

  // Write per-round audit JSON
  const roundPayload = buildPayload(options, gates, roundResults);
  roundPayload.round = roundIndex;
  roundPayload.round_duration_ms = roundDurationMs;
  if (earlyTermination) {
    roundPayload.early_termination = earlyTermination;
  }
  writePayload(roundOutput, roundPayload);

  // Build cumulative round summary
  const roundGateResults = {};
  for (const r of roundResults) {
    roundGateResults[r.id] = {
      status: r.status,
      passed_tests: r.results?.[0]?.test_summary?.passed ?? null,
      failed_tests: r.results?.[0]?.test_summary?.failed ?? null,
      exit_code: r.results?.[0]?.exit_code ?? null,
    };
  }
  cumulativeRounds.push({
    round: roundIndex,
    status: roundStatus,
    gate_results: roundGateResults,
    duration_ms: roundDurationMs,
  });

  if (roundStatus === "FAIL") {
    finalStatus = "FAIL";
  }

  // Check early termination condition
  if (earlyTermination) {
    break;
  }
}

// For multi-round: firstRoundResults already points to the round-1 array
// (filled in-place by the inner loop). For single-round: use singleRoundResults.
// Build per-gate results for the main output payload.
let gateResultsForPayload = [];
if (repeatCount === 1 && singleRoundResults !== null) {
  gateResultsForPayload = singleRoundResults;
} else if (repeatCount > 1 && firstRoundResults !== null) {
  gateResultsForPayload = firstRoundResults;
}

// Build cumulative report for multi-round runs
if (repeatCount > 1) {
  const passedRounds = cumulativeRounds.filter((r) => r.status === "PASS").length;
  const failedRounds = cumulativeRounds.filter((r) => r.status === "FAIL").length;
  // Count consecutive failures at end
  let consecutiveFailures = 0;
  for (let i = cumulativeRounds.length - 1; i >= 0; i -= 1) {
    if (cumulativeRounds[i].status === "FAIL") {
      consecutiveFailures += 1;
    } else {
      break;
    }
  }

  const cumulativeReport = {
    schema: "polaris.e2e.production_stability_validation_cumulative.v1",
    generated_at: new Date().toISOString(),
    rounds: cumulativeRounds,
    cumulative_summary: {
      total_rounds: repeatCount,
      completed_rounds: cumulativeRounds.length,
      passed_rounds: passedRounds,
      failed_rounds: failedRounds,
      consecutive_failures: consecutiveFailures,
      pass_rate: cumulativeRounds.length > 0 ? passedRounds / cumulativeRounds.length : 0,
      early_termination: earlyTermination,
    },
  };

  const cumulativePath = path.join(path.dirname(options.output), "production-stability-cumulative.json");
  writePayload(cumulativePath, cumulativeReport);
}

// Build per-gate results for the main output payload.
// For single-round (repeatCount === 1): use original roundResults directly
//   so that gate["results"][0]["test_summary"]["passed"] etc. continue to work.
// For multi-round (repeatCount > 1): use first-round results so the main output
//   still has real results (with exit_code, test_summary, stdout_tail) for tests.
//   The cumulative JSON already has the full per-round breakdown.
if (cumulativeRounds.length === 1 && singleRoundResults !== null) {
  // Single round: use the saved single-round results (full stdout_tail, test_summary, etc.)
  gateResultsForPayload = singleRoundResults;
} else if (cumulativeRounds.length > 1 && firstRoundResults !== null) {
  // Multi-round: include first-round results so tests that read the main output
  // can still find exit_code, test_summary, etc.
  gateResultsForPayload = firstRoundResults;
}

const finalPayload = buildPayload(options, gates, gateResultsForPayload);
if (cumulativeRounds.length > 1) {
  // Only include cumulative data for multi-round runs.
  finalPayload.cumulative_rounds = cumulativeRounds;
  finalPayload.cumulative_summary = {
    total_rounds: repeatCount,
    completed_rounds: cumulativeRounds.length,
    passed_rounds: cumulativeRounds.filter((r) => r.status === "PASS").length,
    failed_rounds: cumulativeRounds.filter((r) => r.status === "FAIL").length,
    early_termination: earlyTermination,
  };
  finalPayload.status = finalStatus;
}
writePayload(options.output, finalPayload);
process.stdout.write(`${JSON.stringify(finalPayload, null, 2)}\n`);
process.exit(finalStatus === "PASS" ? 0 : 1);
