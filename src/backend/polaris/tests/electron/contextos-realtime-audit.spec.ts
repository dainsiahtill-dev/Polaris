import fs from "node:fs";
import path from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * ContextOS 实时视图 — Playwright 真机审计
 *
 * 目标：在真实 Electron 应用里证明
 *   1) ContextOS 入口可达、视图可渲染；
 *   2) 仪表盘的实时数据来自 Polaris **既有的实时框架**（WebSocket /v2/ws/runtime），而非文件轮询：
 *      把一条真实 schema 的 runtime 事件写入后端 runtime_events 通道文件（runtime.events.jsonl），
 *      重连后 WS 把它作为通道快照推送 → useRuntime → ContextOS 实时呈现。
 *      （ContextOS 自身不读任何文件——它只消费 WS 推送的 props。）
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
  for (const id of ["request", "truthlog", "working_mem", "projection", "role_signal", "budget", "prompt", "llm"]) {
    await expect(window.locator(`[data-testid='contextos-stage-${id}']`)).toBeVisible();
  }
  // 7 component-health cards.
  for (const id of ["truthlog", "working_mem", "projection", "role_signal", "budget", "prompt", "telemetry"]) {
    await expect(window.locator(`[data-testid='contextos-component-${id}']`)).toBeVisible();
  }
  // 5 role cards (pm/architect/chief_engineer/director/qa).
  for (const id of ["pm", "architect", "chief_engineer", "director", "qa"]) {
    await expect(window.locator(`[data-testid='contextos-role-${id}']`)).toBeVisible();
  }

  // The realtime activity chip (WebSocket-driven, no polling) is always present.
  await expect(window.locator("[data-testid='contextos-activity-chip']")).toBeVisible();

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
 * 端到端证明「ContextOS 经 WebSocket 实时框架呈现真实数据，无轮询」：
 *   把真实 schema 的 runtime 事件写入后端 runtime_events 通道文件 → 重连让 WS 推送通道快照 →
 *   进入 ContextOS → 断言仪表盘把这些事件实时呈现（含经 WS meta 保真送达的 items_count / 快照）。
 */
test("ContextOS renders REAL telemetry pushed over the runtime WebSocket (no polling)", async ({ window }, testInfo) => {
  // 1) 解析后端 runtime_events 通道文件（runtime.events.jsonl）的物理路径。
  const resolved = await window.evaluate(async () => {
    const api = (window as unknown as { polaris?: { getBackendInfo: () => Promise<{ baseUrl: string; token: string }> } }).polaris;
    if (!api) return { ok: false, path: "", error: "polaris API missing" };
    const backend = await api.getBackendInfo();
    if (!backend?.baseUrl || !backend?.token) return { ok: false, path: "", error: "backend info missing" };
    const resp = await fetch(`${backend.baseUrl}/files/read?path=runtime/events/runtime.events.jsonl`, {
      headers: { authorization: `Bearer ${backend.token}` },
      cache: "no-store",
    });
    const json = (await resp.json()) as { path?: string };
    return { ok: resp.ok, path: String(json.path || ""), error: "" };
  });

  expect(resolved.ok, `backend file resolution failed: ${resolved.error}`).toBeTruthy();
  expect(resolved.path, "backend must resolve a physical runtime-events path").toBeTruthy();

  // 2) 追加真实 schema 的 runtime 事件（context.build 装配 + context.snapshot 落盘回执）。
  //    这是 WS runtime_events 通道的来源文件；ContextOS 不读它——它只消费 WS 推送的流。
  const nowIso = new Date().toISOString();
  const nowEpoch = Date.now() / 1000;
  const lines = [
    {
      schema_version: 1, ts: nowIso, ts_epoch: nowEpoch, seq: 1, event_id: "e2e-build",
      kind: "observation", actor: "System", name: "context.build", refs: { run_id: "e2e", step: 1 },
      summary: "E2E ContextPack built", ok: true,
      // items_count / total_tokens 经 parseRuntimeEvent 的 meta(=output) 保真送达 WS。
      output: { request_hash: "e2ehash", items_count: 5, total_tokens: 3200, snapshot_path: "runtime/snap/e2e.json" },
    },
    {
      schema_version: 1, ts: nowIso, ts_epoch: nowEpoch + 1, seq: 2, event_id: "e2e-snap",
      kind: "observation", actor: "System", name: "context.snapshot", refs: { run_id: "e2e", step: 1 },
      summary: "E2E snapshot stored", ok: true,
      output: { request_hash: "e2ehash", snapshot_path: "runtime/snap/e2e.json", snapshot_hash: "e2esnap" },
    },
  ];
  const content = `${lines.map((line) => JSON.stringify(line)).join("\n")}\n`;
  fs.mkdirSync(path.dirname(resolved.path), { recursive: true });
  fs.appendFileSync(resolved.path, content, { encoding: "utf8" });

  // 3) 重连 WebSocket（reload），让 runtime_events 通道快照（tail）包含这两条事件。
  await window.reload();
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible({ timeout: 30000 });

  // 4) 进入 ContextOS。
  await enterContextOS(window);
  await expect(window.locator("[data-testid='contextos-workspace']")).toBeVisible();

  // 5) 断言真实事件经 WS 实时呈现（无轮询）。
  const source = window.locator("[data-testid='contextos-telemetry-source']");
  await expect(source).toBeVisible({ timeout: 20000 });
  await expect(source).toContainText("REAL");
  await expect(source).toContainText("投影"); // context.build → projection

  // 新鲜度徽章翻转为「实时遥测」（事件时间戳为当下）。
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("实时遥测");

  // 结构化信号经 WS meta 保真送达：WorkingMem 显示真实在窗项数（items_count=5）。
  await expect(window.locator("[data-testid='contextos-component-working_mem']")).toContainText("5 项在窗");
  // 快照回执（snapshot_hash 签名）在 Receipt · Telemetry 卡呈现。
  await expect(window.locator("[data-testid='contextos-component-telemetry']")).toContainText("快照");

  // 截图供人工视觉审计（真实 WS 数据态）。
  const shot = "/tmp/contextos-ws-realtime.png";
  await window.screenshot({ path: shot, fullPage: true });
  await testInfo.attach("contextos-ws-realtime", { path: shot, contentType: "image/png" });
});
