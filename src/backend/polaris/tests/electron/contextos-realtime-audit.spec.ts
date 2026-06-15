import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * ContextOS 实时视图 — Playwright 真机审计
 *
 * 目标：在真实 Electron 应用里证明 ContextOS 入口可达、视图可渲染、且绑定真实运行时数据
 * （而非仅在 jsdom 单测中渲染）。同时截图供人工视觉审计。
 */

const SCREENSHOT_PATH = "/tmp/contextos-audit.png";

const ignoredConsoleErrorPatterns = [
  /has been blocked by CORS policy/i,
  /Failed to load resource: net::ERR_FAILED/i,
  /Failed to load resource: net::ERR_FILE_NOT_FOUND/i,
  /TypeError: Failed to fetch/i,
  /Unable to preload CSS for \/assets\//i,
];

function getActionableConsoleErrors(errors: string[]): string[] {
  return errors.filter((error) => !ignoredConsoleErrorPatterns.some((pattern) => pattern.test(error)));
}

async function enterContextOS(window: Page): Promise<void> {
  const directEntry = window.locator("[data-testid='control-panel-enter-contextos']");
  if (await directEntry.isVisible().catch(() => false)) {
    await directEntry.click();
    return;
  }
  await window.getByRole("button", { name: /更多功能/ }).click();
  const menuEntry = window.getByTestId("enter-contextos-menu-item");
  if (await menuEntry.isVisible().catch(() => false)) {
    await menuEntry.click();
    return;
  }
  await window.getByRole("menuitem", { name: /ContextOS/i }).click();
}

test("ContextOS entry is reachable and the real-time dashboard renders", async ({ window }, testInfo) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];

  window.on("pageerror", (error) => {
    pageErrors.push(String(error));
  });
  window.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await expect(window.locator("#root")).toHaveCount(1);
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible();

  // Enter ContextOS via the ControlPanel header button (or the 更多功能 dropdown).
  await enterContextOS(window);

  // Shell renders.
  const workspace = window.locator("[data-testid='contextos-workspace']");
  await expect(workspace).toBeVisible();
  await expect(window.getByText("ContextOS 实时视图", { exact: false }).first()).toBeVisible();

  // All 8 pipeline stages are present.
  for (const id of ["request", "turflog", "working_mem", "projection", "role_signal", "budget", "prompt", "llm"]) {
    await expect(window.locator(`[data-testid='contextos-stage-${id}']`)).toBeVisible();
  }
  // 7 component-health cards.
  for (const id of ["turflog", "working_mem", "projection", "role_signal", "budget", "prompt", "telemetry"]) {
    await expect(window.locator(`[data-testid='contextos-component-${id}']`)).toBeVisible();
  }
  // 4 role cards.
  for (const id of ["pm", "architect", "director", "qa"]) {
    await expect(window.locator(`[data-testid='contextos-role-${id}']`)).toBeVisible();
  }

  // Role-tab cross-filter is interactive in the real app.
  await window.locator("[data-testid='contextos-roletab-pm']").click();
  await expect(window.locator("[data-testid='contextos-roletab-pm']")).toHaveAttribute("aria-pressed", "true");
  await window.locator("[data-testid='contextos-roletab-all']").click();

  // Capture a full-page screenshot for human visual audit.
  await window.screenshot({ path: SCREENSHOT_PATH, fullPage: true });
  await testInfo.attach("contextos-dashboard", { path: SCREENSHOT_PATH, contentType: "image/png" });

  // Back button returns to the main view.
  await window.locator("[data-testid='contextos-back']").click();
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible();

  expect(pageErrors, "pageerror should remain empty across the ContextOS flow").toEqual([]);
  expect(
    getActionableConsoleErrors(consoleErrors),
    "console actionable errors should remain empty across the ContextOS flow",
  ).toEqual([]);
});

/**
 * 端到端证明「真正能看到 ContextOS 实时数据」：把一条真实 schema 的观测日志
 * 写入后端解析出的物理路径（runtime/events/llm.observations.jsonl），然后进入
 * ContextOS 视图，断言仪表盘把这些真实事件实时呈现出来。
 */
test("ContextOS renders REAL telemetry seeded into the observation log", async ({ window }, testInfo) => {
  // 1) 通过后端解析出观测日志的物理路径（即便文件尚不存在也会返回路径）。
  const resolved = await window.evaluate(async () => {
    const api = (window as unknown as { polaris?: { getBackendInfo: () => Promise<{ baseUrl: string; token: string }> } }).polaris;
    if (!api) return { ok: false, path: "", error: "polaris API missing" };
    const backend = await api.getBackendInfo();
    if (!backend?.baseUrl || !backend?.token) return { ok: false, path: "", error: "backend info missing" };
    const resp = await fetch(`${backend.baseUrl}/files/read?path=runtime/events/llm.observations.jsonl`, {
      headers: { authorization: `Bearer ${backend.token}` },
      cache: "no-store",
    });
    const json = (await resp.json()) as { path?: string };
    return { ok: resp.ok, path: String(json.path || ""), error: "" };
  });

  expect(resolved.ok, `backend file resolution failed: ${resolved.error}`).toBeTruthy();
  expect(resolved.path, "backend must resolve a physical observation-log path").toBeTruthy();

  // 2) 写入真实 schema 的观测记录（投影 + 携 usage 的调用 + 错误）。
  const nowEpoch = Date.now() / 1000;
  const seededLines = [
    {
      schema_version: 1, ts: new Date().toISOString(), ts_epoch: nowEpoch, seq: 1, event_id: "seed-proj",
      kind: "observation", actor: "PM", name: "prompt_context", refs: { run_id: "seed", step: 1 },
      summary: "Prompt Context Injection", ok: true,
      output: { context_hash: "seedhash", context_snapshot: "runtime/snap/seedhash.json" },
    },
    {
      schema_version: 1, ts: new Date().toISOString(), ts_epoch: nowEpoch + 1, seq: 2, event_id: "seed-call",
      kind: "observation", actor: "PM", name: "llm_call", refs: { run_id: "seed", step: 1, mode: "pm.planning" },
      summary: "seeded planning call", ok: true,
      output: { usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } },
      duration_ms: 2400,
    },
    {
      schema_version: 1, ts: new Date().toISOString(), ts_epoch: nowEpoch + 2, seq: 3, event_id: "seed-dir",
      kind: "observation", actor: "Director", name: "llm_call", refs: { run_id: "seed", mode: "director.execution" },
      summary: "seeded director call", ok: true,
      output: { usage: { prompt_tokens: 800, completion_tokens: 200, total_tokens: 1000 } },
      duration_ms: 1800,
    },
  ];
  const content = `${seededLines.map((line) => JSON.stringify(line)).join("\n")}\n`;
  fs.mkdirSync(path.dirname(resolved.path), { recursive: true });
  fs.writeFileSync(resolved.path, content, { encoding: "utf8" });

  // 3) 进入 ContextOS 并刷新以拾取种子文件（也会被 4s 轮询自动拾取）。
  await enterContextOS(window);
  await expect(window.locator("[data-testid='contextos-workspace']")).toBeVisible();
  await window.locator("[data-testid='contextos-refresh']").click();

  // 4) 断言真实遥测被实时呈现。
  const source = window.locator("[data-testid='contextos-telemetry-source']");
  await expect(source).toBeVisible();
  await expect(source).toContainText("REAL");
  await expect(source).toContainText("投影");
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("实时遥测");
  await expect(window.getByText("Prompt Context Injection", { exact: false }).first()).toBeVisible();
  await expect(window.getByText("seeded planning call", { exact: false }).first()).toBeVisible();

  // 截图供人工视觉审计（真实数据态）。
  const shot = "/tmp/contextos-realdata.png";
  await window.screenshot({ path: shot, fullPage: true });
  await testInfo.attach("contextos-real-telemetry", { path: shot, contentType: "image/png" });
});
