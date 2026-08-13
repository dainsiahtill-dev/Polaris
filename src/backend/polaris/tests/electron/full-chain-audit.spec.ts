import { existsSync, promises as fs } from "node:fs";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";
import { type Locator, type Page } from "@playwright/test";
import { expect, test } from "./fixtures";
import {
  assertExpandedTechEvidenceMatrix,
  collectExpandedTechEvidenceMatrix,
  writeExpandedTechEvidenceMatrix,
} from "./helpers/expandedTechEvidenceMatrix";


import { ChiefEngineerDiagnosticsPayload, ComplexityContributionBreakdown, ComplexityMetrics, DirectorIntegrationQaPayload, DirectorResultArtifact, DirectorResultSource, GAME_PM_MIN_TASKS, IntegrationQaArtifact, PmContractPayload, PmPlanningContribution, ProjectFileSnapshot, RuntimeContributionMetrics, RuntimeEvent, RuntimeLayoutPayload, SettingsPayload, SnapshotPayload, ToolAuditPayload, analyzeToolAudit, auditPmContract, buildComplexityContributionBreakdown, buildFullChainSettingsPayload, buildPmPlanningContribution, buildResumePlanningSeed, captureAuditScreenshot, chiefEngineerHandoffReady, clickWorkspaceBack, compareProjectSnapshots, createComplexProject, detectPmFallbackFailure, detectPromptLeakage, directorFailureReason, dismissEngineFailureDialog, enterDirectorWorkspace, enterPmWorkspace, findForbiddenRuntimeArtifacts, findLatestEventsPath, findScenarioSeedResidue, findToolEventPaths, inspectDirectorCodeChanges, listFilesRecursive, measureComplexity, measureScenarioDefinitionComplexity, normalizeCoveragePath, optionalEnvValue, readJsonFile, readJsonLines, refreshRequiredLlmReadinessThroughSettings, reloadRendererAfterWorkspaceSwitch, requestJson, resolveFullChainStartPhase, resolveGeneratedWorkspaceRoot, resolveProjectScenario, runCourtFlow, runDirectorUntilResultArtifact, runPmRound, scenarioCoveredDomains, scenarioRequiredDomains, scenarioRequiresGameLikeBatch, setReviewViewport, shouldRunFullChainPhase, snapshotProjectFiles, summarizeDirectorArtifactMaterialization, toPosixPath, tryRuntimeArtifact, verifyChiefEngineerPhase, waitForRuntimeArtifact, writeRuntimePlanningSeed, writeUtf8File, writeWorkspacePlanningDocs } from "./_full_chain_audit";

test("unattended full-chain audit with strong JSON evidence package", async ({ window, testEnv }, testInfo) => {
  test.skip(!testEnv.useRealSettings, "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to use real configured LLM settings.");

  const logsRoot = testInfo.outputPath("audit");
  const startEpochSeconds = Date.now() / 1000;
  const auditPath = path.join(logsRoot, `full_chain_audit_${new Date().toISOString().replace(/[:.]/g, "-")}.json`);

  const audit: {
    status: "PASS" | "FAIL";
    workspace: string;
    rounds: number;
    pm_quality_history: Array<{ round: number; score: number; issues: string[] }>;
    leakage_findings: Array<{ type: string; evidence: string; fixed: boolean }>;
    director_tool_audit: ToolAuditPayload;
    seed_metrics: ComplexityMetrics | null;
    runtime_contribution: RuntimeContributionMetrics | null;
    complexity_contribution_breakdown: ComplexityContributionBreakdown | null;
    expanded_tech_evidence_matrix: {
      report_path: string;
      candidate_count: number;
      summary: Record<string, unknown>;
      core_runtime_integrations: Record<string, unknown>;
      core_runtime_evidence_placement: {
        row_count: number;
        missing: string[];
        receipt_id: string;
        handoff_id: string;
        task_projection: Record<string, unknown>;
      } | null;
    } | null;
    qa_gate: {
      passed: boolean | null;
      reason: string;
      evidence_grade: string;
      summary: string;
      result_path: string;
      runtime_result_path: string;
    } | null;
    issues_fixed: Array<{ issue: string; root_cause: string; fix: string; verified: boolean }>;
    acceptance_results: {
      court_phase: "PASS" | "FAIL";
      pm_phase: "PASS" | "FAIL";
      chief_engineer_phase: "PASS" | "FAIL";
      director_phase: "PASS" | "FAIL";
      qa_phase: "PASS" | "FAIL";
    };
    evidence_paths: { screenshots: string[]; logs: string[]; snapshots: string[] };
    next_risks: string[];
  } = {
    status: "FAIL",
    workspace: "",
    rounds: 0,
    pm_quality_history: [],
    leakage_findings: [],
    director_tool_audit: {
      total_calls: 0,
      policy_evidence_count: 0,
      unauthorized_blocked: 0,
      dangerous_commands: 0,
      findings: [],
    },
    seed_metrics: null,
    runtime_contribution: null,
    complexity_contribution_breakdown: null,
    expanded_tech_evidence_matrix: null,
    qa_gate: null,
    issues_fixed: [],
    acceptance_results: {
      court_phase: "FAIL",
      pm_phase: "FAIL",
      chief_engineer_phase: "FAIL",
      director_phase: "FAIL",
      qa_phase: "FAIL",
    },
    evidence_paths: { screenshots: [], logs: [], snapshots: [] },
    next_risks: [],
  };

  let runtimeRoot = "";
  let latestQaReason = "";
  let latestQaEvidenceGrade = "";
  let latestEventsPath = "";
  let baselineSnapshot: ProjectFileSnapshot = {};
  let latestPmPlanningContribution: PmPlanningContribution | null = null;

  try {
    await setReviewViewport(window);
    await dismissEngineFailureDialog(window);
    await expect(window.getByTestId("project-progress-panel")).toBeVisible({ timeout: 60_000 });

    const startPhase = resolveFullChainStartPhase();
    const scenario = resolveProjectScenario();
    const resumeWorkspace = optionalEnvValue("KERNELONE_E2E_RESUME_WORKSPACE");
    if (startPhase !== "court" && !resumeWorkspace) {
      throw new Error(
        `KERNELONE_E2E_RESUME_WORKSPACE is required when KERNELONE_E2E_START_PHASE=${startPhase}`,
      );
    }
    const project = resumeWorkspace
      ? {
        workspace: path.resolve(resumeWorkspace),
        metrics: await measureComplexity(path.resolve(resumeWorkspace)),
        scenario,
      }
      : await createComplexProject(resolveGeneratedWorkspaceRoot(), scenario);
    const scenarioSeedMetrics = measureScenarioDefinitionComplexity(scenario);
    audit.workspace = project.workspace;
    audit.seed_metrics = project.metrics;
    const resumePlanningSeed = startPhase !== "court"
      ? buildResumePlanningSeed(project.workspace, scenario)
      : null;
    const workspacePlanningSeedPaths = resumePlanningSeed
      ? await writeWorkspacePlanningDocs(project.workspace, resumePlanningSeed)
      : [];
    baselineSnapshot = await snapshotProjectFiles(project.workspace);
    const scenarioPath = testInfo.outputPath("project.scenario.json");
    await writeUtf8File(scenarioPath, JSON.stringify({
      key: scenario.key,
      workspacePrefix: scenario.workspacePrefix,
      packageName: scenario.packageName,
      goal: scenario.goal,
      replies: scenario.replies,
      buildRequiredFiles: scenario.buildRequiredFiles,
      testFiles: scenario.testFiles,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(scenarioPath));

    const complexityPath = testInfo.outputPath("complexity.metrics.json");
    await writeUtf8File(complexityPath, JSON.stringify(project.metrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(complexityPath));
    const scenarioSeedMetricsPath = testInfo.outputPath("scenario.seed-definition.metrics.json");
    await writeUtf8File(scenarioSeedMetricsPath, JSON.stringify(scenarioSeedMetrics, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(scenarioSeedMetricsPath));
    const baselineSnapshotPath = testInfo.outputPath("seed.file-snapshot.json");
    await writeUtf8File(baselineSnapshotPath, JSON.stringify(baselineSnapshot, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(baselineSnapshotPath));

    expect(project.metrics.fileCount).toBeGreaterThanOrEqual(10);
    expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(500);
    expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.configFileCount).toBeGreaterThanOrEqual(3);
    expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(2);
    if (scenarioRequiresGameLikeBatch(scenario)) {
      expect(project.metrics.fileCount).toBeGreaterThanOrEqual(30);
      expect(project.metrics.codeLineCount).toBeGreaterThanOrEqual(1200);
      expect(project.metrics.moduleCount).toBeGreaterThanOrEqual(10);
      expect(project.metrics.testFileCount).toBeGreaterThanOrEqual(5);
    }

    const initialSettings = await requestJson<SettingsPayload>(window, "/v2/settings");
    const settingsPayload = buildFullChainSettingsPayload(project.workspace);
    const updatedSettings = await requestJson<SettingsPayload>(window, "/v2/settings", {
      method: "POST",
      body: settingsPayload,
    });
    const settingsSwitchPath = testInfo.outputPath("settings.workspace-switch.json");
    await writeUtf8File(settingsSwitchPath, JSON.stringify({
      requested: settingsPayload,
      before: initialSettings,
      post_response: updatedSettings,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(settingsSwitchPath));
    expect(
      String(updatedSettings.workspace || "").toLowerCase(),
      `settings POST must activate generated workspace; evidence=${toPosixPath(settingsSwitchPath)}`,
    ).toBe(project.workspace.toLowerCase());
    await expect.poll(async () => String((await requestJson<SettingsPayload>(window, "/v2/settings")).workspace || "").toLowerCase(), {
      timeout: 90_000,
      intervals: [500, 1000, 2000, 3000],
    }).toBe(project.workspace.toLowerCase());
    await reloadRendererAfterWorkspaceSwitch(window);
    await dismissEngineFailureDialog(window);

    const layout = await requestJson<RuntimeLayoutPayload>(window, "/v2/runtime/storage/layout");
    runtimeRoot = String(layout.runtime_root || "").trim();
    expect(runtimeRoot).not.toBe("");
    if (resumePlanningSeed) {
      const resetEvidencePath = testInfo.outputPath("resume.reset-tasks.json");
      if (startPhase === "pm" || startPhase === "chief" || startPhase === "director") {
        const preservePlanningContracts = startPhase === "chief" || startPhase === "director";
        const resetResponse = await requestJson<Record<string, unknown>>(window, "/v2/runtime/reset/tasks", {
          method: "POST",
          body: preservePlanningContracts ? { preserve_planning_contracts: true } : {},
        });
        await writeUtf8File(resetEvidencePath, JSON.stringify({
          start_phase: startPhase,
          preserve_planning_contracts: preservePlanningContracts,
          response: resetResponse,
        }, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(resetEvidencePath));
      }
      if (startPhase === "pm" || startPhase === "chief" || startPhase === "director") {
        const staleRuntimeArtifacts = [
          "results/director.result.json",
          "results/integration_qa.result.json",
        ];
        const removedArtifacts: string[] = [];
        for (const relativeArtifact of staleRuntimeArtifacts) {
          const artifactPath = path.join(runtimeRoot, relativeArtifact);
          await fs.rm(artifactPath, { force: true });
          removedArtifacts.push(toPosixPath(artifactPath));
        }
        await writeUtf8File(testInfo.outputPath("resume.reset-artifacts.json"), JSON.stringify({
          start_phase: startPhase,
          removed_artifacts: removedArtifacts,
        }, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(testInfo.outputPath("resume.reset-artifacts.json")));
      }

      const resumeSeedResult = await writeRuntimePlanningSeed(layout, project.workspace, resumePlanningSeed);
      const resumeSeedEvidencePath = testInfo.outputPath("resume.planning-seed.json");
      await writeUtf8File(resumeSeedEvidencePath, JSON.stringify({
        start_phase: startPhase,
        workspace: project.workspace,
        workspace_seed_paths: workspacePlanningSeedPaths.map(toPosixPath),
        runtime_seed_paths: resumeSeedResult.writtenPaths.map(toPosixPath),
        mandatory_tasks: resumePlanningSeed.tasks,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(resumeSeedEvidencePath));
      audit.evidence_paths.logs.push(
        toPosixPath(resumeSeedResult.runtimeRequirementsPath),
        toPosixPath(resumeSeedResult.runtimePlanPath),
        toPosixPath(resumeSeedResult.pipelinePath),
        toPosixPath(resumeSeedResult.progressPath),
      );
      audit.issues_fixed.push({
        issue: `resume_planning_contract_seeded_before_${startPhase}`,
        root_cause: "phase_reuse_context",
        fix: `seeded current scenario requirements/plan into runtime contracts and workspace docs for ${project.workspace}`,
        verified: true,
      });
    }

    const llmPreflight = await refreshRequiredLlmReadinessThroughSettings(window, testInfo);
    audit.evidence_paths.screenshots.push(...llmPreflight.screenshots);
    const llmStatusPath = testInfo.outputPath("llm-readiness.status.json");
    await writeUtf8File(llmStatusPath, JSON.stringify({
      roles_checked: llmPreflight.rolesChecked,
      roles_refreshed: llmPreflight.rolesRefreshed,
      status: llmPreflight.finalStatus,
    }, null, 2));
    audit.evidence_paths.snapshots.push(toPosixPath(llmStatusPath));
    if (llmPreflight.rolesRefreshed.length > 0) {
      audit.issues_fixed.push({
        issue: "llm_role_readiness_stale_or_missing",
        root_cause: "llm_runtime_config",
        fix: `refreshed required roles through Settings deep-test UI: ${llmPreflight.rolesRefreshed.join(", ")}`,
        verified: true,
      });
    }

    let planPath = "";
    if (shouldRunFullChainPhase(startPhase, "court")) {
      const courtFlow = await runCourtFlow(window, scenario);
      await dismissEngineFailureDialog(window);

      const courtShot = await captureAuditScreenshot(window, testInfo, "court-phase");
      audit.evidence_paths.screenshots.push(toPosixPath(courtShot.pngPath), toPosixPath(courtShot.reviewJpgPath));

      if (!courtFlow.dialogueReady || courtFlow.fallbackUsed) {
        audit.issues_fixed.push({
          issue: "court_dialogue_not_ready",
          root_cause: "architect_dialogue",
          fix: "strict full-chain audit now fails instead of drafting from an incomplete Architect dialogue",
          verified: false,
        });
        throw new Error(
          `Court phase failed strict dialogue gate: dialogueReady=${courtFlow.dialogueReady} `
          + `fallbackUsed=${courtFlow.fallbackUsed} screenshot=${toPosixPath(courtShot.reviewJpgPath)}`,
        );
      }

      const docsRoots = [
        path.join(project.workspace, "docs"),
        path.join(project.workspace, ".polaris", "docs"),
      ];
      let docsCount = 0;
      for (const docsRoot of docsRoots) {
        docsCount += (await listFilesRecursive(docsRoot)).length;
      }
      expect(docsCount).toBeGreaterThan(0);
    } else {
      const resumeShot = await captureAuditScreenshot(window, testInfo, `resume-before-${startPhase}`);
      audit.evidence_paths.screenshots.push(toPosixPath(resumeShot.pngPath), toPosixPath(resumeShot.reviewJpgPath));
      audit.issues_fixed.push({
        issue: `court_phase_resumed_before_${startPhase}`,
        root_cause: "resume_strategy",
        fix: `KERNELONE_E2E_START_PHASE=${startPhase} reused workspace ${project.workspace}`,
        verified: true,
      });
    }

    const planArtifact = await waitForRuntimeArtifact(window, "contracts/plan.md", 120_000);
    runtimeRoot = planArtifact.runtimeRoot;
    planPath = planArtifact.artifactPath;
    expect((await fs.readFile(planPath, "utf-8")).trim().length).toBeGreaterThan(0);
    audit.acceptance_results.court_phase = "PASS";
    audit.evidence_paths.logs.push(toPosixPath(planPath));

    const deadlineMs = Date.now() + 45 * 60 * 1000;
    while (Date.now() < deadlineMs) {
      audit.rounds += 1;
      const round = audit.rounds;

      if (shouldRunFullChainPhase(startPhase, "pm")) {
      await dismissEngineFailureDialog(window);
      await enterPmWorkspace(window);
      await expect(window.getByTestId("pm-workspace")).toBeVisible();
      const pmTerminalStatus = await runPmRound(window);
      if (pmTerminalStatus.log_path) audit.evidence_paths.logs.push(toPosixPath(pmTerminalStatus.log_path));
      const pmShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.pm`);
      audit.evidence_paths.screenshots.push(toPosixPath(pmShot.pngPath), toPosixPath(pmShot.reviewJpgPath));

      const snapshot = await requestJson<SnapshotPayload>(window, "/v2/state/snapshot");
      const snapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.snapshot.json`);
      await writeUtf8File(snapshotPath, JSON.stringify(snapshot, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(snapshotPath));

      const pmContractArtifact = await waitForRuntimeArtifact(window, "contracts/pm_tasks.contract.json", 120_000);
      runtimeRoot = pmContractArtifact.runtimeRoot;
      const pmContractPath = pmContractArtifact.artifactPath;
      const pmContract = await readJsonFile<PmContractPayload>(pmContractPath);
      audit.evidence_paths.logs.push(toPosixPath(pmContractPath));
      const score = Number(pmContract?.quality_gate?.score || 0);
      const critical = Number(pmContract?.quality_gate?.critical_issue_count || 0);
      const summary = String(pmContract?.quality_gate?.summary || "").trim();
      const pmFallbackFailure = detectPmFallbackFailure(pmContract);

      const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
      const pmAudit = auditPmContract(pmContract, project.workspace, scenario);
      latestPmPlanningContribution = buildPmPlanningContribution(
        "executed_pm_round",
        round,
        pmContract,
        pmAudit,
        pmContractPath,
      );
      const pmSnapshotGate = (
        (Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0) > 0
        && (Number(snapshot.pm_state?.["completed_task_count"] || 0) > 0 || tasks.length > 0)
      );

      audit.pm_quality_history.push({
        round,
        score,
        issues: [
          summary,
          ...(critical > 0 ? [`critical_issue_count=${critical}`] : []),
          ...(pmAudit.invalidTaskCount > 0 ? [`invalid_tasks=${pmAudit.invalidTaskCount}`] : []),
          ...pmAudit.issues,
        ].filter(Boolean),
      });

      const leakage = [
        ...detectPromptLeakage(JSON.stringify(pmContract || {}), toPosixPath(pmContractPath)),
        ...detectPromptLeakage(await fs.readFile(planPath, "utf-8"), toPosixPath(planPath)),
      ];
      if (leakage.length > 0) audit.leakage_findings.push(...leakage);

      const pmTerminalFailed = Boolean(
        (typeof pmTerminalStatus.exit_code === "number" && pmTerminalStatus.exit_code !== 0)
        || pmTerminalStatus.ok === false
        || String(pmTerminalStatus.status || "").trim().toLowerCase() === "failed",
      );
      if (pmTerminalFailed) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_terminal_failed_${pmTerminalStatus.exit_code ?? "unknown"}`,
          root_cause: "pm_process",
          fix: `fail-fast before waiting for Director artifacts (execution_id=${pmTerminalStatus.execution_id || "unknown"} status=${pmTerminalStatus.status || "unknown"} error=${pmTerminalStatus.error || ""})`,
          verified: false,
        });
        throw new Error(
          `PM phase failed closed before Director wait: `
          + `status=${pmTerminalStatus.status || "unknown"} exit=${pmTerminalStatus.exit_code ?? "unknown"} `
          + `error=${pmTerminalStatus.error || ""} contract=${toPosixPath(pmContractPath)} screenshot=${toPosixPath(pmShot.reviewJpgPath)}`,
        );
      }

      if (pmFallbackFailure) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_fallback_failure_${pmFallbackFailure}`,
          root_cause: "pm_llm_runtime",
          fix: `fail-fast instead of dispatching fallback PM tasks (evidence: ${toPosixPath(pmContractPath)})`,
          verified: false,
        });
        throw new Error(
          `PM phase failed closed because the contract contains fallback/error evidence: ${pmFallbackFailure}; `
          + `contract=${toPosixPath(pmContractPath)}`,
        );
      }

      if (pmAudit.issues.length > 0) {
        audit.issues_fixed.push({
          issue: `round_${round}_pm_contract_quality_strict_failed`,
          root_cause: "pm_contract_quality",
          fix: `fail-fast on PM contract path, acceptance, workspace, and domain coverage issues (evidence: ${toPosixPath(pmContractPath)})`,
          verified: false,
        });
        throw new Error(
          `PM contract strict quality gate failed: ${pmAudit.issues.join("; ")}; `
          + `contract=${toPosixPath(pmContractPath)}`,
        );
      }

      if (pmSnapshotGate && score >= 80 && critical === 0 && pmAudit.issues.length === 0) {
        audit.acceptance_results.pm_phase = "PASS";
      }

      await clickWorkspaceBack(window, "pm-workspace-back");
      } else {
        const snapshot = await requestJson<SnapshotPayload>(window, "/v2/state/snapshot");
        const snapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.snapshot.resumed.json`);
        await writeUtf8File(snapshotPath, JSON.stringify(snapshot, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(snapshotPath));

        const pmContractArtifact = await waitForRuntimeArtifact(window, "contracts/pm_tasks.contract.json", 120_000);
        runtimeRoot = pmContractArtifact.runtimeRoot;
        const pmContractPath = pmContractArtifact.artifactPath;
        const pmContract = await readJsonFile<PmContractPayload>(pmContractPath);
        audit.evidence_paths.logs.push(toPosixPath(pmContractPath));
        const score = Number(pmContract?.quality_gate?.score || 0);
        const critical = Number(pmContract?.quality_gate?.critical_issue_count || 0);
        const tasks = Array.isArray(pmContract?.tasks) ? pmContract.tasks : [];
        const pmAudit = auditPmContract(pmContract, project.workspace, scenario);
        latestPmPlanningContribution = buildPmPlanningContribution(
          "resumed_existing_pm_contract",
          round,
          pmContract,
          pmAudit,
          pmContractPath,
        );
        const pmFallbackFailure = detectPmFallbackFailure(pmContract);

        audit.pm_quality_history.push({
          round,
          score,
          issues: [
            `resumed_existing_pm_contract:${toPosixPath(pmContractPath)}`,
            ...(critical > 0 ? [`critical_issue_count=${critical}`] : []),
            ...(pmAudit.invalidTaskCount > 0 ? [`invalid_tasks=${pmAudit.invalidTaskCount}`] : []),
            ...pmAudit.issues,
            ...(pmFallbackFailure ? [`fallback_failure=${pmFallbackFailure}`] : []),
          ],
        });

        if (score < 80 || critical > 0 || pmAudit.issues.length > 0 || tasks.length === 0 || pmFallbackFailure) {
          throw new Error(
            `Resumed PM contract failed quality gate: score=${score} critical=${critical} `
            + `tasks=${tasks.length} invalidTasks=${pmAudit.invalidTaskCount} fallback=${pmFallbackFailure || "none"} `
            + `strictIssues=${pmAudit.issues.join("; ") || "none"} `
            + `contract=${toPosixPath(pmContractPath)}`,
          );
        }
        audit.acceptance_results.pm_phase = "PASS";
      }

      if (shouldRunFullChainPhase(startPhase, "chief")) {
        await dismissEngineFailureDialog(window);
        const chiefDiagnostics = await verifyChiefEngineerPhase(window);
        const chiefSnapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.chief-engineer-diagnostics.json`);
        await writeUtf8File(chiefSnapshotPath, JSON.stringify(chiefDiagnostics, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(chiefSnapshotPath));
        if (scenarioRequiresGameLikeBatch(scenario)) {
          const plannedBlueprints = Number(chiefDiagnostics.blueprints?.planned_tasks || 0);
          const coveredBlueprints = Number(chiefDiagnostics.blueprints?.covered_tasks || 0);
          const expectedBlueprints = Math.max(GAME_PM_MIN_TASKS, scenarioRequiredDomains(scenario).length);
          expect(
            plannedBlueprints,
            `Chief Engineer must produce a large batch of blueprints before Director handoff; evidence=${toPosixPath(chiefSnapshotPath)}`,
          ).toBeGreaterThanOrEqual(expectedBlueprints);
          expect(
            coveredBlueprints,
            `Chief Engineer must cover every planned blueprint before Director handoff; evidence=${toPosixPath(chiefSnapshotPath)}`,
          ).toBeGreaterThanOrEqual(plannedBlueprints);
        }
        audit.acceptance_results.chief_engineer_phase = "PASS";
        const chiefShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.chief-engineer`);
        audit.evidence_paths.screenshots.push(toPosixPath(chiefShot.pngPath), toPosixPath(chiefShot.reviewJpgPath));
        await clickWorkspaceBack(window, "chief-engineer-workspace-back");
      } else {
        const chiefDiagnostics = await requestJson<ChiefEngineerDiagnosticsPayload>(window, "/v2/chief-engineer/diagnostics");
        const chiefSnapshotPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.chief-engineer-diagnostics.resumed.json`);
        await writeUtf8File(chiefSnapshotPath, JSON.stringify(chiefDiagnostics, null, 2));
        audit.evidence_paths.snapshots.push(toPosixPath(chiefSnapshotPath));
        expect(
          chiefEngineerHandoffReady(chiefDiagnostics),
          `resumed ChiefEngineer diagnostics must be handoff-ready; evidence=${toPosixPath(chiefSnapshotPath)}`,
        ).toBeTruthy();
        audit.acceptance_results.chief_engineer_phase = "PASS";
      }

      let directorResult: DirectorResultArtifact | null = null;
      let directorResultPath = "";
      let directorSuccesses = 0;
      let directorStatus = "";
      let directorResultSource: DirectorResultSource | "" = "";
      let downstreamDirectorFailure = "";

      if (shouldRunFullChainPhase(startPhase, "director")) {
        await dismissEngineFailureDialog(window);
        await enterDirectorWorkspace(window);
        await expect(window.getByTestId("director-workspace")).toBeVisible();
        const director = await runDirectorUntilResultArtifact(window, { minMtimeMs: startEpochSeconds * 1000 });
        if (scenarioRequiresGameLikeBatch(scenario)) {
          const expectedDirectorTasks = Math.max(GAME_PM_MIN_TASKS, scenarioRequiredDomains(scenario).length);
          expect(
            director.linkedTaskCount,
            "Director must receive the large PM task batch only after Chief Engineer blueprints are handoff-ready",
          ).toBeGreaterThanOrEqual(expectedDirectorTasks);
          expect(
            director.uiTaskCount,
            "Director workspace must visibly expose the large task batch before execution",
          ).toBeGreaterThanOrEqual(expectedDirectorTasks);
          if (scenario.key === "card3d") {
            const directorCoveragePaths = director.coveragePaths
              .map((item) => normalizeCoveragePath(item, project.workspace))
              .filter(Boolean);
            const directorCoveredDomains = scenarioCoveredDomains(scenario, directorCoveragePaths);
            const missingDirectorDomains = scenarioRequiredDomains(scenario)
              .filter((domain) => !directorCoveredDomains.includes(domain));
            expect(
              director.coveragePaths.length,
              "Director task exposure must include scope/target paths for card3d domain audit",
            ).toBeGreaterThan(0);
            expect(
              missingDirectorDomains,
              `Director task batch must cover every card3d domain before execution; `
              + `paths=${JSON.stringify(directorCoveragePaths)}`,
            ).toEqual([]);
          }
        }
        if (director.linkedTaskCount > 0 && director.uiTaskCount > 0) {
          audit.acceptance_results.director_phase = "PASS";
        }

        const directorResultArtifact = {
          runtimeRoot: director.runtimeRoot,
          artifactPath: director.artifactPath,
          mtimeMs: director.mtimeMs,
        };
        runtimeRoot = directorResultArtifact.runtimeRoot;
        directorResultPath = directorResultArtifact.artifactPath;
        directorResultSource = director.source;
        directorResult = await readJsonFile<DirectorResultArtifact>(directorResultPath);
        audit.evidence_paths.logs.push(toPosixPath(directorResultPath));
        downstreamDirectorFailure = directorFailureReason(directorResult);
        directorSuccesses = Number(directorResult?.successes || 0);
        directorStatus = String(directorResult?.status || "").trim();

        const directorCodeEvidence = await inspectDirectorCodeChanges(window);
        if (directorSuccesses > 0) {
          expect(
            directorCodeEvidence.eventCount,
            `Director code change view should show task-runtime or realtime file changes after successful execution; result=${toPosixPath(directorResultPath)}`,
          ).toBeGreaterThan(0);
          expect(
            directorCodeEvidence.expanded,
            `Director code change view should allow expanding change details; result=${toPosixPath(directorResultPath)}`,
          ).toBe(true);
        }
        const dirCodeShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.director-code`);
        audit.evidence_paths.screenshots.push(toPosixPath(dirCodeShot.pngPath), toPosixPath(dirCodeShot.reviewJpgPath));

        const dirShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.director`);
        audit.evidence_paths.screenshots.push(toPosixPath(dirShot.pngPath), toPosixPath(dirShot.reviewJpgPath));

        if (downstreamDirectorFailure) {
          audit.issues_fixed.push({
            issue: `round_${round}_${downstreamDirectorFailure}`,
            root_cause: "director_execution",
            fix: `fail-fast on Director terminal failure instead of returning to PM (evidence: ${toPosixPath(directorResultPath)})`,
            verified: false,
          });
          throw new Error(
            `Director phase failed closed: ${downstreamDirectorFailure}; `
            + `result=${toPosixPath(directorResultPath)} screenshot=${toPosixPath(dirShot.reviewJpgPath)}`,
          );
        }
        audit.acceptance_results.director_phase = "PASS";

        if (directorResultSource === "existing_artifact" || directorResultSource === "reconciled_terminal") {
          audit.issues_fixed.push({
            issue: `round_${round}_director_result_reused_${directorResultSource}`,
            root_cause: "resume_strategy",
            fix: `KERNELONE_E2E_START_PHASE=${startPhase} reused fresh terminal Director evidence at ${toPosixPath(directorResultPath)} mtime=${new Date(directorResultArtifact.mtimeMs).toISOString()}`,
            verified: true,
          });
        }

        await clickWorkspaceBack(window, "director-workspace-back");
      } else {
        let directorResultArtifact = await tryRuntimeArtifact(window, "results/director.result.json");
        if (!directorResultArtifact) {
          await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
            method: "POST",
            body: { run_id: `full-chain-resumed-director-${Date.now()}` },
          });
          directorResultArtifact = await tryRuntimeArtifact(window, "results/director.result.json");
        }
        if (!directorResultArtifact) {
          directorResultArtifact = await waitForRuntimeArtifact(
            window,
            "results/director.result.json",
            120_000,
          );
        }
        runtimeRoot = directorResultArtifact.runtimeRoot;
        directorResultPath = directorResultArtifact.artifactPath;
        directorResultSource = "existing_artifact";
        directorResult = await readJsonFile<DirectorResultArtifact>(directorResultPath);
        audit.evidence_paths.logs.push(toPosixPath(directorResultPath));
        downstreamDirectorFailure = directorFailureReason(directorResult);
        directorSuccesses = Number(directorResult?.successes || 0);
        directorStatus = String(directorResult?.status || "").trim();

        if (downstreamDirectorFailure) {
          throw new Error(
            `Resumed Director result failed: ${downstreamDirectorFailure}; `
            + `result=${toPosixPath(directorResultPath)}`,
          );
        }
        audit.acceptance_results.director_phase = "PASS";
      }

      const finalSnapshot = await snapshotProjectFiles(project.workspace);
      const finalMetrics = await measureComplexity(project.workspace);
      audit.runtime_contribution = compareProjectSnapshots(baselineSnapshot, finalSnapshot);
      const forbiddenRuntimeArtifacts = findForbiddenRuntimeArtifacts(project.scenario, audit.runtime_contribution);
      expect(
        forbiddenRuntimeArtifacts,
        `Game-like scenario Director output must preserve the seed Node/TypeScript stack and must not introduce forbidden runtime artifacts; `
        + `artifacts=${JSON.stringify(forbiddenRuntimeArtifacts)}`,
      ).toEqual([]);
      const directorArtifactMaterialization = summarizeDirectorArtifactMaterialization(directorResult);
      const contributionPath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.runtime-contribution.json`);
      const seedResidue = await findScenarioSeedResidue(project.workspace, project.scenario);
      const seedResiduePath = testInfo.outputPath(`round-${String(round).padStart(2, "0")}.seed-residue.json`);
      await writeUtf8File(seedResiduePath, JSON.stringify({
        workspace: toPosixPath(project.workspace),
        scenario: project.scenario.key,
        residue_count: seedResidue.length,
        residues: seedResidue,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(seedResiduePath));
      expect(
        seedResidue,
        `Game-like scenario final source must not retain audit seed markers; evidence=${toPosixPath(seedResiduePath)}`,
      ).toEqual([]);
      audit.complexity_contribution_breakdown = buildComplexityContributionBreakdown({
        scenarioSeedMetrics,
        startPhase,
        currentRunBaselineMetrics: project.metrics,
        baselineSnapshot,
        finalMetrics,
        finalSnapshot,
        pmPlanningDelta: latestPmPlanningContribution,
        directorResultSource: directorResultSource || "unknown",
        directorContribution: audit.runtime_contribution,
        contributionEvidencePath: contributionPath,
      });
      await writeUtf8File(contributionPath, JSON.stringify({
        audit_start_epoch_seconds: startEpochSeconds,
        director_result_source: directorResultSource || "unknown",
        director_result_path: toPosixPath(directorResultPath),
        director_result_mtime_ms: directorResultPath ? (await fs.stat(directorResultPath)).mtimeMs : null,
        director_artifact_source: String(directorResult?.source || ""),
        director_artifact_materialization: directorArtifactMaterialization,
        baseline: baselineSnapshot,
        final: finalSnapshot,
        contribution: audit.runtime_contribution,
        seed_residue_path: toPosixPath(seedResiduePath),
        seed_residue_count: seedResidue.length,
        seed_residue: seedResidue,
        complexity_contribution_breakdown: audit.complexity_contribution_breakdown,
      }, null, 2));
      audit.evidence_paths.snapshots.push(toPosixPath(contributionPath));
      if (directorSuccesses > 0) {
        const changedFileCount = audit.runtime_contribution.addedFiles.length
          + audit.runtime_contribution.modifiedFiles.length
          + audit.runtime_contribution.deletedFiles.length;
        if (shouldRunFullChainPhase(startPhase, "director")) {
          const canReuseMaterializedArtifact = startPhase !== "court"
            && changedFileCount === 0
            && directorArtifactMaterialization.changedFileCount > 0
            && directorArtifactMaterialization.toolEvidenceCount > 0;
          if (canReuseMaterializedArtifact) {
            audit.issues_fixed.push({
              issue: `round_${round}_director_workspace_delta_zero_but_artifact_materialized`,
              root_cause: "resume_strategy",
              fix: `resume run accepted fresh Director artifact materialization evidence instead of forcing duplicate file writes; evidence=${toPosixPath(contributionPath)}`,
              verified: true,
            });
          } else {
            expect(
              changedFileCount,
              `Director phase success must produce auditable current-run contribution or resume artifact materialization; `
              + `source=${directorResultSource || "unknown"} `
              + `artifact_files=${directorArtifactMaterialization.changedFileCount} `
              + `artifact_tools=${directorArtifactMaterialization.toolEvidenceCount} `
              + `evidence=${toPosixPath(contributionPath)}`,
            ).toBeGreaterThan(0);
          }
        } else {
          audit.issues_fixed.push({
            issue: `round_${round}_runtime_contribution_not_recomputed_for_${directorResultSource || "unknown"}`,
            root_cause: "resume_strategy",
            fix: `runtime contribution gate skipped because Director result came from ${directorResultSource || "unknown"}; evidence=${toPosixPath(contributionPath)}`,
            verified: true,
          });
        }
      }

      const qaArtifactMinMtimeMs = shouldRunFullChainPhase(startPhase, "director")
        ? startEpochSeconds * 1000
        : undefined;
      const existingQaArtifact = await tryRuntimeArtifact(
        window,
        "results/integration_qa.result.json",
        qaArtifactMinMtimeMs ? { minMtimeMs: qaArtifactMinMtimeMs } : undefined,
      );
      const existingQa = existingQaArtifact
        ? await readJsonFile<IntegrationQaArtifact>(existingQaArtifact.artifactPath)
        : null;
      if (
        String(existingQa?.reason || "").trim() !== "integration_qa_passed"
        || String(existingQa?.evidence_grade || "").trim() !== "real_command_passed"
      ) {
        await requestJson<DirectorIntegrationQaPayload>(window, "/v2/director/integration-qa", {
          method: "POST",
          body: { run_id: `full-chain-qa-${Date.now()}` },
        });
      }
      const qaArtifact = await waitForRuntimeArtifact(
        window,
        "results/integration_qa.result.json",
        120_000,
        qaArtifactMinMtimeMs ? { minMtimeMs: qaArtifactMinMtimeMs } : undefined,
      );
      runtimeRoot = qaArtifact.runtimeRoot;
      const qaPath = qaArtifact.artifactPath;
      const qa = await readJsonFile<IntegrationQaArtifact>(qaPath);
      latestQaReason = String(qa?.reason || "").trim();
      latestQaEvidenceGrade = String(qa?.evidence_grade || "").trim();
      audit.qa_gate = {
        passed: typeof qa?.passed === "boolean" ? qa.passed : null,
        reason: latestQaReason,
        evidence_grade: latestQaEvidenceGrade || "unknown",
        summary: String(qa?.summary || "").trim(),
        result_path: String(qa?.result_path || "").trim(),
        runtime_result_path: String(qa?.runtime_result_path || "").trim(),
      };
      audit.evidence_paths.logs.push(toPosixPath(qaPath));
      if (latestQaReason === "integration_qa_passed") {
        expect(
          latestQaEvidenceGrade,
          `Integration QA PASS must include strong evidence grade; qa=${toPosixPath(qaPath)} summary=${String(qa?.summary || "")}`,
        ).toBe("real_command_passed");
        audit.acceptance_results.qa_phase = "PASS";

        let qaEvidenceBadge = window.getByTestId("qa-evidence-grade");
        const qaBadgeVisible = await qaEvidenceBadge.isVisible({ timeout: 5_000 }).catch(() => false);
        const qaBadgeText = qaBadgeVisible ? String(await qaEvidenceBadge.textContent().catch(() => "") || "") : "";
        if (
          !qaBadgeVisible
          || !qaBadgeText.includes("real command passed")
          || !qaBadgeText.includes("integration_qa_passed")
        ) {
          await reloadRendererAfterWorkspaceSwitch(window);
          qaEvidenceBadge = window.getByTestId("qa-evidence-grade");
        }
        await expect(
          qaEvidenceBadge,
          `QA phase PASS must be recoverable in the desktop runtime panel; qa=${toPosixPath(qaPath)}`,
        ).toBeVisible({ timeout: 60_000 });
        await expect(qaEvidenceBadge).toContainText("real command passed");
        await expect(qaEvidenceBadge).toContainText("integration_qa_passed");
        const qaShot = await captureAuditScreenshot(window, testInfo, `round-${String(round).padStart(2, "0")}.qa`);
        audit.evidence_paths.screenshots.push(toPosixPath(qaShot.pngPath), toPosixPath(qaShot.reviewJpgPath));
      } else {
        audit.issues_fixed.push({
          issue: `round_${round}_qa_reason_${latestQaReason || "unknown"}`,
          root_cause: latestQaReason.includes("pending") ? "director_execution" : "qa_baseline",
          fix: `fail-fast on QA terminal failure instead of rerunning PM (evidence: ${toPosixPath(qaPath)})`,
          verified: false,
        });
        const failureSignature = JSON.stringify({
          qa_reason: latestQaReason || "unknown",
          qa_evidence_grade: latestQaEvidenceGrade || "unknown",
          director_status: directorStatus || "unknown",
          director_error: String(directorResult?.error || "").trim(),
          director_successes: directorSuccesses,
          director_failures: Number(directorResult?.failures || 0),
          director_total: Number(directorResult?.total || 0),
        });
        audit.next_risks.push(`Downstream QA failure signature: ${failureSignature}`);
        throw new Error(`QA phase failed closed: ${failureSignature}`);
      }

      latestEventsPath = (await findLatestEventsPath(runtimeRoot)) || "";
      if (latestEventsPath) audit.evidence_paths.logs.push(toPosixPath(latestEventsPath));

      if (
        audit.acceptance_results.court_phase === "PASS"
        && audit.acceptance_results.pm_phase === "PASS"
        && audit.acceptance_results.chief_engineer_phase === "PASS"
        && audit.acceptance_results.director_phase === "PASS"
        && audit.acceptance_results.qa_phase === "PASS"
        && audit.leakage_findings.length === 0
      ) {
        break;
      }
    }

    const toolAuditEvents: RuntimeEvent[] = [];
    if (latestEventsPath) {
      toolAuditEvents.push(...await readJsonLines<RuntimeEvent>(latestEventsPath));
    }
    for (const toolEventsPath of await findToolEventPaths(runtimeRoot)) {
      audit.evidence_paths.logs.push(toPosixPath(toolEventsPath));
      toolAuditEvents.push(...await readJsonLines<RuntimeEvent>(toolEventsPath));
    }
    audit.director_tool_audit = analyzeToolAudit(toolAuditEvents, startEpochSeconds);
    if (audit.issues_fixed.length > 0 && audit.acceptance_results.qa_phase === "PASS") {
      audit.issues_fixed = audit.issues_fixed.map((item) => ({ ...item, verified: true }));
    }
    if (audit.director_tool_audit.total_calls === 0) {
      audit.next_risks.push("No explicit tool-call evidence found in runtime events; keep monitoring telemetry coverage.");
    }
    if (audit.director_tool_audit.unauthorized_blocked > 0) {
      audit.next_risks.push(
        `Director policy blocked ${audit.director_tool_audit.unauthorized_blocked} unauthorized tool attempts; verify repeated denials do not hide task drift.`,
      );
    }
    const runtimeContributionFileChanges = audit.runtime_contribution
      ? audit.runtime_contribution.addedFiles.length
        + audit.runtime_contribution.modifiedFiles.length
        + audit.runtime_contribution.deletedFiles.length
      : 0;
    const directorPolicyEvidenceRequired = shouldRunFullChainPhase(startPhase, "director") && runtimeContributionFileChanges > 0;
    if (directorPolicyEvidenceRequired && audit.director_tool_audit.policy_evidence_count === 0) {
      audit.next_risks.push(
        `Director changed ${runtimeContributionFileChanges} files but no director_policy evidence was found in runtime tool events.`,
      );
    }
    if (audit.leakage_findings.length > 0) {
      audit.next_risks.push("Prompt-leakage keywords detected in plan or PM contract.");
    }
    if (latestQaReason && latestQaReason !== "integration_qa_passed") {
      audit.next_risks.push(`Latest QA reason: ${latestQaReason}`);
    }

    const expandedTechMatrix = await collectExpandedTechEvidenceMatrix(window, {
      requireRealChain: true,
      runtimeRootOverride: runtimeRoot,
      workspaceOverride: audit.workspace,
    });
    const expandedTechMatrixPath = await writeExpandedTechEvidenceMatrix(
      testInfo,
      expandedTechMatrix,
      "full-chain-expanded-tech-evidence-matrix.json",
    );
    audit.evidence_paths.snapshots.push(toPosixPath(expandedTechMatrixPath));
    audit.expanded_tech_evidence_matrix = {
      report_path: toPosixPath(expandedTechMatrixPath),
      candidate_count: expandedTechMatrix.expanded_candidates.length,
      summary: expandedTechMatrix.summary,
      core_runtime_integrations: expandedTechMatrix.core_runtime_integrations,
      core_runtime_evidence_placement: expandedTechMatrix.core_runtime_evidence_placement
        ? {
            row_count: expandedTechMatrix.core_runtime_evidence_placement.rows.length,
            missing: expandedTechMatrix.core_runtime_evidence_placement.missing,
            receipt_id: expandedTechMatrix.core_runtime_evidence_placement.receipt_id,
            handoff_id: expandedTechMatrix.core_runtime_evidence_placement.handoff_id,
            task_projection: expandedTechMatrix.core_runtime_evidence_placement.task_projection,
          }
        : null,
    };
    assertExpandedTechEvidenceMatrix(expandedTechMatrix);

    const pass = (
      audit.acceptance_results.court_phase === "PASS"
      && audit.acceptance_results.pm_phase === "PASS"
      && audit.acceptance_results.chief_engineer_phase === "PASS"
      && audit.acceptance_results.director_phase === "PASS"
      && audit.acceptance_results.qa_phase === "PASS"
      && audit.leakage_findings.length === 0
      && (!directorPolicyEvidenceRequired || audit.director_tool_audit.policy_evidence_count > 0)
      && audit.director_tool_audit.dangerous_commands === 0
    );
    audit.status = pass ? "PASS" : "FAIL";
    expect(audit.status).toBe("PASS");
  } finally {
    await fs.mkdir(logsRoot, { recursive: true });
    audit.evidence_paths.logs.push(toPosixPath(auditPath));
    await writeUtf8File(auditPath, JSON.stringify(audit, null, 2));
    await testInfo.attach("full-chain-audit", {
      contentType: "application/json",
      body: Buffer.from(JSON.stringify(audit, null, 2), "utf-8"),
    });
  }
});
