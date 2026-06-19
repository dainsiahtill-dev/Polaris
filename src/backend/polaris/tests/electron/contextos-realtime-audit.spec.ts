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
    await directEntry.evaluate((node) => (node as HTMLButtonElement).click());
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
  // 5 role cards (pm/architect/chief_engineer/director/qa).
  for (const id of ["pm", "architect", "chief_engineer", "director", "qa"]) {
    await expect(window.locator(`[data-testid='contextos-role-${id}']`)).toBeVisible();
  }

  // The pipeline nodes are the realtime dashboard anchors.
  await expect(window.locator("[data-testid='contextos-stage-working_mem']")).toBeVisible();
  await expect(window.locator("[data-testid='contextos-stage-projection']")).toBeVisible();

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
 * 端到端证明「ContextOS 经 WebSocket 实时框架呈现**真实生产形态**的数据，无轮询」。
 *
 * 关键（修正上一版假绿）：真实运行时事件写入的是**按 run 隔离**的文件，而非工作区级文件：
 *   - LLM 调用 → runs/<run_id>/logs/journal.norm.jsonl（CanonicalLogEventV2，channel=llm，
 *     raw.stream_event=llm_completed，raw.data.{prompt,completion}_tokens + metadata.elapsed_ms）。
 *   - 上下文装配 → runs/<run_id>/events/runtime.events.jsonl（prompt_context / context.build /
 *     context.snapshot）。
 * 故本测试按生产真实 schema 播种**per-run**文件，并写 latest_run.json 让 WS 的
 * resolve_current_run_id 指向该 run；重连后 WS 把这两个通道作快照推送 → 仪表盘实时呈现
 * 真实调用次数 / 真实 token / 真实时延 / 投影 / 在窗项数 / 快照回执。
 */
test("ContextOS renders REAL production-shape telemetry over the runtime WebSocket (no polling)", async ({ window }, testInfo) => {
  // 1) 解析工作区级 runtime.events.jsonl 物理路径，据此推导 cache_root（其父的父目录）。
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

  // <cache_root>/events/runtime.events.jsonl → cache_root = dirname(dirname(...)).
  const cacheRoot = path.dirname(path.dirname(resolved.path));
  const runId = "e2e-pm-1";
  const runDir = path.join(cacheRoot, "runs", runId);
  const journalPath = path.join(runDir, "logs", "journal.norm.jsonl");
  const eventsPath = path.join(runDir, "events", "runtime.events.jsonl");

  const nowIso = new Date().toISOString();
  const nowEpoch = Date.now() / 1000;

  // 2a) per-run journal：真实 llm_completed 行（携带真实 per-call usage 与时延）。
  const journalLine = {
    schema_version: 2, event_id: "e2e-llm-1", run_id: runId, seq: 1,
    ts: nowIso, ts_epoch: nowEpoch, channel: "llm", domain: "llm", severity: "info",
    kind: "state", actor: "pm", source: "application.roles.events",
    message: "llm response completed | completion_tokens=1454",
    tags: ["llm_realtime_bridge", "llm_event:llm_call_end", "projection_event:llm_completed"],
    raw: {
      stream_event: "llm_completed", event_type: "llm_call_end", role: "pm",
      data: {
        model: "MiniMax-M3", prompt_tokens: 1932, completion_tokens: 1454,
        context_tokens_after: 1932, metadata: { elapsed_ms: 71431.06 },
      },
    },
  };
  fs.mkdirSync(path.dirname(journalPath), { recursive: true });
  fs.writeFileSync(journalPath, `${JSON.stringify(journalLine)}\n`, { encoding: "utf8" });

  // 2b) per-run runtime events：真实 prompt_context（PM 投影）+ context.build（在窗项数）+
  //     context.snapshot（落盘回执）。全为生产真实 schema。
  const eventLines = [
    {
      schema_version: 1, ts: nowIso, ts_epoch: nowEpoch, seq: 1, event_id: "e2e-pc",
      kind: "observation", actor: "PM", name: "prompt_context", refs: { run_id: runId, step: 1 },
      summary: "Prompt Context Injection", ok: true,
      output: { run_id: runId, phase: "pm.planning", step: 1, persona_id: "pm.v1", strategy: "combined_ranking", token_usage_estimate: 0 },
    },
    {
      schema_version: 1, ts: nowIso, ts_epoch: nowEpoch + 1, seq: 2, event_id: "e2e-build",
      kind: "observation", actor: "System", name: "context.build", refs: { run_id: runId, step: 1 },
      summary: "ContextPack built", ok: true,
      output: { request_hash: "e2ehash", items_count: 5, total_tokens: 3200, snapshot_path: "runtime/snap/e2e.json", snapshot_hash: "e2esnap" },
    },
    {
      schema_version: 1, ts: nowIso, ts_epoch: nowEpoch + 2, seq: 3, event_id: "e2e-snap",
      kind: "observation", actor: "System", name: "context.snapshot", refs: { run_id: runId, step: 1 },
      summary: "Context snapshot stored", ok: true,
      output: { request_hash: "e2ehash", snapshot_path: "runtime/snap/e2e.json", snapshot_hash: "e2esnap" },
    },
  ];
  fs.mkdirSync(path.dirname(eventsPath), { recursive: true });
  fs.writeFileSync(eventsPath, `${eventLines.map((l) => JSON.stringify(l)).join("\n")}\n`, { encoding: "utf8" });

  // 2c) latest_run.json：让 WS resolve_current_run_id 指向该 run（per-run 通道解析的前提）。
  fs.writeFileSync(path.join(cacheRoot, "latest_run.json"), JSON.stringify({ run_id: runId }), { encoding: "utf8" });

  // 3) 重连 WebSocket（reload），让 llm + runtime_events 通道快照（per-run tail）包含这些事件。
  await window.reload();
  await expect(window.locator("[data-testid='project-progress-panel']")).toBeVisible({ timeout: 30000 });

  // 4) 进入 ContextOS。
  await enterContextOS(window);
  await expect(window.locator("[data-testid='contextos-workspace']")).toBeVisible();

  // 5) 断言真实生产形态事件经 WS 实时呈现（无轮询）。
  const source = window.locator("[data-testid='contextos-telemetry-source']");
  await expect(source).toBeVisible({ timeout: 20000 });
  await expect(source).toContainText("REAL");
  await expect(source).toContainText("调用"); // llm_completed → 1 次真实调用
  await expect(source).toContainText("投影"); // prompt_context / context.build → projection

  // 新鲜度徽章翻转为「实时遥测」（事件时间戳为当下）。
  await expect(window.getByTestId("contextos-telemetry-freshness")).toContainText("实时遥测");

  // 真实 per-call token（journal raw.data）实时呈现，标注「实时」，而非「需诊断端点」空态。
  await expect(window.getByText(/tokens · 实时/)).toBeVisible({ timeout: 20000 });
  await expect(window.getByTestId("contextos-tokens-unavailable")).toHaveCount(0);

  // 结构化信号经 WS meta 保真送达：WorkingMem 显示真实在窗项数（items_count=5）。
  await expect(window.locator("[data-testid='contextos-stage-working_mem']")).toContainText("5 项在窗");
  await expect(window.locator("[data-testid='contextos-stage-projection']")).toContainText("2 投影");
  // 快照回执（context.snapshot 的 snapshot_hash 签名）在决策 / 回执流中呈现。
  await expect(window.getByText("Context snapshot stored", { exact: false })).toBeVisible();

  // 截图供人工视觉审计（真实 WS 数据态）。
  const shot = "/tmp/contextos-ws-realtime.png";
  await window.screenshot({ path: shot, fullPage: true });
  await testInfo.attach("contextos-ws-realtime", { path: shot, contentType: "image/png" });
});
