import { execFile } from "node:child_process";
import { existsSync, promises as fs } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { type Page, type TestInfo } from "@playwright/test";
import { CORE_TECH_IDS } from "./data";
import { asNumber, asRecord, asRecords, asString, findRoleSessionKernelAuditEntry, getBackendInfoFromPage, isPathInsideOrSame, listFilesByBasename, listRuntimeAuditJsonlPaths, makeProbe, newestFile, readJsonIfExists, readJsonlFileEntries, repoRoot, requestJson, requestText, stringArray } from "./matrix_helpers";
import { type BackendConnection, type EvidenceProbe, type EvidenceRef, type ExpandedTechEvidenceReport, type JsonRecord } from "./types";

export async function collectElectronRuntimeProbes(page: Page, workspace: string): Promise<EvidenceProbe[]> {
  const probes: EvidenceProbe[] = [];
  let backend: BackendConnection | null = null;
  try {
    backend = await getBackendInfoFromPage(page);
  } catch {
    backend = null;
  }

  try {
    const payload = await page.evaluate(async () => {
      type PolarisApi = {
        getBackendInfo?: () => Promise<{ baseUrl?: string; token?: string }>;
        getBackendStatus?: () => Promise<Record<string, unknown>>;
        secrets?: {
          available?: () => Promise<Record<string, unknown>>;
        };
        pty?: Record<string, unknown>;
      };
      const api = (window as Window & { polaris?: PolarisApi }).polaris;
      const keys = api ? Object.keys(api).sort() : [];
      const backendInfo = api?.getBackendInfo ? await api.getBackendInfo() : {};
      const backendStatus = api?.getBackendStatus ? await api.getBackendStatus() : {};
      const secretAvailability = api?.secrets?.available ? await api.secrets.available() : {};
      return {
        keys,
        backend_info: backendInfo,
        backend_status: backendStatus,
        secret_availability: secretAvailability,
        has_secrets_api: Boolean(api?.secrets),
        has_pty_api: Boolean(api?.pty),
      };
    });
    const payloadRecord = asRecord(payload);
    const backendStatus = asRecord(payloadRecord.backend_status);
    const statusInfo = asRecord(backendStatus.info);
    const backendInfo = asRecord(payloadRecord.backend_info);
    const keys = stringArray(payloadRecord.keys);
    const pass = Boolean(
      backend?.source === "electron_preload" &&
        asString(backendInfo.baseUrl) &&
        asString(backendInfo.token) &&
        backendStatus.ready === true &&
        asString(statusInfo.baseUrl) === asString(backendInfo.baseUrl) &&
        keys.includes("getBackendInfo") &&
        keys.includes("getBackendStatus"),
    );
    probes.push(
      makeProbe({
        id: "electron_preload_supervisor_runtime_probe",
        title: "Electron preload IPC and backend supervisor runtime probe",
        category: "entrypoint",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "probe",
            ref: "window.polaris.getBackendInfo/getBackendStatus",
            value: {
              backend_source: backend?.source || "",
              preload_keys: keys,
              backend_state: asString(backendStatus.state),
              backend_ready: backendStatus.ready === true,
              backend_pid_present: Boolean(asNumber(backendStatus.pid)),
              base_url_matches: asString(statusInfo.baseUrl) === asString(backendInfo.baseUrl),
              token_present: Boolean(asString(backendInfo.token)),
            },
          },
        ],
        findings: pass ? [] : ["Electron preload backend IPC or supervisor status was not available in this entrypoint"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "electron_preload_supervisor_runtime_probe",
        title: "Electron preload IPC and backend supervisor runtime probe",
        category: "entrypoint",
        status: "WARN",
        required: false,
        evidence: [{ type: "probe", ref: "window.polaris.getBackendInfo/getBackendStatus" }],
        findings: [String(error)],
      }),
    );
  }

  try {
    const marker = `e2e-secret-${Date.now()}`;
    const secretValue = `${marker}-value`;
    const result = await page.evaluate(
      async ({ key, value }) => {
        const api = (window as Window & {
          polaris?: {
            secrets?: {
              available?: () => Promise<Record<string, unknown>>;
              set?: (key: string, value: string) => Promise<Record<string, unknown>>;
              get?: (key: string) => Promise<Record<string, unknown>>;
              remove?: (key: string) => Promise<Record<string, unknown>>;
            };
          };
        }).polaris;
        const available = api?.secrets?.available ? await api.secrets.available() : {};
        if (!api?.secrets?.set || !api.secrets.get || !api.secrets.remove) {
          return { available, set_result: {}, get_result: {}, remove_result: {}, get_after_remove: {} };
        }
        const setResult = await api.secrets.set(key, value);
        const getResult = await api.secrets.get(key);
        const removeResult = await api.secrets.remove(key);
        const getAfterRemove = await api.secrets.get(key);
        return {
          available,
          set_result: setResult,
          get_result: getResult,
          remove_result: removeResult,
          get_after_remove: getAfterRemove,
        };
      },
      { key: marker, value: secretValue },
    );
    const available = asRecord(asRecord(result).available);
    const setResult = asRecord(asRecord(result).set_result);
    const getResult = asRecord(asRecord(result).get_result);
    const removeResult = asRecord(asRecord(result).remove_result);
    const getAfterRemove = asRecord(asRecord(result).get_after_remove);
    const bridgeAvailable = available.ok === true && available.available === true;
    const pass = Boolean(
      bridgeAvailable &&
        setResult.ok === true &&
        getResult.ok === true &&
        asString(getResult.value) === secretValue &&
        removeResult.ok === true &&
        getAfterRemove.ok === false,
    );
    probes.push(
      makeProbe({
        id: "electron_secret_safe_storage_runtime_probe",
        title: "Electron safeStorage secret bridge runtime probe",
        category: "security",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "probe",
            ref: "window.polaris.secrets",
            value: {
              available_ok: available.ok === true,
              secret_bridge_available: bridgeAvailable,
              encryption_available: available.encryption_available === true,
              fallback_enabled: available.fallback_enabled === true,
              selected_storage_backend: asString(available.selected_storage_backend),
              storage_mode: asString(available.storage_mode),
              set_ok: setResult.ok === true,
              readback_ok: getResult.ok === true && asString(getResult.value) === secretValue,
              remove_ok: removeResult.ok === true,
              removed_read_fails: getAfterRemove.ok === false,
            },
          },
        ],
        findings: pass ? [] : ["Electron safeStorage was unavailable or secret set/get/remove roundtrip failed"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "electron_secret_safe_storage_runtime_probe",
        title: "Electron safeStorage secret bridge runtime probe",
        category: "security",
        status: "WARN",
        required: false,
        evidence: [{ type: "probe", ref: "window.polaris.secrets" }],
        findings: [String(error)],
      }),
    );
  }

  try {
    const marker = `E2E_PTY_${Date.now()}`;
    const result = await page.evaluate(
      async ({ markerValue, cwd }) => {
        type PtyPayload = { id?: string; data?: string; exitCode?: number | null; signal?: string | null };
        const api = (window as Window & {
          polaris?: {
            pty?: {
              start?: (options: Record<string, unknown>) => Promise<Record<string, unknown>>;
              resize?: (id: string, cols: number, rows: number) => Promise<Record<string, unknown>>;
              close?: (id: string) => Promise<Record<string, unknown>>;
              onData?: (handler: (payload: PtyPayload) => void) => () => void;
              onExit?: (handler: (payload: PtyPayload) => void) => () => void;
            };
          };
        }).polaris;
        if (!api?.pty?.start || !api.pty.resize || !api.pty.close || !api.pty.onData) {
          return { api_present: false };
        }
        const output: string[] = [];
        let exitPayload: PtyPayload | null = null;
        const unsubscribeData = api.pty.onData((payload) => {
          if (payload?.data) {
            output.push(String(payload.data));
          }
        });
        const unsubscribeExit = api.pty.onExit?.((payload) => {
          exitPayload = payload;
        });
        const started = await api.pty.start({
          command: "node",
          args: ["-e", `console.log(${JSON.stringify(markerValue)}); setTimeout(() => {}, 5000)`],
          cwd,
          cols: 80,
          rows: 24,
        });
        const sessionId = String(started.id || "");
        const resizeResult = sessionId ? await api.pty.resize(sessionId, 100, 30) : {};
        const deadline = Date.now() + 8_000;
        while (Date.now() < deadline && !output.join("").includes(markerValue) && !exitPayload) {
          await new Promise((resolve) => window.setTimeout(resolve, 100));
        }
        const closeResult = sessionId ? await api.pty.close(sessionId) : {};
        unsubscribeData();
        unsubscribeExit?.();
        return {
          api_present: true,
          started,
          session_id: sessionId,
          resize_result: resizeResult,
          close_result: closeResult,
          output: output.join(""),
          exit_payload: exitPayload,
        };
      },
      { markerValue: marker, cwd: workspace || "." },
    );
    const started = asRecord(asRecord(result).started);
    const resizeResult = asRecord(asRecord(result).resize_result);
    const closeResult = asRecord(asRecord(result).close_result);
    const output = asString(asRecord(result).output);
    const pass = Boolean(
      asRecord(result).api_present === true &&
        started.ok === true &&
        asString(asRecord(result).session_id) &&
        resizeResult.ok === true &&
        closeResult.ok === true &&
        output.includes(marker),
    );
    probes.push(
      makeProbe({
        id: "electron_pty_runtime_probe",
        title: "Electron PTY bridge runtime probe",
        category: "tooling",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "probe",
            ref: "window.polaris.pty",
            value: {
              api_present: asRecord(result).api_present === true,
              start_ok: started.ok === true,
              session_id: asString(asRecord(result).session_id),
              resize_ok: resizeResult.ok === true,
              resize_error: asString(resizeResult.error),
              close_ok: closeResult.ok === true,
              close_error: asString(closeResult.error),
              output_marker_seen: output.includes(marker),
              output_preview: output.slice(0, 120),
            },
          },
        ],
        findings: pass ? [] : ["Electron PTY start/output/resize/close roundtrip failed"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "electron_pty_runtime_probe",
        title: "Electron PTY bridge runtime probe",
        category: "tooling",
        status: "WARN",
        required: false,
        evidence: [{ type: "probe", ref: "window.polaris.pty" }],
        findings: [String(error)],
      }),
    );
  }

  return probes;
}

export async function collectReadonlyControlPlaneRuntimeProbes(page: Page, workspace: string): Promise<EvidenceProbe[]> {
  const probes: EvidenceProbe[] = [];
  const encodedWorkspace = encodeURIComponent(workspace || ".");

  try {
    const [status, runtimeStatus, config] = await Promise.all([
      requestJson<JsonRecord>(page, `/v2/llm/status?workspace=${encodedWorkspace}`),
      requestJson<JsonRecord>(page, "/v2/llm/runtime-status"),
      requestJson<JsonRecord>(page, "/v2/llm/config"),
    ]);
    const statusRecord = asRecord(status);
    const runtimeRoles = asRecord(asRecord(runtimeStatus).roles);
    const configRoles = asRecord(asRecord(config).roles);
    const roleKeys = new Set([...Object.keys(runtimeRoles), ...Object.keys(configRoles)]);
    const pass = Object.keys(statusRecord).length > 0 && roleKeys.size > 0;
    probes.push(
      makeProbe({
        id: "llm_control_status_runtime_probe",
        title: "LLM control-plane status/config/runtime read-only probe",
        category: "llm_control",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/llm/status",
            value: {
              state: statusRecord.state,
              blocked_roles: statusRecord.blocked_roles,
              factory_blocked_roles: statusRecord.factory_blocked_roles,
            },
          },
          { type: "api", ref: "/v2/llm/runtime-status", value: { role_count: Object.keys(runtimeRoles).length } },
          { type: "api", ref: "/v2/llm/config", value: { role_count: Object.keys(configRoles).length } },
        ],
        findings: pass ? [] : ["LLM status/config/runtime response did not expose role bindings"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "llm_control_status_runtime_probe",
        title: "LLM control-plane status/config/runtime read-only probe",
        category: "llm_control",
        status: "WARN",
        required: false,
        evidence: [
          { type: "api", ref: "/v2/llm/status" },
          { type: "api", ref: "/v2/llm/runtime-status" },
          { type: "api", ref: "/v2/llm/config" },
        ],
        findings: [String(error)],
      }),
    );
  }

  try {
    const providers = asRecord(await requestJson<JsonRecord>(page, "/v2/llm/providers"));
    const providerList = Array.isArray(providers.providers) ? providers.providers : [];
    probes.push(
      makeProbe({
        id: "llm_provider_catalog_runtime_probe",
        title: "LLM provider catalog read-only runtime probe",
        category: "llm_control",
        status: providerList.length > 0 ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/llm/providers",
            value: { provider_count: providerList.length },
          },
        ],
        findings: providerList.length > 0 ? [] : ["provider catalog response did not contain providers"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "llm_provider_catalog_runtime_probe",
        title: "LLM provider catalog read-only runtime probe",
        category: "llm_control",
        status: "WARN",
        required: false,
        evidence: [{ type: "api", ref: "/v2/llm/providers" }],
        findings: [String(error)],
      }),
    );
  }

  try {
    const [migration, memos] = await Promise.all([
      requestJson<JsonRecord>(page, "/v2/runtime/migration/status"),
      requestJson<JsonRecord>(page, "/v2/memos/list"),
    ]);
    const migrationRecord = asRecord(migration);
    const memoItems = Array.isArray(asRecord(memos).items) ? asRecord(memos).items : [];
    const pass = Object.keys(migrationRecord).length > 0 && Array.isArray(memoItems);
    probes.push(
      makeProbe({
        id: "runtime_storage_readonly_control_plane_probe",
        title: "Runtime storage migration and memos projection read-only probe",
        category: "runtime_storage",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/v2/runtime/migration/status",
            value: {
              version: migrationRecord.version,
              archived_counts: migrationRecord.archived_counts,
              strict_mode: migrationRecord.strict_mode,
            },
          },
          { type: "api", ref: "/v2/memos/list", value: { item_count: memoItems.length } },
        ],
        findings: pass ? [] : ["runtime migration or memos response was not structured"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "runtime_storage_readonly_control_plane_probe",
        title: "Runtime storage migration and memos projection read-only probe",
        category: "runtime_storage",
        status: "WARN",
        required: false,
        evidence: [
          { type: "api", ref: "/v2/runtime/migration/status" },
          { type: "api", ref: "/v2/memos/list" },
        ],
        findings: [String(error)],
      }),
    );
  }

  try {
    const metricsText = await requestText(page, "/metrics");
    const pass = metricsText.includes("polaris_requests_total");
    probes.push(
      makeProbe({
        id: "prometheus_metrics_runtime_probe",
        title: "Prometheus metrics endpoint runtime probe",
        category: "observability",
        status: pass ? "PASS" : "WARN",
        required: false,
        evidence: [
          {
            type: "api",
            ref: "/metrics",
            value: {
              chars: metricsText.length,
              has_polaris_requests_total: pass,
              has_task_market_metrics: metricsText.includes("task_market"),
            },
          },
        ],
        findings: pass ? [] : ["metrics endpoint did not include polaris_requests_total"],
      }),
    );
  } catch (error) {
    probes.push(
      makeProbe({
        id: "prometheus_metrics_runtime_probe",
        title: "Prometheus metrics endpoint runtime probe",
        category: "observability",
        status: "WARN",
        required: false,
        evidence: [{ type: "api", ref: "/metrics" }],
        findings: [String(error)],
      }),
    );
  }

  return probes;
}

export function collectE2eRuntimeIsolationProbe(workspace: string, runtimeRoot: string): EvidenceProbe {
  const workspacePath = workspace ? path.resolve(workspace) : "";
  const runtimeRootPath = runtimeRoot ? path.resolve(runtimeRoot) : "";
  const repoPath = path.resolve(repoRoot);
  const workspaceOutsideRepo = Boolean(workspacePath) && !isPathInsideOrSame(workspacePath, repoPath);
  const runtimeRootOutsideRepo = Boolean(runtimeRootPath) && !isPathInsideOrSame(runtimeRootPath, repoPath);
  const runtimeRootDistinct = Boolean(workspacePath && runtimeRootPath) && workspacePath !== runtimeRootPath;
  const pass = workspaceOutsideRepo && runtimeRootOutsideRepo && runtimeRootDistinct;

  return makeProbe({
    id: "e2e_runtime_isolation_probe",
    title: "E2E runtime/workspace isolation probe",
    category: "e2e",
    status: pass ? "PASS" : "WARN",
    required: false,
    evidence: [
      {
        type: "probe",
        ref: "workspace_runtime_isolation",
        value: {
          workspace: workspacePath,
          runtime_root: runtimeRootPath,
          repo_root: repoPath,
          workspace_outside_repo: workspaceOutsideRepo,
          runtime_root_outside_repo: runtimeRootOutsideRepo,
          runtime_root_distinct: runtimeRootDistinct,
        },
      },
    ],
    findings: pass ? [] : ["workspace/runtime_root are not isolated from the Polaris repository"],
  });
}

export async function collectHistoryArchiveReadonlyRuntimeProbe(page: Page): Promise<EvidenceProbe> {
  try {
    const [runsResponse, taskSnapshotsResponse, ledgerProjectionResponse] = await Promise.all([
      requestJson<JsonRecord>(page, "/v2/history/runs?limit=5&source=all"),
      requestJson<JsonRecord>(page, "/v2/history/tasks/snapshots?limit=5"),
      requestJson<JsonRecord>(page, "/v2/control-plane/ledger/projection?max_runs=5"),
    ]);
    const runs = Array.isArray(asRecord(runsResponse).runs) ? asRecord(runsResponse).runs : null;
    const taskSnapshots = Array.isArray(asRecord(taskSnapshotsResponse).snapshots)
      ? asRecord(taskSnapshotsResponse).snapshots
      : null;
    const ledgerProjection = asRecord(ledgerProjectionResponse);
    const pass = Boolean(
      runs &&
      taskSnapshots &&
      ledgerProjection.source === "run_ledger_projection" &&
      Array.isArray(ledgerProjection.projects),
    );

    return makeProbe({
      id: "history_archive_readonly_runtime_probe",
      title: "History/archive read-only runtime probe",
      category: "archive",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "api",
          ref: "/v2/history/runs",
          value: { run_count: runs?.length ?? 0, total: asNumber(asRecord(runsResponse).total) },
        },
        {
          type: "api",
          ref: "/v2/history/tasks/snapshots",
          value: { snapshot_count: taskSnapshots?.length ?? 0, total: asNumber(asRecord(taskSnapshotsResponse).total) },
        },
        {
          type: "api",
          ref: "/v2/control-plane/ledger/projection",
          value: {
            status: String(ledgerProjection.status || ""),
            projected: asNumber(ledgerProjection.projected),
            missing: asNumber(ledgerProjection.missing),
            failed: asNumber(ledgerProjection.failed),
          },
        },
      ],
      findings: pass ? [] : ["history/archive responses did not expose the required Run Ledger projection"],
    });
  } catch (error) {
    return makeProbe({
      id: "history_archive_readonly_runtime_probe",
      title: "History/archive read-only runtime probe",
      category: "archive",
      status: "WARN",
      required: false,
      evidence: [
        { type: "api", ref: "/v2/history/runs" },
        { type: "api", ref: "/v2/history/tasks/snapshots" },
        { type: "api", ref: "/v2/control-plane/ledger/projection" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectResidentSelfLearningRuntimeProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  const workspacePath = workspace ? path.resolve(workspace) : "";
  const repoPath = path.resolve(repoRoot);
  const workspaceOutsideRepo = Boolean(workspacePath) && !isPathInsideOrSame(workspacePath, repoPath);
  const encodedWorkspace = encodeURIComponent(workspacePath);

  if (!workspaceOutsideRepo) {
    return makeProbe({
      id: "resident_self_learning_runtime_probe",
      title: "Resident self-learning runtime tick probe",
      category: "resident",
      status: "WARN",
      required: false,
      evidence: [
        {
          type: "probe",
          ref: "resident_tick_workspace_guard",
          value: {
            workspace: workspacePath,
            repo_root: repoPath,
            workspace_outside_repo: workspaceOutsideRepo,
          },
        },
      ],
      findings: ["resident tick probe skipped because workspace is not isolated from the Polaris repository"],
    });
  }

  try {
    const before = asRecord(
      await requestJson<JsonRecord>(page, `/v2/resident/status?details=true&workspace=${encodedWorkspace}`),
    );
    const beforeRuntime = asRecord(before.runtime);
    const beforeTickCount = asNumber(beforeRuntime.tick_count);
    const tick = asRecord(
      await requestJson<JsonRecord>(page, "/v2/resident/tick?force=true", {
        method: "POST",
        body: { workspace: workspacePath },
      }),
    );
    const after = asRecord(
      await requestJson<JsonRecord>(page, `/v2/resident/status?details=true&workspace=${encodedWorkspace}`),
    );
    const decisions = asRecord(
      await requestJson<JsonRecord>(page, `/v2/resident/decisions?workspace=${encodedWorkspace}&limit=5`),
    );
    const afterRuntime = asRecord(after.runtime);
    const afterTickCount = asNumber(afterRuntime.tick_count);
    const counts = asRecord(after.counts);
    const agenda = asRecord(after.agenda);
    const decisionItems = Array.isArray(decisions.items) ? decisions.items : null;
    const riskRegister = Array.isArray(agenda.risk_register) ? agenda.risk_register : null;
    const pass = Boolean(
      beforeTickCount !== null &&
        afterTickCount !== null &&
        afterTickCount > beforeTickCount &&
        decisionItems &&
        riskRegister,
    );

    return makeProbe({
      id: "resident_self_learning_runtime_probe",
      title: "Resident self-learning runtime tick probe",
      category: "resident",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "api",
          ref: "/v2/resident/status",
          value: {
            before_tick_count: beforeTickCount,
            after_tick_count: afterTickCount,
            counts: {
              decisions: asNumber(counts.decisions),
              goals: asNumber(counts.goals),
              skills: asNumber(counts.skills),
              experiments: asNumber(counts.experiments),
              improvements: asNumber(counts.improvements),
            },
            risk_register_count: riskRegister?.length ?? 0,
          },
        },
        {
          type: "api",
          ref: "/v2/resident/tick",
          value: {
            forced: true,
            tick_runtime_tick_count: asNumber(asRecord(tick.runtime).tick_count),
            last_summary: asRecord(asRecord(tick.runtime).last_summary),
          },
        },
        {
          type: "api",
          ref: "/v2/resident/decisions",
          value: { decision_count: decisionItems?.length ?? 0, total: asNumber(decisions.count) },
        },
      ],
      findings: pass ? [] : ["resident tick did not advance tick_count or expose decisions/agenda projections"],
    });
  } catch (error) {
    return makeProbe({
      id: "resident_self_learning_runtime_probe",
      title: "Resident self-learning runtime tick probe",
      category: "resident",
      status: "WARN",
      required: false,
      evidence: [
        { type: "api", ref: "/v2/resident/status" },
        { type: "api", ref: "/v2/resident/tick" },
        { type: "api", ref: "/v2/resident/decisions" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectResidentGoalPmBridgeRuntimeProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  const workspacePath = workspace ? path.resolve(workspace) : "";
  const repoPath = path.resolve(repoRoot);
  const workspaceOutsideRepo = Boolean(workspacePath) && !isPathInsideOrSame(workspacePath, repoPath);
  const encodedWorkspace = encodeURIComponent(workspacePath);

  if (!workspaceOutsideRepo) {
    return makeProbe({
      id: "resident_goal_pm_bridge_runtime_probe",
      title: "Resident governed goal PM bridge runtime probe",
      category: "resident",
      status: "WARN",
      required: false,
      evidence: [
        {
          type: "probe",
          ref: "resident_goal_workspace_guard",
          value: {
            workspace: workspacePath,
            repo_root: repoPath,
            workspace_outside_repo: workspaceOutsideRepo,
          },
        },
      ],
      findings: ["resident goal PM bridge probe skipped because workspace is not isolated from the Polaris repository"],
    });
  }

  try {
    const uniqueTitle = `E2E runtime PM bridge proof ${Date.now()}`;
    const goal = asRecord(
      await requestJson<JsonRecord>(page, "/v2/resident/goals", {
        method: "POST",
        body: {
          workspace: workspacePath,
          goal_type: "maintenance",
          title: uniqueTitle,
          motivation: "Runtime proof for governed Resident goal PM bridge.",
          source: "e2e_runtime_probe",
          expected_value: 0.7,
          risk_score: 0.1,
          scope: ["src/backend/polaris/tests/electron"],
          budget: { max_tasks: 2, max_parallel_tasks: 1 },
          evidence_refs: ["test-results/electron/web-entry-expanded-tech-evidence-matrix.json"],
          derived_from: ["expanded-tech-evidence-matrix"],
        },
      }),
    );
    const goalId = asString(goal.goal_id);
    if (!goalId) {
      throw new Error("resident goal creation did not return goal_id");
    }

    const approved = asRecord(
      await requestJson<JsonRecord>(page, `/v2/resident/goals/${encodeURIComponent(goalId)}/approve`, {
        method: "POST",
        body: { workspace: workspacePath, note: "E2E runtime proof approval" },
      }),
    );
    const staged = asRecord(
      await requestJson<JsonRecord>(page, `/v2/resident/goals/${encodeURIComponent(goalId)}/stage`, {
        method: "POST",
        body: { workspace: workspacePath, promote_to_pm_runtime: true },
      }),
    );
    const execution = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/v2/resident/goals/${encodeURIComponent(goalId)}/execution?workspace=${encodedWorkspace}`,
      ),
    );
    const artifacts = asRecord(staged.artifacts);
    const pmRun = asRecord(staged.pm_run);
    const pmRunMetadata = asRecord(pmRun.metadata);
    const pass = Boolean(
      asString(approved.status) === "approved" &&
        asString(staged.goal_id) === goalId &&
        asString(artifacts.pm_contract_path) &&
        asString(artifacts.pm_plan_path) &&
        asString(artifacts.backup_manifest_path) &&
        asString(pmRunMetadata.resident_goal_id) === goalId &&
        asString(execution.goal_id) === goalId &&
        asString(execution.stage) &&
        asNumber(execution.percent) !== null,
    );

    return makeProbe({
      id: "resident_goal_pm_bridge_runtime_probe",
      title: "Resident governed goal PM bridge runtime probe",
      category: "resident",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "api",
          ref: "/v2/resident/goals",
          value: {
            goal_id: goalId,
            created_status: asString(goal.status),
            title: uniqueTitle,
          },
        },
        {
          type: "api",
          ref: "/v2/resident/goals/{id}/approve",
          value: { goal_id: goalId, status: asString(approved.status) },
        },
        {
          type: "api",
          ref: "/v2/resident/goals/{id}/stage",
          value: {
            goal_id: asString(staged.goal_id),
            promoted_to_pm_runtime: Boolean(staged.promoted_to_pm_runtime),
            resident_contract_path: asString(artifacts.resident_contract_path),
            resident_plan_path: asString(artifacts.resident_plan_path),
            pm_contract_path: asString(artifacts.pm_contract_path),
            pm_plan_path: asString(artifacts.pm_plan_path),
            backup_manifest_path: asString(artifacts.backup_manifest_path),
            pm_run_metadata: pmRunMetadata,
          },
        },
        {
          type: "api",
          ref: "/v2/resident/goals/{id}/execution",
          value: {
            goal_id: asString(execution.goal_id),
            stage: asString(execution.stage),
            percent: asNumber(execution.percent),
            total_tasks: asNumber(execution.total_tasks),
          },
        },
      ],
      findings: pass ? [] : ["resident goal PM bridge did not expose staged PM artifacts and execution projection"],
    });
  } catch (error) {
    return makeProbe({
      id: "resident_goal_pm_bridge_runtime_probe",
      title: "Resident governed goal PM bridge runtime probe",
      category: "resident",
      status: "WARN",
      required: false,
      evidence: [
        { type: "api", ref: "/v2/resident/goals" },
        { type: "api", ref: "/v2/resident/goals/{id}/approve" },
        { type: "api", ref: "/v2/resident/goals/{id}/stage" },
        { type: "api", ref: "/v2/resident/goals/{id}/execution" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectLlmInterviewSaveRuntimeProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  const workspacePath = workspace ? path.resolve(workspace) : "";
  const repoPath = path.resolve(repoRoot);
  const workspaceOutsideRepo = Boolean(workspacePath) && !isPathInsideOrSame(workspacePath, repoPath);

  if (!workspaceOutsideRepo) {
    return makeProbe({
      id: "llm_interview_save_runtime_probe",
      title: "LLM interview save/readiness index runtime probe",
      category: "llm_control",
      status: "WARN",
      required: false,
      evidence: [
        {
          type: "probe",
          ref: "llm_interview_save_workspace_guard",
          value: {
            workspace: workspacePath,
            repo_root: repoPath,
            workspace_outside_repo: workspaceOutsideRepo,
          },
        },
      ],
      findings: ["LLM interview save probe skipped because workspace is not isolated from the Polaris repository"],
    });
  }

  try {
    const sessionId = `e2e-interview-${Date.now()}`;
    const role = "e2e_probe";
    const providerId = "e2e-provider";
    const model = "e2e-model";
    const saved = asRecord(
      await requestJson<JsonRecord>(page, "/v2/llm/interview/save", {
        method: "POST",
        body: {
          role,
          provider_id: providerId,
          model,
          session_id: sessionId,
          report: {
            id: sessionId,
            overallStatus: "PASS",
            target: {
              role,
              provider_id: providerId,
              model,
            },
            final: {
              ready: true,
              grade: "PASS",
              next_action: "proceed",
            },
            summary: {
              ready: true,
              grade: "PASS",
              source: "e2e_interview_save_runtime_probe",
            },
            suites: {
              interview: { ok: true },
            },
            evaluation: {
              passed: true,
            },
          },
        },
      }),
    );
    const reportPath = asString(saved.report_path);
    const report = reportPath ? await readJsonIfExists<JsonRecord>(reportPath) : null;
    const layout = asRecord(await requestJson<JsonRecord>(page, "/v2/runtime/storage/layout"));
    const layoutPaths = asRecord(layout.paths);
    const workspaceIndexPath = path.join(workspacePath, ".polaris", "llm_test_index.json");
    const globalIndexPath = asString(layoutPaths.global_llm_test_index || layoutPaths.llm_test_index);
    const indexPaths = Array.from(new Set([workspaceIndexPath, globalIndexPath].filter(Boolean)));
    const indexes = await Promise.all(
      indexPaths.map(async (indexPath) => ({
        path: indexPath,
        payload: await readJsonIfExists<JsonRecord>(indexPath),
      })),
    );
    const indexedProviders = indexes.map(({ path: indexPath, payload }) => {
      const provider = asRecord(asRecord(payload).providers ? asRecord(asRecord(payload).providers)[providerId] : {});
      return {
        path: indexPath,
        exists: Boolean(payload),
        provider_last_run_id: asString(provider.last_run_id),
        provider_role: asString(provider.role),
        provider_model: asString(provider.model),
        provider_ready: Boolean(provider.ready),
      };
    });
    const mirrored =
      indexedProviders.length >= 2 &&
      indexedProviders.every((row) => row.provider_last_run_id === sessionId && row.provider_role === role);
    const pass = Boolean(
      saved.saved === true &&
        saved.readiness_updated === true &&
        report &&
        asString(asRecord(report).test_run_id) === sessionId &&
        mirrored,
    );

    return makeProbe({
      id: "llm_interview_save_runtime_probe",
      title: "LLM interview save/readiness index runtime probe",
      category: "llm_control",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "api",
          ref: "/v2/llm/interview/save",
          value: {
            saved: Boolean(saved.saved),
            report_path: reportPath,
            readiness_updated: Boolean(saved.readiness_updated),
            role_readiness_updated: Boolean(saved.role_readiness_updated),
            session_id: sessionId,
          },
        },
        {
          type: "runtime_artifact",
          ref: reportPath,
          value: {
            exists: Boolean(report),
            test_run_id: asString(asRecord(report).test_run_id),
            role: asString(asRecord(report).role),
            provider_id: asString(asRecord(report).provider_id),
            model: asString(asRecord(report).model),
          },
        },
        {
          type: "runtime_artifact",
          ref: "llm_test_index_dual_mirror",
          value: { indexed_providers: indexedProviders },
        },
      ],
      findings: pass ? [] : ["LLM interview save did not update both readiness index mirrors for the saved run"],
    });
  } catch (error) {
    return makeProbe({
      id: "llm_interview_save_runtime_probe",
      title: "LLM interview save/readiness index runtime probe",
      category: "llm_control",
      status: "WARN",
      required: false,
      evidence: [
        { type: "api", ref: "/v2/llm/interview/save" },
        { type: "runtime_artifact", ref: "llm_test_index_dual_mirror" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectRoleSessionAuditExportRuntimeProbe(
  page: Page,
  workspace: string,
  runtimeRoot: string,
): Promise<EvidenceProbe> {
  const workspacePath = workspace ? path.resolve(workspace) : "";
  const runtimeRootPath = runtimeRoot ? path.resolve(runtimeRoot) : "";
  const repoPath = path.resolve(repoRoot);
  const workspaceOutsideRepo = Boolean(workspacePath) && !isPathInsideOrSame(workspacePath, repoPath);

  if (!workspaceOutsideRepo || !runtimeRootPath) {
    return makeProbe({
      id: "role_session_audit_export_runtime_probe",
      title: "Role-session audit export and Kernel audit chain runtime probe",
      category: "audit",
      status: "WARN",
      required: false,
      evidence: [
        {
          type: "probe",
          ref: "role_session_audit_workspace_guard",
          value: {
            workspace: workspacePath,
            runtime_root: runtimeRootPath,
            repo_root: repoPath,
            workspace_outside_repo: workspaceOutsideRepo,
          },
        },
      ],
      findings: ["role-session audit export probe skipped because workspace/runtime_root is not isolated"],
    });
  }

  try {
    const marker = `e2e-role-session-audit-${Date.now()}`;
    const created = asRecord(
      await requestJson<JsonRecord>(page, "/v2/roles/sessions", {
        method: "POST",
        body: {
          role: "pm",
          host_kind: "electron_workbench",
          workspace: workspacePath,
          session_type: "workbench",
          attachment_mode: "isolated",
          title: marker,
          context_config: { source: "expanded_tech_evidence_matrix" },
          capability_profile: { audit: 1 },
        },
      }),
    );
    const session = asRecord(created.session);
    const sessionId = asString(session.id);
    if (!sessionId) {
      throw new Error("role session creation did not return session.id");
    }

    const appended = asRecord(
      await requestJson<JsonRecord>(page, `/v2/roles/sessions/${encodeURIComponent(sessionId)}/audit/events`, {
        method: "POST",
        body: {
          event_type: "message_sent",
          details: {
            marker,
            message_id: `${marker}-message`,
            source: "expanded_tech_evidence_matrix",
          },
        },
      }),
    );
    const auditLog = asRecord(
      await requestJson<JsonRecord>(page, `/v2/roles/sessions/${encodeURIComponent(sessionId)}/audit?limit=20`),
    );
    const exported = asRecord(
      await requestJson<JsonRecord>(page, `/v2/roles/sessions/${encodeURIComponent(sessionId)}/audit/export`, {
        method: "POST",
      }),
    );
    const exportPath = asString(exported.export_path);
    const exportPayload = exportPath ? await readJsonIfExists<JsonRecord>(exportPath) : null;
    const exportedEvents = Array.isArray(asRecord(exportPayload).events) ? asRecord(exportPayload).events : [];
    const auditJsonlPaths = await listRuntimeAuditJsonlPaths(runtimeRootPath);
    const auditJsonlEntries = await readJsonlFileEntries(auditJsonlPaths, 500);
    const kernelEvent = findRoleSessionKernelAuditEntry(auditJsonlEntries, sessionId);
    const rawKernelEvent = asRecord(kernelEvent?.rawEvent);
    const pass = Boolean(
      asString(appended.event ? asRecord(appended.event).type : "") === "message_sent" &&
        Array.isArray(auditLog.audit_events) &&
        asNumber(auditLog.total) !== null &&
        exported.event_count === exportedEvents.length &&
        exportedEvents.some((event) => asString(asRecord(event).type) === "message_sent") &&
        asString(rawKernelEvent.event_id) &&
        asString(rawKernelEvent.prev_hash) &&
        asString(rawKernelEvent.signature),
    );

    return makeProbe({
      id: "role_session_audit_export_runtime_probe",
      title: "Role-session audit export and Kernel audit chain runtime probe",
      category: "audit",
      status: pass ? "PASS" : "WARN",
      required: false,
      evidence: [
        {
          type: "api",
          ref: "/v2/roles/sessions",
          value: { session_id: sessionId, title: asString(session.title), workspace: asString(session.workspace) },
        },
        {
          type: "api",
          ref: "/v2/roles/sessions/{id}/audit/events",
          value: { event: appended.event },
        },
        {
          type: "api",
          ref: "/v2/roles/sessions/{id}/audit",
          value: { total: asNumber(auditLog.total), event_count: Array.isArray(auditLog.audit_events) ? auditLog.audit_events.length : 0 },
        },
        {
          type: "api",
          ref: "/v2/roles/sessions/{id}/audit/export",
          value: { export_path: exportPath, event_count: asNumber(exported.event_count) },
        },
        {
          type: "runtime_artifact",
          ref: exportPath,
          value: {
            exists: Boolean(exportPayload),
            session_id: asString(asRecord(exportPayload).session_id),
            event_count: asNumber(asRecord(exportPayload).event_count),
          },
        },
        {
          type: "event_jsonl",
          ref: kernelEvent?.sourcePath || path.join(runtimeRootPath, "audit"),
          value: {
            matched: Boolean(kernelEvent),
            event_id: asString(rawKernelEvent.event_id),
            prev_hash: asString(rawKernelEvent.prev_hash),
            signature_present: Boolean(asString(rawKernelEvent.signature)),
            event_type: asString(rawKernelEvent.event_type),
            canonical_wrapped: Boolean(kernelEvent?.canonicalWrapped),
            scanned_files: auditJsonlPaths,
          },
        },
      ],
      findings: pass
        ? []
        : ["role-session audit append/export did not produce both exported audit log and Kernel hash-chain evidence"],
    });
  } catch (error) {
    return makeProbe({
      id: "role_session_audit_export_runtime_probe",
      title: "Role-session audit export and Kernel audit chain runtime probe",
      category: "audit",
      status: "WARN",
      required: false,
      evidence: [
        { type: "api", ref: "/v2/roles/sessions" },
        { type: "api", ref: "/v2/roles/sessions/{id}/audit/events" },
        { type: "api", ref: "/v2/roles/sessions/{id}/audit/export" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectAggregateRuntimePlanProbe(
  page: Page,
  workspace: string,
): Promise<{ probe: EvidenceProbe; core: ExpandedTechEvidenceReport["core_runtime_integrations"] }> {
  const emptyCore = {
    expected_count: CORE_TECH_IDS.length,
    actual_count: 0,
    entrypoints_verified_count: 0,
    missing_ids: [...CORE_TECH_IDS],
    unexpected_ids: [],
  };
  try {
    const body = asRecord(
      await requestJson<JsonRecord>(page, "/v1/chat/completions", {
        method: "POST",
        body: {
          model: "polaris.aggregate_llm.v1",
          workspace,
          messages: [
            {
              role: "user",
              content: "Audit aggregate runtime wiring without executing a model turn.",
            },
          ],
          domain: "code",
          failure_signal: "e2e_expanded_matrix_probe",
          execution_mode: "plan_only",
        },
      }),
    );
    const directPlan = asRecord(body.aggregate_plan);
    const choices = Array.isArray(body.choices) ? body.choices : [];
    const choiceMessage = asRecord(asRecord(choices[0]).message);
    let contentPlan: JsonRecord = {};
    try {
      contentPlan = asRecord(JSON.parse(asString(choiceMessage.content)));
    } catch {
      contentPlan = {};
    }
    const runtimeIntegrations = asRecords(directPlan.runtime_integrations || contentPlan.runtime_integrations);
    const ids = runtimeIntegrations.map((item) => asString(item.tech_id)).filter(Boolean);
    const uniqueIds = Array.from(new Set(ids));
    const missingIds = CORE_TECH_IDS.filter((techId) => !uniqueIds.includes(techId));
    const unexpectedIds = uniqueIds.filter((techId) => !CORE_TECH_IDS.includes(techId));
    const entrypointsVerifiedCount = runtimeIntegrations.filter((item) => item.entrypoints_verified === true).length;
    const core = {
      expected_count: CORE_TECH_IDS.length,
      actual_count: uniqueIds.length,
      entrypoints_verified_count: entrypointsVerifiedCount,
      missing_ids: missingIds,
      unexpected_ids: unexpectedIds,
    };
    const status = missingIds.length === 0 && entrypointsVerifiedCount >= CORE_TECH_IDS.length ? "PASS" : "FAIL";
    return {
      core,
      probe: makeProbe({
        id: "aggregate_runtime_integrations_plan_only",
        title: "Aggregate runtime integrations plan-only HTTP probe",
        category: "aggregate_runtime",
        status,
        required: true,
        evidence: [
          {
            type: "api",
            ref: "/v1/chat/completions",
            value: core,
          },
        ],
        findings:
          status === "PASS"
            ? []
            : [
                `missing core ids: ${missingIds.join(", ") || "(none)"}`,
                `entrypoints_verified_count=${entrypointsVerifiedCount}`,
              ],
      }),
    };
  } catch (error) {
    return {
      core: emptyCore,
      probe: makeProbe({
        id: "aggregate_runtime_integrations_plan_only",
        title: "Aggregate runtime integrations plan-only HTTP probe",
        category: "aggregate_runtime",
        status: "FAIL",
        required: true,
        evidence: [{ type: "api", ref: "/v1/chat/completions" }],
        findings: [String(error)],
      }),
    };
  }
}

export async function collectCognitiveRuntimeRoundtripProbe(page: Page, workspace: string): Promise<EvidenceProbe> {
  const unique = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const sessionId = `e2e-expanded-matrix-${unique}`;
  const runId = `e2e-expanded-matrix-run-${unique}`;
  const turnEnvelope = {
    turn_id: `turn-${unique}`,
    session_id: sessionId,
    run_id: runId,
    role: "qa",
    task_id: "e2e::expanded_tech_matrix",
    projection_version: "expanded-tech-evidence-matrix.v1",
    state_version: 1,
  };

  try {
    const receiptResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/runtime-receipts", {
        method: "POST",
        body: {
          workspace,
          receipt_type: "e2e_expanded_tech_matrix_probe",
          session_id: sessionId,
          run_id: runId,
          trace_refs: ["e2e:expanded_tech_evidence_matrix"],
          payload: {
            source: "electron_e2e.expanded_tech_evidence_matrix",
            core_tech_expected_count: CORE_TECH_IDS.length,
          },
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const receipt = asRecord(receiptResponse.receipt);
    const receiptId = asString(receipt.receipt_id);
    if (!receiptId) {
      throw new Error("runtime receipt did not return receipt_id");
    }

    const fetchedReceipt = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/runtime-receipts/${encodeURIComponent(receiptId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedReceiptId = asString(asRecord(fetchedReceipt.receipt).receipt_id);

    const handoffResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/export", {
        method: "POST",
        body: {
          workspace,
          session_id: sessionId,
          run_id: runId,
          reason: "expanded_tech_evidence_matrix_roundtrip",
          receipt_limit: 5,
          turn_envelope: turnEnvelope,
        },
      }),
    );
    const handoff = asRecord(handoffResponse.handoff);
    const handoffId = asString(handoff.handoff_id);
    if (!handoffId) {
      throw new Error("handoff export did not return handoff_id");
    }

    const fetchedHandoff = asRecord(
      await requestJson<JsonRecord>(
        page,
        `/cognitive-runtime/handoffs/${encodeURIComponent(handoffId)}?workspace=${encodeURIComponent(workspace)}`,
      ),
    );
    const fetchedHandoffId = asString(asRecord(fetchedHandoff.handoff).handoff_id);

    const rehydrationResponse = asRecord(
      await requestJson<JsonRecord>(page, "/cognitive-runtime/handoffs/rehydrate", {
        method: "POST",
        body: {
          workspace,
          handoff_id: handoffId,
          target_role: "director",
          target_session_id: `${sessionId}-rehydrated`,
        },
      }),
    );
    const rehydration = asRecord(rehydrationResponse.rehydration);
    const rehydrationId = asString(rehydration.rehydration_id);
    const receiptRefs = Array.isArray(handoff.receipt_refs) ? handoff.receipt_refs.map(String) : [];
    const status =
      fetchedReceiptId === receiptId &&
      fetchedHandoffId === handoffId &&
      Boolean(rehydrationId) &&
      receiptRefs.includes(receiptId)
        ? "PASS"
        : "FAIL";

    return makeProbe({
      id: "cognitive_runtime_receipt_handoff_roundtrip",
      title: "Cognitive Runtime receipt/handoff/rehydrate HTTP roundtrip",
      category: "cognitive_runtime",
      status,
      required: true,
      evidence: [
        { type: "api", ref: "/cognitive-runtime/runtime-receipts", value: { receipt_id: receiptId } },
        { type: "api", ref: "/cognitive-runtime/handoffs/export", value: { handoff_id: handoffId, receipt_refs: receiptRefs } },
        { type: "api", ref: "/cognitive-runtime/handoffs/rehydrate", value: { rehydration_id: rehydrationId } },
      ],
      findings: status === "PASS" ? [] : ["roundtrip identifiers did not line up"],
    });
  } catch (error) {
    return makeProbe({
      id: "cognitive_runtime_receipt_handoff_roundtrip",
      title: "Cognitive Runtime receipt/handoff/rehydrate HTTP roundtrip",
      category: "cognitive_runtime",
      status: "FAIL",
      required: true,
      evidence: [
        { type: "api", ref: "/cognitive-runtime/runtime-receipts" },
        { type: "api", ref: "/cognitive-runtime/handoffs/export" },
        { type: "api", ref: "/cognitive-runtime/handoffs/rehydrate" },
      ],
      findings: [String(error)],
    });
  }
}

export async function collectRuntimeArtifactRefs(runtimeRoot: string): Promise<EvidenceRef[]> {
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

  const refs: EvidenceRef[] = [
    {
      type: "probe",
      ref: "expanded_tech_evidence_matrix.core_runtime_evidence_placement",
    },
  ];
  for (const basename of basenames) {
    const filePath = await newestFile(byName.get(basename) || []);
    if (!filePath) {
      continue;
    }
    refs.push({ type: "runtime_artifact", ref: filePath });
  }
  return refs;
}
