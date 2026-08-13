import { execFile } from "node:child_process";
import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { type Page, type TestInfo } from "@playwright/test";
import { CORE_TECH_IDS } from "./data";
import { asNumber, asRecord, asString, buildCoreRuntimeEvidencePlacement, listFilesByBasename, makeProbe, newestFile, readJsonIfExists, readJsonlFiles, readTextIfExists, requestJson, taskProjectionSummary } from "./matrix_helpers";
import { collectRuntimeArtifactRefs } from "./probes_b";
import { type CoreRuntimeEvidencePlacement, type EvidenceProbe, type ExpandedTechEvidenceReport, type JsonRecord } from "./types";

export async function collectCoreRuntimeEvidencePlacementProbe(
  page: Page,
  workspace: string,
  runtimeRoot: string,
  requireRealChain: boolean,
  core: ExpandedTechEvidenceReport["core_runtime_integrations"],
): Promise<{ probe: EvidenceProbe; placement: CoreRuntimeEvidencePlacement | null }> {
  if (!requireRealChain) {
    return {
      placement: null,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status: "SKIP",
        required: false,
        evidence: [],
        findings: ["real PM/Director/QA chain is not required for this matrix run"],
      }),
    };
  }

  try {
    const auditRefs = await collectRuntimeArtifactRefs(runtimeRoot);
    const taskProjectionEndpoint = `/v2/director/tasks?source=auto&workspace=${encodeURIComponent(workspace)}`;
    const taskRows = await requestJson<unknown[]>(page, taskProjectionEndpoint).catch(() => []);
    const unique = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const auditMarkerValue = {
      schema: "polaris.e2e.core_runtime_evidence_placement.audit_marker.v1",
      source: "electron_e2e.full_chain_runtime",
      generated_at: new Date().toISOString(),
      core_tech_ids: CORE_TECH_IDS,
      expected_sinks: ["audit", "receipt", "handoff", "task_projection"],
      task_projection_endpoint: taskProjectionEndpoint,
    };
    const auditMarkerPath = path.join(runtimeRoot, "audit", `core-runtime-evidence-placement-${unique}.json`);
    await fs.mkdir(path.dirname(auditMarkerPath), { recursive: true });
    await fs.writeFile(auditMarkerPath, `${JSON.stringify(auditMarkerValue, null, 2)}\n`, "utf-8");
    auditRefs.push({ type: "runtime_artifact", ref: auditMarkerPath, value: auditMarkerValue });
    const taskProjection = {
      tasks: Array.isArray(taskRows) ? taskRows : [],
      core_runtime_evidence_placement: {
        schema: "polaris.e2e.core_runtime_evidence_placement.task_projection_marker.v1",
        core_tech_ids: CORE_TECH_IDS,
        source: "director_tasks_projection_wrapper",
      },
    };
    const taskProjectionSummaryValue = taskProjectionSummary(taskProjection);
    const sessionId = `e2e-core-runtime-evidence-${unique}`;
    const runId = `e2e-core-runtime-evidence-run-${unique}`;
    const turnEnvelope = {
      turn_id: `turn-${unique}`,
      session_id: sessionId,
      run_id: runId,
      role: "qa",
      task_id: "e2e::core_runtime_evidence_placement",
      projection_version: "core-runtime-evidence-placement.v1",
      state_version: 1,
    };
    const placementPayload = {
      schema: "polaris.e2e.core_runtime_evidence_placement.v1",
      source: "electron_e2e.full_chain_runtime",
      core_tech_ids: CORE_TECH_IDS,
      aggregate_core: core,
      audit_refs: auditRefs,
      task_projection: {
        endpoint: taskProjectionEndpoint,
        ...taskProjectionSummaryValue,
        sample_task_ids: taskProjection.tasks
          .map((item) => asString(asRecord(item).id || asRecord(item).task_id))
          .filter(Boolean)
          .slice(0, 20),
      },
      expected_sinks: ["audit", "receipt", "handoff", "task_projection"],
    };

    const receiptResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/runtime-receipts", {
        method: "POST",
        body: {
          workspace,
          receipt_type: "e2e_core_runtime_evidence_placement",
          session_id: sessionId,
          run_id: runId,
          trace_refs: [
            "e2e:full_chain",
            "e2e:core_runtime_evidence_placement",
            ...auditRefs.map((ref) => ref.ref),
          ],
          payload: placementPayload,
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const receipt = asRecord(receiptResponse.receipt);
    const receiptId = asString(receipt.receipt_id);
    if (!receiptId) {
      throw new Error("core runtime placement receipt did not return receipt_id");
    }

    const fetchedReceiptResponse = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/runtime-receipts/${encodeURIComponent(receiptId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedReceipt = asRecord(fetchedReceiptResponse.receipt);

    const handoffResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/export", {
        method: "POST",
        body: {
          workspace,
          session_id: sessionId,
          run_id: runId,
          reason: "core_runtime_evidence_placement_16x4",
          receipt_limit: 10,
          turn_envelope: {
            ...turnEnvelope,
            receipt_ids: [receiptId],
          },
        },
      }),
    );
    const handoff = asRecord(handoffResponse.handoff);
    const handoffId = asString(handoff.handoff_id);
    if (!handoffId) {
      throw new Error("core runtime placement handoff did not return handoff_id");
    }

    const fetchedHandoffResponse = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/handoffs/${encodeURIComponent(handoffId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedHandoff = asRecord(fetchedHandoffResponse.handoff);
    const placement = buildCoreRuntimeEvidencePlacement({
      auditRefs,
      coreTechIds: CORE_TECH_IDS,
      receipt: fetchedReceipt,
      handoff: fetchedHandoff,
      taskProjection,
    });
    const status =
      core.missing_ids.length === 0 &&
      placement.rows.length === CORE_TECH_IDS.length &&
      placement.missing.length === 0
        ? "PASS"
        : "FAIL";
    return {
      placement,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status,
        required: true,
        evidence: [
          { type: "api", ref: "/v1/chat/completions", value: core },
          { type: "api", ref: "/cognitive-runtime/runtime-receipts", value: { receipt_id: placement.receipt_id } },
          { type: "api", ref: "/cognitive-runtime/handoffs/export", value: { handoff_id: placement.handoff_id } },
          { type: "api", ref: taskProjectionEndpoint, value: placement.task_projection },
          ...auditRefs,
        ],
        findings:
          status === "PASS"
            ? []
            : [
                `missing core ids: ${core.missing_ids.join(", ") || "(none)"}`,
                `missing placements: ${placement.missing.join(", ") || "(none)"}`,
              ],
      }),
    };
  } catch (error) {
    return {
      placement: null,
      probe: makeProbe({
        id: "core_runtime_evidence_placement",
        title: "16 core runtime technologies placed into audit/receipt/handoff/task projection",
        category: "core_runtime",
        status: "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/cognitive-runtime/runtime-receipts" },
          { type: "api", ref: "/cognitive-runtime/handoffs/export" },
          { type: "api", ref: "/v2/director/tasks?source=auto" },
          { type: "runtime_artifact", ref: runtimeRoot },
        ],
        findings: [String(error)],
      }),
    };
  }
}

export async function collectRuntimeArtifactProbes(runtimeRoot: string, requireRealChain: boolean): Promise<EvidenceProbe[]> {
  const probes: EvidenceProbe[] = [];
  const basenames = new Set([
    "plan.md",
    "pm_tasks.contract.json",
    "director.result.json",
    "integration_qa.result.json",
    "runtime.events.jsonl",
  ]);
  const files = await listFilesByBasename(runtimeRoot, basenames);
  const byName = new Map<string, string[]>();
  for (const filePath of files) {
    const name = path.basename(filePath);
    byName.set(name, [...(byName.get(name) || []), filePath]);
  }

  const planPath = await newestFile(byName.get("plan.md") || []);
  const planText = planPath ? await readTextIfExists(planPath) : null;
  probes.push(
    makeProbe({
      id: "runtime_artifact_plan_contract",
      title: "Runtime plan artifact",
      category: "runtime_artifact",
      status: planText && planText.trim().length > 0 ? "PASS" : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: planPath ? [{ type: "runtime_artifact", ref: planPath, value: { chars: planText?.length || 0 } }] : [],
      findings: planText ? [] : ["plan.md not found under runtime root"],
    }),
  );

  const pmContractPath = await newestFile(byName.get("pm_tasks.contract.json") || []);
  const pmContract = pmContractPath ? asRecord(await readJsonIfExists<JsonRecord>(pmContractPath)) : {};
  const pmTasks = Array.isArray(pmContract.tasks) ? pmContract.tasks : [];
  const quality = asRecord(pmContract.quality_gate || pmContract.quality || pmContract.pm_quality);
  const qualityScore = asNumber(quality.score);
  const criticalIssues = Array.isArray(quality.critical_issues)
    ? quality.critical_issues.length
    : asNumber(quality.critical_issue_count);
  const pmPass =
    pmTasks.length > 0 &&
    (qualityScore === null || qualityScore >= 80) &&
    (criticalIssues === null || criticalIssues === 0);
  probes.push(
    makeProbe({
      id: "runtime_artifact_pm_quality_contract",
      title: "PM task quality contract artifact",
      category: "runtime_artifact",
      status: pmContractPath ? (pmPass ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: pmContractPath
        ? [
            {
              type: "runtime_artifact",
              ref: pmContractPath,
              value: { task_count: pmTasks.length, quality_score: qualityScore, critical_issues: criticalIssues },
            },
          ]
        : [],
      findings: pmContractPath ? (pmPass ? [] : ["PM contract exists but quality/task checks are incomplete"]) : ["PM contract not found"],
    }),
  );

  const directorPath = await newestFile(byName.get("director.result.json") || []);
  const directorResult = directorPath ? asRecord(await readJsonIfExists<JsonRecord>(directorPath)) : {};
  const taskResults = Array.isArray(directorResult.task_results) ? directorResult.task_results : [];
  const failures = asNumber(directorResult.failures) ?? 0;
  const blocked = asNumber(directorResult.blocked) ?? 0;
  const directorPass = taskResults.length > 0 || asString(directorResult.status).length > 0;
  probes.push(
    makeProbe({
      id: "runtime_artifact_director_result",
      title: "Director execution result artifact",
      category: "runtime_artifact",
      status: directorPath ? (directorPass && failures === 0 && blocked === 0 ? "PASS" : "WARN") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: directorPath
        ? [
            {
              type: "runtime_artifact",
              ref: directorPath,
              value: { status: directorResult.status, task_results: taskResults.length, failures, blocked },
            },
          ]
        : [],
      findings: directorPath ? [] : ["director.result.json not found"],
    }),
  );

  const qaPath = await newestFile(byName.get("integration_qa.result.json") || []);
  const qaResult = qaPath ? asRecord(await readJsonIfExists<JsonRecord>(qaPath)) : {};
  const qaReceipt = asRecord(qaResult.cognitive_runtime_receipt);
  const qaPass =
    qaResult.passed === true ||
    asString(qaResult.reason) === "integration_qa_passed" ||
    asString(qaResult.evidence_grade) === "passed";
  probes.push(
    makeProbe({
      id: "runtime_artifact_qa_result_receipt",
      title: "Integration QA result with Cognitive Runtime receipt",
      category: "runtime_artifact",
      status: qaPath ? (qaPass && Boolean(asString(qaReceipt.receipt_id)) ? "PASS" : "WARN") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: qaPath
        ? [
            {
              type: "runtime_artifact",
              ref: qaPath,
              value: {
                passed: qaResult.passed,
                reason: qaResult.reason,
                evidence_grade: qaResult.evidence_grade,
                receipt_id: qaReceipt.receipt_id,
              },
            },
          ]
        : [],
      findings: qaPath ? [] : ["integration_qa.result.json not found"],
    }),
  );

  const eventFiles = files.filter((filePath) => filePath.endsWith(".jsonl"));
  const eventRecords = await readJsonlFiles(eventFiles);
  const eventTypes = eventRecords.map((record) => asString(record.type || record.event_type || record.name)).filter(Boolean);
  const toolEventCount = eventTypes.filter((eventType) => eventType.toLowerCase().includes("tool")).length;
  const dangerousEventCount = eventRecords.filter((record) => JSON.stringify(record).toLowerCase().includes("dangerous")).length;
  probes.push(
    makeProbe({
      id: "runtime_events_tool_policy_audit",
      title: "Runtime event JSONL tool/policy audit evidence",
      category: "events",
      status: eventFiles.length > 0 ? (dangerousEventCount === 0 ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
      required: requireRealChain,
      evidence: [
        {
          type: "event_jsonl",
          ref: runtimeRoot,
          value: { event_files: eventFiles, event_count: eventRecords.length, tool_event_count: toolEventCount, dangerous_event_count: dangerousEventCount },
        },
      ],
      findings: dangerousEventCount > 0 ? ["dangerous command/policy keyword found in runtime event payloads"] : [],
    }),
  );

  return probes;
}

function scanPromptLeakage(planText: string | null, pmContract: JsonRecord): string[] {
  const payload = `${planText || ""}\n${JSON.stringify(pmContract)}`;
  const patterns = [
    /you are/i,
    /system prompt/i,
    /no yapping/i,
    /\u63d0\u793a\u8bcd/i,
    /<thinking>/i,
    /<tool_call>/i,
  ];
  return patterns.filter((pattern) => pattern.test(payload)).map((pattern) => String(pattern));
}

export async function collectPromptLeakageProbe(runtimeRoot: string, requireRealChain: boolean): Promise<EvidenceProbe> {
  const files = await listFilesByBasename(runtimeRoot, new Set(["plan.md", "pm_tasks.contract.json"]));
  const planPath = await newestFile(files.filter((filePath) => path.basename(filePath) === "plan.md"));
  const pmContractPath = await newestFile(files.filter((filePath) => path.basename(filePath) === "pm_tasks.contract.json"));
  const planText = planPath ? await readTextIfExists(planPath) : null;
  const pmContract = pmContractPath ? asRecord(await readJsonIfExists<JsonRecord>(pmContractPath)) : {};
  const findings = scanPromptLeakage(planText, pmContract);
  const hasArtifacts = Boolean(planPath || pmContractPath);
  return makeProbe({
    id: "prompt_leakage_runtime_artifact_scan",
    title: "Prompt leakage scan for runtime plan/task artifacts",
    category: "governance",
    status: hasArtifacts ? (findings.length === 0 ? "PASS" : "FAIL") : requireRealChain ? "FAIL" : "SKIP",
    required: requireRealChain,
    evidence: [
      { type: "runtime_artifact", ref: planPath || "(missing plan.md)" },
      { type: "runtime_artifact", ref: pmContractPath || "(missing pm_tasks.contract.json)" },
    ],
    findings,
  });
}
