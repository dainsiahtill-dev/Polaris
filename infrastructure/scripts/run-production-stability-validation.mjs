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
  return [
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
  ];
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

function spawnCommand(command, env) {
  return new Promise((resolve) => {
    const childEnv = {
      ...process.env,
      ...env,
    };
    const child = spawn(command[0], command.slice(1), {
      cwd: repoRoot,
      env: childEnv,
      stdio: "inherit",
      windowsHide: true,
      shell: process.platform === "win32",
    });
    child.on("error", (error) => {
      resolve({
        command,
        exit_code: 1,
        error: error instanceof Error ? error.message : String(error),
      });
    });
    child.on("exit", (code, signal) => {
      resolve({
        command,
        exit_code: code ?? 1,
        signal: signal || "",
        error: "",
      });
    });
  });
}

async function runGate(gate) {
  if (gate.skipped) {
    return {
      ...sanitizeGateForJson(gate),
      status: gate.required ? "SKIP_REQUIRED" : "SKIP",
      results: [],
      findings: gate.required ? [`required gate skipped: ${gate.id}`] : [],
    };
  }

  const results = [];
  for (const command of gate.commands) {
    const result = await spawnCommand(command, gate.env);
    results.push({
      command: commandLabel(command),
      exit_code: result.exit_code,
      signal: result.signal || "",
      error: result.error || "",
    });
    if (result.exit_code !== 0) {
      break;
    }
  }

  const findings = results
    .filter((result) => result.exit_code !== 0)
    .map((result) => `${gate.id} command failed: ${result.command} exit_code=${result.exit_code}`);

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
  const requiredFailures = results.filter((result) => result.required && result.status !== "PASS");
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

const results = [];
for (const gate of gates) {
  // eslint-disable-next-line no-await-in-loop
  results.push(await runGate(gate));
}

const payload = buildPayload(options, gates, results);
writePayload(options.output, payload);
process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
process.exit(payload.status === "PASS" ? 0 : 1);
