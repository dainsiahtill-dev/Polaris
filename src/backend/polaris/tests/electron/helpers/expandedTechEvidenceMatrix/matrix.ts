import { execFile } from "node:child_process";
import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { type Page, type TestInfo } from "@playwright/test";
import { CANDIDATE_RUNTIME_PROBE_IDS, CANDIDATE_SOURCE_PROBE_IDS, CORE_TECH_IDS, EXPANDED_TECH_CANDIDATES } from "./data";
import { asRecord, asString, buildExpandedCandidateRuntimeCoverage, candidateSourceProbe, collectE2eAttachmentRuntimeProbe, collectWebSocketStaleTokenRuntimeProbe, countStatus, getBackendInfoFromPage, makeProbe, pathExists, refreshCandidateCoverageAndSummary, requestJson, upsertProbe, writeUtf8File } from "./matrix_helpers";
import { collectArchiveStreamRuntimeProbe, collectAuditEvidenceBundleRuntimeProbe, collectEventFactStreamRuntimeProbe, collectFactoryPipelineRuntimeProbe, collectFrontendLlmSettingsRuntimeProbe, collectGraphGovernanceRuntimeProbes, collectKerneloneTraceabilityRuntimeProbe, collectLlmConfigControlPlaneRuntimeProbe, collectLlmEvaluationRuntimeProbe, collectNativeToolRuntimeProbe, collectPermissionPdpRuntimeProbe, collectRuntimeArtifactStoreRuntimeProbe, collectTaskMarketRegressionRuntimeProbe } from "./probes_a";
import { collectAggregateRuntimePlanProbe, collectCognitiveRuntimeRoundtripProbe, collectE2eRuntimeIsolationProbe, collectElectronRuntimeProbes, collectHistoryArchiveReadonlyRuntimeProbe, collectLlmInterviewSaveRuntimeProbe, collectReadonlyControlPlaneRuntimeProbes, collectResidentGoalPmBridgeRuntimeProbe, collectResidentSelfLearningRuntimeProbe, collectRoleSessionAuditExportRuntimeProbe } from "./probes_b";
import { collectCoreRuntimeEvidencePlacementProbe, collectPromptLeakageProbe, collectRuntimeArtifactProbes } from "./probes_c";
import { type BackendConnection, type CollectOptions, type EvidenceProbe, type ExpandedTechEvidenceReport, type JsonRecord } from "./types";

export async function collectExpandedTechEvidenceMatrix(
  page: Page,
  options: CollectOptions = {},
): Promise<ExpandedTechEvidenceReport> {
  const requireRealChain = Boolean(options.requireRealChain);
  const probes: EvidenceProbe[] = [];

  let backend: BackendConnection | null = null;
  try {
    backend = await getBackendInfoFromPage(page);
    probes.push(
      makeProbe({
        id: "backend_connection",
        title: "Backend connection from desktop/browser page",
        category: "entrypoint",
        status: "PASS",
        required: true,
        evidence: [
          {
            type: "api",
            ref: backend.baseUrl,
            value: { token_present: Boolean(backend.token), source: backend.source },
          },
        ],
        findings: [],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "backend_connection",
        title: "Backend connection from desktop/browser page",
        category: "entrypoint",
        status: "FAIL",
        required: true,
        evidence: [],
        findings: [String(error)],
      }),
    );
  }

  let workspace = options.workspaceOverride || "";
  let runtimeRoot = options.runtimeRootOverride || "";
  try {
    const settings = asRecord(await requestJson<JsonRecord>(page, "/v2/settings"));
    const layout = asRecord(await requestJson<JsonRecord>(page, "/v2/runtime/storage/layout"));
    workspace = workspace || asString(settings.workspace) || asString(layout.workspace);
    runtimeRoot = runtimeRoot || asString(layout.runtime_root);
    probes.push(
      makeProbe({
        id: "settings_runtime_layout_api",
        title: "Settings and runtime storage layout API",
        category: "entrypoint",
        status: workspace && runtimeRoot ? "PASS" : "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/v2/settings", value: { workspace: settings.workspace } },
          {
            type: "api",
            ref: "/v2/runtime/storage/layout",
            value: { runtime_root: layout.runtime_root, workspace: layout.workspace },
          },
        ],
        findings: workspace && runtimeRoot ? [] : ["workspace or runtime_root missing from API responses"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "settings_runtime_layout_api",
        title: "Settings and runtime storage layout API",
        category: "entrypoint",
        status: "FAIL",
        required: true,
        evidence: [
          { type: "api", ref: "/v2/settings" },
          { type: "api", ref: "/v2/runtime/storage/layout" },
        ],
        findings: [String(error)],
      }),
    );
  }

  probes.push(...(await collectReadonlyControlPlaneRuntimeProbes(page, workspace || ".")));
  probes.push(collectE2eRuntimeIsolationProbe(workspace, runtimeRoot));
  probes.push(await collectHistoryArchiveReadonlyRuntimeProbe(page));
  probes.push(await collectResidentSelfLearningRuntimeProbe(page, workspace));
  probes.push(await collectResidentGoalPmBridgeRuntimeProbe(page, workspace));
  probes.push(await collectLlmInterviewSaveRuntimeProbe(page, workspace));
  probes.push(await collectRoleSessionAuditExportRuntimeProbe(page, workspace, runtimeRoot));
  probes.push(await collectWebSocketStaleTokenRuntimeProbe(page, workspace));
  probes.push(...(await collectElectronRuntimeProbes(page, workspace || ".")));
  probes.push(...(await collectGraphGovernanceRuntimeProbes()));
  probes.push(await collectTaskMarketRegressionRuntimeProbe());
  probes.push(await collectFrontendLlmSettingsRuntimeProbe());
  probes.push(await collectLlmEvaluationRuntimeProbe());
  probes.push(await collectNativeToolRuntimeProbe());
  probes.push(await collectFactoryPipelineRuntimeProbe());
  probes.push(await collectArchiveStreamRuntimeProbe());
  probes.push(await collectRuntimeArtifactStoreRuntimeProbe());
  probes.push(await collectAuditEvidenceBundleRuntimeProbe());
  probes.push(await collectLlmConfigControlPlaneRuntimeProbe(page));
  probes.push(await collectPermissionPdpRuntimeProbe(page));
  probes.push(await collectEventFactStreamRuntimeProbe(page));
  probes.push(await collectKerneloneTraceabilityRuntimeProbe(page));

  probes.push(
    await candidateSourceProbe(
      "dual_mode_source_assets",
      "Dual-mode desktop/browser source assets",
      "entrypoint",
      CANDIDATE_SOURCE_PROBE_IDS.dual_mode_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "e2e_evidence_source_assets",
      "E2E isolation and evidence attachment source assets",
      "e2e",
      CANDIDATE_SOURCE_PROBE_IDS.e2e_evidence_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "graph_governance_source_assets",
      "Graph/governance source and gate assets",
      "governance",
      CANDIDATE_SOURCE_PROBE_IDS.graph_governance_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "task_market_source_assets",
      "TaskMarket write-side source and gate assets",
      "task_market",
      CANDIDATE_SOURCE_PROBE_IDS.task_market_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "llm_control_source_assets",
      "LLM control/readiness/tooling source assets",
      "llm_control",
      CANDIDATE_SOURCE_PROBE_IDS.llm_control_source_assets,
    ),
  );
  probes.push(
    await candidateSourceProbe(
      "factory_archive_resident_source_assets",
      "Factory/archive/audit/resident source assets",
      "factory_archive_resident",
      CANDIDATE_SOURCE_PROBE_IDS.factory_archive_resident_source_assets,
    ),
  );

  const aggregate = await collectAggregateRuntimePlanProbe(page, workspace || ".");
  probes.push(aggregate.probe);
  probes.push(await collectCognitiveRuntimeRoundtripProbe(page, workspace || "."));
  const placementResult = await collectCoreRuntimeEvidencePlacementProbe(
    page,
    workspace || ".",
    runtimeRoot,
    requireRealChain,
    aggregate.core,
  );
  probes.push(placementResult.probe);

  if (runtimeRoot) {
    probes.push(...(await collectRuntimeArtifactProbes(runtimeRoot, requireRealChain)));
    probes.push(await collectPromptLeakageProbe(runtimeRoot, requireRealChain));
  } else {
    probes.push(
      makeProbe({
        id: "runtime_artifacts_unavailable",
        title: "Runtime artifact root unavailable",
        category: "runtime_artifact",
        status: requireRealChain ? "FAIL" : "SKIP",
        required: requireRealChain,
        evidence: [],
        findings: ["runtime_root is empty"],
      }),
    );
  }

  const candidateRuntimeCoverage = buildExpandedCandidateRuntimeCoverage({
    candidates: EXPANDED_TECH_CANDIDATES,
    probes,
    runtimeProbeCandidateIds: CANDIDATE_RUNTIME_PROBE_IDS,
    sourceProbeCandidateIds: CANDIDATE_SOURCE_PROBE_IDS,
  });

  const report: ExpandedTechEvidenceReport = {
    schema: "polaris.e2e.expanded_tech_evidence_matrix.v1",
    generated_at: new Date().toISOString(),
    workspace,
    runtime_root: runtimeRoot,
    require_real_chain: requireRealChain,
    core_runtime_integrations: aggregate.core,
    core_runtime_evidence_placement: placementResult.placement,
    candidate_runtime_coverage: candidateRuntimeCoverage,
    expanded_candidates: EXPANDED_TECH_CANDIDATES,
    probes,
    summary: {
      pass: countStatus(probes, "PASS"),
      fail: countStatus(probes, "FAIL"),
      warn: countStatus(probes, "WARN"),
      skip: countStatus(probes, "SKIP"),
      required_fail: probes.filter((probe) => probe.required && probe.status === "FAIL").length,
      candidate_count: EXPANDED_TECH_CANDIDATES.length,
    },
  };
  return report;
}

export function assertExpandedTechEvidenceMatrix(
  report: ExpandedTechEvidenceReport,
  options: { requireAllCandidateRuntime?: boolean } = {},
): void {
  const requiredFailures = report.probes.filter((probe) => probe.required && probe.status === "FAIL");
  if (requiredFailures.length > 0) {
    throw new Error(
      `expanded tech evidence matrix has required failures: ${requiredFailures
        .map((probe) => `${probe.id}: ${probe.findings.join("; ")}`)
        .join(" | ")}`,
    );
  }
  if (report.require_real_chain) {
    const placement = report.core_runtime_evidence_placement;
    if (!placement) {
      throw new Error("expanded tech evidence matrix is missing core runtime evidence placement");
    }
    if (placement.rows.length !== CORE_TECH_IDS.length || placement.missing.length > 0) {
      throw new Error(
        `core runtime evidence placement incomplete: rows=${placement.rows.length}/${CORE_TECH_IDS.length} `
        + `missing=${placement.missing.join(", ") || "(none)"}`,
      );
    }
  }
  if (options.requireAllCandidateRuntime) {
    const coverage = report.candidate_runtime_coverage;
    if (!coverage) {
      throw new Error("expanded tech evidence matrix is missing candidate runtime coverage");
    }
    if (coverage.expected_count !== report.expanded_candidates.length || coverage.not_runtime_proved_ids.length > 0) {
      throw new Error(
        `candidate runtime coverage incomplete: runtime_proved=${coverage.runtime_proved_count}/`
        + `${coverage.expected_count} missing=${coverage.not_runtime_proved_ids.join(", ") || "(none)"}`,
      );
    }
  }
}

export async function writeExpandedTechEvidenceMatrix(
  testInfo: TestInfo,
  report: ExpandedTechEvidenceReport,
  filename = "expanded-tech-evidence-matrix.json",
): Promise<string> {
  const attachmentProbe = await collectE2eAttachmentRuntimeProbe(testInfo, filename);
  report.probes = upsertProbe(report.probes, attachmentProbe);
  refreshCandidateCoverageAndSummary(report);

  const outputPath = testInfo.outputPath(filename);
  await writeUtf8File(outputPath, JSON.stringify(report, null, 2));
  const manifestPath = testInfo.outputPath("e2e-auto-attachment-manifest.json");
  if (await pathExists(manifestPath)) {
    await testInfo.attach("e2e-auto-attachment-manifest", {
      path: manifestPath,
      contentType: "application/json",
    });
  }
  await testInfo.attach("expanded-tech-evidence-matrix", {
    path: outputPath,
    contentType: "application/json",
  });
  return outputPath;
}
