import { promises as fs } from "node:fs";
import path from "node:path";
import { type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

type BackendInfo = {
  baseUrl?: string;
  token?: string;
};

type SettingsPayload = {
  workspace?: string;
  pm_runs_director?: boolean;
  pm_model?: string;
  director_model?: string;
};

type PmStatusPayload = {
  running?: boolean;
  pid?: number | null;
  status?: string | null;
  terminal?: boolean;
  ok?: boolean | null;
  exit_code?: number | null;
  error?: string | null;
  execution_id?: string | null;
  log_path?: string | null;
  contract_path?: string | null;
  contract_exists?: boolean;
};

type SnapshotPayload = {
  tasks?: unknown[];
  pm_state?: Record<string, unknown> | null;
};

type RuntimeLayoutPayload = {
  runtime_root?: string;
};

type LlmStatusPayload = {
  state?: string;
  blocked_roles?: unknown[];
  required_ready_roles?: unknown[];
  roles?: Record<string, {
    ready?: boolean;
    readiness_issue?: string;
    provider_id?: string;
    model?: string;
    tested_provider_id?: string;
    tested_model?: string;
  }>;
};

type IntegrationQaArtifact = {
  reason?: string;
  summary?: string;
  ran?: boolean;
  passed?: boolean | null;
};

type DirectorTaskPayload = {
  metadata?: {
    pm_task_id?: string;
  };
};

type PmContractPayload = {
  terminal_error_code?: string;
  terminal_error?: string;
  schema_warnings?: unknown[];
  tasks?: unknown[];
};

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function getBackendInfo(window: Page): Promise<Required<BackendInfo>> {
  const info = await window.evaluate(async () => {
    const api = (window as Window & {
      polaris?: { getBackendInfo?: () => Promise<BackendInfo> };
    }).polaris;
    if (!api?.getBackendInfo) {
      throw new Error("polaris.getBackendInfo missing");
    }
    return await api.getBackendInfo();
  });

  if (!info?.baseUrl || !info?.token) {
    throw new Error("backend info missing");
  }

  return {
    baseUrl: info.baseUrl,
    token: info.token,
  };
}

async function fetchJson<T>(window: Page, endpoint: string): Promise<T> {
  const backend = await getBackendInfo(window);
  return window.evaluate(
    async ({ baseUrl, token, path }) => {
      const response = await fetch(`${baseUrl}${path}`, {
        cache: "no-store",
        headers: {
          authorization: `Bearer ${token}`,
          "Cache-Control": "no-store",
          Pragma: "no-cache",
        },
      });
      if (!response.ok) {
        throw new Error(`fetch ${path} failed: ${response.status}`);
      }
      return (await response.json()) as unknown;
    },
    { baseUrl: backend.baseUrl, token: backend.token, path: endpoint },
  ) as Promise<T>;
}

async function readJsonFile<T>(filePath: string): Promise<T | null> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

async function writeUtf8File(filePath: string, content: string): Promise<void> {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  await fs.writeFile(filePath, content.endsWith("\n") ? content : `${content}\n`, "utf-8");
}

function roleList(value: unknown[] | undefined): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item).trim().toLowerCase()).filter(Boolean)
    : [];
}

function llmBlockedReason(llmStatus: LlmStatusPayload | null, role = "pm"): string {
  if (!llmStatus || String(llmStatus.state || "").trim().toUpperCase() !== "BLOCKED") {
    return "";
  }

  const blockedRoles = roleList(llmStatus.blocked_roles);
  const requiredRoles = roleList(llmStatus.required_ready_roles);
  if (!blockedRoles.includes(role) || (requiredRoles.length > 0 && !requiredRoles.includes(role))) {
    return "";
  }

  const roleStatus = llmStatus.roles?.[role];
  const issue = String(roleStatus?.readiness_issue || "").trim();
  const configured = [roleStatus?.provider_id, roleStatus?.model].filter(Boolean).join("/");
  const tested = [roleStatus?.tested_provider_id, roleStatus?.tested_model].filter(Boolean).join("/");
  return [
    `LLM readiness blocked role=${role}`,
    issue ? `issue=${issue}` : "",
    configured ? `configured=${configured}` : "",
    tested ? `tested=${tested}` : "",
  ].filter(Boolean).join(" ");
}

function pmTerminalFailureReason(status: PmStatusPayload | null): string {
  if (!status) return "";
  const exitCode = typeof status.exit_code === "number" ? status.exit_code : null;
  if (exitCode !== null && exitCode !== 0) {
    return `pm_process_exit_code_${exitCode}`;
  }
  if (status.ok === false) {
    return "pm_process_result_not_ok";
  }
  if (status.terminal && String(status.status || "").trim().toLowerCase() === "failed") {
    return "pm_process_terminal_failed";
  }
  const error = String(status.error || "").trim();
  return error ? `pm_process_error:${error}` : "";
}

function pmContractFailureReason(contract: PmContractPayload | null): string {
  if (!contract || typeof contract !== "object") return "";
  const terminalCode = String(contract.terminal_error_code || "").trim();
  if (terminalCode) return terminalCode;
  const terminalError = String(contract.terminal_error || "").trim();
  if (terminalError) return "pm_contract_terminal_error";
  const serialized = JSON.stringify(contract).toLowerCase();
  if (
    serialized.includes("pm_llm_invoke_failed")
    || serialized.includes("pm_llm_fallback_applied")
    || serialized.includes("fallback_from_failure")
    || serialized.includes("original pm failure/context")
  ) {
    return "pm_contract_contains_llm_failure_evidence";
  }
  return "";
}

function resolvePmContractPath(status: PmStatusPayload, runtimeRoot: string): string {
  const fromStatus = String(status.contract_path || "").trim();
  return fromStatus || path.join(runtimeRoot, "contracts", "pm_tasks.contract.json");
}

async function dismissEngineFailureDialog(window: Page): Promise<void> {
  const dialog = window.getByRole("alertdialog", { name: "Polaris 引擎执行失败" });
  const closeButton = dialog.getByRole("button", { name: "关闭" });

  if (await closeButton.isVisible().catch(() => false)) {
    await closeButton.click();
    await expect(dialog).toBeHidden({ timeout: 10_000 });
  }
}

async function clickAndWaitForPmStart(window: Page): Promise<PmStatusPayload> {
  await window.getByTestId("pm-workspace-run-once").click();

  const deadline = Date.now() + 60_000;
  let lastStatus: PmStatusPayload | null = null;
  let lastLlmStatus: LlmStatusPayload | null = null;
  while (Date.now() < deadline) {
    lastStatus = await fetchJson<PmStatusPayload>(window, "/v2/pm/status");
    if (lastStatus.running) {
      return lastStatus;
    }

    lastLlmStatus = await fetchJson<LlmStatusPayload>(window, "/v2/llm/status").catch(() => null);
    const llmReason = llmBlockedReason(lastLlmStatus, "pm");
    if (llmReason) {
      throw new Error(
        `PM did not start because the LLM readiness gate is blocked: ${llmReason}; `
        + `pm_status=${JSON.stringify(lastStatus)}`,
      );
    }

    const terminalReason = pmTerminalFailureReason(lastStatus);
    if (terminalReason) {
      throw new Error(
        `PM reached a terminal failure before reporting running=true: ${terminalReason}; `
        + `pm_status=${JSON.stringify(lastStatus)}`,
      );
    }

    await sleep(500);
  }

  throw new Error(
    `Timed out waiting for PM to start; `
    + `last_pm_status=${JSON.stringify(lastStatus)} `
    + `last_llm_status=${JSON.stringify({
      state: lastLlmStatus?.state,
      blocked_roles: lastLlmStatus?.blocked_roles,
      required_ready_roles: lastLlmStatus?.required_ready_roles,
    })}`,
  );
}

async function waitForPmFinish(window: Page): Promise<PmStatusPayload> {
  await expect
    .poll(async () => {
      const status = await fetchJson<PmStatusPayload>(window, "/v2/pm/status");
      return Boolean(status.running);
    }, {
      timeout: 20 * 60 * 1000,
      intervals: [1000, 2000, 5000, 10000],
    })
    .toBe(false);

  return fetchJson<PmStatusPayload>(window, "/v2/pm/status");
}

async function waitForSnapshotTasks(window: Page): Promise<SnapshotPayload> {
  await expect
    .poll(async () => {
      const snapshot = await fetchJson<SnapshotPayload>(window, "/state/snapshot");
      return Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0;
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBeGreaterThan(0);

  return fetchJson<SnapshotPayload>(window, "/state/snapshot");
}

async function enterPmWorkspace(window: Page): Promise<void> {
  const directEntry = window.getByTestId("enter-pm-workspace");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  const menuEntry = window.getByTestId("enter-pm-workspace");
  if (await menuEntry.isVisible().catch(() => false)) {
    await menuEntry.click();
    return;
  }
  await window.getByRole("menuitem", { name: /PM\s*(工作区|Workspace)/i }).click();
}

async function enterDirectorWorkspace(window: Page): Promise<void> {
  const directEntry = window.getByTestId("enter-director-workspace");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }

  await window.getByRole("button", { name: /更多功能/ }).click();
  const menuEntry = window.getByTestId("enter-director-workspace");
  if (await menuEntry.isVisible().catch(() => false)) {
    await menuEntry.click();
    return;
  }
  await window.getByRole("menuitem", { name: /Director\s*(工作区|Workspace)/i }).click();
}

test.setTimeout(25 * 60 * 1000);

test("real PM -> Director flow reaches PM and Director workspaces", async ({ window, testEnv }, testInfo) => {
  test.skip(!testEnv.useRealSettings, "Set KERNELONE_E2E_USE_REAL_SETTINGS=1 to use real configured LLM settings.");

  const settings = await fetchJson<SettingsPayload>(window, "/settings");
  expect(String(settings.workspace || "").trim(), "real settings workspace should not be empty").not.toBe("");
  expect(settings.pm_runs_director, "real settings should keep PM -> Director enabled").toBe(true);

  await dismissEngineFailureDialog(window);
  await enterPmWorkspace(window);
  await expect(window.getByTestId("pm-workspace")).toBeVisible();

  const initialLlmStatus = await fetchJson<LlmStatusPayload>(window, "/v2/llm/status");
  const initialLlmBlock = llmBlockedReason(initialLlmStatus, "pm");
  if (initialLlmBlock) {
    const evidencePath = testInfo.outputPath("pm-llm-readiness-block.json");
    const screenshotPath = testInfo.outputPath("pm-llm-readiness-block.png");
    await expect(window.getByTestId("pm-workspace-run-once")).toBeDisabled({ timeout: 15_000 });
    await expect(window.getByTestId("pm-runtime-terminal-banner")).toBeVisible({ timeout: 15_000 });
    await writeUtf8File(evidencePath, JSON.stringify({
      state: initialLlmStatus.state,
      blocked_roles: initialLlmStatus.blocked_roles,
      required_ready_roles: initialLlmStatus.required_ready_roles,
      roles: initialLlmStatus.roles,
    }, null, 2));
    await window.screenshot({ path: screenshotPath, fullPage: true });
    throw new Error(
      `PM start is blocked by the LLM readiness gate before any PM action: ${initialLlmBlock}; `
      + `evidence=${evidencePath} screenshot=${screenshotPath}`,
    );
  }

  await expect(window.getByTestId("pm-workspace-run-once")).toBeEnabled();

  await clickAndWaitForPmStart(window);
  await window.getByTestId("pm-workspace-back").click();
  await expect(window.getByTestId("project-progress-panel")).toBeVisible();
  const runtimeLayout = await fetchJson<RuntimeLayoutPayload>(window, "/runtime/storage-layout");
  const runtimeRoot = String(runtimeLayout.runtime_root || "").trim();
  expect(runtimeRoot, "runtime storage layout should expose a runtime root").not.toBe("");
  const pmTerminalStatus = await waitForPmFinish(window);
  const contractPath = resolvePmContractPath(pmTerminalStatus, runtimeRoot);
  const pmContract = await readJsonFile<PmContractPayload>(contractPath);
  const pmStatusReason = pmTerminalFailureReason(pmTerminalStatus);
  const pmContractReason = pmContractFailureReason(pmContract);
  if (pmStatusReason || pmContractReason) {
    const evidencePath = testInfo.outputPath("pm-terminal-failure.json");
    const screenshotPath = testInfo.outputPath("pm-terminal-failure.png");
    await writeUtf8File(evidencePath, JSON.stringify({
      pm_status: pmTerminalStatus,
      contract_path: contractPath,
      contract_terminal_error_code: pmContract?.terminal_error_code || "",
      contract_terminal_error: pmContract?.terminal_error || "",
      contract_schema_warnings: pmContract?.schema_warnings || [],
      llm_status: await fetchJson<LlmStatusPayload>(window, "/v2/llm/status").catch(() => null),
    }, null, 2));
    await window.screenshot({ path: screenshotPath, fullPage: true });
    throw new Error(
      `PM failed closed before tasks were generated; `
      + `pm_status_reason=${pmStatusReason || "(none)"} `
      + `pm_contract_reason=${pmContractReason || "(none)"} `
      + `contract=${contractPath} evidence=${evidencePath} screenshot=${screenshotPath}`,
    );
  }

  const snapshot = await waitForSnapshotTasks(window);
  const taskCount = Array.isArray(snapshot.tasks) ? snapshot.tasks.length : 0;
  expect(taskCount).toBeGreaterThan(0);

  const lastDirectorStatus = String(snapshot.pm_state?.["last_director_status"] || "").trim();
  expect(lastDirectorStatus, "PM flow should write a Director result status").not.toBe("");
  expect(Number(snapshot.pm_state?.["completed_task_count"] || 0)).toBeGreaterThan(0);

  const integrationQaPath = path.join(runtimeRoot, "results", "integration_qa.result.json");
  await expect
    .poll(async () => {
      const payload = await readJsonFile<IntegrationQaArtifact>(integrationQaPath);
      return String(payload?.reason || "").trim();
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .not.toBe("");

  const integrationQa = await readJsonFile<IntegrationQaArtifact>(integrationQaPath);

  expect(
    ["pending_director_tasks", "director_failures_present", "integration_qa_passed", "integration_qa_failed"].includes(
      String(integrationQa?.reason || ""),
    ),
    `unexpected integration QA reason: ${String(integrationQa?.reason || "")}`,
  ).toBe(true);

  await dismissEngineFailureDialog(window);
  await dismissEngineFailureDialog(window);
  await enterDirectorWorkspace(window);
  await expect(window.getByTestId("director-workspace")).toBeVisible();
  await expect(window.getByTestId("director-workspace-execute")).toBeVisible();
  await expect
    .poll(async () => {
      return window.getByTestId("director-task-item").count();
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBe(taskCount);

  await expect
    .poll(async () => {
      const tasks = await fetchJson<DirectorTaskPayload[]>(window, "/v2/director/tasks?source=auto");
      return Array.isArray(tasks)
        ? tasks.filter((item) => String(item?.metadata?.pm_task_id || "").trim().length > 0).length
        : 0;
    }, {
      timeout: 60_000,
      intervals: [500, 1000, 2000, 3000],
    })
    .toBeGreaterThan(0);
});
